"""Accès aux données avec isolation multi-tenant appliquée par construction.

Le point critique : `TenantRepository` ajoute systématiquement le filtre
`tenant_id` et il n'existe **aucune méthode** permettant de l'omettre. Une fuite
inter-organisations demanderait donc d'écrire délibérément une requête hors de
ce dépôt, ce qui se voit en relecture de code.

Un identifiant appartenant à un autre tenant produit une `TenantIsolationError`,
traduite en 404 : confirmer l'existence d'une ressource d'un autre client serait
déjà une divulgation.
"""

from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, TenantIsolationError
from app.core.security import RequestContext
from app.db.base import (
    AgentConversation,
    Alert,
    AuditLog,
    CalibratedThreshold,
    Inventory,
    Product,
    Recommendation,
    RiskPrediction,
    Shipment,
    ShipmentOutcome,
    Simulation,
    Site,
    Supplier,
    TenantScoped,
)

ModelT = TypeVar("ModelT", bound=TenantScoped)


class TenantRepository:
    """Dépôt générique borné à une organisation."""

    def __init__(self, session: Session, context: RequestContext) -> None:
        self.session = session
        self.context = context

    # --- primitives ---

    def _scoped(self, model: type[ModelT]) -> Select[tuple[ModelT]]:
        if not issubclass(model, TenantScoped):
            raise TypeError(
                f"{model.__name__} n'est pas une table multi-tenant : "
                "elle ne peut pas être interrogée via TenantRepository."
            )
        return select(model).where(model.tenant_id == self.context.tenant_id)

    def list(self, model: type[ModelT], *conditions: Any) -> list[ModelT]:
        stmt = self._scoped(model)
        for condition in conditions:
            stmt = stmt.where(condition)
        return list(self.session.execute(stmt).scalars())

    def get(self, model: type[ModelT], entity_id: str) -> ModelT:
        entity = self.session.get(model, entity_id)
        if entity is None:
            raise NotFoundError(f"{_label(model)} introuvable.")
        if entity.tenant_id != self.context.tenant_id:
            raise TenantIsolationError()
        return entity

    def find(self, model: type[ModelT], *conditions: Any) -> ModelT | None:
        stmt = self._scoped(model)
        for condition in conditions:
            stmt = stmt.where(condition)
        return self.session.execute(stmt.limit(1)).scalars().first()

    def add(self, entity: ModelT) -> ModelT:
        """Force le tenant de l'appelant : une valeur fournie est ignorée."""
        entity.tenant_id = self.context.tenant_id
        self.session.add(entity)
        self.session.flush()
        return entity

    # --- accès nommés, plus lisibles dans les services ---

    def sites(self, *conditions: Any) -> list[Site]:
        return self.list(Site, *conditions)

    def site(self, site_id: str) -> Site:
        return self.get(Site, site_id)

    def suppliers(self, *conditions: Any) -> list[Supplier]:
        return self.list(Supplier, *conditions)

    def products(self, *conditions: Any) -> list[Product]:
        return self.list(Product, *conditions)

    def inventories(self, *conditions: Any) -> list[Inventory]:
        return self.list(Inventory, *conditions)

    def shipments(self, *conditions: Any) -> list[Shipment]:
        return self.list(Shipment, *conditions)

    def shipment(self, shipment_id: str) -> Shipment:
        return self.get(Shipment, shipment_id)

    def shipment_by_reference(self, reference: str) -> Shipment:
        found = self.find(Shipment, Shipment.reference == reference)
        if found is None:
            raise NotFoundError(f"Expédition « {reference} » introuvable.")
        return found

    def alerts(self, *conditions: Any) -> list[Alert]:
        return self.list(Alert, *conditions)

    def recommendations(self, *conditions: Any) -> list[Recommendation]:
        return self.list(Recommendation, *conditions)

    def predictions(self, *conditions: Any) -> list[RiskPrediction]:
        return self.list(RiskPrediction, *conditions)

    def outcomes(self, *conditions: Any) -> list[ShipmentOutcome]:
        return self.list(ShipmentOutcome, *conditions)

    def calibrated_thresholds(self, *conditions: Any) -> list[CalibratedThreshold]:
        return self.list(CalibratedThreshold, *conditions)

    def simulations(self, *conditions: Any) -> list[Simulation]:
        return self.list(Simulation, *conditions)

    def conversations(self, *conditions: Any) -> list[AgentConversation]:
        return self.list(AgentConversation, *conditions)

    # --- audit ---

    def record_audit(
        self,
        *,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        outcome: str = "SUCCESS",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditLog(
                tenant_id=self.context.tenant_id,
                user_id=self.context.user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                details=details or {},
            )
        )


_LABELS: dict[str, str] = {
    "Site": "Site",
    "Supplier": "Fournisseur",
    "Product": "Produit",
    "Inventory": "Stock",
    "Shipment": "Expédition",
    "Alert": "Alerte",
    "Recommendation": "Recommandation",
    "Simulation": "Simulation",
    "RiskPrediction": "Prédiction de risque",
    "ShipmentOutcome": "Résultat d'expédition",
    "CalibratedThreshold": "Seuil calibré",
    "AgentConversation": "Conversation",
}


def _label(model: type) -> str:
    return _LABELS.get(model.__name__, model.__name__)
