"""Traçabilité des valeurs.

Une plateforme de décision B2B se juge sur sa crédibilité. Une valeur affichée
sans indication de sa nature (mesurée ? prévue ? déduite ?) détruit cette
crédibilité au premier écart constaté.

Chaque grandeur exposée par le produit transite donc par `Measure`, qui porte
sa valeur, son état épistémique, sa source et son horodatage.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Confidence, DataState


class DataSourceRef(BaseModel):
    """Référence à une source de données, affichable dans le panneau de preuves."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Identifiant technique, ex. 'open-meteo'")
    label_fr: str = Field(description="Nom affiché à l'utilisateur")
    kind: str = Field(description="weather | satellite | routing | internal | model")
    detail: str | None = Field(default=None, description="Précision : endpoint, version…")

    def __str__(self) -> str:  # pragma: no cover - confort de lecture
        return self.label_fr


class Measure(BaseModel):
    """Une valeur numérique accompagnée de sa provenance.

    `unit` reste dans le domaine parce que l'unité fait partie du sens de la
    valeur : une exposition en millimètres et une exposition en heures ne se
    comparent pas.
    """

    model_config = ConfigDict(frozen=True)

    value: float
    unit: str
    state: DataState
    source: DataSourceRef
    observed_at: datetime | None = None
    valid_at: datetime | None = Field(
        default=None, description="Instant auquel la valeur s'applique (prévision)"
    )
    confidence: Confidence | None = None

    @property
    def is_trustworthy_as_fact(self) -> bool:
        """Vrai seulement si la valeur repose sur une mesure ou une prévision réelle."""
        return self.state in (DataState.OBSERVED, DataState.FORECAST, DataState.DERIVED)


class Evidence(BaseModel):
    """Élément de preuve destiné au panneau « Sources et preuves ».

    Formulé en langage métier : l'utilisateur ne doit jamais avoir à lire un log
    technique pour comprendre sur quoi repose une recommandation.
    """

    label_fr: str
    detail_fr: str
    state: DataState
    source: DataSourceRef
    observed_at: datetime | None = None
    value: float | None = None
    unit: str | None = None


# --- Sources connues du produit -------------------------------------------------

SOURCE_OPEN_METEO = DataSourceRef(
    id="open-meteo",
    label_fr="Open-Meteo (prévisions et réanalyse)",
    kind="weather",
    detail="Modèles météorologiques nationaux agrégés, résolution horaire",
)

SOURCE_OPEN_METEO_ARCHIVE = DataSourceRef(
    id="open-meteo-archive",
    label_fr="Open-Meteo Archive (historique)",
    kind="weather",
    detail="Réanalyse ERA5, utilisée pour la calibration des seuils",
)

SOURCE_OFFLINE_DEMO = DataSourceRef(
    id="jeu-demo-local",
    label_fr="Jeu de démonstration local (données simulées)",
    kind="weather",
    detail=(
        "Utilisé uniquement lorsque l'accès réseau au fournisseur météo est "
        "indisponible. Toutes les valeurs sont étiquetées « Simulé »."
    ),
)

SOURCE_ROAD_NETWORK = DataSourceRef(
    id="reseau-routier-reference",
    label_fr="Réseau routier de référence (axes marocains)",
    kind="routing",
    detail="Villes et axes réels, distances routières de référence",
)

SOURCE_OSRM = DataSourceRef(
    id="osrm",
    label_fr="Moteur de routage OSRM",
    kind="routing",
    detail="Géométrie routière issue d'OpenStreetMap",
)

SOURCE_INTERNAL_DB = DataSourceRef(
    id="atlasagri-db",
    label_fr="Données internes AtlasAgri",
    kind="internal",
    detail="Stocks, fournisseurs, expéditions et contrats de l'organisation",
)

SOURCE_NOWCAST_BASELINE = DataSourceRef(
    id="nowcast-baseline",
    label_fr="Modèle de nowcasting AtlasAgri (baseline statistique)",
    kind="model",
    detail="Persistance amortie + variance climatologique, versionné",
)

SOURCE_RISK_ENGINE = DataSourceRef(
    id="moteur-risque",
    label_fr="Moteur de risque AtlasAgri",
    kind="model",
    detail="Règles déterministes et seuils calibrés par quantiles",
)
