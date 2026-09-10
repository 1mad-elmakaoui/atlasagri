"""Tests du domaine : calculs, seuils, coûts, isolation.

Ces tests valident du comportement métier réel. Ils ne vérifient pas que
Pydantic sait construire un objet.
"""

from __future__ import annotations

import pytest

from app.domain.crops import get_crop
from app.domain.enums import RiskLevel, RiskType, TransportMode
from app.domain.geo import Coordinates, haversine_km, segment_overlap_fraction
from app.domain.optimization import OptimizationWeights, get_profile
from app.domain.road_risk import RAIN_ACCUMULATION, RAIN_INTENSITY, WIND_GUST
from app.domain.thresholds import (
    InsufficientHistoryError,
    QuantileThresholdCalibrator,
    quantile,
)
from app.domain.transport import estimate_cost


class TestGeo:
    def test_distance_agadir_casablanca_matches_reality(self):
        # Distance orthodromique réelle : environ 400 km.
        distance = haversine_km(Coordinates(30.4278, -9.5981), Coordinates(33.5731, -7.5898))
        assert 390 < distance < 410

    def test_invalid_latitude_is_rejected(self):
        with pytest.raises(ValueError, match="Latitude"):
            Coordinates(120.0, 0.0)

    def test_segment_fully_inside_zone_reports_full_overlap(self):
        centre = Coordinates(31.6, -8.0)
        overlap = segment_overlap_fraction(
            Coordinates(31.55, -8.05), Coordinates(31.65, -7.95), centre, 50
        )
        assert overlap == 1.0

    def test_segment_outside_zone_reports_no_overlap(self):
        overlap = segment_overlap_fraction(
            Coordinates(30.4, -9.6), Coordinates(30.5, -9.5), Coordinates(34.0, -5.0), 50
        )
        assert overlap == 0.0


class TestQuantileCalibration:
    """La méthode de l'article : seuils dérivés de la distribution locale."""

    def test_thresholds_follow_local_distribution(self):
        observations = list(range(100))  # distribution uniforme 0–99
        threshold = QuantileThresholdCalibrator().calibrate(
            observations=observations,
            risk_type=RiskType.HEAVY_RAIN,
            variable="precipitation_48h_mm",
            unit="mm",
            source_label_fr="Test",
        )
        assert threshold.moderate_at == pytest.approx(quantile(observations, 0.33), abs=1)
        assert threshold.high_at == pytest.approx(quantile(observations, 0.67), abs=1)
        assert threshold.moderate_at < threshold.high_at < threshold.critical_at

    def test_small_sample_is_refused_rather_than_guessed(self):
        with pytest.raises(InsufficientHistoryError, match="minimum requis"):
            QuantileThresholdCalibrator().calibrate(
                observations=[1.0] * 10,
                risk_type=RiskType.HEAVY_RAIN,
                variable="x",
                unit="mm",
                source_label_fr="Test",
            )

    def test_calibrated_threshold_carries_provenance(self):
        threshold = QuantileThresholdCalibrator().calibrate(
            observations=[float(i) for i in range(200)],
            risk_type=RiskType.HEAVY_RAIN,
            variable="precipitation_48h_mm",
            unit="mm",
            source_label_fr="Réanalyse ERA5",
            calibration_window="2015-2024",
        )
        assert threshold.origin == "calibrated"
        assert "percentiles" in threshold.provenance_fr
        assert "ERA5" in threshold.provenance_fr

    def test_no_colombian_threshold_leaks_into_moroccan_crops(self):
        """Garde-fou contre la reprise des valeurs de l'article.

        Table III de l'article, pour la ceinture caféière andine :
        déficit pluviométrique 48 h à 29,6 / 44,4 mm, anomalie thermique à
        1,9 / 2,0 °C.

        Le contrôle est fait **par grandeur** et non par simple coïncidence
        numérique : un seuil de gel absolu à 2,0 °C n'a rien à voir avec une
        anomalie de 2,0 °C. Un test purement numérique signalerait ce faux
        positif et finirait par être désactivé, ce qui ferait perdre la
        protection réelle.
        """
        colombian_precipitation = {29.6, 44.4}
        colombian_anomaly = {1.9, 2.0}

        for crop_code in ("TOMATE", "AGRUME", "OLIVE", "BLE", "BETTERAVE", "FRAISE"):
            for threshold in get_crop(crop_code).thresholds:
                bounds = {threshold.moderate_at, threshold.high_at, threshold.critical_at}

                if "precipitation" in threshold.variable:
                    assert not (bounds & colombian_precipitation), (
                        f"{crop_code}/{threshold.variable} reprend un seuil "
                        "pluviométrique de l'article colombien"
                    )
                if "anomaly" in threshold.variable:
                    assert not (bounds & colombian_anomaly), (
                        f"{crop_code}/{threshold.variable} reprend une anomalie "
                        "thermique de l'article colombien"
                    )

    def test_provisional_thresholds_declare_themselves_as_such(self):
        """Un seuil non calibré doit le dire, jusque dans l'interface."""
        threshold = get_crop("TOMATE").threshold_for(RiskType.HEAVY_RAIN)
        assert threshold.origin == "provisional_expert"
        assert "à valider par un agronome" in threshold.provenance_fr


class TestCropThresholds:
    def test_frost_threshold_reads_downward(self):
        frost = get_crop("TOMATE").threshold_for(RiskType.FROST)
        assert frost.direction == "below"
        assert frost.evaluate(10.0) is RiskLevel.LOW
        assert frost.evaluate(1.0) is RiskLevel.HIGH
        assert frost.evaluate(-2.0) is RiskLevel.CRITICAL

    def test_seasonal_sensitivity_peaks_during_harvest(self):
        tomato = get_crop("TOMATE")
        assert tomato.sensitivity_at(12) > tomato.sensitivity_at(7)

    def test_unknown_crop_fails_loudly(self):
        with pytest.raises(KeyError, match="Culture inconnue"):
            get_crop("QUINOA")


class TestRoadRisk:
    """Le risque routier se mesure autrement que le risque agronomique."""

    def test_short_intense_rain_is_flagged_even_with_small_total(self):
        # 12 mm/h pendant la traversée : dangereux, alors que le cumul absolu
        # resterait anodin pour une culture au champ.
        assert RAIN_INTENSITY.evaluate(12.0) is RiskLevel.HIGH

    def test_antecedent_saturation_is_separate_from_intensity(self):
        # Sols saturés sans pluie au moment du passage : risque réel de coupure.
        assert RAIN_ACCUMULATION.evaluate(70.0) is RiskLevel.HIGH
        assert RAIN_INTENSITY.evaluate(0.5) is RiskLevel.LOW

    def test_gust_threshold_reflects_heavy_vehicle_limits(self):
        assert WIND_GUST.evaluate(50.0) is RiskLevel.LOW
        assert WIND_GUST.evaluate(85.0) is RiskLevel.HIGH
        assert WIND_GUST.evaluate(110.0) is RiskLevel.CRITICAL

    def test_severity_saturates_at_critical(self):
        assert WIND_GUST.severity(200.0) == 1.0


class TestTransportCost:
    def test_reference_shipment_cost_is_plausible(self):
        # 180 t Agadir → Casablanca en frigorifique : ordre de grandeur attendu
        # autour de 40–45 kMAD pour le fret routier marocain.
        cost = estimate_cost(
            distance_km=542,
            duration_hours=5.1,
            volume_tonnes=180,
            mode=TransportMode.REFRIGERATED_TRUCK,
        )
        assert 40_000 < cost.total_mad < 48_000

    def test_truck_count_rounds_up(self):
        # 180 t en frigorifique (24 t/camion) : 7,5 → 8 camions.
        cost = estimate_cost(
            distance_km=100, duration_hours=1, volume_tonnes=180,
            mode=TransportMode.REFRIGERATED_TRUCK,
        )
        assert cost.trucks_required == 8

    def test_longer_trip_costs_more_for_refrigerated_goods(self):
        short = estimate_cost(
            distance_km=542, duration_hours=5.1, volume_tonnes=180,
            mode=TransportMode.REFRIGERATED_TRUCK,
        )
        long = estimate_cost(
            distance_km=542, duration_hours=8.0, volume_tonnes=180,
            mode=TransportMode.REFRIGERATED_TRUCK,
        )
        assert long.total_mad > short.total_mad

    def test_zero_volume_is_rejected(self):
        with pytest.raises(ValueError, match="volume"):
            estimate_cost(
                distance_km=100, duration_hours=1, volume_tonnes=0,
                mode=TransportMode.STANDARD_TRUCK,
            )


class TestOptimizationProfiles:
    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValueError, match="sommer à 1"):
            OptimizationWeights(risk=0.5, cost=0.5, duration=0.5, exposure=0.5)

    def test_perishable_weights_risk_above_cost(self):
        weights = get_profile("PERISSABLE_FROID").weights
        assert weights.risk > weights.cost

    def test_bulk_weights_cost_above_risk(self):
        weights = get_profile("VRAC_FAIBLE_MARGE").weights
        assert weights.cost > weights.risk

    def test_no_universal_weighting_is_shared(self):
        """Deux profils différents ne peuvent pas avoir la même pondération."""
        cold = get_profile("PERISSABLE_FROID").weights
        bulk = get_profile("VRAC_FAIBLE_MARGE").weights
        assert cold.model_dump() != bulk.model_dump()

    def test_unknown_profile_fails_loudly(self):
        with pytest.raises(KeyError, match="Profil d'optimisation inconnu"):
            get_profile("INEXISTANT")
