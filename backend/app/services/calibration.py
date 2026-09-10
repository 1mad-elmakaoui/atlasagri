"""Calibration des seuils sur historique réel.

Applique au Maroc la méthode de l'article de Silva-Sosa : les bornes de risque
sont dérivées des **quantiles de la distribution historique locale**, et non
fixées en valeur absolue.

C'est le seul transfert honnête possible depuis un travail colombien. On
transfère le procédé, jamais les nombres : un déficit pluviométrique signalant
une sécheresse sévère en zone andine n'a aucune signification transposable dans
le Souss semi-aride.

Ce module remplace les seuils provisoires livrés par défaut. Un seuil calibré
porte sa fenêtre, sa taille d'échantillon et ses quantiles ; l'interface affiche
cette provenance, ce qui permet à un exploitant de constater qu'une évaluation
repose sur dix ans de données locales plutôt que sur une hypothèse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.db.base import CalibratedThreshold
from app.db.repositories import TenantRepository
from app.domain.crops import CROPS
from app.domain.enums import DataState, RiskType
from app.domain.geo import Coordinates
from app.domain.morocco import REGIONS
from app.domain.plausibility import envelope_for
from app.domain.thresholds import (
    InsufficientHistoryError,
    QuantileThresholdCalibrator,
    RiskThreshold,
)
from app.providers.registry import get_weather_provider
from app.providers.weather.interface import WeatherSeries

logger = get_logger(__name__)


class SimulatedHistoryError(ProviderUnavailableError):
    """Tentative de calibration sur un historique simulé."""

    code = "historique_simule"

# Dix ans couvrent plusieurs cycles secs et humides. Une fenêtre plus courte
# ferait passer une succession d'années sèches pour la normale, et abaisserait
# les seuils au point de ne plus rien détecter.
DEFAULT_YEARS = 10

# L'archive météo accuse quelques jours de latence : on s'arrête avant.
ARCHIVE_LAG_DAYS = 7

# Quantiles opérationnels, volontairement différents de ceux de l'article.
#
# Silva-Sosa retient les 33ᵉ et 67ᵉ percentiles « pour créer des catégories de
# risque équilibrées » — un objectif d'analyse, où l'on veut trois classes de
# taille comparable.
#
# Un produit d'alerte a l'objectif inverse. Avec 33/67, un tiers des journées
# serait classé « modéré » et un tiers « élevé » : l'exploitant recevrait des
# alertes en permanence et cesserait de les lire en une semaine. Une alerte doit
# signaler ce qui sort de l'ordinaire, donc porter sur la queue de distribution.
#
# Nous conservons la méthode de l'article — quantiles empiriques de la
# distribution locale, avec provenance — et changeons les niveaux.
OPERATIONAL_QUANTILES = (0.85, 0.95, 0.99)


@dataclass(frozen=True)
class CalibrationTarget:
    """Grandeur à calibrer, et façon de l'extraire d'une série horaire."""

    risk_type: RiskType
    variable: str
    unit: str
    window_hours: int
    aggregation: str  # "sum" | "max" | "min"
    attribute: str
    direction: str = "above"
    # Pour les cumuls de pluie, la plupart des fenêtres sont sèches. Les inclure
    # placerait les percentiles 33 et 67 à zéro et rendrait le seuil inutile :
    # on calibre donc sur les seuls épisodes réellement pluvieux.
    minimum_value: float | None = None


TARGETS: tuple[CalibrationTarget, ...] = (
    CalibrationTarget(
        risk_type=RiskType.HEAVY_RAIN,
        variable="precipitation_48h_mm",
        unit="mm",
        window_hours=48,
        aggregation="sum",
        attribute="precipitation_mm",
        minimum_value=1.0,
    ),
    CalibrationTarget(
        risk_type=RiskType.HEAT_STRESS,
        variable="temperature_max_c",
        unit="°C",
        window_hours=24,
        aggregation="max",
        attribute="temperature_c",
    ),
    CalibrationTarget(
        risk_type=RiskType.FROST,
        variable="temperature_min_c",
        unit="°C",
        window_hours=24,
        aggregation="min",
        attribute="temperature_c",
        direction="below",
    ),
    CalibrationTarget(
        risk_type=RiskType.STRONG_WIND,
        variable="wind_gust_kmh",
        unit="km/h",
        window_hours=24,
        aggregation="max",
        attribute="wind_gust_kmh",
    ),
)


@dataclass
class CalibrationReport:
    """Compte rendu d'une campagne de calibration."""

    calibrated: list[CalibratedThreshold]
    skipped: list[dict[str, str]]
    window: str

    def as_summary_fr(self) -> dict:
        return {
            "fenetre": self.window,
            "seuils_calibres": len(self.calibrated),
            "seuils_ignores": len(self.skipped),
            "detail_calibres": [
                {
                    "region": REGIONS[t.region_code].name_fr
                    if t.region_code in REGIONS
                    else t.region_code,
                    "culture": t.crop_code,
                    "variable": t.variable,
                    "modere": round(t.moderate_at, 1),
                    "eleve": round(t.high_at, 1),
                    "critique": round(t.critical_at, 1) if t.critical_at is not None else None,
                    "unite": t.unit,
                    "echantillon": t.sample_size,
                }
                for t in self.calibrated
            ],
            "detail_ignores": self.skipped,
        }


class ThresholdCalibrationService:
    """Interroge l'archive météo et dérive des seuils par région et culture."""

    def __init__(
        self,
        repo: TenantRepository,
        *,
        calibrator: QuantileThresholdCalibrator | None = None,
    ) -> None:
        self.repo = repo
        low, high, critical = OPERATIONAL_QUANTILES
        self.calibrator = calibrator or QuantileThresholdCalibrator(
            low_quantile=low, high_quantile=high, critical_quantile=critical
        )

    async def calibrate_region(
        self, region_code: str, *, years: int = DEFAULT_YEARS
    ) -> CalibrationReport:
        if region_code not in REGIONS:
            raise InsufficientHistoryError(f"Région inconnue : « {region_code} ».")

        region = REGIONS[region_code]
        end = date.today() - timedelta(days=ARCHIVE_LAG_DAYS)
        start = date(end.year - years, end.month, end.day)
        window = f"{start.isoformat()}..{end.isoformat()}"

        provider = get_weather_provider()
        try:
            series = await provider.get_historical(
                Coordinates(region.center.latitude, region.center.longitude),
                start_date=start.isoformat(),
                end_date=end.isoformat(),
            )
        except ProviderUnavailableError as exc:
            # On remonte l'indisponibilité telle quelle : calibrer sur une série
            # partielle produirait des seuils faux qui feraient ensuite autorité.
            raise ProviderUnavailableError(
                f"Calibration impossible pour {region.name_fr} : "
                f"l'archive météo est injoignable. {exc.message_fr}"
            ) from exc

        # Un jeu de démonstration produirait des seuils crédibles en apparence
        # et faux en pratique. Les persister ferait autorité sur des évaluations
        # réelles : on refuse plutôt que de contaminer la configuration.
        premiere = next(iter(series.hours), None)
        if premiere is not None and premiere.state is DataState.SIMULATED:
            raise SimulatedHistoryError(
                "Calibration refusée : l'historique provient d'un jeu de "
                "démonstration simulé. Un seuil calibré sur des données simulées "
                "ferait autorité sur des décisions réelles. "
                "Configurer WEATHER_PROVIDER=openmeteo pour une calibration valide."
            )

        calibrated: list[CalibratedThreshold] = []
        skipped: list[dict[str, str]] = []

        for crop_code in region.main_crops:
            if crop_code not in CROPS:
                continue
            for target in TARGETS:
                # On ne calibre que les grandeurs pour lesquelles la culture a
                # une sensibilité déclarée : produire un seuil de gel pour une
                # culture qui n'y est pas sensible n'aurait aucun sens.
                if CROPS[crop_code].threshold_for(target.risk_type) is None:
                    continue

                observations = _extract(series, target)
                try:
                    seuil = self.calibrator.calibrate(
                        observations=observations,
                        risk_type=target.risk_type,
                        variable=target.variable,
                        unit=target.unit,
                        direction=target.direction,  # type: ignore[arg-type]
                        source_label_fr=series.source.label_fr,
                        region_code=region_code,
                        crop_code=crop_code,
                        calibration_window=window,
                    )
                except InsufficientHistoryError as exc:
                    skipped.append(
                        {
                            "region": region.name_fr,
                            "culture": crop_code,
                            "variable": target.variable,
                            "motif_fr": str(exc),
                        }
                    )
                    continue

                motif = _implausible(seuil)
                if motif is not None:
                    skipped.append(
                        {
                            "region": region.name_fr,
                            "culture": crop_code,
                            "variable": target.variable,
                            "motif_fr": motif,
                        }
                    )
                    continue

                calibrated.append(self._persist(seuil, region_code, crop_code))

        self.repo.session.commit()
        logger.info(
            "Calibration terminée",
            context={
                "region": region_code,
                "calibres": len(calibrated),
                "ignores": len(skipped),
                "fenetre": window,
            },
        )
        return CalibrationReport(calibrated=calibrated, skipped=skipped, window=window)

    async def calibrate_active_regions(
        self, *, years: int = DEFAULT_YEARS
    ) -> CalibrationReport:
        """Calibre les régions où l'organisation possède réellement des sites."""
        actives = sorted({site.region_code for site in self.repo.sites()})
        cumul = CalibrationReport(calibrated=[], skipped=[], window="")

        for code in actives:
            if code not in REGIONS:
                continue
            rapport = await self.calibrate_region(code, years=years)
            cumul.calibrated.extend(rapport.calibrated)
            cumul.skipped.extend(rapport.skipped)
            cumul.window = rapport.window
        return cumul

    def _persist(
        self, seuil: RiskThreshold, region_code: str, crop_code: str
    ) -> CalibratedThreshold:
        """Écrit ou met à jour le seuil pour cette portée.

        La mise à jour est ici légitime, contrairement aux résultats de terrain :
        un seuil est un paramètre du système, recalculé à chaque campagne, pas
        une observation qu'on aurait le droit de réécrire.
        """
        existant = next(
            (
                t
                for t in self.repo.calibrated_thresholds(
                    CalibratedThreshold.region_code == region_code,
                    CalibratedThreshold.crop_code == crop_code,
                    CalibratedThreshold.variable == seuil.variable,
                )
            ),
            None,
        )
        cible = existant or CalibratedThreshold(
            tenant_id=self.repo.context.tenant_id,
            region_code=region_code,
            crop_code=crop_code,
            risk_type=seuil.risk_type.value,
            variable=seuil.variable,
        )

        cible.unit = seuil.unit
        cible.direction = seuil.direction
        cible.moderate_at = seuil.moderate_at
        cible.high_at = seuil.high_at
        cible.critical_at = seuil.critical_at
        cible.source_label = seuil.source_label_fr
        cible.calibration_window = seuil.calibration_window or ""
        cible.sample_size = seuil.calibration_sample_size or 0
        cible.low_quantile = (seuil.quantiles_used or (0.33, 0.67))[0]
        cible.high_quantile = (seuil.quantiles_used or (0.33, 0.67))[1]
        cible.calibrated_on = datetime.now(UTC)

        if existant is None:
            self.repo.add(cible)
        return cible


def _implausible(seuil: RiskThreshold) -> str | None:
    """Contrôle qu'un seuil calibré a un sens agronomique.

    La statistique produit une valeur quelle que soit la distribution ; encore
    faut-il que l'aléa existe localement et que les bornes se distinguent.
    """
    enveloppe = envelope_for(seuil.risk_type)
    if enveloppe is None:
        return None
    return enveloppe.reject_reason(seuil.moderate_at, seuil.high_at, seuil.critical_at)


def _extract(series: WeatherSeries, target: CalibrationTarget) -> list[float]:
    """Découpe la série en fenêtres glissantes et agrège chacune.

    Le pas est d'un jour et non d'une heure : des fenêtres de 48 h décalées
    d'une heure sont quasi identiques, et les traiter comme des observations
    indépendantes gonflerait artificiellement la taille d'échantillon, donc la
    confiance apparente du seuil.
    """
    hours = series.hours
    if not hours:
        return []

    valeurs: list[float] = []
    pas = 24
    for debut in range(0, max(0, len(hours) - target.window_hours), pas):
        fenetre = hours[debut : debut + target.window_hours]
        mesures = [
            getattr(h, target.attribute)
            for h in fenetre
            if getattr(h, target.attribute) is not None
        ]
        if len(mesures) < target.window_hours * 0.7:
            continue  # fenêtre trop lacunaire pour être représentative

        if target.aggregation == "sum":
            valeur = sum(mesures)
        elif target.aggregation == "max":
            valeur = max(mesures)
        else:
            valeur = min(mesures)

        if target.minimum_value is not None and valeur < target.minimum_value:
            continue
        valeurs.append(valeur)

    return valeurs


def load_overrides(
    repo: TenantRepository, *, region_code: str, crop_code: str
) -> dict[RiskType, RiskThreshold]:
    """Seuils calibrés à substituer aux valeurs provisoires.

    Renvoie un dictionnaire vide si rien n'est calibré pour cette portée : le
    moteur retombe alors sur les seuils provisoires, qui se déclarent comme tels.
    """
    rows = repo.calibrated_thresholds(
        CalibratedThreshold.region_code == region_code,
        CalibratedThreshold.crop_code == crop_code,
    )

    overrides: dict[RiskType, RiskThreshold] = {}
    for row in rows:
        overrides[RiskType(row.risk_type)] = RiskThreshold(
            risk_type=RiskType(row.risk_type),
            variable=row.variable,
            unit=row.unit,
            direction=row.direction,  # type: ignore[arg-type]
            moderate_at=row.moderate_at,
            high_at=row.high_at,
            critical_at=row.critical_at,
            origin="calibrated",
            source_label_fr=row.source_label,
            region_code=row.region_code,
            crop_code=row.crop_code,
            calibration_window=row.calibration_window,
            calibration_sample_size=row.sample_size,
            calibrated_on=row.calibrated_on.date(),
            quantiles_used=(row.low_quantile, row.high_quantile),
        )
    return overrides
