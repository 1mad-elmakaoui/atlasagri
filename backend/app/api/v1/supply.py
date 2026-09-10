"""Stocks, fournisseurs, alertes, recommandations et simulations."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from app.api.deps import current_context, rate_limit, tenant_repository
from app.api.schemas import AlertAcknowledgement, DecisionRequest, SimulationRequest
from app.core.errors import AuthorizationError, ValidationError
from app.core.security import RequestContext
from app.core.untrusted import sanitize_user_text
from app.db.base import Alert, Product, Recommendation, Simulation, Site, Supplier
from app.db.repositories import TenantRepository
from app.domain.enums import RecommendationStatus
from app.domain.morocco import REGIONS
from app.services.agricultural_impact import AgriculturalImpactEngine
from app.services.nowcasting import NowcastingService
from app.services.simulation import SCENARIOS, SimulationService
from app.services.supply_chain_risk import (
    SupplyChainRiskEngine,
    calculate_reorder_quantity,
    calculate_stock_coverage,
)

router = APIRouter(tags=["Chaîne d'approvisionnement"], dependencies=[Depends(rate_limit)])


@router.get("/stocks")
def inventory(repo: TenantRepository = Depends(tenant_repository)) -> dict:
    rows = []
    for row in repo.inventories():
        site = repo.get(Site, row.site_id)
        product = repo.get(Product, row.product_id)
        coverage = calculate_stock_coverage(
            site_name_fr=site.name,
            product_name_fr=product.name,
            quantity_tonnes=row.quantity_tonnes,
            safety_stock_tonnes=row.safety_stock_tonnes,
            daily_demand_tonnes=row.average_daily_demand_tonnes,
        )
        suppliers = repo.suppliers(Supplier.crop_code == product.crop_code)
        lead_time = min((s.lead_time_days for s in suppliers), default=None)

        entry = {
            "id": row.id,
            "site": site.name,
            "region": site.region_code,
            "produit": product.name,
            "culture": product.crop_code,
            "quantite_tonnes": coverage.quantity_tonnes,
            "stock_securite_tonnes": coverage.safety_stock_tonnes,
            "demande_quotidienne_tonnes": coverage.daily_demand_tonnes,
            "couverture_jours": coverage.coverage_days,
            "couverture_utilisable_jours": coverage.usable_coverage_days,
            "sous_stock_securite": coverage.below_safety_stock,
            "delai_fournisseur_jours": lead_time,
            "resume_fr": coverage.as_business_summary_fr(),
        }
        if lead_time is not None:
            entry.update(
                calculate_reorder_quantity(
                    daily_demand_tonnes=row.average_daily_demand_tonnes,
                    lead_time_days=lead_time,
                    safety_stock_tonnes=row.safety_stock_tonnes,
                    current_quantity_tonnes=row.quantity_tonnes,
                )
            )
        rows.append(entry)

    rows.sort(key=lambda r: r["couverture_utilisable_jours"])
    return {"stocks": rows, "nombre": len(rows)}


@router.get("/fournisseurs")
async def suppliers(
    horizon_hours: int = Query(default=48, ge=1, le=168),
    repo: TenantRepository = Depends(tenant_repository),
) -> dict:
    impact = AgriculturalImpactEngine()
    nowcasting = NowcastingService()
    engine = SupplyChainRiskEngine()
    rows = []

    for supplier in repo.suppliers():
        site = repo.get(Site, supplier.site_id)
        nowcast = await nowcasting.nowcast(site.latitude, site.longitude)
        horizon = min(
            (h.horizon_hours for h in nowcast.horizons), key=lambda h: abs(h - horizon_hours)
        )
        agri = impact.evaluate(
            nowcast=nowcast,
            crop_code=supplier.crop_code,
            region_code=site.region_code,
            horizon_hours=horizon,
        )
        risk = engine.assess_supplier_exposure(
            supplier_id=supplier.id,
            supplier_name_fr=supplier.name,
            region_label_fr=REGIONS[site.region_code].name_fr
            if site.region_code in REGIONS
            else site.region_code,
            lead_time_days=supplier.lead_time_days,
            reliability=supplier.reliability,
            environmental_risk_score=agri.score,
            horizon_hours=horizon,
            confidence=agri.confidence,
            cause_fr=(
                agri.dominant_driver.label_fr
                if agri.dominant_driver
                else "Aucune condition défavorable détectée"
            ),
        )
        rows.append(
            {
                "id": supplier.id,
                "nom": supplier.name,
                "code": supplier.code,
                "site": site.name,
                "region": site.region_code,
                "zone_fr": risk.geographic_area_fr,
                "culture": supplier.crop_code,
                "delai_jours": supplier.lead_time_days,
                "fiabilite": supplier.reliability,
                "capacite_quotidienne_tonnes": supplier.daily_capacity_tonnes,
                "prix_mad_par_tonne": supplier.unit_price_mad_per_tonne,
                "fournisseur_de_secours": supplier.is_backup,
                "niveau_exposition": risk.level.label_fr,
                "code_exposition": risk.level.value,
                "score_exposition": risk.score,
                "implications_fr": list(risk.operational_implications_fr),
                "latitude": site.latitude,
                "longitude": site.longitude,
            }
        )

    rows.sort(key=lambda r: r["score_exposition"], reverse=True)
    return {"fournisseurs": rows, "nombre": len(rows)}


@router.get("/alertes")
def alerts(
    only_open: bool = Query(default=False),
    repo: TenantRepository = Depends(tenant_repository),
) -> dict:
    conditions = [Alert.acknowledged.is_(False)] if only_open else []
    rows = sorted(repo.alerts(*conditions), key=lambda a: a.created_at, reverse=True)
    return {
        "alertes": [
            {
                "id": a.id,
                "niveau": a.level,
                "type_risque": a.risk_type,
                "quoi_fr": a.what_fr,
                "ou_fr": a.where_fr,
                "quand_fr": a.when_fr,
                "impact_fr": a.impact_fr,
                "action_fr": a.action_fr,
                "sujet_type": a.subject_type,
                "sujet_id": a.subject_id,
                "traitee": a.acknowledged,
                "cree_le": a.created_at,
            }
            for a in rows
        ],
        "nombre": len(rows),
    }


@router.post("/alertes/{alert_id}/traiter")
def acknowledge_alert(
    alert_id: str,
    payload: AlertAcknowledgement,
    repo: TenantRepository = Depends(tenant_repository),
    context: RequestContext = Depends(current_context),
) -> dict:
    alert = repo.get(Alert, alert_id)
    alert.acknowledged = payload.acknowledged
    alert.acknowledged_by_user_id = context.user_id if payload.acknowledged else None
    repo.record_audit(
        action="alert:acknowledge",
        resource_type="ALERT",
        resource_id=alert.id,
        details={"traitee": payload.acknowledged},
    )
    repo.session.commit()
    return {"id": alert.id, "traitee": alert.acknowledged}


@router.get("/recommandations")
def recommendations(repo: TenantRepository = Depends(tenant_repository)) -> dict:
    rows = sorted(repo.recommendations(), key=lambda r: r.created_at, reverse=True)
    return {
        "recommandations": [
            {
                "id": r.id,
                "titre": r.title,
                "sujet_type": r.subject_type,
                "sujet_id": r.subject_id,
                "option_retenue": r.recommended_option_id,
                "statut": r.status,
                "statut_fr": RecommendationStatus(r.status).label_fr,
                "resume_fr": r.summary_fr,
                "raisons_fr": r.reasons_fr,
                "contreparties_fr": r.tradeoffs_fr,
                "impact_attendu": r.expected_impact,
                "confiance": r.confidence,
                "entrees": r.inputs,
                "alternatives_considerees": r.alternatives_considered,
                "sources": r.data_sources,
                "decidee_par": r.decided_by_user_id,
                "decidee_le": r.decided_at,
                "note_decision": r.decision_note,
                "cree_le": r.created_at,
            }
            for r in rows
        ],
        "nombre": len(rows),
    }


@router.post("/recommandations/{recommendation_id}/decision")
def decide(
    recommendation_id: str,
    payload: DecisionRequest,
    repo: TenantRepository = Depends(tenant_repository),
    context: RequestContext = Depends(current_context),
) -> dict:
    """Décision humaine sur une recommandation.

    Réservée aux rôles décisionnaires : une recommandation à impact
    opérationnel ne doit pas pouvoir être validée par un compte en lecture.
    """
    if not context.can_decide:
        raise AuthorizationError(
            "Seuls les responsables supply chain ou opérations peuvent statuer "
            "sur une recommandation."
        )

    try:
        status = RecommendationStatus(payload.decision.upper())
    except ValueError as exc:
        raise ValidationError(
            f"Décision invalide : « {payload.decision} ». "
            "Valeurs attendues : ACCEPTED, REJECTED, MODIFIED."
        ) from exc

    if status is RecommendationStatus.PROPOSED:
        raise ValidationError("« PROPOSED » n'est pas une décision.")

    recommendation = repo.get(Recommendation, recommendation_id)
    recommendation.status = status.value
    recommendation.decided_by_user_id = context.user_id
    recommendation.decided_at = datetime.now(UTC)
    recommendation.decision_note = (
        sanitize_user_text(payload.note, max_length=1000) if payload.note else None
    )

    # La décision humaine est tracée : c'est elle qui permettra, à terme, de
    # mesurer si les recommandations suivies évitent réellement des pertes.
    repo.record_audit(
        action="recommendation:decide",
        resource_type="RECOMMENDATION",
        resource_id=recommendation.id,
        details={"decision": status.value, "option": recommendation.recommended_option_id},
    )
    repo.session.commit()

    return {
        "id": recommendation.id,
        "statut": status.value,
        "statut_fr": status.label_fr,
        "decidee_le": recommendation.decided_at,
        "message_fr": (
            "Décision enregistrée. AtlasAgri ne déclenche aucune action "
            "opérationnelle : la mise en œuvre reste du ressort de vos équipes."
        ),
    }


@router.get("/simulations/scenarios")
def scenarios() -> dict:
    return {
        "scenarios": [
            {
                "code": code,
                "libelle_fr": label,
                "parametre_fr": (
                    "Heures de retard"
                    if code in ("retard_fournisseur", "depart_retarde")
                    else "Nœud routier neutralisé (optionnel)"
                ),
            }
            for code, label in SCENARIOS.items()
        ]
    }


@router.post("/simulations")
async def run_simulation(
    payload: SimulationRequest,
    repo: TenantRepository = Depends(tenant_repository),
    context: RequestContext = Depends(current_context),
) -> dict:
    result = await SimulationService(repo).run(
        scenario=payload.scenario,
        shipment_reference=payload.shipment_reference,
        parameter_value=payload.parameter_value,
        blocked_node_code=payload.blocked_node_code,
    )

    record = Simulation(
        tenant_id=context.tenant_id,
        scenario_code=payload.scenario,
        label_fr=result["scenario"],
        parameters=payload.model_dump(),
        baseline_result=result["situation_actuelle"],
        simulated_result=result["situation_simulee"],
        created_by_user_id=context.user_id,
    )
    repo.add(record)
    repo.session.commit()

    return result | {"simulation_id": record.id}
