"""Vocabulaire du domaine.

Ces énumérations sont la référence unique des noms métier. Les mêmes valeurs
circulent en base, dans l'API, dans les outils MCP et dans l'interface : aucune
traduction intermédiaire, donc aucune divergence possible.
"""

from __future__ import annotations

from enum import Enum


class RiskLevel(str, Enum):
    """Niveaux de risque.

    L'article de référence utilise trois niveaux (Low/Moderate/High). Nous en
    ajoutons un quatrième, CRITICAL, parce qu'un produit opérationnel doit
    distinguer « activer un plan de secours » de « la situation est déjà
    compromise » : ces deux cas n'appellent pas la même urgence de décision.
    """

    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def label_fr(self) -> str:
        return {
            RiskLevel.LOW: "Faible",
            RiskLevel.MODERATE: "Modéré",
            RiskLevel.HIGH: "Élevé",
            RiskLevel.CRITICAL: "Critique",
        }[self]

    @property
    def rank(self) -> int:
        return {
            RiskLevel.LOW: 0,
            RiskLevel.MODERATE: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3,
        }[self]

    @classmethod
    def from_score(cls, score: float) -> RiskLevel:
        """Convertit un score continu [0,1] en niveau catégoriel.

        Les bornes sont volontairement fixes et publiques : c'est la lecture du
        score, pas un seuil agronomique. Les seuils agronomiques, eux, sont
        calibrés par quantiles dans `thresholds.py`.
        """
        if score < 0.25:
            return cls.LOW
        if score < 0.50:
            return cls.MODERATE
        if score < 0.75:
            return cls.HIGH
        return cls.CRITICAL


class DataState(str, Enum):
    """État épistémique d'une valeur.

    Règle produit non négociable : une valeur inférée ne doit jamais être
    présentée comme une mesure. Chaque valeur exposée porte son état.
    """

    OBSERVED = "OBSERVED"     # mesuré par une station ou une réanalyse
    FORECAST = "FORECAST"     # prévu par un service météorologique
    DERIVED = "DERIVED"       # calculé à partir d'observations (SPI, ET0, anomalie)
    INFERRED = "INFERRED"     # déduit par un modèle ou une règle métier
    SIMULATED = "SIMULATED"   # jeu de données de démonstration, jamais une mesure

    @property
    def label_fr(self) -> str:
        return {
            DataState.OBSERVED: "Observé",
            DataState.FORECAST: "Prévu",
            DataState.DERIVED: "Calculé",
            DataState.INFERRED: "Estimé",
            DataState.SIMULATED: "Simulé",
        }[self]


class RiskType(str, Enum):
    HEAVY_RAIN = "HEAVY_RAIN"
    FLOOD = "FLOOD"
    HEAT_STRESS = "HEAT_STRESS"
    FROST = "FROST"
    DROUGHT = "DROUGHT"
    STRONG_WIND = "STRONG_WIND"
    CHERGUI = "CHERGUI"          # vent chaud et sec du Sahara, spécifique au Maroc
    HAIL = "HAIL"
    FOG = "FOG"
    ROAD_DISRUPTION = "ROAD_DISRUPTION"
    SUPPLIER_DISRUPTION = "SUPPLIER_DISRUPTION"
    STOCKOUT = "STOCKOUT"

    @property
    def label_fr(self) -> str:
        return {
            RiskType.HEAVY_RAIN: "Fortes précipitations",
            RiskType.FLOOD: "Inondation",
            RiskType.HEAT_STRESS: "Stress thermique",
            RiskType.FROST: "Gel",
            RiskType.DROUGHT: "Déficit hydrique",
            RiskType.STRONG_WIND: "Vent fort",
            RiskType.CHERGUI: "Chergui",
            RiskType.HAIL: "Grêle",
            RiskType.FOG: "Brouillard",
            RiskType.ROAD_DISRUPTION: "Perturbation routière",
            RiskType.SUPPLIER_DISRUPTION: "Perturbation fournisseur",
            RiskType.STOCKOUT: "Rupture de stock",
        }[self]


class AlternativeType(str, Enum):
    """Classes d'alternatives que le moteur sait produire."""

    CURRENT_PLAN = "CURRENT_PLAN"
    ALTERNATE_ROUTE = "ALTERNATE_ROUTE"
    ALTERNATE_SUPPLIER = "ALTERNATE_SUPPLIER"
    ALTERNATE_WAREHOUSE = "ALTERNATE_WAREHOUSE"
    DEPARTURE_SHIFT = "DEPARTURE_SHIFT"
    INVENTORY_TRANSFER = "INVENTORY_TRANSFER"
    SPLIT_SHIPMENT = "SPLIT_SHIPMENT"

    @property
    def label_fr(self) -> str:
        return {
            AlternativeType.CURRENT_PLAN: "Plan actuel",
            AlternativeType.ALTERNATE_ROUTE: "Itinéraire alternatif",
            AlternativeType.ALTERNATE_SUPPLIER: "Fournisseur alternatif",
            AlternativeType.ALTERNATE_WAREHOUSE: "Entrepôt alternatif",
            AlternativeType.DEPARTURE_SHIFT: "Décalage de départ",
            AlternativeType.INVENTORY_TRANSFER: "Transfert de stock",
            AlternativeType.SPLIT_SHIPMENT: "Expédition fractionnée",
        }[self]


class AlternativeStatus(str, Enum):
    """Une alternative infaisable est écartée, jamais classée.

    Classer une option qui viole une contrainte dure reviendrait à recommander
    quelque chose d'inapplicable : c'est pire qu'inutile.
    """

    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"


class SiteType(str, Enum):
    FARM = "FARM"
    WAREHOUSE = "WAREHOUSE"
    HUB = "HUB"
    CUSTOMER = "CUSTOMER"
    PORT = "PORT"

    @property
    def label_fr(self) -> str:
        return {
            SiteType.FARM: "Ferme",
            SiteType.WAREHOUSE: "Entrepôt",
            SiteType.HUB: "Plateforme logistique",
            SiteType.CUSTOMER: "Client",
            SiteType.PORT: "Port",
        }[self]


class TransportMode(str, Enum):
    REFRIGERATED_TRUCK = "REFRIGERATED_TRUCK"
    STANDARD_TRUCK = "STANDARD_TRUCK"
    BULK_TRUCK = "BULK_TRUCK"

    @property
    def label_fr(self) -> str:
        return {
            TransportMode.REFRIGERATED_TRUCK: "Camion frigorifique",
            TransportMode.STANDARD_TRUCK: "Camion standard",
            TransportMode.BULK_TRUCK: "Camion vrac",
        }[self]


class ShipmentStatus(str, Enum):
    PLANNED = "PLANNED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"

    @property
    def label_fr(self) -> str:
        return {
            ShipmentStatus.PLANNED: "Planifiée",
            ShipmentStatus.IN_TRANSIT: "En transit",
            ShipmentStatus.DELIVERED: "Livrée",
            ShipmentStatus.CANCELLED: "Annulée",
        }[self]


class UserRole(str, Enum):
    ADMIN = "ADMIN"
    SUPPLY_CHAIN_MANAGER = "SUPPLY_CHAIN_MANAGER"
    OPERATIONS_MANAGER = "OPERATIONS_MANAGER"
    ANALYST = "ANALYST"
    EXECUTIVE = "EXECUTIVE"

    @property
    def label_fr(self) -> str:
        return {
            UserRole.ADMIN: "Administrateur",
            UserRole.SUPPLY_CHAIN_MANAGER: "Responsable supply chain",
            UserRole.OPERATIONS_MANAGER: "Responsable opérations",
            UserRole.ANALYST: "Analyste",
            UserRole.EXECUTIVE: "Direction",
        }[self]


class RecommendationStatus(str, Enum):
    """L'humain garde le contrôle : rien n'est exécuté automatiquement."""

    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    MODIFIED = "MODIFIED"

    @property
    def label_fr(self) -> str:
        return {
            RecommendationStatus.PROPOSED: "Proposée",
            RecommendationStatus.ACCEPTED: "Acceptée",
            RecommendationStatus.REJECTED: "Ignorée",
            RecommendationStatus.MODIFIED: "Modifiée",
        }[self]


class Confidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def label_fr(self) -> str:
        return {
            Confidence.LOW: "Faible",
            Confidence.MEDIUM: "Moyenne",
            Confidence.HIGH: "Élevée",
        }[self]


# Horizons de nowcasting repris de l'article de référence (6/12/24/48 h).
# Configurables : ce sont des valeurs par défaut, pas une contrainte du modèle.
DEFAULT_HORIZONS_HOURS: tuple[int, ...] = (6, 12, 24, 48)
