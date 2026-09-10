"""Contrat RoutingProvider et schémas d'itinéraire.

Séparation essentielle : le fournisseur de routage produit de la **géométrie et
des attributs de trajet** (tracé, distance, durée nominale). Il ne calcule
aucun risque et n'attribue aucun score. L'exposition et le classement
appartiennent au moteur de risque et au moteur d'optimisation.

Mélanger les deux rendrait impossible de changer de fournisseur sans réécrire
la logique métier.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.geo import Coordinates
from app.domain.provenance import DataSourceRef


class RouteSegment(BaseModel):
    """Tronçon élémentaire d'un itinéraire.

    Le grain « tronçon » est nécessaire : un itinéraire n'est presque jamais
    exposé sur toute sa longueur. Sans segments, on ne pourrait ni localiser la
    zone problématique sur la carte, ni calculer à quel moment le véhicule y
    passe.
    """

    model_config = ConfigDict(frozen=True)

    from_code: str
    from_name_fr: str
    from_coordinates: tuple[float, float] = Field(description="(latitude, longitude)")
    to_code: str
    to_name_fr: str
    to_coordinates: tuple[float, float]
    distance_km: float
    duration_hours: float
    road_ref: str
    road_class: str
    reliability: float = Field(ge=0, le=1, description="Robustesse structurelle de l'axe")
    notes_fr: str = ""
    geometry: tuple[tuple[float, float], ...] = Field(
        default=(),
        description=(
            "Tracé réel du tronçon en (longitude, latitude). Vide lorsque le "
            "fournisseur ne fournit pas de géométrie fine : le tronçon est alors "
            "représenté par la droite entre ses extrémités."
        ),
    )


class RouteGeometry(BaseModel):
    """Itinéraire complet tel que produit par le fournisseur de routage.

    Ne contient **aucun** score de risque : c'est un fait géographique, pas une
    évaluation.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    label_fr: str
    node_codes: tuple[str, ...]
    segments: tuple[RouteSegment, ...]
    total_distance_km: float
    total_duration_hours: float
    source: DataSourceRef

    @property
    def coordinates_path(self) -> list[tuple[float, float]]:
        """Tracé en (longitude, latitude), prêt pour MapLibre.

        Utilise la géométrie fine quand le fournisseur en donne une, et retombe
        sinon sur les droites entre extrémités de tronçons. La carte affiche
        donc le tracé le plus précis disponible, sans que l'appelant ait à
        savoir quel fournisseur est actif.
        """
        if not self.segments:
            return []

        detaille: list[tuple[float, float]] = []
        for segment in self.segments:
            if segment.geometry:
                # On évite de répéter le point de jonction entre deux tronçons.
                points = list(segment.geometry)
                if detaille and points and detaille[-1] == points[0]:
                    points = points[1:]
                detaille.extend(points)
            else:
                depart = (segment.from_coordinates[1], segment.from_coordinates[0])
                arrivee = (segment.to_coordinates[1], segment.to_coordinates[0])
                if not detaille or detaille[-1] != depart:
                    detaille.append(depart)
                detaille.append(arrivee)
        return detaille

    @property
    def has_detailed_geometry(self) -> bool:
        """Vrai si au moins un tronçon porte un tracé routier réel."""
        return any(s.geometry for s in self.segments)

    @property
    def average_reliability(self) -> float:
        """Fiabilité pondérée par la distance : un long tronçon fragile pèse plus."""
        if not self.segments:
            return 1.0
        total = sum(s.distance_km for s in self.segments)
        if total == 0:
            return min(s.reliability for s in self.segments)
        return sum(s.reliability * s.distance_km for s in self.segments) / total

    def cumulative_hours_at_segment(self, index: int) -> tuple[float, float]:
        """Heures écoulées depuis le départ à l'entrée et à la sortie d'un tronçon.

        C'est ce qui permet de savoir *quand* le véhicule traverse une zone, et
        donc s'il y sera pendant la fenêtre de perturbation.
        """
        entry = sum(s.duration_hours for s in self.segments[:index])
        return entry, entry + self.segments[index].duration_hours


class RoutingProvider(ABC):
    @property
    @abstractmethod
    def label_fr(self) -> str: ...

    @property
    @abstractmethod
    def source(self) -> DataSourceRef: ...

    @abstractmethod
    async def route(
        self, origin_code: str, destination_code: str, *, via: tuple[str, ...] = ()
    ) -> RouteGeometry:
        """Itinéraire le plus rapide entre deux nœuds."""

    @abstractmethod
    async def alternatives(
        self,
        origin_code: str,
        destination_code: str,
        *,
        max_routes: int = 4,
        avoid_nodes: tuple[str, ...] = (),
    ) -> list[RouteGeometry]:
        """Plusieurs itinéraires réellement distincts entre deux nœuds."""

    @abstractmethod
    def nearest_node_code(self, coordinates: Coordinates) -> str: ...

    @abstractmethod
    async def health(self) -> dict[str, Any]: ...
