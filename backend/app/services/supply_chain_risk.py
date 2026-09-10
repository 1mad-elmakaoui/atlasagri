"""Moteur de risque supply chain.

Traduit une exposition environnementale en **conséquence opérationnelle**.

Le principe, tiré du domaine et non de l'article : un risque météorologique
n'est un risque d'entreprise que rapporté à la couverture de stock, au délai de
réapprovisionnement et à l'engagement de service. Une perturbation de 24 h sur
un produit couvert par douze jours de stock n'est pas un incident ; la même
perturbation sur un produit couvert par deux jours en est un.

Toutes les grandeurs sont calculées ici, en Python. Aucune n'est demandée au
modèle de langage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ValidationError
from app.domain.enums import Confidence, RiskLevel, RiskType
from app.domain.provenance import SOURCE_INTERNAL_DB, SOURCE_RISK_ENGINE, DataSourceRef


class StockCoverage(BaseModel):
    """Couverture de stock d'un produit sur un site."""

    model_config = ConfigDict(frozen=True)

    site_name_fr: str
    product_name_fr: str
    quantity_tonnes: float
    safety_stock_tonnes: float
    daily_demand_tonnes: float

    coverage_days: float = Field(description="Jours de consommation couverts par le stock total")
    usable_coverage_days: float = Field(
        description="Jours couverts au-dessus du stock de sécurité — la vraie marge de manœuvre"
    )
    below_safety_stock: bool

    def as_business_summary_fr(self) -> str:
        if self.below_safety_stock:
            return (
                f"{self.product_name_fr} — {self.site_name_fr} : stock sous le seuil de "
                f"sécurité, {self.coverage_days:.1f} jours de couverture."
            )
        return (
            f"{self.product_name_fr} — {self.site_name_fr} : "
            f"{self.usable_coverage_days:.1f} jours de marge au-dessus du stock de sécurité."
        )


class SupplyChainRisk(BaseModel):
    """Risque supply chain complet, avec son entité, sa cause et son horizon."""

    model_config = ConfigDict(frozen=True)

    entity_type: str
    entity_id: str
    entity_label_fr: str
    risk_type: RiskType
    score: float = Field(ge=0, le=1)
    level: RiskLevel
    horizon_hours: int
    confidence: Confidence

    cause_fr: str
    geographic_area_fr: str
    affected_products_fr: tuple[str, ...]
    drivers: tuple[dict[str, Any], ...]
    operational_implications_fr: tuple[str, ...]

    sources: tuple[DataSourceRef, ...]
    computed_at: datetime


def calculate_stock_coverage(
    *,
    site_name_fr: str,
    product_name_fr: str,
    quantity_tonnes: float,
    safety_stock_tonnes: float,
    daily_demand_tonnes: float,
) -> StockCoverage:
    """Couverture de stock en jours.

    La distinction entre couverture totale et couverture *utilisable* est
    volontaire : le stock de sécurité n'est pas disponible pour absorber une
    perturbation, il est là pour absorber la variabilité normale de la demande.
    Le confondre avec de la marge conduirait à sous-estimer le risque.
    """
    if daily_demand_tonnes <= 0:
        raise ValidationError(
            f"Demande quotidienne invalide pour {product_name_fr} : "
            "la couverture de stock ne peut pas être calculée sans demande."
        )

    coverage = quantity_tonnes / daily_demand_tonnes
    usable = max(0.0, (quantity_tonnes - safety_stock_tonnes) / daily_demand_tonnes)

    return StockCoverage(
        site_name_fr=site_name_fr,
        product_name_fr=product_name_fr,
        quantity_tonnes=quantity_tonnes,
        safety_stock_tonnes=safety_stock_tonnes,
        daily_demand_tonnes=daily_demand_tonnes,
        coverage_days=round(coverage, 2),
        usable_coverage_days=round(usable, 2),
        below_safety_stock=quantity_tonnes < safety_stock_tonnes,
    )


def calculate_reorder_quantity(
    *,
    daily_demand_tonnes: float,
    lead_time_days: float,
    safety_stock_tonnes: float,
    current_quantity_tonnes: float,
) -> dict[str, float]:
    """Point de commande et quantité à réapprovisionner.

    Point de commande = consommation pendant le délai + stock de sécurité.
    C'est la formule standard ; elle est ici parce qu'elle doit être calculée
    par le backend et jamais estimée par le modèle de langage.
    """
    if daily_demand_tonnes < 0 or lead_time_days < 0:
        raise ValidationError("Demande et délai doivent être positifs.")

    reorder_point = daily_demand_tonnes * lead_time_days + safety_stock_tonnes
    shortfall = max(0.0, reorder_point - current_quantity_tonnes)

    return {
        "point_de_commande_tonnes": round(reorder_point, 2),
        "quantite_a_commander_tonnes": round(shortfall, 2),
        "commande_necessaire": shortfall > 0,
    }


class SupplyChainRiskEngine:
    """Combine exposition environnementale, stocks et délais fournisseurs."""

    def assess_stock_risk(
        self,
        *,
        coverage: StockCoverage,
        supplier_lead_time_days: float,
        disruption_duration_days: float,
        entity_id: str,
        horizon_hours: int,
        confidence: Confidence,
        geographic_area_fr: str,
        cause_fr: str,
    ) -> SupplyChainRisk:
        """Évalue le risque de rupture d'un stock face à une perturbation.

        Le raisonnement est celui d'un responsable supply chain : le stock
        doit couvrir le délai de réapprovisionnement **plus** la durée de la
        perturbation. S'il ne le fait pas, l'écart est le nombre de jours de
        rupture attendus.
        """
        required_days = supplier_lead_time_days + disruption_duration_days
        gap_days = required_days - coverage.usable_coverage_days

        if gap_days <= 0:
            score = max(0.0, 0.2 * (1 - coverage.usable_coverage_days / max(required_days, 0.1)))
        else:
            # Un écart d'une journée sur un besoin de cinq est moins grave qu'un
            # écart de quatre jours : on rapporte l'écart au besoin total.
            score = min(1.0, 0.35 + 0.65 * min(1.0, gap_days / max(required_days, 0.1)))

        if coverage.below_safety_stock:
            score = min(1.0, score + 0.2)

        implications: list[str] = []
        if gap_days > 0:
            implications.append(
                f"Le stock disponible couvre {coverage.usable_coverage_days:.1f} jours alors que "
                f"le réapprovisionnement en demande {required_days:.1f} "
                f"(délai fournisseur {supplier_lead_time_days:.1f} j + perturbation "
                f"{disruption_duration_days:.1f} j). Écart : {gap_days:.1f} jour(s)."
            )
            implications.append(
                "Un réapprovisionnement anticipé ou une source alternative est nécessaire "
                "pour éviter une rupture."
            )
        else:
            implications.append(
                f"Le stock couvre le délai de réapprovisionnement avec "
                f"{-gap_days:.1f} jour(s) de marge."
            )
        if coverage.below_safety_stock:
            implications.append(
                "Le stock est déjà sous le seuil de sécurité : la marge d'absorption "
                "d'un aléa de demande est épuisée."
            )

        score = round(score, 3)
        return SupplyChainRisk(
            entity_type="INVENTORY",
            entity_id=entity_id,
            entity_label_fr=f"{coverage.product_name_fr} — {coverage.site_name_fr}",
            risk_type=RiskType.STOCKOUT,
            score=score,
            level=RiskLevel.from_score(score),
            horizon_hours=horizon_hours,
            confidence=confidence,
            cause_fr=cause_fr,
            geographic_area_fr=geographic_area_fr,
            affected_products_fr=(coverage.product_name_fr,),
            drivers=(
                {
                    "libelle_fr": "Couverture utilisable",
                    "valeur": coverage.usable_coverage_days,
                    "unite": "jours",
                },
                {
                    "libelle_fr": "Délai fournisseur",
                    "valeur": supplier_lead_time_days,
                    "unite": "jours",
                },
                {
                    "libelle_fr": "Durée de perturbation estimée",
                    "valeur": disruption_duration_days,
                    "unite": "jours",
                },
                {
                    "libelle_fr": "Écart de couverture",
                    "valeur": round(gap_days, 2),
                    "unite": "jours",
                },
            ),
            operational_implications_fr=tuple(implications),
            sources=(SOURCE_INTERNAL_DB, SOURCE_RISK_ENGINE),
            computed_at=datetime.now(UTC),
        )

    def assess_supplier_exposure(
        self,
        *,
        supplier_id: str,
        supplier_name_fr: str,
        region_label_fr: str,
        lead_time_days: float,
        reliability: float,
        environmental_risk_score: float,
        horizon_hours: int,
        confidence: Confidence,
        cause_fr: str,
    ) -> SupplyChainRisk:
        """Exposition d'un fournisseur.

        Un fournisseur peu fiable dans une zone exposée cumule deux fragilités ;
        un fournisseur très fiable dans la même zone reste préférable. Le délai
        pèse également : plus il est long, moins on peut corriger tardivement.
        """
        unreliability = 1.0 - max(0.0, min(1.0, reliability))
        lead_time_penalty = min(0.25, lead_time_days / 40.0)

        score = round(
            min(1.0, environmental_risk_score * (0.6 + unreliability) + lead_time_penalty), 3
        )

        implications = []
        if environmental_risk_score >= 0.5:
            implications.append(
                f"{supplier_name_fr} est situé dans une zone exposée sur cet horizon : "
                "sa capacité de livraison peut être affectée."
            )
        if unreliability > 0.1:
            implications.append(
                f"Fiabilité de livraison observée : {reliability:.0%}. "
                "Prévoir une marge sur les engagements pris en aval."
            )
        if lead_time_days >= 4:
            implications.append(
                f"Délai de {lead_time_days:.0f} jours : une bascule vers ce fournisseur "
                "doit être décidée suffisamment tôt pour être utile."
            )
        if not implications:
            implications.append("Aucune fragilité particulière détectée sur cet horizon.")

        return SupplyChainRisk(
            entity_type="SUPPLIER",
            entity_id=supplier_id,
            entity_label_fr=supplier_name_fr,
            risk_type=RiskType.SUPPLIER_DISRUPTION,
            score=score,
            level=RiskLevel.from_score(score),
            horizon_hours=horizon_hours,
            confidence=confidence,
            cause_fr=cause_fr,
            geographic_area_fr=region_label_fr,
            affected_products_fr=(),
            drivers=(
                {
                    "libelle_fr": "Exposition environnementale de la zone",
                    "valeur": round(environmental_risk_score, 3),
                    "unite": "score",
                },
                {"libelle_fr": "Fiabilité de livraison", "valeur": reliability, "unite": "ratio"},
                {
                    "libelle_fr": "Délai de réapprovisionnement",
                    "valeur": lead_time_days,
                    "unite": "jours",
                },
            ),
            operational_implications_fr=tuple(implications),
            sources=(SOURCE_INTERNAL_DB, SOURCE_RISK_ENGINE),
            computed_at=datetime.now(UTC),
        )
