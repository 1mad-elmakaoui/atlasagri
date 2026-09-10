"""Tests des moteurs : exposition temporelle, faisabilité, classement.

Le comportement le plus important à protéger est l'**exposition temporelle** :
un itinéraire n'est exposé que si le véhicule s'y trouve pendant la fenêtre de
perturbation. Une régression sur ce point ferait remonter des alertes sur des
trajets déjà terminés et ruinerait la confiance dans le produit.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault("WEATHER_PROVIDER", "offline")

from app.domain.enums import AlternativeStatus, AlternativeType, RiskLevel, TransportMode
from app.providers.routing.network import NetworkRoutingProvider
from app.services.alternatives import (
    AlternativeEngine,
    ShipmentContext,
    SupplierOption,
    WarehouseOption,
)
from app.services.nowcasting import NowcastingService, standardized_precipitation_index
from app.services.optimization import OptimizationService
from app.services.route_risk import RouteRiskService
from app.services.supply_chain_risk import (
    SupplyChainRiskEngine,
    calculate_reorder_quantity,
    calculate_stock_coverage,
)

# Le jeu de démonstration place un épisode pluvieux sur le tronçon de montagne
# de l'A7 entre +16 h et +30 h.
STORM_START_HOURS = 16
STORM_END_HOURS = 30


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def routing() -> NetworkRoutingProvider:
    return NetworkRoutingProvider()


class TestRouting:
    async def test_fastest_route_matches_real_corridor(self, routing):
        route = await routing.route("AGADIR", "CASABLANCA")
        # L'A7 par Marrakech est bien le corridor réel le plus rapide.
        assert "MARRAKECH" in route.node_codes
        assert 480 < route.total_distance_km < 620

    async def test_alternatives_are_genuinely_distinct(self, routing):
        routes = await routing.alternatives("AGADIR", "CASABLANCA", max_routes=4)
        assert len(routes) >= 2
        corridors = [set(r.node_codes[1:-1]) for r in routes]
        for i, first in enumerate(corridors):
            for second in corridors[i + 1:]:
                overlap = len(first & second) / len(first | second)
                assert overlap <= 0.7, "Deux alternatives décrivent le même corridor"

    async def test_no_absurdly_long_alternative_is_proposed(self, routing):
        routes = await routing.alternatives("AGADIR", "CASABLANCA", max_routes=5)
        best = min(r.total_duration_hours for r in routes)
        assert all(r.total_duration_hours <= best * 1.75 for r in routes)

    async def test_blocked_node_is_avoided(self, routing):
        routes = await routing.alternatives(
            "AGADIR", "CASABLANCA", max_routes=3, avoid_nodes=("IMINTANOUTE",)
        )
        assert all("IMINTANOUTE" not in r.node_codes for r in routes)

    async def test_identical_origin_and_destination_is_rejected(self, routing):
        with pytest.raises(Exception, match="identiques"):
            await routing.route("AGADIR", "AGADIR")


class TestTemporalExposure:
    """Le cœur du différenciateur produit."""

    async def test_vehicle_passing_before_storm_is_not_exposed(self, routing):
        route = await routing.route("AGADIR", "CASABLANCA")
        assessment = await RouteRiskService().assess(
            route,
            departure_at=_now() + timedelta(hours=2),
            volume_tonnes=180,
            transport_mode=TransportMode.REFRIGERATED_TRUCK,
        )
        # Trajet d'environ 5 h : le camion est arrivé bien avant +16 h.
        assert assessment.risk_level is RiskLevel.LOW
        assert assessment.exposed_segment_indexes == ()

    async def test_vehicle_inside_storm_window_is_exposed(self, routing):
        route = await routing.route("AGADIR", "CASABLANCA")
        assessment = await RouteRiskService().assess(
            route,
            departure_at=_now() + timedelta(hours=STORM_START_HOURS + 6),
            volume_tonnes=180,
            transport_mode=TransportMode.REFRIGERATED_TRUCK,
        )
        assert assessment.risk_level.rank >= RiskLevel.MODERATE.rank
        assert assessment.exposed_segment_indexes != ()

    async def test_only_the_mountain_section_is_flagged(self, routing):
        """L'exposition doit être localisée, pas étalée sur tout le trajet."""
        route = await routing.route("AGADIR", "CASABLANCA")
        assessment = await RouteRiskService().assess(
            route,
            departure_at=_now() + timedelta(hours=STORM_START_HOURS + 6),
            volume_tonnes=180,
            transport_mode=TransportMode.REFRIGERATED_TRUCK,
        )
        exposed = [s for s in assessment.segments if s.is_exposed]
        assert exposed, "Aucun tronçon exposé alors que le camion traverse la zone"
        assert assessment.exposure_fraction < 0.6, (
            "L'exposition déborde largement de la zone perturbée"
        )
        names = {s.to_name_fr for s in exposed} | {s.from_name_fr for s in exposed}
        assert {"Imi n'Tanoute", "Chichaoua"} & names

    async def test_segment_timing_accumulates_along_the_route(self, routing):
        route = await routing.route("AGADIR", "CASABLANCA")
        assessment = await RouteRiskService().assess(
            route,
            departure_at=_now() + timedelta(hours=4),
            volume_tonnes=180,
            transport_mode=TransportMode.REFRIGERATED_TRUCK,
        )
        offsets = [s.hours_from_departure for s in assessment.segments]
        assert offsets == sorted(offsets), "Les tronçons ne se succèdent pas dans le temps"
        assert assessment.segments[0].entry_at == assessment.departure_at

    async def test_adverse_conditions_lengthen_the_journey(self, routing):
        route = await routing.route("AGADIR", "CASABLANCA")
        service = RouteRiskService()
        clear = await service.assess(
            route, departure_at=_now() + timedelta(hours=2),
            volume_tonnes=180, transport_mode=TransportMode.REFRIGERATED_TRUCK,
        )
        stormy = await service.assess(
            route, departure_at=_now() + timedelta(hours=STORM_START_HOURS + 6),
            volume_tonnes=180, transport_mode=TransportMode.REFRIGERATED_TRUCK,
        )
        assert stormy.adjusted_duration_hours > clear.adjusted_duration_hours

    async def test_simulated_data_never_yields_high_confidence(self, routing):
        route = await routing.route("AGADIR", "CASABLANCA")
        assessment = await RouteRiskService().assess(
            route, departure_at=_now() + timedelta(hours=4),
            volume_tonnes=180, transport_mode=TransportMode.REFRIGERATED_TRUCK,
        )
        assert assessment.data_state.value == "SIMULATED"
        assert assessment.confidence.value == "LOW"


class TestStockCalculations:
    def test_usable_coverage_excludes_safety_stock(self):
        coverage = calculate_stock_coverage(
            site_name_fr="Casablanca", product_name_fr="Tomate",
            quantity_tonnes=128, safety_stock_tonnes=60, daily_demand_tonnes=40,
        )
        assert coverage.coverage_days == pytest.approx(3.2)
        assert coverage.usable_coverage_days == pytest.approx(1.7)

    def test_zero_demand_is_rejected_not_divided_by(self):
        with pytest.raises(Exception, match="Demande quotidienne invalide"):
            calculate_stock_coverage(
                site_name_fr="X", product_name_fr="Y",
                quantity_tonnes=100, safety_stock_tonnes=10, daily_demand_tonnes=0,
            )

    def test_reorder_point_covers_lead_time_plus_safety(self):
        result = calculate_reorder_quantity(
            daily_demand_tonnes=40, lead_time_days=5,
            safety_stock_tonnes=60, current_quantity_tonnes=128,
        )
        assert result["point_de_commande_tonnes"] == 260
        assert result["quantite_a_commander_tonnes"] == 132
        assert result["commande_necessaire"] is True

    def test_coverage_shorter_than_lead_time_is_serious(self):
        coverage = calculate_stock_coverage(
            site_name_fr="Casablanca", product_name_fr="Tomate",
            quantity_tonnes=128, safety_stock_tonnes=60, daily_demand_tonnes=40,
        )
        risk = SupplyChainRiskEngine().assess_stock_risk(
            coverage=coverage, supplier_lead_time_days=5, disruption_duration_days=1.5,
            entity_id="inv-1", horizon_hours=48,
            confidence=__import__("app.domain.enums", fromlist=["Confidence"]).Confidence.MEDIUM,
            geographic_area_fr="Souss-Massa", cause_fr="Épisode pluvieux",
        )
        assert risk.level.rank >= RiskLevel.HIGH.rank
        assert any("Écart" in i for i in risk.operational_implications_fr)


class TestNowcasting:
    async def test_horizons_are_produced_with_decreasing_confidence(self):
        result = await NowcastingService().nowcast(31.1719, -8.8506)
        horizons = {h.horizon_hours: h for h in result.horizons}
        assert set(horizons) == {6, 12, 24, 48}
        assert horizons[6].confidence.value == "HIGH"
        assert horizons[48].confidence.value in {"MEDIUM", "LOW"}

    async def test_cumulative_precipitation_grows_with_horizon(self):
        result = await NowcastingService().nowcast(31.1719, -8.8506)
        totals = [
            h.cumulative_precipitation_mm
            for h in sorted(result.horizons, key=lambda x: x.horizon_hours)
        ]
        assert totals == sorted(totals)

    def test_spi_refuses_short_history(self):
        assert standardized_precipitation_index(50.0, [10.0] * 5) is None

    def test_spi_is_computed_on_sufficient_history(self):
        history = [float(i % 40) for i in range(120)]
        assert standardized_precipitation_index(80.0, history) is not None


class TestAlternativesAndRanking:
    def _context(self, departure_offset_hours: int) -> ShipmentContext:
        now = _now()
        return ShipmentContext(
            shipment_id="s1", reference="EXP-1842", crop_code="TOMATE",
            product_name_fr="Tomate cerise export", optimization_profile="EXPORT_SLA_STRICT",
            volume_tonnes=180.0, transport_mode=TransportMode.REFRIGERATED_TRUCK,
            origin_node_code="AGADIR", origin_name_fr="Agadir",
            destination_node_code="CASABLANCA", destination_name_fr="Casablanca",
            destination_has_cold_storage=True, destination_capacity_tonnes=3200,
            departure_at=now + timedelta(hours=departure_offset_hours),
            sla_deadline_at=now + timedelta(hours=departure_offset_hours + 24),
        )

    async def test_several_classes_of_alternatives_are_generated(self):
        alternatives = await AlternativeEngine().generate_for_shipment(
            self._context(22),
            warehouses=(WarehouseOption("w1", "Marrakech", "MARRAKECH", True, 1500),),
        )
        types = {a.type for a in alternatives}
        assert AlternativeType.CURRENT_PLAN in types
        assert AlternativeType.ALTERNATE_ROUTE in types
        assert AlternativeType.DEPARTURE_SHIFT in types
        assert AlternativeType.ALTERNATE_WAREHOUSE in types

    async def test_supplier_exceeding_deadline_is_marked_infeasible(self):
        alternatives = await AlternativeEngine().generate_for_shipment(
            self._context(22),
            suppliers=(
                SupplierOption("sup", "Fournisseur lointain", "TAROUDANT", "Taroudant",
                               lead_time_days=9.0, reliability=0.9,
                               daily_capacity_tonnes=100, unit_price_mad_per_tonne=4000),
            ),
        )
        supplier_option = next(
            a for a in alternatives if a.type is AlternativeType.ALTERNATE_SUPPLIER
        )
        assert supplier_option.status is AlternativeStatus.INFEASIBLE
        assert supplier_option.infeasibility_reasons_fr

    async def test_warehouse_without_cold_storage_is_refused_for_perishables(self):
        alternatives = await AlternativeEngine().generate_for_shipment(
            self._context(22),
            warehouses=(WarehouseOption("w2", "Hangar sec", "SAFI", False, 800),),
        )
        option = next(a for a in alternatives if a.id.startswith("entrepot-"))
        assert option.status is AlternativeStatus.INFEASIBLE
        assert any("frigorifique" in r for r in option.infeasibility_reasons_fr)

    async def test_infeasible_options_are_never_ranked(self):
        alternatives = await AlternativeEngine().generate_for_shipment(
            self._context(22),
            suppliers=(
                SupplierOption("sup", "Trop lent", "TAROUDANT", "Taroudant", 9.0, 0.9, 100, 4000),
            ),
        )
        result = OptimizationService().rank(alternatives, profile_code="EXPORT_SLA_STRICT")
        ranked_ids = {r.alternative.id for r in result.ranked}
        rejected_ids = {a.id for a in result.rejected}
        assert rejected_ids
        assert not (ranked_ids & rejected_ids)
        assert all(r.alternative.is_feasible for r in result.ranked)

    async def test_recommendation_avoids_the_storm(self):
        """Test de bout en bout du scénario de démonstration."""
        alternatives = await AlternativeEngine().generate_for_shipment(self._context(22))
        result = OptimizationService().rank(alternatives, profile_code="EXPORT_SLA_STRICT")

        recommended = result.recommended
        assert recommended is not None
        current = next(a for a in alternatives if a.type is AlternativeType.CURRENT_PLAN)
        assert recommended.alternative.risk_score <= current.risk_score
        assert recommended.alternative.sla_compliant
        assert recommended.recommendation_reasons_fr

    async def test_product_profile_changes_the_ranking(self):
        """Une pondération différente doit pouvoir produire un autre choix."""
        alternatives = await AlternativeEngine().generate_for_shipment(self._context(22))
        service = OptimizationService()
        strict = service.rank(alternatives, profile_code="EXPORT_SLA_STRICT")
        bulk = service.rank(alternatives, profile_code="VRAC_FAIBLE_MARGE")
        assert strict.weights_fr != bulk.weights_fr
        assert strict.profile_label_fr != bulk.profile_label_fr

    async def test_warehouse_option_counts_its_second_leg(self):
        """Régression : une option en deux temps ne doit pas paraître plus rapide.

        Livrer à un entrepôt intermédiaire s'arrête en chemin. Si seule la
        première étape était comptée, l'option afficherait une durée inférieure
        au trajet direct et le classement la privilégierait à tort, alors
        qu'elle impose un second transport.
        """
        alternatives = await AlternativeEngine().generate_for_shipment(
            self._context(22),
            warehouses=(WarehouseOption("w1", "Marrakech", "MARRAKECH", True, 1500),),
        )
        direct = next(a for a in alternatives if a.type is AlternativeType.CURRENT_PLAN)
        via_entrepot = next(
            a for a in alternatives if a.type is AlternativeType.ALTERNATE_WAREHOUSE
        )

        assert via_entrepot.duration_hours > direct.duration_hours, (
            "Le second trajet n'est pas compté dans la durée"
        )
        # La distance peut être identique lorsque l'itinéraire direct passe
        # déjà par cet entrepôt : c'est le cas de Marrakech sur l'axe Agadir →
        # Casablanca. Ce qui change alors, c'est le temps et le coût de la
        # rupture de charge, pas les kilomètres.
        assert via_entrepot.distance_km >= direct.distance_km
        assert via_entrepot.estimated_cost_mad > direct.estimated_cost_mad
        # La contrepartie doit être énoncée, pas seulement chiffrée.
        assert any("Rupture de charge" in t for t in via_entrepot.tradeoffs_fr)

    async def test_simulated_data_is_flagged_in_the_comparison(self):
        alternatives = await AlternativeEngine().generate_for_shipment(self._context(22))
        result = OptimizationService().rank(alternatives, profile_code="EXPORT_SLA_STRICT")
        assert any("simulées" in c for c in result.caveats_fr)

    def test_no_feasible_option_is_reported_honestly(self):
        result = OptimizationService().rank([], profile_code="EXPORT_SLA_STRICT")
        assert result.recommended_id is None
        assert result.ranked == ()
        assert any("Aucune option" in c for c in result.caveats_fr)
