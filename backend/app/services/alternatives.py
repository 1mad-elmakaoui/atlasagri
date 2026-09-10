"""Moteur de génération d'alternatives.

Ce module **génère et filtre**. Il ne classe pas : le classement appartient à
`optimization.py`. Cette séparation est délibérée — mélanger les deux rendrait
impossible de changer de stratégie de classement sans toucher à la génération,
et masquerait la distinction entre *ce qui est possible* et *ce qui est
préférable*.

Deux notions à ne pas confondre :

- **Contrainte dure** : une option qui la viole est infaisable et sort du
  classement. Recommander un plan qui ne respecte pas l'engagement de service,
  ou qui dépasse la capacité disponible, serait pire qu'inutile : ce serait
  dangereux.
- **Préférence** : ce qui distingue deux options toutes deux applicables.
  C'est là qu'intervient la pondération multicritère.

Les options écartées sont conservées avec leur motif : l'utilisateur doit
pouvoir constater qu'une piste évidente a bien été examinée puis rejetée, et
pourquoi.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from app.core.logging import get_logger
from app.domain.crops import get_crop
from app.domain.enums import (
    AlternativeStatus,
    AlternativeType,
    Confidence,
    DataState,
    RiskLevel,
    TransportMode,
)
from app.domain.provenance import SOURCE_INTERNAL_DB, DataSourceRef
from app.domain.transport import estimate_cost
from app.providers.registry import get_routing_provider
from app.services.route_risk import RouteAssessment, RouteRiskService

logger = get_logger(__name__)

# Décalages de départ examinés, en heures. Les valeurs négatives correspondent à
# une avance : c'est souvent la meilleure réponse à un front qui arrive, et
# aucun moteur ne la trouverait s'il n'explorait que les retards.
DEPARTURE_SHIFTS_HOURS: tuple[float, ...] = (-10, -6, -3, 3, 6, 10, 14)

# Délai minimal de préparation avant qu'une expédition puisse partir.
MIN_PREPARATION_HOURS = 3.0


@dataclass(frozen=True)
class ShipmentContext:
    """Tout ce qu'il faut connaître d'une expédition pour lui chercher des options."""

    shipment_id: str
    reference: str
    crop_code: str
    product_name_fr: str
    optimization_profile: str
    volume_tonnes: float
    transport_mode: TransportMode
    origin_node_code: str
    origin_name_fr: str
    destination_node_code: str
    destination_name_fr: str
    destination_has_cold_storage: bool
    destination_capacity_tonnes: float | None
    departure_at: datetime
    sla_deadline_at: datetime


@dataclass(frozen=True)
class SupplierOption:
    """Fournisseur mobilisable comme source alternative."""

    supplier_id: str
    name_fr: str
    node_code: str
    site_name_fr: str
    lead_time_days: float
    reliability: float
    daily_capacity_tonnes: float
    unit_price_mad_per_tonne: float


@dataclass(frozen=True)
class WarehouseOption:
    """Entrepôt mobilisable comme destination ou source de transfert."""

    site_id: str
    name_fr: str
    node_code: str
    has_cold_storage: bool
    capacity_tonnes: float | None
    available_quantity_tonnes: float = 0.0


class Alternative(BaseModel):
    """Option opérationnelle évaluée.

    Contient tout ce qu'il faut pour l'afficher, la comparer et la tracer.
    Aucun champ n'est rempli par un modèle de langage.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    type: AlternativeType
    label_fr: str
    description_fr: str
    status: AlternativeStatus
    infeasibility_reasons_fr: tuple[str, ...] = ()

    risk_score: float = Field(ge=0, le=1)
    risk_level: RiskLevel
    disruption_probability: float = Field(ge=0, le=1)
    exposure_fraction: float = Field(ge=0, le=1)
    reliability: float = Field(ge=0, le=1)

    duration_hours: float
    distance_km: float
    estimated_cost_mad: float
    departure_at: datetime
    estimated_arrival_at: datetime

    sla_compliant: bool
    sla_margin_hours: float | None
    capacity_available: bool

    risk_factors_fr: tuple[str, ...] = ()
    tradeoffs_fr: tuple[str, ...] = ()

    confidence: Confidence
    data_state: DataState
    data_sources: tuple[DataSourceRef, ...]

    route_assessment: RouteAssessment | None = None

    @property
    def is_feasible(self) -> bool:
        return self.status is AlternativeStatus.FEASIBLE


class AlternativeEngine:
    """Produit les options envisageables face à une perturbation."""

    def __init__(self, route_risk: RouteRiskService | None = None) -> None:
        self.route_risk = route_risk or RouteRiskService()

    async def generate_for_shipment(
        self,
        context: ShipmentContext,
        *,
        suppliers: tuple[SupplierOption, ...] = (),
        warehouses: tuple[WarehouseOption, ...] = (),
        max_routes: int = 3,
    ) -> list[Alternative]:
        alternatives: list[Alternative] = []

        routes = await get_routing_provider().alternatives(
            context.origin_node_code, context.destination_node_code, max_routes=max_routes
        )
        if not routes:
            return alternatives

        current_route = routes[0]

        # 1. Plan actuel — sert de référence de comparaison.
        alternatives.append(
            await self._from_route(
                context,
                route=current_route,
                departure_at=context.departure_at,
                alternative_type=AlternativeType.CURRENT_PLAN,
                identifier="plan-actuel",
                label_fr="Plan actuel",
                description_fr=(
                    f"Itinéraire prévu {context.origin_name_fr} → "
                    f"{context.destination_name_fr}, départ inchangé."
                ),
            )
        )

        # 2. Itinéraires alternatifs, même départ.
        for index, route in enumerate(routes[1:], start=1):
            alternatives.append(
                await self._from_route(
                    context,
                    route=route,
                    departure_at=context.departure_at,
                    alternative_type=AlternativeType.ALTERNATE_ROUTE,
                    identifier=f"itineraire-{index}",
                    label_fr=route.label_fr,
                    description_fr=(
                        "Itinéraire alternatif empruntant un corridor différent, "
                        "départ inchangé."
                    ),
                )
            )

        # 3. Décalages de départ sur l'itinéraire habituel.
        alternatives.extend(await self._departure_shifts(context, current_route))

        # 4. Fournisseurs alternatifs.
        alternatives.extend(await self._supplier_options(context, suppliers))

        # 5. Entrepôts alternatifs et transferts de stock.
        alternatives.extend(await self._warehouse_options(context, warehouses))

        return alternatives

    # --- générateurs par classe ---

    async def _departure_shifts(
        self, context: ShipmentContext, route
    ) -> list[Alternative]:
        options: list[Alternative] = []
        earliest = datetime.now(UTC) + timedelta(hours=MIN_PREPARATION_HOURS)

        for shift in DEPARTURE_SHIFTS_HOURS:
            departure = context.departure_at + timedelta(hours=shift)
            direction = "Avancer" if shift < 0 else "Décaler"
            label = f"{direction} le départ de {abs(shift):.0f} h"
            identifier = (
                f"depart-{'avance' if shift < 0 else 'retard'}-{abs(shift):.0f}h"
            )

            if departure < earliest:
                options.append(
                    _infeasible(
                        identifier=identifier,
                        alternative_type=AlternativeType.DEPARTURE_SHIFT,
                        label_fr=label,
                        description_fr=(
                            f"Départ avancé à {departure:%d/%m %H:%M}, "
                            "avant le délai de préparation minimal."
                        ),
                        reasons=(
                            f"Départ trop proche : un minimum de "
                            f"{MIN_PREPARATION_HOURS:.0f} h est nécessaire pour préparer "
                            "et charger l'expédition.",
                        ),
                        departure_at=departure,
                    )
                )
                continue

            options.append(
                await self._from_route(
                    context,
                    route=route,
                    departure_at=departure,
                    alternative_type=AlternativeType.DEPARTURE_SHIFT,
                    identifier=identifier,
                    label_fr=label,
                    description_fr=(
                        f"Même itinéraire, départ reporté à {departure:%d/%m %H:%M} "
                        "pour éviter la fenêtre de perturbation."
                        if shift > 0
                        else f"Même itinéraire, départ avancé à {departure:%d/%m %H:%M} "
                        "pour passer avant la perturbation."
                    ),
                )
            )
        return options

    async def _supplier_options(
        self, context: ShipmentContext, suppliers: tuple[SupplierOption, ...]
    ) -> list[Alternative]:
        options: list[Alternative] = []
        routing = get_routing_provider()

        for supplier in suppliers:
            identifier = f"fournisseur-{supplier.supplier_id[:8]}"
            label = f"Passer par {supplier.name_fr}"

            # Contrainte dure : le fournisseur doit pouvoir livrer le volume
            # avant l'échéance, compte tenu de son délai et de sa capacité.
            hours_available = (
                context.sla_deadline_at - datetime.now(UTC)
            ).total_seconds() / 3600
            lead_time_hours = supplier.lead_time_days * 24
            days_to_produce = context.volume_tonnes / max(supplier.daily_capacity_tonnes, 0.1)

            blocking: list[str] = []
            if lead_time_hours >= hours_available:
                blocking.append(
                    f"Délai de {supplier.lead_time_days:.0f} jours incompatible avec "
                    f"l'échéance ({hours_available / 24:.1f} jours restants)."
                )
            if supplier.daily_capacity_tonnes < context.volume_tonnes / 3:
                blocking.append(
                    f"Capacité de {supplier.daily_capacity_tonnes:.0f} t/jour : "
                    f"{days_to_produce:.1f} jours seraient nécessaires pour "
                    f"{context.volume_tonnes:.0f} t."
                )

            if blocking:
                options.append(
                    _infeasible(
                        identifier=identifier,
                        alternative_type=AlternativeType.ALTERNATE_SUPPLIER,
                        label_fr=label,
                        description_fr=(
                            f"Sourcing depuis {supplier.site_name_fr} "
                            f"({supplier.name_fr})."
                        ),
                        reasons=tuple(blocking),
                        departure_at=context.departure_at,
                    )
                )
                continue

            route = await routing.route(supplier.node_code, context.destination_node_code)
            departure = datetime.now(UTC) + timedelta(hours=lead_time_hours)

            alternative = await self._from_route(
                context,
                route=route,
                departure_at=departure,
                alternative_type=AlternativeType.ALTERNATE_SUPPLIER,
                identifier=identifier,
                label_fr=label,
                description_fr=(
                    f"Sourcing depuis {supplier.site_name_fr}, délai "
                    f"{supplier.lead_time_days:.0f} jours, fiabilité "
                    f"{supplier.reliability:.0%}."
                ),
                # Le changement de fournisseur modifie le prix de la marchandise,
                # pas seulement le transport : l'ignorer fausserait l'arbitrage.
                goods_cost_delta_mad=(
                    supplier.unit_price_mad_per_tonne * context.volume_tonnes
                ),
            )
            options.append(alternative)

        return options

    async def _warehouse_options(
        self, context: ShipmentContext, warehouses: tuple[WarehouseOption, ...]
    ) -> list[Alternative]:
        options: list[Alternative] = []
        routing = get_routing_provider()
        crop = get_crop(context.crop_code)

        for warehouse in warehouses:
            # Un entrepôt rattaché au même nœud routier que l'origine ou la
            # destination ne constitue pas un réacheminement : deux sites
            # distincts peuvent partager un nœud (deux entrepôts casablancais,
            # par exemple), et l'itinéraire serait alors dégénéré.
            if warehouse.node_code in (
                context.destination_node_code,
                context.origin_node_code,
            ):
                continue

            identifier = f"entrepot-{warehouse.site_id[:8]}"
            blocking: list[str] = []

            if crop.requires_cold_chain and not warehouse.has_cold_storage:
                blocking.append(
                    f"{warehouse.name_fr} ne dispose pas de stockage frigorifique, "
                    f"indispensable pour {crop.name_fr.lower()}."
                )
            if (
                warehouse.capacity_tonnes is not None
                and warehouse.capacity_tonnes < context.volume_tonnes
            ):
                blocking.append(
                    f"Capacité de {warehouse.capacity_tonnes:.0f} t insuffisante pour "
                    f"{context.volume_tonnes:.0f} t."
                )

            if blocking:
                options.append(
                    _infeasible(
                        identifier=identifier,
                        alternative_type=AlternativeType.ALTERNATE_WAREHOUSE,
                        label_fr=f"Livrer à {warehouse.name_fr}",
                        description_fr=f"Réacheminement vers {warehouse.name_fr}.",
                        reasons=tuple(blocking),
                        departure_at=context.departure_at,
                    )
                )
                continue

            route = await routing.route(context.origin_node_code, warehouse.node_code)

            # Un entrepôt intermédiaire ne livre pas la marchandise à
            # destination : il faut chiffrer le second trajet. Sans cela,
            # l'option paraîtrait artificiellement bon marché simplement parce
            # qu'elle s'arrête en chemin — et le tableau comparatif mettrait en
            # regard des choses qui ne se comparent pas.
            onward = await routing.route(warehouse.node_code, context.destination_node_code)
            onward_cost = estimate_cost(
                distance_km=onward.total_distance_km,
                duration_hours=onward.total_duration_hours,
                volume_tonnes=context.volume_tonnes,
                mode=context.transport_mode,
            )

            # Délai de remise en charge sur la plateforme intermédiaire :
            # déchargement, contrôle, rechargement. L'ignorer laisserait croire
            # qu'une rupture de charge est gratuite en temps.
            transit_hours = 3.0

            options.append(
                await self._from_route(
                    context,
                    route=route,
                    departure_at=context.departure_at,
                    alternative_type=AlternativeType.ALTERNATE_WAREHOUSE,
                    identifier=identifier,
                    label_fr=f"Livrer à {warehouse.name_fr}",
                    description_fr=(
                        f"Réacheminement vers {warehouse.name_fr}, puis redistribution "
                        f"vers {context.destination_name_fr} "
                        f"({onward.total_distance_km:.0f} km supplémentaires)."
                    ),
                    goods_cost_delta_mad=onward_cost.total_mad,
                    # Durée et distance totales, second trajet compris. Sans
                    # cela, l'option paraîtrait plus rapide que le trajet direct
                    # simplement parce qu'elle s'arrête en chemin — et le
                    # classement multicritère la privilégierait à tort.
                    extra_distance_km=onward.total_distance_km,
                    extra_duration_hours=onward.total_duration_hours + transit_hours,
                    extra_tradeoffs=(
                        f"Rupture de charge à {warehouse.name_fr} : "
                        f"{onward.total_distance_km:.0f} km et "
                        f"{onward_cost.total_mad:,.0f} MAD de réacheminement, "
                        f"plus environ {transit_hours:.0f} h de transit sur site."
                        .replace(",", " "),
                        "Immobilise temporairement de la capacité de stockage "
                        "sur un site intermédiaire.",
                    ),
                )
            )

        return options

    # --- construction commune ---

    async def _from_route(
        self,
        context: ShipmentContext,
        *,
        route,
        departure_at: datetime,
        alternative_type: AlternativeType,
        identifier: str,
        label_fr: str,
        description_fr: str,
        goods_cost_delta_mad: float = 0.0,
        extra_distance_km: float = 0.0,
        extra_duration_hours: float = 0.0,
        extra_tradeoffs: tuple[str, ...] = (),
    ) -> Alternative:
        assessment = await self.route_risk.assess(
            route,
            departure_at=departure_at,
            volume_tonnes=context.volume_tonnes,
            transport_mode=context.transport_mode,
            sla_deadline_at=context.sla_deadline_at,
        )

        # L'évaluation porte sur le trajet tracé ; les options en deux temps
        # ajoutent un second trajet qui compte dans la comparaison et dans le
        # respect de l'échéance, mais pas dans le tracé affiché sur la carte.
        total_duration_hours = round(assessment.adjusted_duration_hours + extra_duration_hours, 2)
        final_arrival = assessment.estimated_arrival_at + timedelta(hours=extra_duration_hours)
        sla_margin = round(
            (context.sla_deadline_at - final_arrival).total_seconds() / 3600, 2
        )
        sla_compliant = sla_margin >= 0

        capacity_ok = (
            context.destination_capacity_tonnes is None
            or context.destination_capacity_tonnes >= context.volume_tonnes
        )

        blocking: list[str] = []
        if not sla_compliant:
            blocking.append(
                f"Arrivée estimée le {final_arrival:%d/%m à %H:%M}, "
                f"soit {abs(sla_margin):.1f} h après l'échéance de service."
            )
        if not capacity_ok:
            blocking.append(
                f"Capacité de destination insuffisante pour {context.volume_tonnes:.0f} t."
            )

        return Alternative(
            id=identifier,
            type=alternative_type,
            label_fr=label_fr,
            description_fr=description_fr,
            status=(
                AlternativeStatus.INFEASIBLE if blocking else AlternativeStatus.FEASIBLE
            ),
            infeasibility_reasons_fr=tuple(blocking),
            risk_score=assessment.risk_score,
            risk_level=assessment.risk_level,
            disruption_probability=assessment.disruption_probability,
            exposure_fraction=assessment.exposure_fraction,
            reliability=assessment.reliability,
            duration_hours=total_duration_hours,
            distance_km=round(assessment.distance_km + extra_distance_km, 1),
            estimated_cost_mad=round(assessment.cost_mad + goods_cost_delta_mad, 0),
            departure_at=departure_at,
            estimated_arrival_at=final_arrival,
            sla_compliant=sla_compliant,
            sla_margin_hours=sla_margin,
            capacity_available=capacity_ok,
            risk_factors_fr=assessment.main_risk_factors_fr,
            tradeoffs_fr=extra_tradeoffs,
            confidence=assessment.confidence,
            data_state=assessment.data_state,
            data_sources=(*assessment.sources, SOURCE_INTERNAL_DB),
            route_assessment=assessment,
        )


def _infeasible(
    *,
    identifier: str,
    alternative_type: AlternativeType,
    label_fr: str,
    description_fr: str,
    reasons: tuple[str, ...],
    departure_at: datetime,
) -> Alternative:
    """Option écartée, conservée avec son motif.

    Les valeurs numériques sont volontairement neutres : une option infaisable
    ne doit pas apparaître comme séduisante dans un tableau comparatif.
    """
    return Alternative(
        id=identifier,
        type=alternative_type,
        label_fr=label_fr,
        description_fr=description_fr,
        status=AlternativeStatus.INFEASIBLE,
        infeasibility_reasons_fr=reasons,
        risk_score=1.0,
        risk_level=RiskLevel.CRITICAL,
        disruption_probability=1.0,
        exposure_fraction=0.0,
        reliability=0.0,
        duration_hours=0.0,
        distance_km=0.0,
        estimated_cost_mad=0.0,
        departure_at=departure_at,
        estimated_arrival_at=departure_at,
        sla_compliant=False,
        sla_margin_hours=None,
        capacity_available=False,
        confidence=Confidence.LOW,
        data_state=DataState.INFERRED,
        data_sources=(SOURCE_INTERNAL_DB,),
    )
