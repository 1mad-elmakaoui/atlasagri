"""Classement multicritère des alternatives.

Ce module ne génère rien : il reçoit des options déjà produites et validées par
`alternatives.py`, et les ordonne.

Trois règles gouvernent le classement :

1. **Les options infaisables ne sont pas classées.** Elles sont retournées à
   part, avec leur motif de rejet, pour que l'utilisateur voie ce qui a été
   examiné.
2. **La normalisation est relative au lot d'options comparées.** Un coût de
   45 000 MAD n'est ni bon ni mauvais dans l'absolu ; il l'est par rapport aux
   autres options disponibles pour cette expédition.
3. **La pondération dépend du produit.** Elle est affichée à l'utilisateur :
   savoir que le système a privilégié le risque à 40 % et le coût à 10 % permet
   de contester l'arbitrage, ce qui est sain.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Confidence, RiskLevel
from app.domain.optimization import OptimizationProfile, get_profile
from app.services.alternatives import Alternative


class CriterionScore(BaseModel):
    """Score normalisé d'un critère, avec sa valeur brute.

    Garder la valeur brute à côté du score normalisé permet d'expliquer
    « 45 500 MAD, soit 8 % de plus que le plan actuel » plutôt que
    « score de coût 0,62 », qui ne veut rien dire pour un exploitant.
    """

    model_config = ConfigDict(frozen=True)

    label_fr: str
    raw_value: float
    unit_fr: str
    normalized: float = Field(ge=0, le=1, description="0 = meilleur, 1 = pire du lot")
    weight: float


class RankedAlternative(BaseModel):
    """Alternative avec son score global et sa comparaison au plan actuel."""

    model_config = ConfigDict(frozen=True)

    alternative: Alternative
    rank: int
    total_score: float = Field(description="Score composite, plus bas = meilleur")
    criteria: tuple[CriterionScore, ...]

    risk_delta_points: float | None = Field(
        default=None, description="Écart de risque en points de pourcentage vs plan actuel"
    )
    cost_delta_mad: float | None = None
    cost_delta_percent: float | None = None
    duration_delta_hours: float | None = None

    is_recommended: bool = False
    recommendation_reasons_fr: tuple[str, ...] = ()
    tradeoffs_fr: tuple[str, ...] = ()


class RankingResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_code: str
    profile_label_fr: str
    profile_rationale_fr: str
    weights_fr: dict[str, float]

    ranked: tuple[RankedAlternative, ...]
    rejected: tuple[Alternative, ...]
    recommended_id: str | None
    confidence: Confidence
    caveats_fr: tuple[str, ...] = ()

    @property
    def recommended(self) -> RankedAlternative | None:
        return next((r for r in self.ranked if r.is_recommended), None)


@dataclass(frozen=True)
class _Criterion:
    key: str
    label_fr: str
    unit_fr: str
    weight_attribute: str
    extractor: str
    higher_is_worse: bool = True


CRITERIA: tuple[_Criterion, ...] = (
    _Criterion("risk", "Risque de perturbation", "score", "risk", "risk_score"),
    _Criterion("cost", "Coût estimé", "MAD", "cost", "estimated_cost_mad"),
    _Criterion("duration", "Durée de trajet", "h", "duration", "duration_hours"),
    _Criterion(
        "exposure", "Exposition aux zones à risque", "part du trajet",
        "exposure", "exposure_fraction",
    ),
    _Criterion("reliability", "Fiabilité de l'axe", "ratio", "reliability", "reliability", False),
)


class OptimizationService:
    """Ordonne des alternatives faisables selon un profil de produit."""

    def rank(
        self, alternatives: list[Alternative], *, profile_code: str | None = None
    ) -> RankingResult:
        profile = get_profile(profile_code)

        feasible = [a for a in alternatives if a.is_feasible]
        rejected = tuple(a for a in alternatives if not a.is_feasible)

        if not feasible:
            return RankingResult(
                profile_code=profile.code,
                profile_label_fr=profile.label_fr,
                profile_rationale_fr=profile.rationale_fr,
                weights_fr=profile.weights.as_dict_fr(),
                ranked=(),
                rejected=rejected,
                recommended_id=None,
                confidence=Confidence.LOW,
                caveats_fr=(
                    "Aucune option ne respecte l'ensemble des contraintes. "
                    "Une décision humaine est nécessaire : arbitrer sur l'engagement "
                    "de service, la capacité ou le volume expédié.",
                ),
            )

        scored = [
            (alternative, self._criteria_for(alternative, feasible, profile))
            for alternative in feasible
        ]
        scored.sort(key=lambda item: sum(c.normalized * c.weight for c in item[1]))

        current = next(
            (a for a in feasible if a.type.value == "CURRENT_PLAN"), None
        )

        ranked: list[RankedAlternative] = []
        for position, (alternative, criteria) in enumerate(scored, start=1):
            total = round(sum(c.normalized * c.weight for c in criteria), 4)
            ranked.append(
                RankedAlternative(
                    alternative=alternative,
                    rank=position,
                    total_score=total,
                    criteria=criteria,
                    **_deltas(alternative, current),
                )
            )

        best = ranked[0]
        recommended = _apply_recommendation(best, current)
        ranked[0] = recommended

        return RankingResult(
            profile_code=profile.code,
            profile_label_fr=profile.label_fr,
            profile_rationale_fr=profile.rationale_fr,
            weights_fr=profile.weights.as_dict_fr(),
            ranked=tuple(ranked),
            rejected=rejected,
            recommended_id=recommended.alternative.id,
            confidence=_overall_confidence(feasible),
            caveats_fr=_caveats(feasible, ranked),
        )

    def _criteria_for(
        self,
        alternative: Alternative,
        population: list[Alternative],
        profile: OptimizationProfile,
    ) -> tuple[CriterionScore, ...]:
        scores: list[CriterionScore] = []

        for criterion in CRITERIA:
            weight = getattr(profile.weights, criterion.weight_attribute)
            if weight == 0:
                continue

            value = float(getattr(alternative, criterion.extractor))
            values = [float(getattr(a, criterion.extractor)) for a in population]
            scores.append(
                CriterionScore(
                    label_fr=criterion.label_fr,
                    raw_value=round(value, 4),
                    unit_fr=criterion.unit_fr,
                    normalized=_normalize(value, values, criterion.higher_is_worse),
                    weight=weight,
                )
            )
        return tuple(scores)


def duree_fr(heures: float) -> str:
    """Formate une durée en français parlé.

    « 0,3 h » ou « 28,9 h » ne correspond à aucun usage : un exploitant dit
    « 18 minutes » et « 28 h 54 ». Ces phrases figurent dans les raisons de la
    recommandation, c'est-à-dire le texte le plus lu du produit.
    """
    total_minutes = round(abs(heures) * 60)
    h, m = divmod(total_minutes, 60)
    if h == 0:
        return f"{m} minute{'s' if m > 1 else ''}"
    if m == 0:
        return f"{h} h"
    return f"{h} h {m:02d}"


def _normalize(value: float, population: list[float], higher_is_worse: bool) -> float:
    """Min-max sur le lot comparé.

    Quand toutes les options ont la même valeur, l'écart-type est nul : renvoyer
    0 pour toutes est correct — ce critère ne les départage pas.
    """
    low, high = min(population), max(population)
    if high == low:
        return 0.0
    ratio = (value - low) / (high - low)
    return round(ratio if higher_is_worse else 1 - ratio, 4)


def _deltas(alternative: Alternative, current: Alternative | None) -> dict:
    if current is None or alternative.id == current.id:
        return {}
    cost_delta = alternative.estimated_cost_mad - current.estimated_cost_mad
    return {
        "risk_delta_points": round(
            (alternative.disruption_probability - current.disruption_probability) * 100, 1
        ),
        "cost_delta_mad": round(cost_delta, 0),
        "cost_delta_percent": (
            round(cost_delta / current.estimated_cost_mad * 100, 1)
            if current.estimated_cost_mad
            else None
        ),
        "duration_delta_hours": round(
            alternative.duration_hours - current.duration_hours, 2
        ),
    }


def _apply_recommendation(
    best: RankedAlternative, current: Alternative | None
) -> RankedAlternative:
    """Formule les raisons et les contreparties de l'option retenue.

    Les contreparties sont énoncées explicitement : une recommandation qui ne
    dit pas ce qu'elle coûte n'est pas une recommandation, c'est un argumentaire.
    """
    alternative = best.alternative
    reasons: list[str] = []
    tradeoffs: list[str] = list(alternative.tradeoffs_fr)

    if current is not None and alternative.id != current.id:
        if best.risk_delta_points is not None and best.risk_delta_points < -1:
            reasons.append(
                f"Réduit le risque de perturbation de "
                f"{abs(best.risk_delta_points):.0f} points par rapport au plan actuel."
            )
        if best.cost_delta_mad is not None and abs(best.cost_delta_mad) >= 500:
            # En dessous de 500 MAD sur une expédition à plus de 40 000 MAD,
            # l'écart est dans le bruit du modèle de coût : le présenter comme un
            # argument donnerait une fausse impression de précision.
            if best.cost_delta_mad > 0:
                tradeoffs.append(
                    f"Coût supplémentaire de {best.cost_delta_mad:,.0f} MAD "
                    f"({best.cost_delta_percent:+.1f} %).".replace(",", " ")
                )
            elif best.cost_delta_mad < 0:
                reasons.append(
                    f"Coûte {abs(best.cost_delta_mad):,.0f} MAD de moins que le "
                    "plan actuel.".replace(",", " ")
                )
        if best.duration_delta_hours is not None:
            if best.duration_delta_hours > 0.25:
                tradeoffs.append(
                    f"Allonge le trajet de {duree_fr(best.duration_delta_hours)}."
                )
            elif best.duration_delta_hours < -0.25:
                reasons.append(
                    f"Raccourcit le trajet de {duree_fr(best.duration_delta_hours)}."
                )
    else:
        reasons.append(
            "Aucune alternative examinée ne fait mieux que le plan actuel sur "
            "l'ensemble des critères retenus."
        )

    if alternative.sla_compliant and alternative.sla_margin_hours is not None:
        reasons.append(
            f"Respecte l'engagement de service avec {duree_fr(alternative.sla_margin_hours)} "
            "de marge."
        )
    if alternative.risk_level is RiskLevel.LOW:
        reasons.append("Aucun tronçon exposé sur la fenêtre de passage prévue.")
    elif alternative.exposure_fraction > 0:
        tradeoffs.append(
            f"{alternative.exposure_fraction:.0%} du trajet reste exposé : "
            "un suivi en cours de route reste nécessaire."
        )

    return best.model_copy(
        update={
            "is_recommended": True,
            "recommendation_reasons_fr": tuple(reasons),
            "tradeoffs_fr": tuple(tradeoffs),
        }
    )


def _overall_confidence(feasible: list[Alternative]) -> Confidence:
    ranks = [a.confidence for a in feasible]
    if any(c is Confidence.LOW for c in ranks):
        return Confidence.LOW
    if all(c is Confidence.HIGH for c in ranks):
        return Confidence.HIGH
    return Confidence.MEDIUM


def _caveats(feasible: list[Alternative], ranked: list[RankedAlternative]) -> tuple[str, ...]:
    caveats: list[str] = []

    if any(a.data_state.value == "SIMULATED" for a in feasible):
        caveats.append(
            "Cette comparaison repose sur des données météo simulées "
            "(mode démonstration). Elle ne doit pas fonder une décision réelle."
        )

    if len(ranked) >= 2:
        gap = ranked[1].total_score - ranked[0].total_score
        if gap < 0.05:
            caveats.append(
                "Les deux premières options sont très proches : l'écart de score "
                "est inférieur à la précision du modèle. Le choix peut légitimement "
                "se faire sur un critère opérationnel non modélisé."
            )

    if len(feasible) == 1:
        caveats.append(
            "Une seule option respecte les contraintes : il n'y a pas de véritable "
            "arbitrage à opérer."
        )

    return tuple(caveats)
