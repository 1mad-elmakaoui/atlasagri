"""Fiabilité des prédictions et pertes évitées.

Confronte ce que le système a annoncé à ce que le terrain a observé. C'est la
seule mesure qui permette de dire si le produit vaut quelque chose.

Deux principes gouvernent ce module :

1. **On mesure sur la partition d'évaluation uniquement.** Les résultats
   réservés à l'évaluation ne servent jamais à ajuster les seuils. Mesurer sur
   les données ayant servi à calibrer produirait un score flatteur et faux.

2. **On refuse de conclure sur un échantillon insuffisant.** Un score de Brier
   calculé sur sept expéditions n'a aucune valeur informative. Le module dit
   alors combien de retours manquent, plutôt que d'afficher un chiffre que
   quelqu'un finirait par mettre dans une présentation commerciale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.db.repositories import TenantRepository

logger = get_logger(__name__)

# En dessous, aucune statistique n'est publiée. Le seuil est délibérément bas
# pour un produit jeune, et reste au-dessus du point où une poignée de cas
# domine entièrement le résultat.
MINIMUM_SAMPLE = 25

# Bornes des tranches de probabilité pour la courbe de fiabilité.
BINS = ((0.0, 0.15), (0.15, 0.35), (0.35, 0.55), (0.55, 0.75), (0.75, 1.01))


@dataclass(frozen=True)
class Pair:
    """Couple (probabilité annoncée, perturbation observée)."""

    reference: str
    predicted: float
    observed: bool
    delay_hours: float
    loss_mad: float
    followed_recommendation: bool


class ReliabilityService:
    def __init__(self, repo: TenantRepository) -> None:
        self.repo = repo

    def pairs(self, *, split: str = "evaluation") -> list[Pair]:
        """Couples exploitables, restreints à une partition."""
        predictions = {p.id: p for p in self.repo.predictions()}
        couples: list[Pair] = []

        # Une correction remplace la version qu'elle référence : on ne garde que
        # les versions finales, sinon un même événement compterait deux fois.
        outcomes = self.repo.outcomes()
        remplaces = {o.supersedes_id for o in outcomes if o.supersedes_id}

        for outcome in outcomes:
            if outcome.id in remplaces:
                continue
            if outcome.evaluation_split != split:
                continue
            if outcome.prediction_id is None or not outcome.delivered:
                continue

            prediction = predictions.get(outcome.prediction_id)
            if prediction is None:
                continue

            couples.append(
                Pair(
                    reference=prediction.subject_reference,
                    predicted=prediction.disruption_probability,
                    observed=bool(outcome.disruption_occurred),
                    delay_hours=outcome.disruption_delay_hours or 0.0,
                    loss_mad=outcome.estimated_loss_mad or 0.0,
                    followed_recommendation=(
                        prediction.recommended_option_id is not None
                        and outcome.planned_route_followed is False
                    ),
                )
            )
        return couples

    def evaluate(self, *, split: str = "evaluation") -> dict[str, Any]:
        couples = self.pairs(split=split)
        collectes = len(self.repo.outcomes())

        if len(couples) < MINIMUM_SAMPLE:
            return {
                "mesurable": False,
                "couples_disponibles": len(couples),
                "retours_collectes": collectes,
                "minimum_requis": MINIMUM_SAMPLE,
                "message_fr": (
                    f"Fiabilité non mesurable : {len(couples)} couple(s) "
                    f"prédiction/résultat sur la partition d'évaluation, "
                    f"minimum {MINIMUM_SAMPLE}. "
                    "Aucun score n'est publié tant que l'échantillon ne permet pas "
                    "de conclure."
                ),
                "prochaine_etape_fr": (
                    "Recueillir davantage de retours terrain depuis la page "
                    "« Retour terrain »."
                ),
            }

        brier = sum((c.predicted - float(c.observed)) ** 2 for c in couples) / len(couples)
        taux_observe = sum(1 for c in couples if c.observed) / len(couples)
        # Score de Brier d'une prévision constante égale au taux de base : c'est
        # la référence à battre. Un modèle qui ne fait pas mieux n'apporte rien.
        brier_reference = taux_observe * (1 - taux_observe)
        gain = (
            (brier_reference - brier) / brier_reference if brier_reference > 0 else 0.0
        )

        return {
            "mesurable": True,
            "partition": split,
            "couples": len(couples),
            "score_de_brier": round(brier, 4),
            "score_de_reference": round(brier_reference, 4),
            "gain_sur_reference": round(gain, 3),
            "taux_de_perturbation_observe": round(taux_observe, 3),
            "interpretation_fr": _interpret(brier, brier_reference, gain),
            "courbe_de_fiabilite": self._reliability_curve(couples),
            "pertes": self._losses(couples),
        }

    def _reliability_curve(self, couples: list[Pair]) -> list[dict[str, Any]]:
        """Taux observé par tranche de probabilité annoncée.

        C'est la lecture la plus parlante pour un non-statisticien : « quand le
        système annonce 30 %, cela se produit-il environ trois fois sur dix ? »
        """
        courbe: list[dict[str, Any]] = []
        for bas, haut in BINS:
            tranche = [c for c in couples if bas <= c.predicted < haut]
            if not tranche:
                continue
            observe = sum(1 for c in tranche if c.observed) / len(tranche)
            annonce = sum(c.predicted for c in tranche) / len(tranche)
            courbe.append(
                {
                    "tranche_fr": f"{bas:.0%} – {min(haut, 1.0):.0%}",
                    "annonce_moyen": round(annonce, 3),
                    "observe": round(observe, 3),
                    "effectif": len(tranche),
                    "ecart": round(observe - annonce, 3),
                }
            )
        return courbe

    def _losses(self, couples: list[Pair]) -> dict[str, Any]:
        """Pertes constatées, et ce qu'on peut honnêtement en dire.

        La « perte évitée » est la mesure la plus demandée commercialement et la
        plus facile à falsifier. On ne compare que ce qui est comparable : les
        expéditions signalées à risque où la recommandation a été suivie, contre
        celles où elle ne l'a pas été.

        Tant que les deux groupes ne sont pas suffisamment fournis, le module
        expose les pertes constatées et refuse d'annoncer un montant évité.
        """
        a_risque = [c for c in couples if c.predicted >= 0.35]
        suivies = [c for c in a_risque if c.followed_recommendation]
        non_suivies = [c for c in a_risque if not c.followed_recommendation]

        perte_totale = sum(c.loss_mad for c in couples)
        comparable = len(suivies) >= 10 and len(non_suivies) >= 10

        resultat: dict[str, Any] = {
            "perte_totale_constatee_mad": round(perte_totale, 0),
            "expeditions_signalees_a_risque": len(a_risque),
            "recommandation_suivie": len(suivies),
            "recommandation_non_suivie": len(non_suivies),
            "comparaison_possible": comparable,
        }

        if not comparable:
            resultat["message_fr"] = (
                "Perte évitée non quantifiable : il faut au moins dix expéditions "
                "à risque dans chaque groupe (recommandation suivie et non suivie) "
                "pour que la comparaison ait un sens. "
                "Les pertes constatées sont affichées telles quelles."
            )
            return resultat

        moyenne_suivie = sum(c.loss_mad for c in suivies) / len(suivies)
        moyenne_non_suivie = sum(c.loss_mad for c in non_suivies) / len(non_suivies)
        resultat |= {
            "perte_moyenne_recommandation_suivie_mad": round(moyenne_suivie, 0),
            "perte_moyenne_recommandation_ignoree_mad": round(moyenne_non_suivie, 0),
            "ecart_par_expedition_mad": round(moyenne_non_suivie - moyenne_suivie, 0),
            "message_fr": (
                "Écart observé entre les deux groupes. Il s'agit d'une "
                "association, pas d'un effet causal démontré : les expéditions "
                "où la recommandation est suivie peuvent différer par ailleurs."
            ),
        }
        return resultat


def _interpret(brier: float, reference: float, gain: float) -> str:
    if gain <= 0:
        return (
            f"Le score de Brier ({brier:.3f}) n'est pas meilleur que celui d'une "
            f"prévision constante ({reference:.3f}). Sur cet échantillon, les "
            "probabilités annoncées n'apportent pas d'information exploitable. "
            "Les seuils et les règles de risque doivent être revus."
        )
    if gain < 0.15:
        return (
            f"Le score de Brier ({brier:.3f}) dépasse légèrement la référence "
            f"({reference:.3f}), de {gain:.0%}. Le signal existe mais reste faible."
        )
    return (
        f"Le score de Brier ({brier:.3f}) améliore la référence ({reference:.3f}) "
        f"de {gain:.0%}. Les probabilités annoncées portent une information réelle."
    )
