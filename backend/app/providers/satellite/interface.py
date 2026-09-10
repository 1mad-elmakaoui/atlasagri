"""Contrat SatelliteProvider.

Position de principe : sans identifiants Copernicus valides, le produit déclare
les indicateurs satellite **indisponibles**. Il ne produit pas de NDVI
plausible mais inventé.

Un NDVI fabriqué serait particulièrement toxique : c'est un indicateur que les
utilisateurs reconnaissent et auquel ils accordent du crédit. Le voir affiché
sans qu'aucune image n'ait été acquise détruirait la confiance dans l'ensemble
du produit le jour où l'écart serait constaté.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import DataState
from app.domain.geo import Coordinates
from app.domain.provenance import DataSourceRef


class VegetationObservation(BaseModel):
    """Indicateur de végétation dérivé d'une acquisition satellite."""

    model_config = ConfigDict(frozen=True)

    acquired_on: date
    ndvi: float | None = Field(default=None, ge=-1, le=1)
    ndwi: float | None = Field(default=None, ge=-1, le=1)
    # Part de la zone écartée du calcul : nuages, ombres de nuages, neige.
    # Ce n'est pas la couverture nuageuse de la scène entière — c'est ce qui
    # importe ici, à savoir quelle proportion de *cette parcelle* n'a pas pu
    # être mesurée.
    masked_share_percent: float | None = Field(default=None, ge=0, le=100)
    # NDVI est un indice calculé à partir de bandes mesurées : DERIVED, pas OBSERVED.
    state: DataState = DataState.DERIVED
    source: DataSourceRef
    retrieved_at: datetime


class SatelliteAvailability(BaseModel):
    """Réponse renvoyée quand aucune observation n'est disponible.

    Le produit préfère dire clairement pourquoi il ne sait pas, plutôt que de
    renvoyer une valeur par défaut que l'interface afficherait comme un fait.
    """

    model_config = ConfigDict(frozen=True)

    available: bool = False
    reason_fr: str
    remediation_fr: str | None = None


class SatelliteProvider(ABC):
    @property
    @abstractmethod
    def label_fr(self) -> str: ...

    @abstractmethod
    async def get_vegetation(
        self, coordinates: Coordinates, *, days_back: int = 30
    ) -> VegetationObservation | SatelliteAvailability:
        """Dernier indicateur de végétation, ou motif d'indisponibilité."""

    @abstractmethod
    async def health(self) -> dict[str, Any]: ...
