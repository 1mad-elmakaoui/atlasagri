"""Routage sur le graphe routier marocain de référence.

Ce fournisseur calcule des itinéraires par Dijkstra sur le réseau décrit dans
`domain/morocco.py` : villes réelles, axes réels, distances routières de
référence.

Il ne fournit pas de géométrie routière fine (le tracé relie les villes en
ligne droite sur la carte). C'est suffisant pour **comparer des itinéraires
entre eux**, ce qui est l'usage du produit, et insuffisant pour du guidage —
d'où l'adaptateur OSRM, activable quand une géométrie précise est requise.

Génération d'alternatives : méthode de pénalisation itérative. On calcule le
meilleur trajet, on pénalise ses arêtes, on recalcule. Cela produit des
itinéraires réellement distincts (corridor de montagne, corridor littoral,
détour intérieur) plutôt que des variantes marginales du même trajet, ce que
donnerait une recherche des k plus courts chemins classique.
"""

from __future__ import annotations

import heapq
from typing import Any

from app.core.errors import NotFoundError, ValidationError
from app.domain.geo import Coordinates
from app.domain.morocco import EDGES, NODES, RoadEdge, nearest_node, neighbours
from app.domain.provenance import SOURCE_ROAD_NETWORK, DataSourceRef
from app.providers.routing.interface import RouteGeometry, RouteSegment, RoutingProvider

# Facteur appliqué aux arêtes déjà empruntées lors de la recherche
# d'alternatives. 2.6 est un compromis empirique : en dessous, les alternatives
# rejouent le même corridor ; au-dessus, elles deviennent absurdement longues.
DIVERSITY_PENALTY = 2.6

# Une alternative dont la durée dépasse ce multiple du meilleur trajet n'est pas
# une option qu'un exploitant envisagerait. La proposer quand même ferait perdre
# confiance dans toute la liste : mieux vaut trois alternatives crédibles que
# cinq dont deux sont absurdes.
MAX_DURATION_RATIO = 1.75

# Deux itinéraires partageant plus de cette proportion de leurs nœuds
# intermédiaires décrivent le même corridor. Les présenter côte à côte donnerait
# une illusion de choix.
MAX_NODE_OVERLAP = 0.7


class NetworkRoutingProvider(RoutingProvider):
    @property
    def label_fr(self) -> str:
        return "Réseau routier de référence"

    @property
    def source(self) -> DataSourceRef:
        return SOURCE_ROAD_NETWORK

    def nearest_node_code(self, coordinates: Coordinates) -> str:
        return nearest_node(coordinates).code

    async def route(
        self, origin_code: str, destination_code: str, *, via: tuple[str, ...] = ()
    ) -> RouteGeometry:
        self._validate(origin_code, destination_code)

        if via:
            path: list[str] = [origin_code]
            for waypoint in (*via, destination_code):
                leg = _dijkstra(path[-1], waypoint)
                if leg is None:
                    raise NotFoundError(
                        f"Aucun itinéraire routier entre {_name(path[-1])} et {_name(waypoint)}."
                    )
                path.extend(leg[1:])
        else:
            found = _dijkstra(origin_code, destination_code)
            if found is None:
                raise NotFoundError(
                    f"Aucun itinéraire routier entre {_name(origin_code)} "
                    f"et {_name(destination_code)}."
                )
            path = found

        return self._build(path, route_id="itineraire-direct", label_fr="Itinéraire le plus rapide")

    async def alternatives(
        self,
        origin_code: str,
        destination_code: str,
        *,
        max_routes: int = 4,
        avoid_nodes: tuple[str, ...] = (),
    ) -> list[RouteGeometry]:
        self._validate(origin_code, destination_code)

        blocked = set(avoid_nodes) - {origin_code, destination_code}
        penalties: dict[tuple[str, str], float] = {}
        routes: list[RouteGeometry] = []
        seen_paths: set[tuple[str, ...]] = set()

        for _ in range(max_routes * 3):  # marge : certaines tentatives redonnent un doublon
            if len(routes) >= max_routes:
                break

            path = _dijkstra(
                origin_code, destination_code, penalties=penalties, blocked=blocked
            )
            if path is None:
                break

            key = tuple(path)
            if key not in seen_paths:
                seen_paths.add(key)
                candidate = self._build(
                    path,
                    route_id=f"itineraire-{len(routes) + 1}",
                    label_fr=_route_label(path, len(routes)),
                )
                if _is_credible_alternative(candidate, routes):
                    routes.append(candidate)

            # Pénalise les arêtes du trajet trouvé pour forcer un vrai détour.
            for a, b in zip(path, path[1:], strict=False):
                for edge_key in ((a, b), (b, a)):
                    penalties[edge_key] = penalties.get(edge_key, 1.0) * DIVERSITY_PENALTY

        if not routes:
            raise NotFoundError(
                f"Aucun itinéraire disponible entre {_name(origin_code)} "
                f"et {_name(destination_code)} avec les contraintes demandées."
            )
        return routes

    async def health(self) -> dict[str, Any]:
        return {
            "fournisseur": self.label_fr,
            "disponible": True,
            "message_fr": (
                f"{len(NODES)} nœuds et {len(EDGES)} axes chargés. "
                "Distances routières de référence, sans géométrie fine."
            ),
        }

    # --- construction ---

    def _validate(self, origin_code: str, destination_code: str) -> None:
        for code in (origin_code, destination_code):
            if code not in NODES:
                raise ValidationError(f"Nœud routier inconnu : « {code} ».")
        if origin_code == destination_code:
            raise ValidationError("L'origine et la destination sont identiques.")

    def _build(self, path: list[str], *, route_id: str, label_fr: str) -> RouteGeometry:
        segments: list[RouteSegment] = []
        for a, b in zip(path, path[1:], strict=False):
            edge = _edge_between(a, b)
            node_a, node_b = NODES[a], NODES[b]
            segments.append(
                RouteSegment(
                    from_code=a,
                    from_name_fr=node_a.name_fr,
                    from_coordinates=(node_a.coordinates.latitude, node_a.coordinates.longitude),
                    to_code=b,
                    to_name_fr=node_b.name_fr,
                    to_coordinates=(node_b.coordinates.latitude, node_b.coordinates.longitude),
                    distance_km=edge.distance_km,
                    duration_hours=round(edge.nominal_duration_hours, 3),
                    road_ref=edge.road_ref,
                    road_class=edge.road_class,
                    reliability=edge.reliability,
                    notes_fr=edge.notes_fr,
                )
            )

        return RouteGeometry(
            id=route_id,
            label_fr=label_fr,
            node_codes=tuple(path),
            segments=tuple(segments),
            total_distance_km=round(sum(s.distance_km for s in segments), 1),
            total_duration_hours=round(sum(s.duration_hours for s in segments), 2),
            source=SOURCE_ROAD_NETWORK,
        )


# --- algorithme -------------------------------------------------------------

def _dijkstra(
    origin: str,
    destination: str,
    *,
    penalties: dict[tuple[str, str], float] | None = None,
    blocked: set[str] | None = None,
) -> list[str] | None:
    """Plus court chemin en durée, avec pénalités et nœuds interdits optionnels.

    Le coût est la **durée** et non la distance : un détour autoroutier plus
    long en kilomètres est souvent plus rapide, et c'est la durée qui compte
    pour une marchandise périssable.
    """
    penalties = penalties or {}
    blocked = blocked or set()

    if origin in blocked or destination in blocked:
        return None

    best_cost: dict[str, float] = {origin: 0.0}
    previous: dict[str, str] = {}
    queue: list[tuple[float, str]] = [(0.0, origin)]
    settled: set[str] = set()

    while queue:
        cost, node = heapq.heappop(queue)
        if node in settled:
            continue
        settled.add(node)

        if node == destination:
            path = [node]
            while path[-1] != origin:
                path.append(previous[path[-1]])
            return list(reversed(path))

        for edge in neighbours(node):
            target = edge.to_code
            if target in blocked or target in settled:
                continue
            weight = edge.nominal_duration_hours * penalties.get((node, target), 1.0)
            candidate = cost + weight
            if candidate < best_cost.get(target, float("inf")):
                best_cost[target] = candidate
                previous[target] = node
                heapq.heappush(queue, (candidate, target))

    return None


def _edge_between(a: str, b: str) -> RoadEdge:
    for edge in neighbours(a):
        if edge.to_code == b:
            return edge
    raise NotFoundError(f"Aucun axe routier entre {_name(a)} et {_name(b)}.")


def _is_credible_alternative(
    candidate: RouteGeometry, accepted: list[RouteGeometry]
) -> bool:
    """Une alternative doit être à la fois praticable et réellement différente."""
    if not accepted:
        return True

    best_duration = min(r.total_duration_hours for r in accepted)
    if candidate.total_duration_hours > best_duration * MAX_DURATION_RATIO:
        return False

    candidate_nodes = set(candidate.node_codes[1:-1])
    for existing in accepted:
        existing_nodes = set(existing.node_codes[1:-1])
        union = candidate_nodes | existing_nodes
        if not union:
            continue
        if len(candidate_nodes & existing_nodes) / len(union) > MAX_NODE_OVERLAP:
            return False
    return True


def _name(code: str) -> str:
    node = NODES.get(code)
    return node.name_fr if node else code


def _route_label(path: list[str], rank: int) -> str:
    """Nomme l'itinéraire par ses villes intermédiaires marquantes.

    « Itinéraire 2 » n'apprend rien à un exploitant ; « via Essaouira et Safi »
    lui permet de reconnaître immédiatement le corridor dont on parle.
    """
    if rank == 0:
        prefix = "Itinéraire actuel"
    else:
        prefix = f"Alternative {rank}"

    intermediate = [NODES[c].name_fr for c in path[1:-1] if NODES[c].is_logistics_hub]
    if not intermediate:
        intermediate = [NODES[c].name_fr for c in path[1:-1]][:2]
    if intermediate:
        return f"{prefix} — via {', '.join(intermediate[:3])}"
    return f"{prefix} — {NODES[path[0]].name_fr} → {NODES[path[-1]].name_fr}"
