"""Adaptateur OSRM — géométrie routière réelle.

Le graphe de référence embarqué relie les villes par des droites : il suffit à
**comparer** des itinéraires, pas à les représenter fidèlement. OSRM fournit le
tracé réel issu d'OpenStreetMap, avec ses virages, ses contournements et ses
échangeurs.

Deux modes de déploiement :

- **Serveur public de démonstration** (`router.project-osrm.org`) : gratuit,
  sans clé, mais sans engagement de service et limité en débit. Convient à une
  démonstration, pas à une exploitation.
- **Instance dédiée** : un conteneur OSRM alimenté par l'extrait OpenStreetMap
  du Maroc. C'est la cible pour un déploiement client — pas de dépendance à un
  service tiers, pas de limite de débit.

Le découpage en tronçons est conservé, parce que tout le moteur d'exposition en
dépend : sans tronçons horodatés, impossible de dire *quand* le véhicule
traverse une zone perturbée.
"""

from __future__ import annotations

import math
from typing import Any

from app.core.config import settings
from app.core.errors import NotFoundError, ProviderUnavailableError, ValidationError
from app.core.logging import get_logger
from app.domain.geo import Coordinates, haversine_km
from app.domain.morocco import NODES, nearest_node
from app.domain.provenance import SOURCE_OSRM, DataSourceRef
from app.providers.base import TtlCache, fetch_json
from app.providers.routing.interface import RouteGeometry, RouteSegment, RoutingProvider

logger = get_logger(__name__)

# Longueur cible d'un tronçon lorsqu'on découpe une géométrie OSRM continue.
# Assez court pour situer une perturbation, assez long pour que la fenêtre de
# traversée reste significative face à la résolution horaire de la météo.
TARGET_SEGMENT_KM = 55.0

# Fiabilité par défaut d'un tronçon OSRM. OSRM ne renseigne pas la robustesse
# structurelle d'un axe : on ne l'invente pas, on prend une valeur neutre et on
# le signale dans les notes du tronçon.
DEFAULT_RELIABILITY = 0.90


class OsrmRoutingProvider(RoutingProvider):
    """Calcule des itinéraires réels via un serveur OSRM."""

    def __init__(self, base_url: str | None = None) -> None:
        url = (base_url or settings.osrm_base_url or "").rstrip("/")
        if not url:
            raise ValidationError(
                "OSRM_BASE_URL n'est pas renseigné. "
                "Exemple public : https://router.project-osrm.org"
            )
        self.base_url = url
        self._cache = TtlCache(ttl_seconds=1800)

    @property
    def label_fr(self) -> str:
        return "OSRM (géométrie OpenStreetMap)"

    @property
    def source(self) -> DataSourceRef:
        return SOURCE_OSRM

    def nearest_node_code(self, coordinates: Coordinates) -> str:
        return nearest_node(coordinates).code

    async def route(
        self, origin_code: str, destination_code: str, *, via: tuple[str, ...] = ()
    ) -> RouteGeometry:
        self._validate(origin_code, destination_code)
        codes = (origin_code, *via, destination_code)
        payload = await self._call(codes, alternatives=0)

        routes = payload.get("routes") or []
        if not routes:
            raise NotFoundError(
                f"OSRM n'a trouvé aucun itinéraire entre {_name(origin_code)} "
                f"et {_name(destination_code)}."
            )
        return self._build(
            routes[0],
            waypoint_codes=codes,
            route_id="itineraire-direct",
            label_fr="Itinéraire le plus rapide",
        )

    async def alternatives(
        self,
        origin_code: str,
        destination_code: str,
        *,
        max_routes: int = 4,
        avoid_nodes: tuple[str, ...] = (),
    ) -> list[RouteGeometry]:
        self._validate(origin_code, destination_code)

        payload = await self._call(
            (origin_code, destination_code), alternatives=max(0, max_routes - 1)
        )
        routes = payload.get("routes") or []
        if not routes:
            raise NotFoundError(
                f"OSRM n'a trouvé aucun itinéraire entre {_name(origin_code)} "
                f"et {_name(destination_code)}."
            )

        interdits = set(avoid_nodes) - {origin_code, destination_code}
        resultats: list[RouteGeometry] = []

        for index, brut in enumerate(routes[:max_routes]):
            itineraire = self._build(
                brut,
                waypoint_codes=(origin_code, destination_code),
                route_id=f"itineraire-{index + 1}" if index else "itineraire-direct",
                label_fr="",
            )
            # Un nœud interdit se traduit par un itinéraire qui ne doit pas le
            # traverser. OSRM ne sait pas exclure un point : on filtre après coup.
            if interdits & set(itineraire.node_codes):
                continue

            resultats.append(
                itineraire.model_copy(
                    update={"label_fr": _label(itineraire.node_codes, len(resultats))}
                )
            )

        if not resultats:
            raise NotFoundError(
                f"Aucun itinéraire OSRM ne satisfait les contraintes entre "
                f"{_name(origin_code)} et {_name(destination_code)}."
            )
        return resultats

    async def health(self) -> dict[str, Any]:
        try:
            await self.route("AGADIR", "MARRAKECH")
        except (ProviderUnavailableError, NotFoundError) as exc:
            return {
                "fournisseur": self.label_fr,
                "disponible": False,
                "message_fr": f"Serveur OSRM injoignable : {exc.message_fr}",
            }
        return {
            "fournisseur": self.label_fr,
            "disponible": True,
            "message_fr": (
                f"Géométrie routière réelle via {self.base_url}. "
                "Les tracés affichés suivent les routes existantes."
            ),
        }

    # --- appel ---

    async def _call(self, codes: tuple[str, ...], *, alternatives: int) -> dict[str, Any]:
        points = ";".join(
            f"{NODES[c].coordinates.longitude:.6f},{NODES[c].coordinates.latitude:.6f}"
            for c in codes
        )
        cle = f"{points}:{alternatives}"
        if (cache := self._cache.get(cle)) is not None:
            return cache

        payload = await fetch_json(
            f"{self.base_url}/route/v1/driving/{points}",
            params={
                "overview": "full",
                "geometries": "geojson",
                "alternatives": str(alternatives) if alternatives else "false",
                "steps": "false",
            },
            provider_label_fr=self.label_fr,
        )

        if payload.get("code") != "Ok":
            raise ProviderUnavailableError(
                f"OSRM a répondu « {payload.get('code', 'inconnu')} » : "
                f"{payload.get('message', 'aucun détail fourni')}."
            )

        self._cache.set(cle, payload)
        return payload

    # --- construction ---

    def _validate(self, origin_code: str, destination_code: str) -> None:
        for code in (origin_code, destination_code):
            if code not in NODES:
                raise ValidationError(f"Nœud routier inconnu : « {code} ».")
        if origin_code == destination_code:
            raise ValidationError("L'origine et la destination sont identiques.")

    def _build(
        self,
        raw: dict[str, Any],
        *,
        waypoint_codes: tuple[str, ...],
        route_id: str,
        label_fr: str,
    ) -> RouteGeometry:
        coordonnees = [
            (float(lon), float(lat))
            for lon, lat in raw.get("geometry", {}).get("coordinates", [])
        ]
        if len(coordonnees) < 2:
            raise ProviderUnavailableError(
                "OSRM a renvoyé un itinéraire sans géométrie exploitable."
            )

        distance_km = float(raw.get("distance", 0.0)) / 1000.0
        duree_h = float(raw.get("duration", 0.0)) / 3600.0
        if distance_km <= 0 or duree_h <= 0:
            raise ProviderUnavailableError(
                "OSRM a renvoyé un itinéraire de distance ou de durée nulle."
            )

        segments = _split_into_segments(coordonnees, distance_km, duree_h)
        codes = _traversed_nodes(coordonnees)

        return RouteGeometry(
            id=route_id,
            label_fr=label_fr or _label(codes, 0),
            node_codes=codes or waypoint_codes,
            segments=segments,
            total_distance_km=round(distance_km, 1),
            total_duration_hours=round(duree_h, 2),
            source=SOURCE_OSRM,
        )


def _split_into_segments(
    coordonnees: list[tuple[float, float]], distance_km: float, duree_h: float
) -> tuple[RouteSegment, ...]:
    """Découpe une géométrie continue en tronçons nommés et horodatables.

    OSRM renvoie un tracé d'un seul tenant. Le moteur d'exposition, lui, a
    besoin de tronçons : c'est ce qui permet de dire à quelle heure le véhicule
    se trouve à tel endroit, et donc s'il croise réellement la perturbation.

    La durée est répartie au prorata de la distance. C'est une approximation —
    OSRM connaît les vitesses par tronçon mais ne les expose pas sans demander
    le détail des manœuvres, dix fois plus volumineux pour un gain marginal ici.
    """
    if len(coordonnees) < 2:
        return ()

    # Longueurs cumulées le long du tracé.
    longueurs: list[float] = [0.0]
    for avant, apres in zip(coordonnees, coordonnees[1:], strict=False):
        longueurs.append(
            longueurs[-1]
            + haversine_km(Coordinates(avant[1], avant[0]), Coordinates(apres[1], apres[0]))
        )
    longueur_totale = longueurs[-1] or distance_km or 1.0

    nombre = max(1, min(24, math.ceil(longueur_totale / TARGET_SEGMENT_KM)))
    pas = longueur_totale / nombre

    segments: list[RouteSegment] = []
    debut_index = 0

    for i in range(nombre):
        cible = pas * (i + 1)
        fin_index = debut_index
        while fin_index < len(longueurs) - 1 and longueurs[fin_index] < cible:
            fin_index += 1
        if fin_index <= debut_index:
            fin_index = min(debut_index + 1, len(coordonnees) - 1)

        tranche = coordonnees[debut_index : fin_index + 1]
        if len(tranche) < 2:
            break

        depart = Coordinates(tranche[0][1], tranche[0][0])
        arrivee = Coordinates(tranche[-1][1], tranche[-1][0])
        longueur_troncon = longueurs[fin_index] - longueurs[debut_index]
        part = longueur_troncon / longueur_totale if longueur_totale else 0.0

        segments.append(
            RouteSegment(
                from_code=nearest_node(depart).code,
                from_name_fr=nearest_node(depart).name_fr,
                from_coordinates=(depart.latitude, depart.longitude),
                to_code=nearest_node(arrivee).code,
                to_name_fr=nearest_node(arrivee).name_fr,
                to_coordinates=(arrivee.latitude, arrivee.longitude),
                distance_km=round(distance_km * part, 1),
                duration_hours=round(duree_h * part, 3),
                road_ref="OSM",
                road_class="nationale",
                reliability=DEFAULT_RELIABILITY,
                notes_fr=(
                    "Robustesse de l'axe non renseignée par OSRM : valeur neutre appliquée"
                ),
                geometry=tuple(tranche),
            )
        )
        debut_index = fin_index

    return tuple(segments)


def _traversed_nodes(coordonnees: list[tuple[float, float]]) -> tuple[str, ...]:
    """Villes de référence effectivement longées par le tracé.

    Sert à nommer l'itinéraire en termes reconnaissables par un exploitant :
    « via Safi, El Jadida » plutôt qu'une suite de coordonnées.
    """
    rencontres: list[str] = []
    for code, node in NODES.items():
        proche = any(
            haversine_km(node.coordinates, Coordinates(lat, lon)) <= 12.0
            for lon, lat in coordonnees[::10]
        )
        if proche:
            rencontres.append(code)

    if not rencontres:
        return ()

    # Ordonne les villes selon leur position le long du tracé.
    def rang(code: str) -> int:
        node = NODES[code]
        return min(
            range(len(coordonnees)),
            key=lambda i: haversine_km(
                node.coordinates, Coordinates(coordonnees[i][1], coordonnees[i][0])
            ),
        )

    return tuple(sorted(rencontres, key=rang))


def _label(codes: tuple[str, ...], rank: int) -> str:
    prefix = "Itinéraire actuel" if rank == 0 else f"Alternative {rank}"
    hubs = [NODES[c].name_fr for c in codes[1:-1] if c in NODES and NODES[c].is_logistics_hub]
    if not hubs:
        hubs = [NODES[c].name_fr for c in codes[1:-1] if c in NODES][:2]
    if hubs:
        return f"{prefix} — via {', '.join(hubs[:3])}"
    return prefix


def _name(code: str) -> str:
    node = NODES.get(code)
    return node.name_fr if node else code
