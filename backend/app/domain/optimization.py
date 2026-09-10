"""Pondérations d'optimisation multicritère.

Principe : il n'existe pas de pondération universelle. Des tomates
réfrigérées et du blé en vrac n'ont pas la même fonction d'objectif — pour
l'une le risque et le délai dominent, pour l'autre le coût.

Les profils ci-dessous sont des **valeurs par défaut par nature de produit**.
Un tenant peut les surcharger : c'est une donnée de configuration, pas une
constante du code.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field, model_validator


class OptimizationWeights(BaseModel):
    """Poids d'une fonction de score multicritère. Leur somme vaut 1."""

    risk: float = Field(ge=0, le=1)
    cost: float = Field(ge=0, le=1)
    duration: float = Field(ge=0, le=1)
    exposure: float = Field(ge=0, le=1)
    reliability: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def _sum_to_one(self) -> OptimizationWeights:
        total = self.risk + self.cost + self.duration + self.exposure + self.reliability
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Les poids d'optimisation doivent sommer à 1, obtenu {total:.4f}. "
                "Une somme différente rendrait les scores incomparables entre profils."
            )
        return self

    def as_dict_fr(self) -> dict[str, float]:
        """Poids affichables, pour que l'utilisateur voie ce qui a été privilégié."""
        return {
            "Risque de perturbation": self.risk,
            "Coût": self.cost,
            "Délai": self.duration,
            "Exposition aux zones à risque": self.exposure,
            "Fiabilité de l'axe": self.reliability,
        }


@dataclass(frozen=True)
class OptimizationProfile:
    code: str
    label_fr: str
    rationale_fr: str
    weights: OptimizationWeights


PROFILES: dict[str, OptimizationProfile] = {
    "PERISSABLE_FROID": OptimizationProfile(
        code="PERISSABLE_FROID",
        label_fr="Périssable sous chaîne du froid",
        rationale_fr=(
            "La valeur marchande dépend de l'intégrité du produit à l'arrivée. "
            "Un surcoût de transport reste très inférieur à la perte d'un lot "
            "déclassé : le risque et le délai priment sur le coût."
        ),
        weights=OptimizationWeights(risk=0.40, cost=0.10, duration=0.25, exposure=0.20, reliability=0.05),
    ),
    "PERISSABLE_STANDARD": OptimizationProfile(
        code="PERISSABLE_STANDARD",
        label_fr="Périssable sans chaîne du froid",
        rationale_fr=(
            "Le délai compte, mais la marge de manœuvre est plus large qu'en "
            "chaîne du froid : le coût reprend du poids."
        ),
        weights=OptimizationWeights(risk=0.32, cost=0.20, duration=0.25, exposure=0.18, reliability=0.05),
    ),
    "VRAC_FAIBLE_MARGE": OptimizationProfile(
        code="VRAC_FAIBLE_MARGE",
        label_fr="Vrac à faible marge",
        rationale_fr=(
            "La marge unitaire est trop faible pour absorber un surcoût logistique "
            "important. Le coût domine, le risque reste second."
        ),
        weights=OptimizationWeights(risk=0.22, cost=0.45, duration=0.13, exposure=0.15, reliability=0.05),
    ),
    "EXPORT_SLA_STRICT": OptimizationProfile(
        code="EXPORT_SLA_STRICT",
        label_fr="Export sous engagement de délai strict",
        rationale_fr=(
            "Un créneau portuaire ou un engagement client manqué a un coût "
            "disproportionné. Le respect du délai et la fiabilité de l'axe dominent."
        ),
        weights=OptimizationWeights(risk=0.28, cost=0.10, duration=0.34, exposure=0.16, reliability=0.12),
    ),
}

DEFAULT_PROFILE_CODE = "PERISSABLE_STANDARD"


def get_profile(code: str | None) -> OptimizationProfile:
    if not code:
        return PROFILES[DEFAULT_PROFILE_CODE]
    if code not in PROFILES:
        raise KeyError(
            f"Profil d'optimisation inconnu : '{code}'. Profils : {sorted(PROFILES)}"
        )
    return PROFILES[code]
