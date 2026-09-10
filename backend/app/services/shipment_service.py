"""Service d'expédition : assemble les données métier pour les moteurs.

Sert de point de jonction entre la base et les moteurs déterministes. Les
outils MCP et l'API REST passent tous les deux par ici, ce qui garantit que le
copilote et l'interface voient exactement la même chose — un écart entre les
deux serait immédiatement perçu comme un défaut de fiabilité.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.db.base import Inventory, Product, Shipment, Site, Supplier
from app.db.repositories import TenantRepository
from app.domain.crops import get_crop
from app.domain.enums import AlternativeType, SiteType, TransportMode
from app.services.alternatives import (
    Alternative,
    AlternativeEngine,
    ShipmentContext,
    SupplierOption,
    WarehouseOption,
)
from app.services.optimization import OptimizationService, RankingResult

logger = get_logger(__name__)

# Version du moteur de décision, figée avec chaque prédiction : une évolution
# des règles ne doit pas être confondue avec une dérive du terrain.
ENGINE_VERSION = "decision-1.1.0"


class ShipmentService:
    """Charge une expédition et produit ses alternatives classées."""

    def __init__(
        self,
        repo: TenantRepository,
        *,
        engine: AlternativeEngine | None = None,
        optimizer: OptimizationService | None = None,
    ) -> None:
        self.repo = repo
        self.engine = engine or AlternativeEngine()
        self.optimizer = optimizer or OptimizationService()

    # --- chargement ---

    def resolve(self, identifier: str) -> Shipment:
        """Retrouve une expédition par identifiant ou par référence.

        Accepter les deux évite à l'utilisateur — et au copilote — de manipuler
        des UUID alors que les équipes parlent en références « EXP-1842 ».
        """
        if identifier.upper().startswith(("EXP-", "GHB-")):
            return self.repo.shipment_by_reference(identifier.upper())
        return self.repo.shipment(identifier)

    def build_context(self, shipment: Shipment) -> ShipmentContext:
        product = self.repo.get(Product, shipment.product_id)
        origin = self.repo.get(Site, shipment.origin_site_id)
        destination = self.repo.get(Site, shipment.destination_site_id)

        return ShipmentContext(
            shipment_id=shipment.id,
            reference=shipment.reference,
            crop_code=product.crop_code,
            product_name_fr=product.name,
            optimization_profile=product.optimization_profile,
            volume_tonnes=shipment.volume_tonnes,
            transport_mode=TransportMode(shipment.transport_mode),
            origin_node_code=origin.road_node_code,
            origin_name_fr=origin.name,
            destination_node_code=destination.road_node_code,
            destination_name_fr=destination.name,
            destination_has_cold_storage=destination.has_cold_storage,
            destination_capacity_tonnes=destination.capacity_tonnes,
            departure_at=_as_utc(shipment.departure_at),
            sla_deadline_at=_as_utc(shipment.sla_deadline_at),
        )

    def supplier_options(self, shipment: Shipment) -> tuple[SupplierOption, ...]:
        """Fournisseurs de la même culture, hors fournisseur déjà retenu."""
        product = self.repo.get(Product, shipment.product_id)
        candidates = self.repo.suppliers(Supplier.crop_code == product.crop_code)

        options: list[SupplierOption] = []
        for supplier in candidates:
            if shipment.supplier_id and supplier.id == shipment.supplier_id:
                continue
            site = self.repo.get(Site, supplier.site_id)
            options.append(
                SupplierOption(
                    supplier_id=supplier.id,
                    name_fr=supplier.name,
                    node_code=site.road_node_code,
                    site_name_fr=site.name,
                    lead_time_days=supplier.lead_time_days,
                    reliability=supplier.reliability,
                    daily_capacity_tonnes=supplier.daily_capacity_tonnes,
                    unit_price_mad_per_tonne=supplier.unit_price_mad_per_tonne,
                )
            )
        return tuple(options)

    def warehouse_options(self, shipment: Shipment) -> tuple[WarehouseOption, ...]:
        warehouses = self.repo.sites(
            Site.site_type.in_([SiteType.WAREHOUSE.value, SiteType.HUB.value])
        )
        # L'origine est exclue autant que la destination : un entrepôt qui est
        # déjà le point de départ n'est pas une destination alternative, et
        # produirait un itinéraire d'un site vers lui-même.
        excluded = {shipment.destination_site_id, shipment.origin_site_id}
        return tuple(
            WarehouseOption(
                site_id=site.id,
                name_fr=site.name,
                node_code=site.road_node_code,
                has_cold_storage=site.has_cold_storage,
                capacity_tonnes=site.capacity_tonnes,
            )
            for site in warehouses
            if site.id not in excluded
        )

    # --- décision ---

    async def alternatives_for(self, shipment: Shipment) -> list[Alternative]:
        context = self.build_context(shipment)
        return await self.engine.generate_for_shipment(
            context,
            suppliers=self.supplier_options(shipment),
            warehouses=self.warehouse_options(shipment),
        )

    async def decide(
        self, shipment: Shipment, *, persist_prediction: bool = True
    ) -> tuple[ShipmentContext, RankingResult]:
        context = self.build_context(shipment)
        alternatives = await self.engine.generate_for_shipment(
            context,
            suppliers=self.supplier_options(shipment),
            warehouses=self.warehouse_options(shipment),
        )
        ranking = self.optimizer.rank(alternatives, profile_code=context.optimization_profile)

        if persist_prediction:
            self._snapshot_prediction(shipment, context, alternatives, ranking)

        return context, ranking

    def _snapshot_prediction(
        self,
        shipment: Shipment,
        context: ShipmentContext,
        alternatives: list[Alternative],
        ranking: RankingResult,
    ) -> None:
        """Fige ce qui a été annoncé, pour pouvoir le confronter au réel.

        Une probabilité recalculée après coup n'est pas celle qui a été montrée
        à l'utilisateur : sans cet instantané, aucune calibration honnête n'est
        possible.

        L'échec d'écriture ne doit jamais empêcher l'affichage d'une décision :
        la traçabilité est importante, la décision l'est davantage.
        """
        from app.services.outcome_collection import record_prediction

        current = next((a for a in alternatives if a.type is AlternativeType.CURRENT_PLAN), None)
        if current is None or current.route_assessment is None:
            return

        assessment = current.route_assessment
        recommandee = ranking.recommended

        try:
            record_prediction(
                self.repo,
                shipment=shipment,
                reference=context.reference,
                probability=current.disruption_probability,
                risk_level=current.risk_level.value,
                risk_score=current.risk_score,
                confidence=current.confidence.value,
                data_state=current.data_state.value,
                route_nodes=list(assessment.node_codes),
                exposed_segments=[
                    {
                        "de": s.from_name_fr,
                        "vers": s.to_name_fr,
                        "axe": s.road_ref,
                        "niveau": s.level.value,
                        "severite": s.severity,
                    }
                    for s in assessment.segments
                    if s.is_exposed
                ],
                recommended_option_id=recommandee.alternative.id if recommandee else None,
                recommended_option_label=(
                    recommandee.alternative.label_fr if recommandee else None
                ),
                engine_version=ENGINE_VERSION,
            )
            self.repo.session.commit()
        except Exception:  # noqa: BLE001
            logger.warning(
                "Instantané de prédiction non enregistré",
                context={"reference": context.reference},
            )
            self.repo.session.rollback()

    # --- stocks ---

    def inventory_for_product(self, product_id: str) -> list[tuple[Inventory, Site, Product]]:
        rows = self.repo.inventories(Inventory.product_id == product_id)
        return [
            (row, self.repo.get(Site, row.site_id), self.repo.get(Product, row.product_id))
            for row in rows
        ]

    def product_by_code_or_name(self, needle: str) -> Product:
        needle_lower = needle.strip().lower()
        for product in self.repo.products():
            if needle_lower in (product.code.lower(), product.name.lower()):
                return product
        for product in self.repo.products():
            if needle_lower in product.name.lower():
                return product
        # Recherche par culture en dernier recours : « tomate » doit trouver
        # « Tomate cerise export » sans que l'utilisateur connaisse le libellé exact.
        try:
            crop = get_crop(needle.strip().upper())
        except KeyError:
            crop = None
        if crop is not None:
            matches = self.repo.products(Product.crop_code == crop.code)
            if matches:
                return matches[0]
        raise NotFoundError(f"Aucun produit ne correspond à « {needle} ».")


def _as_utc(value: datetime) -> datetime:
    """SQLite restitue des datetimes naïfs : on rétablit le fuseau.

    Sans cela, une soustraction entre un datetime naïf et un datetime conscient
    lève une exception au moment du calcul de marge SLA.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
