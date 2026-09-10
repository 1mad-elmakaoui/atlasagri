"""Modèles de persistance.

Deux règles structurent ce module :

1. Toute table métier porte `tenant_id` **non nul et indexé**. L'isolation n'est
   pas une convention d'usage : elle est dans le schéma.
2. Les décisions (recommandations, simulations, actions de l'agent) conservent
   leurs entrées et leurs sources sérialisées. Une recommandation dont on ne
   peut plus reconstituer le raisonnement n'est pas auditable, donc pas
   utilisable dans un contexte d'entreprise.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class TenantScoped:
    """Marqueur des tables soumises à l'isolation multi-tenant.

    Les dépôts refusent toute requête sur une classe portant ce marqueur si
    aucun filtre de tenant n'est appliqué (cf. `repositories.py`).
    """

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )


# --------------------------------------------------------------------------
# Organisation et utilisateurs
# --------------------------------------------------------------------------

class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Configuration par client : profils d'optimisation, SLA par défaut, seuils surchargés.
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    users: Mapped[list[User]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class User(Base, TimestampMixin, TenantScoped):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped[Tenant] = relationship(back_populates="users")


# --------------------------------------------------------------------------
# Réseau physique
# --------------------------------------------------------------------------

class Site(Base, TimestampMixin, TenantScoped):
    """Ferme, entrepôt, plateforme, client ou port.

    Table unique volontairement : ces objets partagent coordonnées, région,
    capacité et rattachement au réseau routier. Les séparer en cinq tables
    dupliquerait ces colonnes sans rien apporter.
    """

    __tablename__ = "sites"
    __table_args__ = (Index("ix_sites_tenant_type", "tenant_id", "site_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    site_type: Mapped[str] = mapped_column(String(32), nullable=False)
    region_code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    # Nœud du réseau routier auquel le site est rattaché.
    road_node_code: Mapped[str] = mapped_column(String(48), nullable=False)
    capacity_tonnes: Mapped[float | None] = mapped_column(Float)
    has_cold_storage: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)


class Supplier(Base, TimestampMixin, TenantScoped):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    crop_code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    lead_time_days: Mapped[float] = mapped_column(Float, nullable=False)
    # Fiabilité contractuelle observée : part des livraisons conformes.
    reliability: Mapped[float] = mapped_column(Float, default=0.9)
    daily_capacity_tonnes: Mapped[float] = mapped_column(Float, nullable=False)
    unit_price_mad_per_tonne: Mapped[float] = mapped_column(Float, nullable=False)
    is_backup: Mapped[bool] = mapped_column(Boolean, default=False)

    site: Mapped[Site] = relationship()


class Product(Base, TimestampMixin, TenantScoped):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    crop_code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    optimization_profile: Mapped[str] = mapped_column(String(48), nullable=False)
    unit_value_mad_per_tonne: Mapped[float] = mapped_column(Float, nullable=False)


class Inventory(Base, TimestampMixin, TenantScoped):
    __tablename__ = "inventories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "product_id", name="uq_inventory_site_product"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    quantity_tonnes: Mapped[float] = mapped_column(Float, nullable=False)
    safety_stock_tonnes: Mapped[float] = mapped_column(Float, default=0.0)
    average_daily_demand_tonnes: Mapped[float] = mapped_column(Float, nullable=False)

    site: Mapped[Site] = relationship()
    product: Mapped[Product] = relationship()


class Shipment(Base, TimestampMixin, TenantScoped):
    __tablename__ = "shipments"
    __table_args__ = (Index("ix_shipments_tenant_status", "tenant_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    reference: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))
    origin_site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    destination_site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    volume_tonnes: Mapped[float] = mapped_column(Float, nullable=False)
    transport_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PLANNED")
    departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sla_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Itinéraire retenu : suite de codes de nœuds. La géométrie est recalculée
    # par le fournisseur de routage, pas figée en base.
    planned_route_nodes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    baseline_cost_mad: Mapped[float | None] = mapped_column(Float)

    product: Mapped[Product] = relationship()
    supplier: Mapped[Supplier | None] = relationship()
    origin_site: Mapped[Site] = relationship(foreign_keys=[origin_site_id])
    destination_site: Mapped[Site] = relationship(foreign_keys=[destination_site_id])


# --------------------------------------------------------------------------
# Décision et traçabilité
# --------------------------------------------------------------------------

class RiskPrediction(Base, TimestampMixin, TenantScoped):
    """Prédiction figée au moment où une décision a été prise.

    Sans cet enregistrement, aucune calibration n'est possible : calibrer
    demande des couples (probabilité annoncée, événement observé), et une
    probabilité recalculée après coup ne serait pas celle qui a été montrée à
    l'utilisateur.

    L'enregistrement est **immuable**. Une nouvelle évaluation de la même
    expédition crée une nouvelle ligne : on veut pouvoir constater comment la
    prédiction a évolué à l'approche du départ, pas écraser l'historique.
    """

    __tablename__ = "risk_predictions"
    __table_args__ = (
        Index("ix_prediction_tenant_subject", "tenant_id", "subject_type", "subject_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False, default="SHIPMENT")
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    subject_reference: Mapped[str] = mapped_column(String(32), nullable=False)

    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Fenêtre à laquelle la prédiction s'applique : sert à apparier avec le
    # résultat observé, et à mesurer l'anticipation réellement offerte.
    horizon_hours: Mapped[float] = mapped_column(Float, nullable=False)

    disruption_probability: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    data_state: Mapped[str] = mapped_column(String(16), nullable=False)

    plan_route_nodes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    exposed_segments: Mapped[list[Any]] = mapped_column(JSON, default=list)
    recommended_option_id: Mapped[str | None] = mapped_column(String(64))
    recommended_option_label: Mapped[str | None] = mapped_column(String(255))
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)


class ShipmentOutcome(Base, TimestampMixin, TenantScoped):
    """Ce qui s'est réellement passé, tel que rapporté par le terrain.

    Trois règles, reprises de la discipline de l'article d'Ahmadi et al. :

    1. **Immuabilité.** Les réponses brutes ne sont jamais modifiées. Une
       correction crée un nouvel enregistrement pointant vers celui qu'il
       remplace (`supersedes_id`). On ne réécrit pas une mesure de terrain.
    2. **Partition figée à l'écriture.** Chaque résultat est affecté à
       « calibration » ou « évaluation » au moment de sa création, jamais après.
       Choisir la partition plus tard permettrait d'ajuster un modèle contre les
       données censées le juger — c'est ainsi qu'une calibration devient
       silencieusement circulaire.
    3. **Traçabilité de la source.** Qui a répondu, quand, et sur quelle
       prédiction. Un retour anonyme et non daté n'est pas exploitable.
    """

    __tablename__ = "shipment_outcomes"
    __table_args__ = (
        Index("ix_outcome_tenant_shipment", "tenant_id", "shipment_id"),
        Index("ix_outcome_split", "tenant_id", "evaluation_split"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    shipment_id: Mapped[str] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    prediction_id: Mapped[str | None] = mapped_column(
        ForeignKey("risk_predictions.id", ondelete="SET NULL")
    )
    supersedes_id: Mapped[str | None] = mapped_column(String(36))

    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    collected_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # « calibration » ou « evaluation ».
    evaluation_split: Mapped[str] = mapped_column(String(16), nullable=False)

    # --- Réponses structurées, dérivées de l'instrument ---
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False)
    non_delivery_reason: Mapped[str | None] = mapped_column(String(32))
    arrival_delta_hours: Mapped[float | None] = mapped_column(Float)
    planned_route_followed: Mapped[bool | None] = mapped_column(Boolean)
    actual_route_note: Mapped[str | None] = mapped_column(Text)

    disruption_occurred: Mapped[bool | None] = mapped_column(Boolean)
    disruption_type: Mapped[str | None] = mapped_column(String(32))
    disruption_location: Mapped[str | None] = mapped_column(String(255))
    disruption_delay_hours: Mapped[float | None] = mapped_column(Float)

    quality_affected: Mapped[bool | None] = mapped_column(Boolean)
    estimated_loss_mad: Mapped[float | None] = mapped_column(Float)
    comment: Mapped[str | None] = mapped_column(Text)

    # Réponses brutes telles que saisies, conservées intactes : elles permettent
    # de recoder les données plus tard sans redemander au terrain.
    raw_answers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    instrument_version: Mapped[str] = mapped_column(String(32), nullable=False)


class CalibratedThreshold(Base, TimestampMixin, TenantScoped):
    """Seuil dérivé d'un historique réel, avec sa provenance complète.

    Remplace un seuil provisoire pour une région et une culture données. La
    provenance est stockée avec la valeur : un seuil dont on ne peut plus dire
    d'où il vient redevient une hypothèse.
    """

    __tablename__ = "calibrated_thresholds"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "region_code", "crop_code", "risk_type", "variable",
            name="uq_threshold_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    region_code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    crop_code: Mapped[str | None] = mapped_column(String(48), index=True)
    risk_type: Mapped[str] = mapped_column(String(40), nullable=False)
    variable: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)

    moderate_at: Mapped[float] = mapped_column(Float, nullable=False)
    high_at: Mapped[float] = mapped_column(Float, nullable=False)
    critical_at: Mapped[float | None] = mapped_column(Float)

    source_label: Mapped[str] = mapped_column(String(255), nullable=False)
    calibration_window: Mapped[str] = mapped_column(String(32), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    low_quantile: Mapped[float] = mapped_column(Float, nullable=False)
    high_quantile: Mapped[float] = mapped_column(Float, nullable=False)
    calibrated_on: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Recommendation(Base, TimestampMixin, TenantScoped):
    """Recommandation proposée à un humain.

    Les colonnes `inputs`, `alternatives_considered` et `data_sources` existent
    pour répondre à la question « pourquoi cette recommandation est-elle
    apparue ? » plusieurs mois après les faits.
    """

    __tablename__ = "recommendations"
    __table_args__ = (Index("ix_reco_tenant_status", "tenant_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    recommended_option_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PROPOSED")
    summary_fr: Mapped[str] = mapped_column(Text, nullable=False)
    reasons_fr: Mapped[list[Any]] = mapped_column(JSON, default=list)
    tradeoffs_fr: Mapped[list[Any]] = mapped_column(JSON, default=list)
    expected_impact: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)

    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    alternatives_considered: Mapped[list[Any]] = mapped_column(JSON, default=list)
    data_sources: Mapped[list[Any]] = mapped_column(JSON, default=list)

    decided_by_user_id: Mapped[str | None] = mapped_column(String(36))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)


class Alert(Base, TimestampMixin, TenantScoped):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_tenant_ack", "tenant_id", "acknowledged"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # Structure QUOI / OÙ / QUAND / IMPACT / ACTION.
    what_fr: Mapped[str] = mapped_column(Text, nullable=False)
    where_fr: Mapped[str] = mapped_column(String(255), nullable=False)
    when_fr: Mapped[str] = mapped_column(String(255), nullable=False)
    impact_fr: Mapped[str] = mapped_column(Text, nullable=False)
    action_fr: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(32))
    subject_id: Mapped[str | None] = mapped_column(String(36))
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_by_user_id: Mapped[str | None] = mapped_column(String(36))


class Simulation(Base, TimestampMixin, TenantScoped):
    __tablename__ = "simulations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scenario_code: Mapped[str] = mapped_column(String(64), nullable=False)
    label_fr: Mapped[str] = mapped_column(String(255), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    baseline_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    simulated_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)


class AgentConversation(Base, TimestampMixin, TenantScoped):
    __tablename__ = "agent_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    messages: Mapped[list[Any]] = mapped_column(JSON, default=list)


class AuditLog(Base, TenantScoped):
    """Journal d'audit : qui a fait quoi, quand, sur quelle ressource."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_tenant_time", "tenant_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
