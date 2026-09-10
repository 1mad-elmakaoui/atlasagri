"""Vue générale : ce qu'un responsable doit voir en ouvrant l'application."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import rate_limit, tenant_repository
from app.db.base import Alert, Product, Recommendation, Site
from app.db.repositories import TenantRepository
from app.domain.enums import RiskLevel
from app.domain.morocco import REGIONS
from app.providers.registry import providers_health
from app.services.agricultural_impact import AgriculturalImpactEngine
from app.services.nowcasting import NowcastingService
from app.services.shipment_service import ShipmentService
from app.services.supply_chain_risk import calculate_stock_coverage

router = APIRouter(
    prefix="/tableau-de-bord", tags=["Vue générale"], dependencies=[Depends(rate_limit)]
)


@router.get("")
async def overview(repo: TenantRepository = Depends(tenant_repository)) -> dict:
    """Synthèse : risque global, expositions et opportunités d'action.

    Le « risque global » est le plus élevé des risques d'expédition, non une
    moyenne : une moyenne diluerait une expédition critique parmi dix
    expéditions saines et masquerait précisément ce qu'il faut voir.
    """
    service = ShipmentService(repo)

    exposed_shipments = []
    worst_rank = -1

    for shipment in repo.shipments():
        alternatives = await service.alternatives_for(shipment)
        current = next((a for a in alternatives if a.type.value == "CURRENT_PLAN"), None)
        if current is None:
            continue

        product = repo.get(Product, shipment.product_id)
        recommended = next(
            (
                a
                for a in alternatives
                if a.is_feasible and a.risk_score < current.risk_score - 0.05
            ),
            None,
        )
        worst_rank = max(worst_rank, current.risk_level.rank)

        if current.risk_level.rank >= RiskLevel.MODERATE.rank:
            exposed_shipments.append(
                {
                    "reference": shipment.reference,
                    "produit": product.name,
                    "origine": repo.get(Site, shipment.origin_site_id).name,
                    "destination": repo.get(Site, shipment.destination_site_id).name,
                    "volume_tonnes": shipment.volume_tonnes,
                    "niveau_risque": current.risk_level.label_fr,
                    "rang": current.risk_level.rank,
                    "probabilite_perturbation": current.disruption_probability,
                    "depart_prevu": shipment.departure_at,
                    "echeance_respectee": current.sla_compliant,
                    "facteurs_fr": list(current.risk_factors_fr)[:2],
                    "action_possible_fr": (
                        recommended.label_fr if recommended is not None else None
                    ),
                    "gain_risque_points": (
                        round(
                            (current.disruption_probability - recommended.disruption_probability)
                            * 100,
                            1,
                        )
                        if recommended is not None
                        else None
                    ),
                }
            )

    exposed_shipments.sort(key=lambda s: s["rang"], reverse=True)

    critical_stock = []
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
        if coverage.usable_coverage_days < 3.0 or coverage.below_safety_stock:
            critical_stock.append(
                {
                    "site": site.name,
                    "produit": product.name,
                    "couverture_jours": coverage.coverage_days,
                    "couverture_utilisable_jours": coverage.usable_coverage_days,
                    "sous_stock_securite": coverage.below_safety_stock,
                    "resume_fr": coverage.as_business_summary_fr(),
                }
            )
    critical_stock.sort(key=lambda s: s["couverture_utilisable_jours"])

    alerts = repo.alerts(Alert.acknowledged.is_(False))
    pending = repo.recommendations(Recommendation.status == "PROPOSED")

    global_level = RiskLevel.LOW if worst_rank < 0 else list(RiskLevel)[worst_rank]

    return {
        "risque_global": {
            "niveau": global_level.label_fr,
            "code": global_level.value,
            "explication_fr": (
                "Le risque global reprend le niveau de l'expédition la plus exposée. "
                "Une moyenne masquerait une expédition critique isolée."
            ),
        },
        "indicateurs": {
            "expeditions_suivies": len(repo.shipments()),
            "expeditions_exposees": len(exposed_shipments),
            "alertes_non_traitees": len(alerts),
            "recommandations_en_attente": len(pending),
            "stocks_critiques": len(critical_stock),
            "fournisseurs_suivis": len(repo.suppliers()),
        },
        "expeditions_exposees": exposed_shipments[:8],
        "stocks_critiques": critical_stock[:6],
        "alertes": [
            {
                "id": a.id,
                "niveau": a.level,
                "quoi_fr": a.what_fr,
                "ou_fr": a.where_fr,
                "quand_fr": a.when_fr,
                "impact_fr": a.impact_fr,
                "action_fr": a.action_fr,
                "cree_le": a.created_at,
            }
            for a in sorted(alerts, key=lambda a: a.created_at, reverse=True)[:5]
        ],
        "opportunites_action": [
            {
                "reference": s["reference"],
                "action_fr": s["action_possible_fr"],
                "gain_risque_points": s["gain_risque_points"],
            }
            for s in exposed_shipments
            if s["action_possible_fr"]
        ][:5],
        "sources": await providers_health(),
    }


@router.get("/regions")
async def regions_at_risk(
    horizon_hours: int = 24, repo: TenantRepository = Depends(tenant_repository)
) -> dict:
    """Exposition par région, pour la carte et le tableau de bord."""
    engine = AgriculturalImpactEngine()
    service = NowcastingService()

    # Régions où l'organisation possède réellement des sites : afficher tout le
    # Maroc à une coopérative du Souss serait du bruit.
    active_regions = {site.region_code for site in repo.sites()}
    entries = []

    for code in sorted(active_regions):
        region = REGIONS.get(code)
        if region is None:
            continue
        nowcast = await service.nowcast(region.center.latitude, region.center.longitude)
        horizon = min(
            (h.horizon_hours for h in nowcast.horizons),
            key=lambda h: abs(h - horizon_hours),
        )

        worst = None
        for crop_code in region.main_crops:
            try:
                risk = engine.evaluate(
                    nowcast=nowcast,
                    crop_code=crop_code,
                    region_code=code,
                    horizon_hours=horizon,
                )
            except KeyError:
                continue
            if worst is None or risk.score > worst.score:
                worst = risk

        entries.append(
            {
                "code": region.code,
                "nom_fr": region.name_fr,
                "latitude": region.center.latitude,
                "longitude": region.center.longitude,
                "profil_agricole_fr": region.agricultural_profile_fr,
                "cultures": list(region.main_crops),
                "niveau_risque": worst.level.label_fr if worst else None,
                "code_risque": worst.level.value if worst else None,
                "score_risque": worst.score if worst else None,
                "culture_exposee": worst.crop_name_fr if worst else None,
                "facteur_principal_fr": (
                    worst.dominant_driver.label_fr
                    if worst and worst.dominant_driver
                    else None
                ),
                "etat_des_donnees": worst.data_state.label_fr if worst else None,
                "confiance": worst.confidence.label_fr if worst else None,
            }
        )

    entries.sort(key=lambda e: e["score_risque"] or 0, reverse=True)
    return {"horizon_heures": horizon_hours, "regions": entries}
