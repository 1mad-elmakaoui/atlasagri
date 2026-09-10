"""Moteur d'impact agricole.

Traduit des conditions environnementales en risque pour une culture, dans une
région, à une saison donnée.

    météo + culture + région + saison/stade = risque agricole

Deux principes gouvernent ce module :

1. **Aucun seuil colombien n'y figure.** Les seuils viennent de la culture
   (`domain/crops.py`) ou d'une calibration par quantiles sur l'historique
   local. Chaque évaluation expose la provenance du seuil utilisé.
2. **Le moteur ne rédige pas de prose.** Il produit des faits structurés.
   La mise en français destinée à l'utilisateur appartient à la couche
   d'explication, et l'éventuelle reformulation par l'agent vient encore après.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.crops import Crop, get_crop
from app.domain.enums import Confidence, DataState, RiskLevel, RiskType
from app.domain.provenance import SOURCE_RISK_ENGINE, DataSourceRef
from app.domain.thresholds import RiskThreshold
from app.services.nowcasting import HorizonForecast, NowcastResult

# Correspondance entre un type de risque et la grandeur prévue qui le porte.
VARIABLE_FOR_RISK: dict[RiskType, str] = {
    RiskType.HEAVY_RAIN: "cumulative_precipitation_mm",
    RiskType.HEAT_STRESS: "max_temperature_c",
    RiskType.FROST: "min_temperature_c",
    RiskType.STRONG_WIND: "max_wind_gust_kmh",
    RiskType.DROUGHT: "min_soil_moisture",
}


class RiskDriver(BaseModel):
    """Facteur ayant contribué à un niveau de risque.

    Porte la valeur, le seuil franchi et la provenance de ce seuil : c'est ce
    qui permet à l'utilisateur de contester une évaluation en connaissance de
    cause, au lieu de subir un score opaque.
    """

    model_config = ConfigDict(frozen=True)

    risk_type: RiskType
    label_fr: str
    variable: str
    value: float
    unit: str
    threshold_moderate: float
    threshold_high: float
    level: RiskLevel
    severity: float = Field(ge=0, le=1)
    state: DataState
    threshold_provenance_fr: str


class AgriculturalRisk(BaseModel):
    """Risque agricole évalué pour une culture, un lieu et un horizon."""

    model_config = ConfigDict(frozen=True)

    crop_code: str
    crop_name_fr: str
    region_code: str | None
    coordinates: tuple[float, float]
    horizon_hours: int
    valid_from: datetime
    valid_to: datetime

    score: float = Field(ge=0, le=1)
    level: RiskLevel
    confidence: Confidence
    drivers: tuple[RiskDriver, ...]
    seasonal_sensitivity: float = Field(
        description="Multiplicateur appliqué en période sensible de la culture"
    )
    data_state: DataState
    sources: tuple[DataSourceRef, ...]
    computed_at: datetime

    @property
    def dominant_driver(self) -> RiskDriver | None:
        return max(self.drivers, key=lambda d: d.severity, default=None)


class AgriculturalImpactEngine:
    """Évalue le risque agricole à partir d'un nowcast."""

    def evaluate(
        self,
        *,
        nowcast: NowcastResult,
        crop_code: str,
        region_code: str | None = None,
        horizon_hours: int = 24,
        threshold_overrides: dict[RiskType, RiskThreshold] | None = None,
    ) -> AgriculturalRisk:
        crop = get_crop(crop_code)
        forecast = nowcast.at_horizon(horizon_hours)
        month = forecast.valid_to.astimezone(UTC).month
        sensitivity = crop.sensitivity_at(month)

        drivers = self._drivers_for(crop, forecast, threshold_overrides or {})
        score = self._aggregate(drivers, sensitivity)

        return AgriculturalRisk(
            crop_code=crop.code,
            crop_name_fr=crop.name_fr,
            region_code=region_code,
            coordinates=nowcast.coordinates,
            horizon_hours=horizon_hours,
            valid_from=forecast.valid_from,
            valid_to=forecast.valid_to,
            score=score,
            level=RiskLevel.from_score(score),
            confidence=forecast.confidence,
            drivers=drivers,
            seasonal_sensitivity=sensitivity,
            data_state=nowcast.input_state,
            sources=(nowcast.input_source, SOURCE_RISK_ENGINE),
            computed_at=datetime.now(UTC),
        )

    # --- calcul ---

    def _drivers_for(
        self,
        crop: Crop,
        forecast: HorizonForecast,
        overrides: dict[RiskType, RiskThreshold],
    ) -> tuple[RiskDriver, ...]:
        drivers: list[RiskDriver] = []

        for risk_type, attribute in VARIABLE_FOR_RISK.items():
            threshold = overrides.get(risk_type) or crop.threshold_for(risk_type)
            if threshold is None:
                continue

            value = getattr(forecast, attribute, None)
            if value is None:
                # Donnée absente : on n'évalue pas ce facteur plutôt que de
                # supposer qu'il est favorable.
                continue

            level = threshold.evaluate(value)
            if level is RiskLevel.LOW:
                continue

            drivers.append(
                RiskDriver(
                    risk_type=risk_type,
                    label_fr=risk_type.label_fr,
                    variable=threshold.variable,
                    value=round(float(value), 2),
                    unit=threshold.unit,
                    threshold_moderate=threshold.moderate_at,
                    threshold_high=threshold.high_at,
                    level=level,
                    severity=round(threshold.normalized_severity(float(value)), 3),
                    state=forecast.state,
                    threshold_provenance_fr=threshold.provenance_fr,
                )
            )

        return tuple(sorted(drivers, key=lambda d: d.severity, reverse=True))

    def _aggregate(self, drivers: tuple[RiskDriver, ...], sensitivity: float) -> float:
        """Combine plusieurs facteurs en un score unique.

        La somme serait fausse (deux risques modérés ne font pas un risque
        critique) et le maximum seul perdrait l'information de cumul. On retient
        donc le facteur dominant, majoré d'une fraction décroissante des
        suivants : deux menaces simultanées aggravent la situation sans la
        doubler mécaniquement.
        """
        if not drivers:
            return 0.0

        ordered = sorted((d.severity for d in drivers), reverse=True)
        score = ordered[0]
        for rank, severity in enumerate(ordered[1:], start=1):
            score += severity * (0.35 / rank)

        return round(min(1.0, score * sensitivity), 3)


def summarize_for_business(risk: AgriculturalRisk) -> dict[str, Any]:
    """Résumé en langage métier, sans vocabulaire technique.

    Utilisé par l'interface et par le panneau de preuves. Le moteur reste
    responsable des faits ; cette fonction ne fait que les formuler.
    """
    dominant = risk.dominant_driver
    if dominant is None:
        situation = "Aucune condition défavorable détectée sur cet horizon."
    else:
        situation = (
            f"{dominant.label_fr} : {dominant.value} {dominant.unit} attendus "
            f"sur les {risk.horizon_hours} prochaines heures, "
            f"pour un seuil d'alerte à {dominant.threshold_moderate} {dominant.unit}."
        )

    caveat = None
    if risk.data_state is DataState.SIMULATED:
        caveat = (
            "Attention : cette évaluation repose sur des données simulées "
            "(mode démonstration) et ne doit pas fonder une décision réelle."
        )

    return {
        "culture": risk.crop_name_fr,
        "niveau": risk.level.label_fr,
        "score": risk.score,
        "confiance": risk.confidence.label_fr,
        "situation_fr": situation,
        "periode_sensible": risk.seasonal_sensitivity > 1.0,
        "avertissement_fr": caveat,
        "facteurs": [
            {
                "type": d.label_fr,
                "valeur": d.value,
                "unite": d.unit,
                "niveau": d.level.label_fr,
                "origine_seuil_fr": d.threshold_provenance_fr,
            }
            for d in risk.drivers
        ],
    }
