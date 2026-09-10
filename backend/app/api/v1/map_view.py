"""Carte opérationnelle : sites, régions et itinéraires en GeoJSON."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import rate_limit, tenant_repository
from app.db.base import Product, Site
from app.db.repositories import TenantRepository
from app.domain.enums import RiskLevel, SiteType
from app.domain.morocco import NODES
from app.services.shipment_service import ShipmentService

router = APIRouter(prefix="/carte", tags=["Carte"], dependencies=[Depends(rate_limit)])


@router.get("/sites")
def sites(
    region_code: str | None = Query(default=None),
    site_type: str | None = Query(default=None),
    repo: TenantRepository = Depends(tenant_repository),
) -> dict:
    """Sites de l'organisation, au format GeoJSON."""
    conditions = []
    if region_code:
        conditions.append(Site.region_code == region_code)
    if site_type:
        conditions.append(Site.site_type == site_type)

    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [site.longitude, site.latitude]},
            "properties": {
                "id": site.id,
                "code": site.code,
                "nom": site.name,
                "type": site.site_type,
                "type_fr": SiteType(site.site_type).label_fr,
                "region": site.region_code,
                "capacite_tonnes": site.capacity_tonnes,
                "stockage_frigorifique": site.has_cold_storage,
                "noeud_routier": site.road_node_code,
            },
        }
        for site in repo.sites(*conditions)
    ]
    return {"type": "FeatureCollection", "features": features}


@router.get("/expedition/{reference}")
async def shipment_layers(
    reference: str, repo: TenantRepository = Depends(tenant_repository)
) -> dict:
    """Couches cartographiques d'une expédition.

    L'état de la carte reflète la recommandation : l'itinéraire retenu est
    marqué comme tel, les tronçons exposés sont isolés dans leur propre couche,
    et les options moins bien classées portent leur rang pour que l'interface
    puisse les atténuer visuellement.
    """
    service = ShipmentService(repo)
    shipment = service.resolve(reference)
    context, ranking = await service.decide(shipment)

    routes = []
    exposed_segments = []

    for ranked in ranking.ranked:
        assessment = ranked.alternative.route_assessment
        if assessment is None:
            continue

        routes.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": assessment.coordinates_path},
                "properties": {
                    "id": ranked.alternative.id,
                    "libelle": ranked.alternative.label_fr,
                    "rang": ranked.rank,
                    "recommandee": ranked.is_recommended,
                    "plan_actuel": ranked.alternative.type.value == "CURRENT_PLAN",
                    "niveau_risque": ranked.alternative.risk_level.value,
                    "niveau_risque_fr": ranked.alternative.risk_level.label_fr,
                    "distance_km": ranked.alternative.distance_km,
                    "duree_h": ranked.alternative.duration_hours,
                    "cout_mad": ranked.alternative.estimated_cost_mad,
                    "probabilite_perturbation": ranked.alternative.disruption_probability,
                    "villes": [
                        NODES[c].name_fr for c in assessment.node_codes if c in NODES
                    ],
                },
            }
        )

        if ranked.is_recommended or ranked.alternative.type.value == "CURRENT_PLAN":
            for segment in assessment.segments:
                if not segment.is_exposed:
                    continue
                exposed_segments.append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [segment.from_coordinates[1], segment.from_coordinates[0]],
                                [segment.to_coordinates[1], segment.to_coordinates[0]],
                            ],
                        },
                        "properties": {
                            "itineraire_id": ranked.alternative.id,
                            "de": segment.from_name_fr,
                            "vers": segment.to_name_fr,
                            "axe": segment.road_ref,
                            "niveau_risque": segment.level.value,
                            "niveau_risque_fr": segment.level.label_fr,
                            "severite": segment.severity,
                            "passage_prevu_fr": (
                                f"{segment.entry_at:%d/%m %H:%M} → {segment.exit_at:%H:%M}"
                            ),
                            "motifs_fr": list(segment.reasons_fr),
                        },
                    }
                )

    origin = repo.get(Site, shipment.origin_site_id)
    destination = repo.get(Site, shipment.destination_site_id)
    product = repo.get(Product, shipment.product_id)

    return {
        "expedition": {
            "reference": shipment.reference,
            "produit": product.name,
            "volume_tonnes": shipment.volume_tonnes,
        },
        "points": {
            "type": "FeatureCollection",
            "features": [
                _point(origin, "Origine"),
                _point(destination, "Destination"),
            ],
        },
        "itineraires": {"type": "FeatureCollection", "features": routes},
        "troncons_exposes": {"type": "FeatureCollection", "features": exposed_segments},
        "option_recommandee": ranking.recommended_id,
        "legende": _legend(),
    }


def _point(site: Site, role_fr: str) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [site.longitude, site.latitude]},
        "properties": {
            "nom": site.name,
            "role_fr": role_fr,
            "type_fr": SiteType(site.site_type).label_fr,
        },
    }


def _legend() -> list[dict]:
    """Légende en français, servie par le serveur.

    Elle vit ici plutôt que dans le frontend pour que les couleurs et les
    libellés restent alignés sur les niveaux réellement produits par le moteur.
    """
    return [
        {"code": RiskLevel.LOW.value, "libelle_fr": "Risque faible", "couleur": "#15803d"},
        {"code": RiskLevel.MODERATE.value, "libelle_fr": "Risque modéré", "couleur": "#ca8a04"},
        {"code": RiskLevel.HIGH.value, "libelle_fr": "Risque élevé", "couleur": "#ea580c"},
        {"code": RiskLevel.CRITICAL.value, "libelle_fr": "Risque critique", "couleur": "#b91c1c"},
        {"code": "RECOMMENDED", "libelle_fr": "Itinéraire recommandé", "couleur": "#1d4ed8"},
        {"code": "CURRENT", "libelle_fr": "Itinéraire actuel", "couleur": "#64748b"},
    ]
