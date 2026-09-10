"""Modèle de coût de transport routier.

Le coût doit être **explicable ligne à ligne**. Un exploitant qui voit
« +3 500 MAD » doit pouvoir savoir d'où viennent ces 3 500 dirhams, sinon il ne
fera pas confiance à l'arbitrage proposé.

Les paramètres reflètent des ordres de grandeur du fret routier marocain. Ils
sont configurables par organisation : une flotte propre et un affrètement
externe n'ont pas la même structure de coût.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.enums import TransportMode


@dataclass(frozen=True)
class TransportCostParameters:
    """Paramètres de coût pour un mode de transport."""

    cost_per_tonne_km_mad: float
    truck_capacity_tonnes: float
    fixed_cost_mad: float
    hourly_equipment_cost_mad: float = 0.0
    label_fr: str = ""


COST_PARAMETERS: dict[TransportMode, TransportCostParameters] = {
    TransportMode.REFRIGERATED_TRUCK: TransportCostParameters(
        cost_per_tonne_km_mad=0.40,
        truck_capacity_tonnes=24.0,
        fixed_cost_mad=2500.0,
        # Le groupe froid consomme tant que le camion roule : c'est ce terme qui
        # fait qu'un détour de deux heures coûte réellement plus cher, et pas
        # seulement en kilomètres.
        hourly_equipment_cost_mad=55.0,
        label_fr="Camion frigorifique",
    ),
    TransportMode.STANDARD_TRUCK: TransportCostParameters(
        cost_per_tonne_km_mad=0.35,
        truck_capacity_tonnes=26.0,
        fixed_cost_mad=2000.0,
        label_fr="Camion standard",
    ),
    TransportMode.BULK_TRUCK: TransportCostParameters(
        cost_per_tonne_km_mad=0.28,
        truck_capacity_tonnes=30.0,
        fixed_cost_mad=1800.0,
        label_fr="Camion vrac",
    ),
}


@dataclass(frozen=True)
class CostBreakdown:
    """Décomposition du coût, destinée à être affichée telle quelle."""

    distance_cost_mad: float
    fixed_cost_mad: float
    equipment_cost_mad: float
    trucks_required: int
    total_mad: float

    def as_lines_fr(self) -> list[dict[str, float | str]]:
        lines: list[dict[str, float | str]] = [
            {"poste": "Transport (distance × volume)", "montant_mad": self.distance_cost_mad},
            {
                "poste": f"Frais fixes ({self.trucks_required} camions)",
                "montant_mad": self.fixed_cost_mad,
            },
        ]
        if self.equipment_cost_mad > 0:
            lines.append(
                {"poste": "Groupe froid (durée du trajet)", "montant_mad": self.equipment_cost_mad}
            )
        return lines


def estimate_cost(
    *,
    distance_km: float,
    duration_hours: float,
    volume_tonnes: float,
    mode: TransportMode,
) -> CostBreakdown:
    """Estime le coût d'un trajet.

    Le nombre de camions est arrondi au supérieur : on ne peut pas affréter 7,5
    camions. Cet arrondi crée des effets de seuil réels que le modèle doit
    refléter — c'est précisément ce qui rend parfois une expédition fractionnée
    plus intéressante qu'un envoi unique.
    """
    if volume_tonnes <= 0:
        raise ValueError("Le volume doit être strictement positif.")

    params = COST_PARAMETERS[mode]
    trucks = max(1, math.ceil(volume_tonnes / params.truck_capacity_tonnes))

    distance_cost = distance_km * volume_tonnes * params.cost_per_tonne_km_mad
    fixed_cost = params.fixed_cost_mad * trucks / max(1, trucks)  # forfait par expédition
    equipment_cost = params.hourly_equipment_cost_mad * duration_hours * trucks

    return CostBreakdown(
        distance_cost_mad=round(distance_cost, 0),
        fixed_cost_mad=round(fixed_cost, 0),
        equipment_cost_mad=round(equipment_cost, 0),
        trucks_required=trucks,
        total_mad=round(distance_cost + fixed_cost + equipment_cost, 0),
    )
