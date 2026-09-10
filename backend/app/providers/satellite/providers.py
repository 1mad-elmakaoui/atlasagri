"""Fournisseur satellite par défaut.

`UnavailableSatelliteProvider` est le comportement assumé tant que Copernicus
n'est pas configuré : il répond honnêtement « indisponible » et indique la
marche à suivre, plutôt que d'afficher un NDVI qu'aucune image n'a produit.

L'adaptateur réel vit dans `app.providers.satellite.copernicus`.
"""

from __future__ import annotations

from typing import Any

from app.domain.geo import Coordinates
from app.providers.satellite.interface import (
    SatelliteAvailability,
    SatelliteProvider,
)


class UnavailableSatelliteProvider(SatelliteProvider):
    """Fournisseur par défaut : déclare l'indisponibilité sans rien inventer."""

    @property
    def label_fr(self) -> str:
        return "Imagerie satellite (non configurée)"

    async def get_vegetation(
        self, coordinates: Coordinates, *, days_back: int = 30
    ) -> SatelliteAvailability:
        return SatelliteAvailability(
            available=False,
            reason_fr=(
                "Aucun indicateur de végétation n'est disponible : la connexion "
                "à Copernicus Data Space n'est pas configurée sur cette installation."
            ),
            remediation_fr=(
                "Renseigner COPERNICUS_CLIENT_ID et COPERNICUS_CLIENT_SECRET, "
                "puis passer SATELLITE_PROVIDER=copernicus."
            ),
        )

    async def health(self) -> dict[str, Any]:
        return {
            "fournisseur": self.label_fr,
            "disponible": False,
            "message_fr": (
                "Non configurée. Les indicateurs de végétation sont déclarés "
                "indisponibles ; aucune valeur n'est estimée à leur place."
            ),
        }
