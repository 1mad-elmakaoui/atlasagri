"""Enveloppes de plausibilité des seuils calibrés.

Un seuil dérivé d'un historique local peut être statistiquement correct et
agronomiquement absurde. Deux situations le produisent :

1. **L'aléa n'existe pas localement.** Agadir ne gèle pratiquement jamais. La
   distribution des températures minimales n'y a donc pas de queue froide, et
   un quantile bas renvoie « gel à 11 °C » — une valeur que la méthode calcule
   sans broncher et qui déclencherait des alertes de gel toute l'année.

2. **La distribution ne discrimine pas.** Si les trois bornes tombent à moins
   d'un écart utile les unes des autres, le seuil ne sépare rien : tout devient
   critique en même temps, ou rien ne l'est jamais.

Ces enveloppes rejettent les deux cas. Un seuil rejeté n'est pas remplacé par une
approximation : le moteur retombe sur le seuil provisoire, qui se déclare comme
provisoire. Mieux vaut une hypothèse assumée qu'un chiffre calibré trompeur.

Les bornes ci-dessous délimitent le domaine où un seuil a un sens agronomique.
Elles ne sont pas des seuils : elles disent seulement dans quel intervalle un
seuil peut raisonnablement tomber.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import RiskType


@dataclass(frozen=True)
class PlausibilityEnvelope:
    """Domaine admissible d'un seuil, et écart minimal entre ses bornes."""

    risk_type: RiskType
    lowest_meaningful: float
    highest_meaningful: float
    minimum_separation: float
    rationale_fr: str

    def reject_reason(
        self, moderate: float, high: float, critical: float | None
    ) -> str | None:
        """Motif de rejet, ou None si le seuil est exploitable."""
        bornes = [moderate, high] + ([critical] if critical is not None else [])

        hors_domaine = [
            b
            for b in bornes
            if not self.lowest_meaningful <= b <= self.highest_meaningful
        ]
        if hors_domaine:
            return (
                f"Seuil hors du domaine agronomiquement significatif "
                f"([{self.lowest_meaningful}, {self.highest_meaningful}]) : {self.rationale_fr} "
                f"L'aléa ne se produit probablement pas dans cette zone."
            )

        if abs(high - moderate) < self.minimum_separation:
            return (
                f"Bornes trop rapprochées ({abs(high - moderate):.1f} < "
                f"{self.minimum_separation}) : le seuil ne distinguerait pas "
                "un niveau de risque d'un autre."
            )
        return None


ENVELOPES: dict[RiskType, PlausibilityEnvelope] = {
    RiskType.HEAVY_RAIN: PlausibilityEnvelope(
        risk_type=RiskType.HEAVY_RAIN,
        lowest_meaningful=8.0,
        highest_meaningful=250.0,
        minimum_separation=6.0,
        rationale_fr=(
            "En dessous de 8 mm sur 48 h aucune culture marocaine n'est menacée ; "
            "au-delà de 250 mm on sort des épisodes documentés."
        ),
    ),
    RiskType.HEAT_STRESS: PlausibilityEnvelope(
        risk_type=RiskType.HEAT_STRESS,
        lowest_meaningful=28.0,
        highest_meaningful=50.0,
        minimum_separation=2.0,
        rationale_fr=(
            "Le stress thermique des cultures maraîchères commence vers 28 °C ; "
            "en deçà il s'agit de conditions ordinaires."
        ),
    ),
    RiskType.FROST: PlausibilityEnvelope(
        risk_type=RiskType.FROST,
        lowest_meaningful=-12.0,
        highest_meaningful=6.0,
        minimum_separation=1.0,
        rationale_fr=(
            "Un risque de gel ne se conçoit qu'au voisinage de 0 °C. "
            "Un seuil au-dessus de 6 °C signale une zone sans gel."
        ),
    ),
    RiskType.STRONG_WIND: PlausibilityEnvelope(
        risk_type=RiskType.STRONG_WIND,
        lowest_meaningful=40.0,
        highest_meaningful=160.0,
        minimum_separation=8.0,
        rationale_fr=(
            "En dessous de 40 km/h les rafales n'affectent ni les cultures ni "
            "la circulation des poids lourds."
        ),
    ),
    RiskType.DROUGHT: PlausibilityEnvelope(
        risk_type=RiskType.DROUGHT,
        lowest_meaningful=0.02,
        highest_meaningful=0.45,
        minimum_separation=0.03,
        rationale_fr="L'humidité volumique du sol reste comprise entre 2 % et 45 %.",
    ),
}


def envelope_for(risk_type: RiskType) -> PlausibilityEnvelope | None:
    return ENVELOPES.get(risk_type)
