"""Cultures, produits et sensibilités agro-climatiques.

Les seuils déclarés ici sont des **valeurs de départ provisoires**, étiquetées
comme telles. Ils sont destinés à être remplacés par des seuils calibrés sur
l'historique marocain (cf. `QuantileThresholdCalibrator`) dès qu'un historique
suffisant est disponible, puis validés par des agronomes.

Cette honnêteté est structurelle et non cosmétique : `RiskThreshold.origin`
circule jusqu'à l'interface, où l'utilisateur voit si une évaluation repose sur
un seuil calibré ou sur une hypothèse à confirmer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import RiskType
from app.domain.thresholds import RiskThreshold

PROVISIONAL_SOURCE = (
    "Valeur de départ AtlasAgri, établie à partir de la littérature agronomique "
    "générale et à calibrer sur données marocaines"
)


@dataclass(frozen=True)
class GrowthWindow:
    """Fenêtre de sensibilité d'une culture, par mois.

    L'article de référence montre qu'une corrélation agrégée peut masquer une
    sensibilité réelle : le riz y présentait r = 0,09 avec la pluie mensuelle
    alors que sa sensibilité est forte mais concentrée sur des stades précis.
    On modélise donc explicitement la période sensible plutôt que de raisonner
    en moyenne annuelle.
    """

    label_fr: str
    months: tuple[int, ...]
    sensitivity_multiplier: float = 1.0


@dataclass(frozen=True)
class Crop:
    code: str
    name_fr: str
    perishability_days: int
    requires_cold_chain: bool
    thresholds: tuple[RiskThreshold, ...]
    growth_windows: tuple[GrowthWindow, ...]
    notes_fr: str = ""

    def threshold_for(self, risk_type: RiskType) -> RiskThreshold | None:
        for t in self.thresholds:
            if t.risk_type is risk_type:
                return t
        return None

    def sensitivity_at(self, month: int) -> float:
        """Multiplicateur de sensibilité pour un mois donné (1.0 hors fenêtre sensible)."""
        for window in self.growth_windows:
            if month in window.months:
                return window.sensitivity_multiplier
        return 1.0


def _threshold(
    risk_type: RiskType,
    variable: str,
    unit: str,
    moderate: float,
    high: float,
    critical: float | None,
    crop_code: str,
    direction: str = "above",
) -> RiskThreshold:
    return RiskThreshold(
        risk_type=risk_type,
        variable=variable,
        unit=unit,
        direction=direction,  # type: ignore[arg-type]
        moderate_at=moderate,
        high_at=high,
        critical_at=critical,
        origin="provisional_expert",
        source_label_fr=PROVISIONAL_SOURCE,
        crop_code=crop_code,
    )


CROPS: dict[str, Crop] = {
    "TOMATE": Crop(
        code="TOMATE",
        name_fr="Tomate",
        perishability_days=10,
        requires_cold_chain=True,
        thresholds=(
            _threshold(RiskType.HEAVY_RAIN, "precipitation_48h_mm", "mm", 25, 50, 80, "TOMATE"),
            _threshold(RiskType.HEAT_STRESS, "temperature_max_c", "°C", 32, 36, 40, "TOMATE"),
            _threshold(RiskType.FROST, "temperature_min_c", "°C", 4, 2, 0, "TOMATE", "below"),
            _threshold(RiskType.STRONG_WIND, "wind_gust_kmh", "km/h", 50, 70, 90, "TOMATE"),
        ),
        growth_windows=(
            GrowthWindow("Campagne primeurs (récolte et export)", (10, 11, 12, 1, 2, 3, 4, 5), 1.4),
        ),
        notes_fr=(
            "Culture sous serre dans le Souss-Massa, destinée majoritairement à l'export. "
            "La chaîne du froid conditionne la valeur marchande : une rupture de froid "
            "coûte plus cher qu'un retard de quelques heures."
        ),
    ),
    "AGRUME": Crop(
        code="AGRUME",
        name_fr="Agrumes",
        perishability_days=21,
        requires_cold_chain=True,
        thresholds=(
            _threshold(RiskType.HEAVY_RAIN, "precipitation_48h_mm", "mm", 35, 65, 100, "AGRUME"),
            _threshold(RiskType.HEAT_STRESS, "temperature_max_c", "°C", 38, 42, 45, "AGRUME"),
            _threshold(RiskType.FROST, "temperature_min_c", "°C", 2, 0, -2, "AGRUME", "below"),
            _threshold(RiskType.STRONG_WIND, "wind_gust_kmh", "km/h", 60, 80, 100, "AGRUME"),
        ),
        growth_windows=(GrowthWindow("Récolte", (11, 12, 1, 2, 3, 4), 1.3),),
        notes_fr="Le vent fort provoque des chutes de fruits et des blessures d'épiderme.",
    ),
    "OLIVE": Crop(
        code="OLIVE",
        name_fr="Olive",
        perishability_days=5,
        requires_cold_chain=False,
        thresholds=(
            _threshold(RiskType.HEAVY_RAIN, "precipitation_48h_mm", "mm", 40, 70, 110, "OLIVE"),
            _threshold(RiskType.HEAT_STRESS, "temperature_max_c", "°C", 40, 44, 47, "OLIVE"),
            _threshold(RiskType.STRONG_WIND, "wind_gust_kmh", "km/h", 65, 85, 105, "OLIVE"),
        ),
        growth_windows=(GrowthWindow("Récolte et trituration", (10, 11, 12, 1), 1.5),),
        notes_fr=(
            "Le délai entre récolte et trituration conditionne l'acidité de l'huile : "
            "un retard logistique dégrade directement la qualité commerciale."
        ),
    ),
    "BLE": Crop(
        code="BLE",
        name_fr="Blé",
        perishability_days=180,
        requires_cold_chain=False,
        thresholds=(
            _threshold(RiskType.HEAVY_RAIN, "precipitation_48h_mm", "mm", 45, 80, 120, "BLE"),
            _threshold(RiskType.HEAT_STRESS, "temperature_max_c", "°C", 34, 38, 42, "BLE"),
            _threshold(RiskType.DROUGHT, "soil_moisture_index", "indice", 0.35, 0.25, 0.15, "BLE", "below"),
        ),
        growth_windows=(
            GrowthWindow("Montaison et remplissage du grain", (3, 4, 5), 1.6),
            GrowthWindow("Moisson", (6, 7), 1.2),
        ),
        notes_fr=(
            "Peu périssable, donc la contrainte dominante est le coût de transport et "
            "non le délai. Sensible au stress thermique au remplissage du grain."
        ),
    ),
    "BETTERAVE": Crop(
        code="BETTERAVE",
        name_fr="Betterave sucrière",
        perishability_days=14,
        requires_cold_chain=False,
        thresholds=(
            _threshold(RiskType.HEAVY_RAIN, "precipitation_48h_mm", "mm", 40, 70, 100, "BETTERAVE"),
            _threshold(RiskType.HEAT_STRESS, "temperature_max_c", "°C", 36, 40, 44, "BETTERAVE"),
        ),
        growth_windows=(GrowthWindow("Arrachage et livraison sucrerie", (5, 6, 7), 1.4),),
        notes_fr="Les fortes pluies rendent les parcelles impraticables pour l'arrachage.",
    ),
    "FRAISE": Crop(
        code="FRAISE",
        name_fr="Fraise",
        perishability_days=5,
        requires_cold_chain=True,
        thresholds=(
            _threshold(RiskType.HEAVY_RAIN, "precipitation_48h_mm", "mm", 20, 40, 65, "FRAISE"),
            _threshold(RiskType.HEAT_STRESS, "temperature_max_c", "°C", 30, 34, 38, "FRAISE"),
            _threshold(RiskType.FROST, "temperature_min_c", "°C", 3, 1, -1, "FRAISE", "below"),
        ),
        growth_windows=(GrowthWindow("Récolte et export", (12, 1, 2, 3, 4, 5), 1.5),),
        notes_fr=(
            "Très forte périssabilité : la fraise du Loukkos est expédiée vers l'Europe "
            "en 48 h maximum. Toute rupture de froid est quasi rédhibitoire."
        ),
    ),
    "POMME_DE_TERRE": Crop(
        code="POMME_DE_TERRE",
        name_fr="Pomme de terre",
        perishability_days=45,
        requires_cold_chain=False,
        thresholds=(
            _threshold(RiskType.HEAVY_RAIN, "precipitation_48h_mm", "mm", 35, 60, 95, "POMME_DE_TERRE"),
            _threshold(RiskType.HEAT_STRESS, "temperature_max_c", "°C", 33, 37, 41, "POMME_DE_TERRE"),
        ),
        growth_windows=(GrowthWindow("Arrachage", (4, 5, 6, 11, 12), 1.3),),
    ),
}


def get_crop(code: str) -> Crop:
    if code not in CROPS:
        raise KeyError(
            f"Culture inconnue : '{code}'. Cultures disponibles : {sorted(CROPS)}"
        )
    return CROPS[code]
