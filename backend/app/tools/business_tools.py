"""Outils métier exposés à Claude.

Chaque outil couvre **une** capacité et renvoie une structure typée, jamais un
bloc de texte. Claude compare et explique ; il ne recalcule rien.

Les libellés de sortie sont en français : ils remontent tels quels dans
l'interface et dans les réponses du copilote, sans traduction intermédiaire qui
introduirait des écarts de vocabulaire entre les deux.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.errors import NotFoundError
from app.core.untrusted import sanitize_user_text
from app.db.base import Alert, Inventory, Product, Recommendation, Site, Supplier
from app.domain.crops import CROPS, get_crop
from app.domain.enums import (
    DEFAULT_HORIZONS_HOURS,
    Confidence,
    RiskLevel,
    UserRole,
)
from app.domain.morocco import NODES, REGIONS
from app.providers.registry import get_satellite_provider
from app.providers.satellite.interface import SatelliteAvailability
from app.services.agricultural_impact import AgriculturalImpactEngine, summarize_for_business
from app.services.calibration import load_overrides
from app.services.nowcasting import NowcastingService
from app.services.shipment_service import ShipmentService
from app.services.supply_chain_risk import (
    SupplyChainRiskEngine,
    calculate_reorder_quantity,
    calculate_stock_coverage,
)
from app.tools.registry import ToolContext, tool

DECIDERS = (UserRole.SUPPLY_CHAIN_MANAGER, UserRole.OPERATIONS_MANAGER)


# ---------------------------------------------------------------------------
# Météo et environnement
# ---------------------------------------------------------------------------

class ForecastInput(BaseModel):
    region_code: str | None = Field(
        default=None, description=f"Code de région marocaine. Valeurs : {sorted(REGIONS)}"
    )
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    horizons_hours: list[int] = Field(
        default_factory=lambda: list(DEFAULT_HORIZONS_HOURS),
        description="Horizons de prévision en heures, par exemple [6, 12, 24, 48]",
    )


@tool(
    name="get_weather_nowcast",
    description_fr=(
        "Prévisions météorologiques agrégées par horizon de décision (6, 12, 24, 48 h) "
        "pour une région marocaine ou un point géographique. Renvoie le cumul de "
        "précipitations, les températures extrêmes, les rafales et un niveau de confiance."
    ),
    input_model=ForecastInput,
    decision_support_fr=(
        "Savoir ce qui est attendu et de combien de temps on dispose avant que "
        "les conditions se dégradent."
    ),
)
async def get_weather_nowcast(payload: ForecastInput, context: ToolContext) -> dict[str, Any]:
    latitude, longitude = _resolve_point(payload.region_code, payload.latitude, payload.longitude)
    horizons = tuple(sorted({h for h in payload.horizons_hours if 1 <= h <= 168}))
    result = await NowcastingService().nowcast(
        latitude, longitude, horizons=horizons or DEFAULT_HORIZONS_HOURS
    )

    return {
        "lieu": {"latitude": latitude, "longitude": longitude, "region": payload.region_code},
        "etat_des_donnees": result.input_state.label_fr,
        "source": result.input_source.label_fr,
        "modele": result.horizons[0].model_version if result.horizons else None,
        "horizons": [
            {
                "horizon_heures": h.horizon_hours,
                "cumul_precipitations_mm": h.cumulative_precipitation_mm,
                "temperature_max_c": h.max_temperature_c,
                "temperature_min_c": h.min_temperature_c,
                "rafale_max_kmh": h.max_wind_gust_kmh,
                "humidite_moyenne": h.mean_humidity,
                "confiance": h.confidence.label_fr,
            }
            for h in result.horizons
        ],
    }


class SatelliteInput(BaseModel):
    region_code: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


@tool(
    name="get_satellite_observation",
    description_fr=(
        "Indicateurs de végétation issus de l'imagerie satellite (NDVI, NDWI). "
        "Renvoie explicitement une indisponibilité si la source n'est pas configurée : "
        "aucune valeur n'est estimée à la place."
    ),
    input_model=SatelliteInput,
    decision_support_fr="Évaluer l'état de la végétation avant récolte.",
)
async def get_satellite_observation(
    payload: SatelliteInput, context: ToolContext
) -> dict[str, Any]:
    from app.domain.geo import Coordinates

    latitude, longitude = _resolve_point(payload.region_code, payload.latitude, payload.longitude)
    result = await get_satellite_provider().get_vegetation(Coordinates(latitude, longitude))

    if isinstance(result, SatelliteAvailability):
        return {
            "disponible": False,
            "motif_fr": result.reason_fr,
            "remediation_fr": result.remediation_fr,
        }
    return {
        "disponible": True,
        "date_acquisition": result.acquired_on.isoformat(),
        "ndvi": result.ndvi,
        "ndwi": result.ndwi,
        # Part de la parcelle écartée du calcul (nuage, ombre, neige). Une
        # valeur élevée ne rend pas la mesure fausse : elle indique qu'elle
        # porte sur une fraction réduite de la surface.
        "part_masquee_pct": result.masked_share_percent,
        "etat_des_donnees": result.state.label_fr,
        "source": result.source.label_fr,
    }


# ---------------------------------------------------------------------------
# Risque agricole
# ---------------------------------------------------------------------------

class CropRiskInput(BaseModel):
    crop_code: str = Field(description=f"Culture évaluée. Valeurs : {sorted(CROPS)}")
    region_code: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    horizon_hours: int = Field(default=24, ge=1, le=168)


@tool(
    name="get_crop_risk",
    description_fr=(
        "Risque agro-climatique pour une culture et une zone sur un horizon donné. "
        "Chaque facteur indique la valeur attendue, le seuil franchi et la provenance "
        "de ce seuil (calibré sur historique local ou provisoire à valider)."
    ),
    input_model=CropRiskInput,
    decision_support_fr="Anticiper une perte de qualité ou de rendement au champ.",
)
async def get_crop_risk(payload: CropRiskInput, context: ToolContext) -> dict[str, Any]:
    get_crop(payload.crop_code)  # échoue tôt si la culture est inconnue
    latitude, longitude = _resolve_point(payload.region_code, payload.latitude, payload.longitude)

    nowcast = await NowcastingService().nowcast(latitude, longitude)
    risk = AgriculturalImpactEngine().evaluate(
        nowcast=nowcast,
        crop_code=payload.crop_code,
        region_code=payload.region_code,
        horizon_hours=_closest_horizon(payload.horizon_hours, nowcast),
        # Un seuil calibré sur l'historique local prime sur la valeur
        # provisoire livrée par défaut, quand il en existe un.
        threshold_overrides=(
            load_overrides(
                context.repo, region_code=payload.region_code, crop_code=payload.crop_code
            )
            if payload.region_code
            else {}
        ),
    )
    return summarize_for_business(risk) | {
        "horizon_heures": risk.horizon_hours,
        "region": payload.region_code,
    }


class AtRiskRegionsInput(BaseModel):
    horizon_hours: int = Field(default=24, ge=1, le=168)
    minimum_level: RiskLevel = Field(
        default=RiskLevel.MODERATE, description="Niveau minimal retenu dans la réponse"
    )


@tool(
    name="get_at_risk_regions",
    description_fr=(
        "Régions marocaines exposées sur un horizon donné, évaluées pour leurs "
        "cultures principales. Classées du risque le plus élevé au plus faible."
    ),
    input_model=AtRiskRegionsInput,
    decision_support_fr="Repérer où porter l'attention en priorité.",
)
async def get_at_risk_regions(payload: AtRiskRegionsInput, context: ToolContext) -> dict[str, Any]:
    engine = AgriculturalImpactEngine()
    service = NowcastingService()
    exposed: list[dict[str, Any]] = []

    for region in REGIONS.values():
        nowcast = await service.nowcast(
            region.center.latitude, region.center.longitude
        )
        horizon = _closest_horizon(payload.horizon_hours, nowcast)

        worst: dict[str, Any] | None = None
        for crop_code in region.main_crops:
            if crop_code not in CROPS:
                continue
            risk = engine.evaluate(
                nowcast=nowcast,
                crop_code=crop_code,
                region_code=region.code,
                horizon_hours=horizon,
            )
            if worst is None or risk.score > worst["score"]:
                worst = {
                    "region": region.name_fr,
                    "region_code": region.code,
                    "culture_la_plus_exposee": risk.crop_name_fr,
                    "niveau": risk.level.label_fr,
                    "score": risk.score,
                    "rang": risk.level.rank,
                    "facteur_principal": (
                        risk.dominant_driver.label_fr if risk.dominant_driver else None
                    ),
                    "confiance": risk.confidence.label_fr,
                    "etat_des_donnees": risk.data_state.label_fr,
                }
        if worst and worst["rang"] >= payload.minimum_level.rank:
            exposed.append(worst)

    exposed.sort(key=lambda item: item["score"], reverse=True)
    return {
        "horizon_heures": payload.horizon_hours,
        "seuil_retenu": payload.minimum_level.label_fr,
        "regions_exposees": exposed,
        "nombre": len(exposed),
    }


# ---------------------------------------------------------------------------
# Expéditions, itinéraires et alternatives
# ---------------------------------------------------------------------------

class ShipmentInput(BaseModel):
    shipment_reference: str = Field(
        description="Référence d'expédition, par exemple « EXP-1842 », ou identifiant interne"
    )


@tool(
    name="get_shipment",
    description_fr=(
        "Détail d'une expédition : produit, origine, destination, volume, mode de "
        "transport, départ prévu et échéance de service."
    ),
    input_model=ShipmentInput,
    decision_support_fr="Situer l'expédition avant toute analyse de risque.",
)
async def get_shipment(payload: ShipmentInput, context: ToolContext) -> dict[str, Any]:
    service = ShipmentService(context.repo)
    shipment = service.resolve(payload.shipment_reference)
    ctx = service.build_context(shipment)

    return {
        "reference": shipment.reference,
        "produit": ctx.product_name_fr,
        "culture": ctx.crop_code,
        "origine": ctx.origin_name_fr,
        "destination": ctx.destination_name_fr,
        "volume_tonnes": ctx.volume_tonnes,
        "mode_transport": ctx.transport_mode.label_fr,
        "statut": shipment.status,
        "depart_prevu": ctx.departure_at.isoformat(),
        "echeance_service": ctx.sla_deadline_at.isoformat(),
        "heures_avant_depart": round(
            (ctx.departure_at - datetime.now(UTC)).total_seconds() / 3600, 1
        ),
        "profil_optimisation": ctx.optimization_profile,
    }


@tool(
    name="get_route_options",
    description_fr=(
        "Itinéraires possibles pour une expédition, avec pour chacun le risque de "
        "perturbation, la durée, le coût, l'exposition et le respect de l'échéance. "
        "L'exposition est calculée tronçon par tronçon sur la fenêtre horaire où le "
        "véhicule s'y trouve réellement."
    ),
    input_model=ShipmentInput,
    decision_support_fr="Comparer des itinéraires sur des critères homogènes.",
)
async def get_route_options(payload: ShipmentInput, context: ToolContext) -> dict[str, Any]:
    service = ShipmentService(context.repo)
    shipment = service.resolve(payload.shipment_reference)
    alternatives = await service.alternatives_for(shipment)

    routes = [a for a in alternatives if a.route_assessment is not None]
    return {
        "reference": shipment.reference,
        "itineraires": [_route_summary(a) for a in routes],
        "nombre": len(routes),
    }


@tool(
    name="get_route_risk",
    description_fr=(
        "Analyse détaillée de l'itinéraire actuel d'une expédition : tronçons exposés, "
        "heure de passage prévue sur chacun, conditions attendues et motifs."
    ),
    input_model=ShipmentInput,
    decision_support_fr="Comprendre précisément où et quand le trajet est menacé.",
)
async def get_route_risk(payload: ShipmentInput, context: ToolContext) -> dict[str, Any]:
    service = ShipmentService(context.repo)
    shipment = service.resolve(payload.shipment_reference)
    alternatives = await service.alternatives_for(shipment)

    current = next(
        (a for a in alternatives if a.type.value == "CURRENT_PLAN" and a.route_assessment), None
    )
    if current is None or current.route_assessment is None:
        raise NotFoundError("Aucun itinéraire courant n'a pu être évalué pour cette expédition.")

    assessment = current.route_assessment
    return {
        "reference": shipment.reference,
        "itineraire": assessment.label_fr,
        "niveau_risque": assessment.risk_level.label_fr,
        "score_risque": assessment.risk_score,
        "probabilite_perturbation": assessment.disruption_probability,
        "avertissement_probabilite_fr": (
            "Indicateur dérivé de règles explicites, utile pour comparer des "
            "itinéraires entre eux. Il n'est pas calibré sur un historique "
            "d'incidents et ne doit pas être lu comme une probabilité statistique."
        ),
        "part_du_trajet_exposee": assessment.exposure_fraction,
        "distance_km": assessment.distance_km,
        "duree_estimee_h": assessment.adjusted_duration_hours,
        "arrivee_estimee": assessment.estimated_arrival_at.isoformat(),
        "echeance_respectee": assessment.sla_compliant,
        "marge_echeance_h": assessment.sla_margin_hours,
        "etat_des_donnees": assessment.data_state.label_fr,
        "confiance": assessment.confidence.label_fr,
        "troncons_exposes": [
            {
                "de": s.from_name_fr,
                "vers": s.to_name_fr,
                "axe": s.road_ref,
                "distance_km": s.distance_km,
                "passage_prevu": f"{s.entry_at:%d/%m %H:%M} → {s.exit_at:%H:%M}",
                "heures_apres_depart": s.hours_from_departure,
                "niveau": s.level.label_fr,
                "intensite_pluie_mm_h": s.rain_intensity_mm_h,
                "cumul_24h_avant_mm": s.antecedent_rain_24h_mm,
                "rafale_kmh": s.max_wind_gust_kmh,
                "motifs": list(s.reasons_fr),
            }
            for s in assessment.segments
            if s.is_exposed
        ],
    }


@tool(
    name="generate_alternatives",
    description_fr=(
        "Génère et classe toutes les options pour une expédition : itinéraires, "
        "décalages de départ, fournisseurs, entrepôts. Renvoie les options faisables "
        "classées, l'option recommandée avec ses raisons et ses contreparties, et les "
        "options écartées avec leur motif de rejet."
    ),
    input_model=ShipmentInput,
    decision_support_fr=(
        "Passer du constat de risque à une décision argumentée : que faire, "
        "pourquoi, et ce que cela coûte."
    ),
)
async def generate_alternatives(payload: ShipmentInput, context: ToolContext) -> dict[str, Any]:
    service = ShipmentService(context.repo)
    shipment = service.resolve(payload.shipment_reference)
    ctx, ranking = await service.decide(shipment)

    return {
        "reference": shipment.reference,
        "produit": ctx.product_name_fr,
        "profil_optimisation": ranking.profile_label_fr,
        "justification_ponderation_fr": ranking.profile_rationale_fr,
        "ponderations": ranking.weights_fr,
        "option_recommandee": ranking.recommended_id,
        "confiance": ranking.confidence.label_fr,
        "reserves_fr": list(ranking.caveats_fr),
        "options": [
            {
                "rang": r.rank,
                "id": r.alternative.id,
                "type": r.alternative.type.label_fr,
                "libelle": r.alternative.label_fr,
                "description": r.alternative.description_fr,
                "niveau_risque": r.alternative.risk_level.label_fr,
                "probabilite_perturbation": r.alternative.disruption_probability,
                "cout_mad": r.alternative.estimated_cost_mad,
                "duree_h": r.alternative.duration_hours,
                "depart": r.alternative.departure_at.isoformat(),
                "arrivee_estimee": r.alternative.estimated_arrival_at.isoformat(),
                "echeance_respectee": r.alternative.sla_compliant,
                "marge_echeance_h": r.alternative.sla_margin_hours,
                "part_exposee": r.alternative.exposure_fraction,
                "score_global": r.total_score,
                "ecart_risque_points": r.risk_delta_points,
                "ecart_cout_mad": r.cost_delta_mad,
                "ecart_cout_pct": r.cost_delta_percent,
                "ecart_duree_h": r.duration_delta_hours,
                "recommandee": r.is_recommended,
                "raisons": list(r.recommendation_reasons_fr),
                "contreparties": list(r.tradeoffs_fr),
                "facteurs_de_risque": list(r.alternative.risk_factors_fr),
            }
            for r in ranking.ranked
        ],
        "options_ecartees": [
            {
                "libelle": a.label_fr,
                "type": a.type.label_fr,
                "motifs_rejet": list(a.infeasibility_reasons_fr),
            }
            for a in ranking.rejected
        ],
    }


# ---------------------------------------------------------------------------
# Stocks et fournisseurs
# ---------------------------------------------------------------------------

class InventoryInput(BaseModel):
    product: str | None = Field(
        default=None, description="Code, nom de produit ou culture. Vide = tous les produits."
    )


@tool(
    name="get_inventory_status",
    description_fr=(
        "Couverture de stock par site et par produit, en jours de consommation. "
        "Distingue la couverture totale de la couverture utilisable au-dessus du "
        "stock de sécurité."
    ),
    input_model=InventoryInput,
    decision_support_fr="Savoir combien de temps on tient sans réapprovisionnement.",
)
async def get_inventory_status(payload: InventoryInput, context: ToolContext) -> dict[str, Any]:
    service = ShipmentService(context.repo)
    repo = context.repo

    if payload.product:
        product = service.product_by_code_or_name(payload.product)
        rows = repo.inventories(Inventory.product_id == product.id)
    else:
        rows = repo.inventories()

    entries: list[dict[str, Any]] = []
    for row in rows:
        site = repo.get(Site, row.site_id)
        product = repo.get(Product, row.product_id)
        coverage = calculate_stock_coverage(
            site_name_fr=site.name,
            product_name_fr=product.name,
            quantity_tonnes=row.quantity_tonnes,
            safety_stock_tonnes=row.safety_stock_tonnes,
            daily_demand_tonnes=row.average_daily_demand_tonnes,
        )
        entries.append(
            {
                "site": site.name,
                "produit": product.name,
                "quantite_tonnes": coverage.quantity_tonnes,
                "stock_securite_tonnes": coverage.safety_stock_tonnes,
                "demande_quotidienne_tonnes": coverage.daily_demand_tonnes,
                "couverture_jours": coverage.coverage_days,
                "couverture_utilisable_jours": coverage.usable_coverage_days,
                "sous_stock_securite": coverage.below_safety_stock,
                "resume_fr": coverage.as_business_summary_fr(),
            }
        )

    entries.sort(key=lambda e: e["couverture_utilisable_jours"])
    return {"stocks": entries, "nombre": len(entries), "source": "Données internes AtlasAgri"}


class StockCoverageInput(BaseModel):
    product: str = Field(description="Code, nom de produit ou culture")
    disruption_duration_days: float = Field(
        default=1.0, ge=0, le=60, description="Durée de perturbation envisagée, en jours"
    )


@tool(
    name="calculate_stock_coverage",
    description_fr=(
        "Confronte la couverture de stock d'un produit au délai de réapprovisionnement "
        "et à une durée de perturbation, et indique s'il y a un risque de rupture "
        "et de combien de jours."
    ),
    input_model=StockCoverageInput,
    decision_support_fr="Décider s'il faut réapprovisionner ou changer de source.",
)
async def calculate_stock_coverage_tool(
    payload: StockCoverageInput, context: ToolContext
) -> dict[str, Any]:
    service = ShipmentService(context.repo)
    repo = context.repo
    product = service.product_by_code_or_name(payload.product)

    suppliers = repo.suppliers(Supplier.crop_code == product.crop_code)
    if not suppliers:
        raise NotFoundError(
            f"Aucun fournisseur référencé pour {product.name} : "
            "le délai de réapprovisionnement ne peut pas être établi."
        )
    lead_time = min(s.lead_time_days for s in suppliers)

    engine = SupplyChainRiskEngine()
    results: list[dict[str, Any]] = []

    for row in repo.inventories(Inventory.product_id == product.id):
        site = repo.get(Site, row.site_id)
        coverage = calculate_stock_coverage(
            site_name_fr=site.name,
            product_name_fr=product.name,
            quantity_tonnes=row.quantity_tonnes,
            safety_stock_tonnes=row.safety_stock_tonnes,
            daily_demand_tonnes=row.average_daily_demand_tonnes,
        )
        risk = engine.assess_stock_risk(
            coverage=coverage,
            supplier_lead_time_days=lead_time,
            disruption_duration_days=payload.disruption_duration_days,
            entity_id=row.id,
            horizon_hours=int(payload.disruption_duration_days * 24) or 24,
            confidence=Confidence.MEDIUM,
            geographic_area_fr=REGIONS[site.region_code].name_fr
            if site.region_code in REGIONS
            else site.region_code,
            cause_fr="Perturbation envisagée sur l'approvisionnement",
        )
        reorder = calculate_reorder_quantity(
            daily_demand_tonnes=row.average_daily_demand_tonnes,
            lead_time_days=lead_time,
            safety_stock_tonnes=row.safety_stock_tonnes,
            current_quantity_tonnes=row.quantity_tonnes,
        )
        results.append(
            {
                "site": site.name,
                "couverture_jours": coverage.coverage_days,
                "couverture_utilisable_jours": coverage.usable_coverage_days,
                "delai_fournisseur_jours": lead_time,
                "niveau_risque": risk.level.label_fr,
                "score": risk.score,
                "implications_fr": list(risk.operational_implications_fr),
                **reorder,
            }
        )

    results.sort(key=lambda r: r["score"], reverse=True)
    return {
        "produit": product.name,
        "duree_perturbation_jours": payload.disruption_duration_days,
        "sites": results,
    }


class SupplierExposureInput(BaseModel):
    crop_code: str | None = Field(default=None, description="Filtrer par culture")
    horizon_hours: int = Field(default=48, ge=1, le=168)


@tool(
    name="get_supplier_exposure",
    description_fr=(
        "Exposition des fournisseurs : risque environnemental de leur zone, fiabilité "
        "de livraison observée et délai de réapprovisionnement, combinés en un niveau "
        "d'exposition."
    ),
    input_model=SupplierExposureInput,
    decision_support_fr="Identifier quel fournisseur risque de ne pas livrer.",
)
async def get_supplier_exposure(
    payload: SupplierExposureInput, context: ToolContext
) -> dict[str, Any]:
    repo = context.repo
    conditions = [Supplier.crop_code == payload.crop_code] if payload.crop_code else []
    suppliers = repo.suppliers(*conditions)

    impact = AgriculturalImpactEngine()
    nowcasting = NowcastingService()
    engine = SupplyChainRiskEngine()
    entries: list[dict[str, Any]] = []

    for supplier in suppliers:
        site = repo.get(Site, supplier.site_id)
        nowcast = await nowcasting.nowcast(site.latitude, site.longitude)
        horizon = _closest_horizon(payload.horizon_hours, nowcast)
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
        entries.append(
            {
                "fournisseur": supplier.name,
                "site": site.name,
                "culture": supplier.crop_code,
                "zone": risk.geographic_area_fr,
                "niveau": risk.level.label_fr,
                "score": risk.score,
                "delai_jours": supplier.lead_time_days,
                "fiabilite": supplier.reliability,
                "capacite_quotidienne_tonnes": supplier.daily_capacity_tonnes,
                "fournisseur_de_secours": supplier.is_backup,
                "implications_fr": list(risk.operational_implications_fr),
            }
        )

    entries.sort(key=lambda e: e["score"], reverse=True)
    return {"fournisseurs": entries, "nombre": len(entries)}


# ---------------------------------------------------------------------------
# Décision : création d'une proposition soumise à un humain
# ---------------------------------------------------------------------------

class RecommendationInput(BaseModel):
    shipment_reference: str
    recommended_option_id: str = Field(
        description="Identifiant de l'option retenue, tel que renvoyé par generate_alternatives"
    )
    title: str = Field(max_length=200)
    summary_fr: str = Field(max_length=2000)
    reasons_fr: list[str] = Field(default_factory=list, max_length=8)
    tradeoffs_fr: list[str] = Field(default_factory=list, max_length=8)


@tool(
    name="create_recommendation",
    description_fr=(
        "Enregistre une recommandation soumise à validation humaine. "
        "N'exécute aucune action opérationnelle : la décision reste à un utilisateur "
        "habilité, qui peut l'accepter, la modifier ou l'ignorer. "
        "Les chiffres (risque, coût, délai) sont repris des moteurs de calcul, jamais "
        "saisis librement."
    ),
    input_model=RecommendationInput,
    allowed_roles=DECIDERS,
    read_only=False,
    decision_support_fr="Formaliser une proposition traçable et auditable.",
)
async def create_recommendation(
    payload: RecommendationInput, context: ToolContext
) -> dict[str, Any]:
    service = ShipmentService(context.repo)
    shipment = service.resolve(payload.shipment_reference)
    ctx, ranking = await service.decide(shipment)

    chosen = next(
        (r for r in ranking.ranked if r.alternative.id == payload.recommended_option_id), None
    )
    if chosen is None:
        # Refus délibéré : accepter un identifiant arbitraire permettrait
        # d'enregistrer une recommandation portant sur une option qui n'a jamais
        # été évaluée, donc invérifiable.
        raise NotFoundError(
            f"L'option « {payload.recommended_option_id} » ne fait pas partie des "
            f"options évaluées pour {shipment.reference}. "
            f"Options disponibles : {', '.join(r.alternative.id for r in ranking.ranked)}."
        )

    record = Recommendation(
        tenant_id=context.request.tenant_id,
        subject_type="SHIPMENT",
        subject_id=shipment.id,
        title=sanitize_user_text(payload.title, max_length=200),
        recommended_option_id=chosen.alternative.id,
        status="PROPOSED",
        summary_fr=sanitize_user_text(payload.summary_fr, max_length=2000),
        reasons_fr=[sanitize_user_text(r, max_length=400) for r in payload.reasons_fr]
        or list(chosen.recommendation_reasons_fr),
        tradeoffs_fr=[sanitize_user_text(t, max_length=400) for t in payload.tradeoffs_fr]
        or list(chosen.tradeoffs_fr),
        # Les valeurs d'impact viennent du moteur, pas du modèle : c'est ce qui
        # rend la recommandation vérifiable après coup.
        expected_impact={
            "probabilite_perturbation": chosen.alternative.disruption_probability,
            "ecart_risque_points": chosen.risk_delta_points,
            "cout_mad": chosen.alternative.estimated_cost_mad,
            "ecart_cout_mad": chosen.cost_delta_mad,
            "duree_h": chosen.alternative.duration_hours,
            "ecart_duree_h": chosen.duration_delta_hours,
            "echeance_respectee": chosen.alternative.sla_compliant,
        },
        confidence=ranking.confidence.value,
        inputs={
            "reference": shipment.reference,
            "produit": ctx.product_name_fr,
            "volume_tonnes": ctx.volume_tonnes,
            "depart_prevu": ctx.departure_at.isoformat(),
            "echeance": ctx.sla_deadline_at.isoformat(),
            "profil_optimisation": ranking.profile_code,
            "ponderations": ranking.weights_fr,
        },
        alternatives_considered=[
            {
                "id": r.alternative.id,
                "libelle": r.alternative.label_fr,
                "rang": r.rank,
                "score": r.total_score,
                "risque": r.alternative.risk_level.value,
                "cout_mad": r.alternative.estimated_cost_mad,
            }
            for r in ranking.ranked
        ]
        + [
            {"libelle": a.label_fr, "ecartee": True, "motifs": list(a.infeasibility_reasons_fr)}
            for a in ranking.rejected
        ],
        data_sources=sorted(
            {s.label_fr for s in chosen.alternative.data_sources}
        ),
    )
    context.repo.add(record)
    context.session.commit()

    return {
        "recommandation_id": record.id,
        "statut": "Proposée",
        "option_retenue": chosen.alternative.label_fr,
        "en_attente_de": "validation par un responsable habilité",
        "message_fr": (
            "La recommandation a été enregistrée et attend une validation humaine. "
            "Aucune action opérationnelle n'a été déclenchée."
        ),
    }


class AlertInput(BaseModel):
    level: RiskLevel
    what_fr: str = Field(max_length=1000)
    where_fr: str = Field(max_length=200)
    when_fr: str = Field(max_length=200)
    impact_fr: str = Field(max_length=1000)
    action_fr: str = Field(max_length=1000)
    shipment_reference: str | None = None


@tool(
    name="create_alert",
    description_fr=(
        "Crée une alerte structurée en QUOI / OÙ / QUAND / IMPACT / ACTION. "
        "Visible dans l'interface ; ne déclenche aucune action automatique."
    ),
    input_model=AlertInput,
    allowed_roles=DECIDERS,
    read_only=False,
    decision_support_fr="Porter un risque à l'attention des équipes.",
)
async def create_alert(payload: AlertInput, context: ToolContext) -> dict[str, Any]:
    subject_id = None
    if payload.shipment_reference:
        subject_id = ShipmentService(context.repo).resolve(payload.shipment_reference).id

    alert = Alert(
        tenant_id=context.request.tenant_id,
        level=payload.level.value,
        risk_type="ROAD_DISRUPTION",
        what_fr=sanitize_user_text(payload.what_fr, max_length=1000),
        where_fr=sanitize_user_text(payload.where_fr, max_length=200),
        when_fr=sanitize_user_text(payload.when_fr, max_length=200),
        impact_fr=sanitize_user_text(payload.impact_fr, max_length=1000),
        action_fr=sanitize_user_text(payload.action_fr, max_length=1000),
        subject_type="SHIPMENT" if subject_id else None,
        subject_id=subject_id,
    )
    context.repo.add(alert)
    context.session.commit()
    return {"alerte_id": alert.id, "niveau": payload.level.label_fr, "statut": "Créée"}


class CalibrationStatusInput(BaseModel):
    region_code: str | None = None


@tool(
    name="get_threshold_calibration_status",
    description_fr=(
        "État de calibration des seuils de risque : lesquels reposent sur "
        "l'historique local et lesquels restent des valeurs provisoires. "
        "Indique la fenêtre de calibration et la taille d'échantillon."
    ),
    input_model=CalibrationStatusInput,
    decision_support_fr=(
        "Savoir quelle confiance accorder à une évaluation de risque selon "
        "l'origine du seuil qui la fonde."
    ),
)
async def get_threshold_calibration_status(
    payload: CalibrationStatusInput, context: ToolContext
) -> dict[str, Any]:
    from app.db.base import CalibratedThreshold

    conditions = (
        [CalibratedThreshold.region_code == payload.region_code]
        if payload.region_code
        else []
    )
    rows = context.repo.calibrated_thresholds(*conditions)

    return {
        "seuils_calibres": [
            {
                "region": REGIONS[r.region_code].name_fr
                if r.region_code in REGIONS
                else r.region_code,
                "culture": r.crop_code,
                "variable": r.variable,
                "modere": round(r.moderate_at, 1),
                "eleve": round(r.high_at, 1),
                "critique": round(r.critical_at, 1) if r.critical_at is not None else None,
                "unite": r.unit,
                "fenetre": r.calibration_window,
                "echantillon": r.sample_size,
                "quantiles": [r.low_quantile, r.high_quantile],
                "calibre_le": r.calibrated_on.date().isoformat(),
            }
            for r in rows
        ],
        "nombre": len(rows),
        "note_fr": (
            "Les cultures et régions absentes de cette liste reposent encore sur "
            "des seuils provisoires, signalés comme tels dans l'interface."
            if rows
            else "Aucun seuil calibré : toutes les évaluations reposent sur des "
            "valeurs provisoires à valider par un agronome."
        ),
    }


# ---------------------------------------------------------------------------
# Collecte des résultats observés
# ---------------------------------------------------------------------------

class PendingOutcomesInput(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


@tool(
    name="get_pending_outcomes",
    description_fr=(
        "Expéditions arrivées à échéance dont le résultat réel n'a pas encore été "
        "recueilli, avec la probabilité de perturbation qui avait été annoncée. "
        "Classées de la plus ancienne à la plus récente."
    ),
    input_model=PendingOutcomesInput,
    decision_support_fr=(
        "Savoir auprès de qui aller chercher le retour terrain qui permettra "
        "de vérifier, puis de corriger, les prédictions du système."
    ),
)
async def get_pending_outcomes(
    payload: PendingOutcomesInput, context: ToolContext
) -> dict[str, Any]:
    from app.services.outcome_collection import OutcomeCollectionService

    attente = OutcomeCollectionService(context.repo).pending(limit=payload.limit)
    return {
        "en_attente": [
            {
                "reference": p.reference,
                "produit": p.product_fr,
                "trajet": f"{p.origin_fr} → {p.destination_fr}",
                "echeance": p.sla_deadline_at.isoformat(),
                "heures_depuis_echeance": p.hours_since_deadline,
                "probabilite_annoncee": p.predicted_probability,
                "niveau_annonce": p.predicted_level_fr,
            }
            for p in attente
        ],
        "nombre": len(attente),
    }


class OutcomeSurveyInput(BaseModel):
    shipment_reference: str
    answers: dict[str, Any] = Field(
        default_factory=dict,
        description="Réponses déjà recueillies, indexées par code de question",
    )


@tool(
    name="get_outcome_question",
    description_fr=(
        "Question suivante du questionnaire de retour terrain, compte tenu des "
        "réponses déjà données. L'enchaînement est déterministe : la même "
        "situation produit toujours la même séquence."
    ),
    input_model=OutcomeSurveyInput,
    decision_support_fr="Conduire l'entretien de retour terrain question par question.",
)
async def get_outcome_question(
    payload: OutcomeSurveyInput, context: ToolContext
) -> dict[str, Any]:
    from app.services.outcome_collection import OutcomeCollectionService

    return OutcomeCollectionService(context.repo).step(
        payload.shipment_reference, answers=payload.answers
    )


class SubmitOutcomeInput(BaseModel):
    shipment_reference: str
    answers: dict[str, Any] = Field(
        description="Réponses complètes au questionnaire, indexées par code de question"
    )
    correcting: bool = Field(
        default=False,
        description=(
            "Vrai pour corriger un résultat déjà enregistré. La version "
            "précédente est conservée : les réponses initiales ne sont jamais effacées."
        ),
    )


@tool(
    name="submit_shipment_outcome",
    description_fr=(
        "Enregistre ce qui s'est réellement passé sur une expédition. "
        "L'enregistrement est immuable et sa partition (calibration ou évaluation) "
        "est figée à l'écriture. Refuse un questionnaire incomplet."
    ),
    input_model=SubmitOutcomeInput,
    allowed_roles=(*DECIDERS, UserRole.ANALYST),
    read_only=False,
    decision_support_fr=(
        "Alimenter la boucle de retour qui permet de calibrer les seuils, "
        "de mesurer la fiabilité des prédictions et de chiffrer les pertes évitées."
    ),
)
async def submit_shipment_outcome(
    payload: SubmitOutcomeInput, context: ToolContext
) -> dict[str, Any]:
    from app.services.outcome_collection import OutcomeCollectionService

    resultat = OutcomeCollectionService(context.repo).submit(
        payload.shipment_reference,
        answers=payload.answers,
        user_id=context.request.user_id,
        correcting=payload.correcting,
    )
    return {
        "resultat_id": resultat.id,
        "reference": payload.shipment_reference,
        "partition": resultat.evaluation_split,
        "apparie_a_une_prediction": resultat.prediction_id is not None,
        "message_fr": (
            "Retour terrain enregistré. Il alimentera la calibration des seuils "
            "et la mesure de fiabilité des prédictions."
            if resultat.evaluation_split == "calibration"
            else "Retour terrain enregistré. Il est réservé à l'évaluation : il "
            "servira à juger le système, jamais à l'ajuster."
        ),
    }


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

class ScenarioInput(BaseModel):
    scenario: str = Field(
        description=(
            "Scénario simulé. Valeurs : 'retard_fournisseur' (délai allongé), "
            "'route_indisponible' (un axe devient impraticable), "
            "'depart_retarde' (départ repoussé)."
        )
    )
    shipment_reference: str
    parameter_value: float = Field(
        default=48.0,
        description=(
            "Ampleur du scénario : heures de retard, ou heures de report du départ. "
            "Ignoré pour 'route_indisponible'."
        ),
    )
    blocked_node_code: str | None = Field(
        default=None, description="Nœud routier rendu indisponible, pour 'route_indisponible'"
    )


@tool(
    name="simulate_scenario",
    description_fr=(
        "Simule un scénario et compare la situation actuelle à la situation simulée : "
        "niveau de risque, options restantes et meilleure réponse dans chaque cas. "
        "Aucune donnée n'est modifiée."
    ),
    input_model=ScenarioInput,
    decision_support_fr="Éprouver un plan avant de s'y engager.",
)
async def simulate_scenario(payload: ScenarioInput, context: ToolContext) -> dict[str, Any]:
    from app.services.simulation import SimulationService

    return await SimulationService(context.repo).run(
        scenario=payload.scenario,
        shipment_reference=payload.shipment_reference,
        parameter_value=payload.parameter_value,
        blocked_node_code=payload.blocked_node_code,
    )


# ---------------------------------------------------------------------------
# Utilitaires internes
# ---------------------------------------------------------------------------

def _resolve_point(
    region_code: str | None, latitude: float | None, longitude: float | None
) -> tuple[float, float]:
    if latitude is not None and longitude is not None:
        return latitude, longitude
    if region_code:
        if region_code not in REGIONS:
            raise NotFoundError(
                f"Région inconnue : « {region_code} ». "
                f"Régions disponibles : {', '.join(sorted(REGIONS))}."
            )
        centre = REGIONS[region_code].center
        return centre.latitude, centre.longitude
    raise NotFoundError(
        "Préciser soit une région, soit un couple latitude/longitude."
    )


def _closest_horizon(requested: int, nowcast) -> int:
    """Horizon disponible le plus proche de celui demandé.

    Le modèle demande parfois 36 h alors que les horizons produits sont
    6/12/24/48. Renvoyer le plus proche vaut mieux qu'échouer sur un détail.
    """
    available = [h.horizon_hours for h in nowcast.horizons]
    if not available:
        raise NotFoundError("Aucun horizon de prévision disponible.")
    return min(available, key=lambda h: abs(h - requested))


def _route_summary(alternative) -> dict[str, Any]:
    assessment = alternative.route_assessment
    return {
        "id": alternative.id,
        "libelle": alternative.label_fr,
        "type": alternative.type.label_fr,
        "niveau_risque": alternative.risk_level.label_fr,
        "probabilite_perturbation": alternative.disruption_probability,
        "distance_km": alternative.distance_km,
        "duree_h": alternative.duration_hours,
        "cout_mad": alternative.estimated_cost_mad,
        "echeance_respectee": alternative.sla_compliant,
        "marge_echeance_h": alternative.sla_margin_hours,
        "part_exposee": alternative.exposure_fraction,
        "villes_traversees": [NODES[c].name_fr for c in assessment.node_codes if c in NODES],
        "facteurs_de_risque": list(alternative.risk_factors_fr),
        "faisable": alternative.is_feasible,
        "motifs_rejet": list(alternative.infeasibility_reasons_fr),
    }
