"""Expéditions, itinéraires et alternatives."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import rate_limit, tenant_repository
from app.db.base import Product, Site
from app.db.repositories import TenantRepository
from app.domain.morocco import NODES
from app.services.shipment_service import ShipmentService

router = APIRouter(prefix="/expeditions", tags=["Expéditions"], dependencies=[Depends(rate_limit)])


@router.get("")
async def list_shipments(repo: TenantRepository = Depends(tenant_repository)) -> dict:
    """Liste des expéditions avec leur niveau de risque.

    Le risque est calculé à la volée pour chaque expédition. C'est acceptable
    au volume du MVP grâce au cache météo ; au-delà, ce calcul devra être
    précalculé et rafraîchi périodiquement.
    """
    service = ShipmentService(repo)
    rows = []

    for shipment in repo.shipments():
        product = repo.get(Product, shipment.product_id)
        origin = repo.get(Site, shipment.origin_site_id)
        destination = repo.get(Site, shipment.destination_site_id)

        entry = {
            "id": shipment.id,
            "reference": shipment.reference,
            "produit": product.name,
            "culture": product.crop_code,
            "origine": origin.name,
            "destination": destination.name,
            "volume_tonnes": shipment.volume_tonnes,
            "statut": shipment.status,
            "depart_prevu": shipment.departure_at,
            "echeance_service": shipment.sla_deadline_at,
            "niveau_risque": None,
            "probabilite_perturbation": None,
        }

        alternatives = await service.alternatives_for(shipment)
        current = next((a for a in alternatives if a.type.value == "CURRENT_PLAN"), None)
        if current is not None:
            entry["niveau_risque"] = current.risk_level.label_fr
            entry["rang_risque"] = current.risk_level.rank
            entry["probabilite_perturbation"] = current.disruption_probability
            entry["echeance_respectee"] = current.sla_compliant
        rows.append(entry)

    rows.sort(key=lambda r: r.get("rang_risque", -1), reverse=True)
    return {"expeditions": rows, "nombre": len(rows)}


@router.get("/{reference}")
async def shipment_detail(
    reference: str, repo: TenantRepository = Depends(tenant_repository)
) -> dict:
    """Détail complet : contexte, itinéraires évalués et recommandation."""
    service = ShipmentService(repo)
    shipment = service.resolve(reference)
    context, ranking = await service.decide(shipment)

    recommended = ranking.recommended
    return {
        "expedition": {
            "id": shipment.id,
            "reference": shipment.reference,
            "produit": context.product_name_fr,
            "culture": context.crop_code,
            "origine": context.origin_name_fr,
            "destination": context.destination_name_fr,
            "volume_tonnes": context.volume_tonnes,
            "mode_transport": context.transport_mode.label_fr,
            "statut": shipment.status,
            "depart_prevu": context.departure_at,
            "echeance_service": context.sla_deadline_at,
        },
        "profil_optimisation": {
            "code": ranking.profile_code,
            "libelle_fr": ranking.profile_label_fr,
            "justification_fr": ranking.profile_rationale_fr,
            "ponderations": ranking.weights_fr,
        },
        "confiance": ranking.confidence.label_fr,
        "reserves_fr": list(ranking.caveats_fr),
        "recommandation": _serialize_ranked(recommended) if recommended else None,
        "alternatives": [_serialize_ranked(r) for r in ranking.ranked],
        "alternatives_ecartees": [
            {
                "id": a.id,
                "libelle_fr": a.label_fr,
                "type_fr": a.type.label_fr,
                "motifs_rejet_fr": list(a.infeasibility_reasons_fr),
            }
            for a in ranking.rejected
        ],
    }


def _serialize_ranked(ranked) -> dict:
    """Sérialise une alternative classée, tracé de carte compris."""
    alternative = ranked.alternative
    assessment = alternative.route_assessment

    payload = {
        "id": alternative.id,
        "rang": ranked.rank,
        "type": alternative.type.value,
        "type_fr": alternative.type.label_fr,
        "libelle_fr": alternative.label_fr,
        "description_fr": alternative.description_fr,
        "recommandee": ranked.is_recommended,
        "score_global": ranked.total_score,
        "niveau_risque": alternative.risk_level.value,
        "niveau_risque_fr": alternative.risk_level.label_fr,
        "score_risque": alternative.risk_score,
        "probabilite_perturbation": alternative.disruption_probability,
        "part_exposee": alternative.exposure_fraction,
        "fiabilite": alternative.reliability,
        "distance_km": alternative.distance_km,
        "duree_h": alternative.duration_hours,
        "cout_mad": alternative.estimated_cost_mad,
        "depart": alternative.departure_at,
        "arrivee_estimee": alternative.estimated_arrival_at,
        "echeance_respectee": alternative.sla_compliant,
        "marge_echeance_h": alternative.sla_margin_hours,
        "confiance": alternative.confidence.label_fr,
        "etat_des_donnees": alternative.data_state.label_fr,
        "sources": [s.label_fr for s in alternative.data_sources],
        "facteurs_de_risque_fr": list(alternative.risk_factors_fr),
        "raisons_fr": list(ranked.recommendation_reasons_fr),
        "contreparties_fr": list(ranked.tradeoffs_fr),
        "ecart_risque_points": ranked.risk_delta_points,
        "ecart_cout_mad": ranked.cost_delta_mad,
        "ecart_cout_pct": ranked.cost_delta_percent,
        "ecart_duree_h": ranked.duration_delta_hours,
        "criteres": [
            {
                "libelle_fr": c.label_fr,
                "valeur": c.raw_value,
                "unite_fr": c.unit_fr,
                "normalise": c.normalized,
                "poids": c.weight,
            }
            for c in ranked.criteria
        ],
    }

    if assessment is not None:
        payload["trace"] = assessment.coordinates_path
        payload["villes"] = [NODES[c].name_fr for c in assessment.node_codes if c in NODES]
        payload["troncons"] = [
            {
                "index": s.index,
                "de": s.from_name_fr,
                "vers": s.to_name_fr,
                "axe": s.road_ref,
                "distance_km": s.distance_km,
                "depart_troncon": s.entry_at,
                "arrivee_troncon": s.exit_at,
                "heures_apres_depart": s.hours_from_departure,
                "niveau_risque": s.level.value,
                "niveau_risque_fr": s.level.label_fr,
                "severite": s.severity,
                "expose": s.is_exposed,
                "intensite_pluie_mm_h": s.rain_intensity_mm_h,
                "cumul_24h_avant_mm": s.antecedent_rain_24h_mm,
                "rafale_kmh": s.max_wind_gust_kmh,
                "motifs_fr": list(s.reasons_fr),
                "trace": [
                    [s.from_coordinates[1], s.from_coordinates[0]],
                    [s.to_coordinates[1], s.to_coordinates[0]],
                ],
            }
            for s in assessment.segments
        ]
    return payload
