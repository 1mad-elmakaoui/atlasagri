"""Moteur de simulation « et si ? ».

Recalcule la décision sous une hypothèse modifiée et compare les deux
situations. Rien n'est écrit en base : une simulation ne doit jamais altérer
l'état réel de l'organisation.

La comparaison porte sur ce qui intéresse un décideur — le risque, la marge
d'échéance, le coût, et surtout **ce qu'il reste comme options** — et non sur
un score abstrait.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

from app.core.errors import ValidationError
from app.db.repositories import TenantRepository
from app.domain.morocco import NODES
from app.providers.registry import get_routing_provider
from app.services.alternatives import AlternativeEngine, ShipmentContext
from app.services.optimization import OptimizationService, RankingResult, duree_fr  # noqa: F401
from app.services.shipment_service import ShipmentService

SCENARIOS = {
    "retard_fournisseur": "Retard d'un fournisseur",
    "route_indisponible": "Axe routier indisponible",
    "depart_retarde": "Départ repoussé",
}


class SimulationService:
    def __init__(self, repo: TenantRepository) -> None:
        self.repo = repo
        self.shipments = ShipmentService(repo)
        self.engine = AlternativeEngine()
        self.optimizer = OptimizationService()

    async def run(
        self,
        *,
        scenario: str,
        shipment_reference: str,
        parameter_value: float = 48.0,
        blocked_node_code: str | None = None,
    ) -> dict[str, Any]:
        if scenario not in SCENARIOS:
            raise ValidationError(
                f"Scénario inconnu : « {scenario} ». "
                f"Scénarios disponibles : {', '.join(SCENARIOS)}."
            )

        shipment = self.shipments.resolve(shipment_reference)
        base_context = self.shipments.build_context(shipment)
        suppliers = self.shipments.supplier_options(shipment)
        warehouses = self.shipments.warehouse_options(shipment)

        baseline = await self._evaluate(base_context, suppliers, warehouses)

        if scenario == "route_indisponible":
            simulated_context = base_context
            blocked = blocked_node_code or self._most_exposed_node(baseline)
            if blocked and blocked not in NODES:
                raise ValidationError(f"Nœud routier inconnu : « {blocked} ».")
            simulated = await self._evaluate(
                simulated_context, suppliers, warehouses, avoid_nodes=(blocked,) if blocked else ()
            )
            hypothesis = (
                f"L'axe passant par {NODES[blocked].name_fr} devient impraticable."
                if blocked
                else "Aucun axe critique identifié à neutraliser."
            )
        elif scenario == "depart_retarde":
            simulated_context = replace(
                base_context,
                departure_at=base_context.departure_at + timedelta(hours=parameter_value),
            )
            simulated = await self._evaluate(simulated_context, suppliers, warehouses)
            hypothesis = f"Le départ est repoussé de {parameter_value:.0f} heures."
        else:  # retard_fournisseur
            delayed = tuple(
                replace(s, lead_time_days=s.lead_time_days + parameter_value / 24)
                for s in suppliers
            )
            simulated_context = base_context
            simulated = await self._evaluate(simulated_context, delayed, warehouses)
            hypothesis = (
                f"Tous les fournisseurs alternatifs subissent un retard de "
                f"{parameter_value:.0f} heures."
            )

        return {
            "scenario": SCENARIOS[scenario],
            "reference": shipment.reference,
            "hypothese_fr": hypothesis,
            "situation_actuelle": _snapshot(baseline),
            "situation_simulee": _snapshot(simulated),
            "evolution_fr": _describe_change(baseline, simulated),
            "avertissement_fr": (
                "Simulation : aucune donnée n'a été modifiée et aucune action "
                "n'a été déclenchée."
            ),
        }

    def _most_exposed_node(self, result: RankingResult) -> str | None:
        """Nœud routier le plus exposé du plan actuel.

        Sert de cible par défaut au scénario « axe indisponible » : neutraliser
        un nœud au hasard n'apprendrait rien, alors que neutraliser le point de
        passage le plus menacé répond à la question que se pose réellement
        l'exploitant.
        """
        for ranked in result.ranked:
            assessment = ranked.alternative.route_assessment
            if assessment is None:
                continue
            exposed = [s for s in assessment.segments if s.is_exposed]
            if not exposed:
                continue
            worst = max(exposed, key=lambda s: s.severity)
            for code in assessment.node_codes:
                if NODES.get(code) and NODES[code].name_fr in (
                    worst.from_name_fr,
                    worst.to_name_fr,
                ):
                    return code
        return None

    async def _evaluate(
        self,
        context: ShipmentContext,
        suppliers,
        warehouses,
        *,
        avoid_nodes: tuple[str, ...] = (),
    ) -> RankingResult:
        if avoid_nodes:
            # On recalcule les itinéraires en évitant le nœud neutralisé, puis
            # on laisse le moteur d'alternatives faire le reste.
            routing = get_routing_provider()
            available = await routing.alternatives(
                context.origin_node_code,
                context.destination_node_code,
                max_routes=3,
                avoid_nodes=avoid_nodes,
            )
            if not available:
                return self.optimizer.rank([], profile_code=context.optimization_profile)

        alternatives = await self.engine.generate_for_shipment(
            context, suppliers=suppliers, warehouses=warehouses
        )
        if avoid_nodes:
            alternatives = [
                a
                for a in alternatives
                if a.route_assessment is None
                or not set(avoid_nodes) & set(a.route_assessment.node_codes)
            ]
        return self.optimizer.rank(alternatives, profile_code=context.optimization_profile)


def _snapshot(result: RankingResult) -> dict[str, Any]:
    recommended = result.recommended
    return {
        "options_faisables": len(result.ranked),
        "options_ecartees": len(result.rejected),
        "meilleure_option": recommended.alternative.label_fr if recommended else None,
        "niveau_risque": (
            recommended.alternative.risk_level.label_fr if recommended else "Aucune option"
        ),
        "probabilite_perturbation": (
            recommended.alternative.disruption_probability if recommended else None
        ),
        "cout_mad": recommended.alternative.estimated_cost_mad if recommended else None,
        "marge_echeance_h": (
            recommended.alternative.sla_margin_hours if recommended else None
        ),
        "confiance": result.confidence.label_fr,
    }


def _describe_change(before: RankingResult, after: RankingResult) -> list[str]:
    """Formule l'écart en langage métier plutôt qu'en variation de score."""
    changes: list[str] = []
    first, second = before.recommended, after.recommended

    if first is None or second is None:
        if second is None:
            changes.append(
                "Dans ce scénario, plus aucune option ne respecte les contraintes. "
                "Un arbitrage humain devient nécessaire."
            )
        return changes

    delta_risk = (
        second.alternative.disruption_probability - first.alternative.disruption_probability
    ) * 100
    if abs(delta_risk) >= 1:
        direction = "augmente" if delta_risk > 0 else "diminue"
        changes.append(
            f"Le risque de perturbation de la meilleure option {direction} de "
            f"{abs(delta_risk):.0f} points."
        )

    delta_cost = second.alternative.estimated_cost_mad - first.alternative.estimated_cost_mad
    if abs(delta_cost) >= 100:
        changes.append(
            f"Le coût de la meilleure option varie de {delta_cost:+,.0f} MAD.".replace(",", " ")
        )

    if first.alternative.label_fr != second.alternative.label_fr:
        changes.append(
            f"La meilleure option change : « {first.alternative.label_fr} » devient "
            f"« {second.alternative.label_fr} »."
        )

    delta_options = len(after.ranked) - len(before.ranked)
    if delta_options < 0:
        changes.append(
            f"{abs(delta_options)} option(s) deviennent infaisables : la marge de "
            "manœuvre se réduit."
        )

    if not changes:
        changes.append(
            "Ce scénario ne modifie pas sensiblement la décision : le plan reste valable."
        )
    return changes
