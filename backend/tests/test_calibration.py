"""Tests de la calibration et de la mesure de fiabilité.

Ce que ces tests protègent avant tout, c'est le **refus de conclure** : une
calibration sur données simulées, un seuil agronomiquement absurde ou un score
sur échantillon insuffisant doivent être écartés, pas publiés.

Un produit qui affiche un chiffre faux avec assurance est plus dangereux qu'un
produit qui dit ne pas savoir.
"""

from __future__ import annotations

import os
import random

import pytest

os.environ.setdefault("WEATHER_PROVIDER", "offline")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.core.security import RequestContext
from app.db.base import Tenant, User
from app.db.repositories import TenantRepository
from app.db.seed import seed
from app.db.session import SessionLocal, create_all
from app.domain.enums import RiskType, UserRole
from app.domain.plausibility import envelope_for
from app.domain.thresholds import QuantileThresholdCalibrator
from app.services.calibration import (
    OPERATIONAL_QUANTILES,
    SimulatedHistoryError,
    ThresholdCalibrationService,
    _implausible,
)
from app.services.reliability import MINIMUM_SAMPLE, Pair, ReliabilityService


@pytest.fixture
def repo():
    create_all()
    session = SessionLocal()
    if session.query(Tenant).count() == 0:
        seed(session)
    tenant = session.query(Tenant).filter(Tenant.slug == "souss-primeurs").one()
    user = session.query(User).filter(User.tenant_id == tenant.id).first()
    yield TenantRepository(
        session,
        RequestContext(
            user_id=user.id, tenant_id=tenant.id, role=UserRole.ADMIN, email=user.email
        ),
    )
    session.close()


def _calibrate(observations, risk_type, variable, unit, direction="above"):
    low, high, critical = OPERATIONAL_QUANTILES
    return QuantileThresholdCalibrator(
        low_quantile=low, high_quantile=high, critical_quantile=critical
    ).calibrate(
        observations=observations, risk_type=risk_type, variable=variable,
        unit=unit, direction=direction, source_label_fr="ERA5",
        calibration_window="2016-2026",
    )


class TestOperationalQuantiles:
    def test_quantiles_target_the_tail_not_balanced_thirds(self):
        """L'article vise des catégories équilibrées ; un produit d'alerte non.

        Avec les 33ᵉ/67ᵉ percentiles de l'article, un tiers des journées serait
        classé « modéré » et un tiers « élevé ». L'exploitant recevrait des
        alertes en permanence et cesserait de les lire.
        """
        low, high, critical = OPERATIONAL_QUANTILES
        assert low >= 0.80
        assert high >= 0.90
        assert critical >= 0.95
        assert low < high < critical


class TestPlausibility:
    """La statistique produit une valeur ; encore faut-il qu'elle ait un sens."""

    def test_heat_threshold_is_kept_where_heat_actually_occurs(self):
        random.seed(11)
        marrakech = [random.gauss(29, 7) for _ in range(400)]
        seuil = _calibrate(marrakech, RiskType.HEAT_STRESS, "temperature_max_c", "°C")
        assert _implausible(seuil) is None
        assert seuil.moderate_at > 30

    def test_heat_threshold_is_rejected_on_a_temperate_coast(self):
        """Agadir ne connaît pas de stress thermique : ne pas en inventer un."""
        random.seed(11)
        agadir = [random.gauss(23.5, 2.5) for _ in range(400)]
        seuil = _calibrate(agadir, RiskType.HEAT_STRESS, "temperature_max_c", "°C")
        motif = _implausible(seuil)
        assert motif is not None
        assert "ne se produit probablement pas" in motif

    def test_frost_threshold_is_rejected_where_it_never_freezes(self):
        random.seed(11)
        littoral = [random.gauss(13, 3.5) for _ in range(400)]
        seuil = _calibrate(littoral, RiskType.FROST, "temperature_min_c", "°C", "below")
        assert _implausible(seuil) is not None

    def test_frost_threshold_is_kept_where_frost_occurs(self):
        random.seed(11)
        plateau = [random.gauss(6, 5) for _ in range(400)]
        seuil = _calibrate(plateau, RiskType.FROST, "temperature_min_c", "°C", "below")
        assert _implausible(seuil) is None
        assert seuil.high_at < 3

    def test_non_discriminating_threshold_is_rejected(self):
        """Trois bornes à un km/h d'écart ne séparent aucun niveau de risque."""
        random.seed(11)
        plates = [random.gauss(90, 0.3) for _ in range(400)]
        seuil = _calibrate(plates, RiskType.STRONG_WIND, "wind_gust_kmh", "km/h")
        motif = _implausible(seuil)
        assert motif is not None
        assert "trop rapprochées" in motif

    def test_realistic_gusts_are_kept(self):
        random.seed(11)
        rafales = [max(5, random.gammavariate(4, 9)) for _ in range(400)]
        seuil = _calibrate(rafales, RiskType.STRONG_WIND, "wind_gust_kmh", "km/h")
        assert _implausible(seuil) is None

    def test_every_envelope_declares_its_rationale(self):
        for risk_type in (
            RiskType.HEAVY_RAIN, RiskType.HEAT_STRESS, RiskType.FROST, RiskType.STRONG_WIND
        ):
            enveloppe = envelope_for(risk_type)
            assert enveloppe is not None
            assert enveloppe.rationale_fr.strip()


class TestSimulatedDataRefusal:
    async def test_calibration_on_simulated_history_is_refused(self, repo):
        """Un seuil calibré sur des données simulées ferait autorité à tort."""
        with pytest.raises(SimulatedHistoryError, match="démonstration simulé"):
            await ThresholdCalibrationService(repo).calibrate_region(
                "SOUSS_MASSA", years=2
            )

    async def test_refusal_names_the_remedy(self, repo):
        with pytest.raises(SimulatedHistoryError, match="openmeteo"):
            await ThresholdCalibrationService(repo).calibrate_region("SOUSS_MASSA", years=2)


class TestReliability:
    def _pairs(self, count: int, informative: bool) -> list[Pair]:
        random.seed(5)
        couples = []
        for _ in range(count):
            p = random.random()
            observe = random.random() < p
            couples.append(
                Pair("EXP", p if informative else 0.4, observe, 0.0, 0.0, False)
            )
        return couples

    def test_no_score_is_published_below_the_minimum_sample(self, repo):
        rapport = ReliabilityService(repo).evaluate()
        assert rapport["mesurable"] is False
        assert rapport["minimum_requis"] == MINIMUM_SAMPLE
        assert "non mesurable" in rapport["message_fr"]
        assert "score_de_brier" not in rapport

    def test_brier_rewards_an_informative_forecast(self):
        couples = self._pairs(400, informative=True)
        brier = sum((c.predicted - float(c.observed)) ** 2 for c in couples) / len(couples)
        taux = sum(1 for c in couples if c.observed) / len(couples)
        assert brier < taux * (1 - taux), "Un système informatif doit battre la référence"

    def test_brier_penalises_a_blind_forecast(self):
        couples = self._pairs(400, informative=False)
        brier = sum((c.predicted - float(c.observed)) ** 2 for c in couples) / len(couples)
        taux = sum(1 for c in couples if c.observed) / len(couples)
        assert brier >= taux * (1 - taux) * 0.98

    def test_avoided_loss_is_not_claimed_without_both_groups(self, repo):
        """La perte évitée est la mesure la plus facile à falsifier."""
        service = ReliabilityService(repo)
        pertes = service._losses(
            [Pair("EXP", 0.6, True, 2.0, 10_000.0, True) for _ in range(4)]
        )
        assert pertes["comparaison_possible"] is False
        assert "non quantifiable" in pertes["message_fr"]
        assert "ecart_par_expedition_mad" not in pertes

    def test_avoided_loss_is_reported_as_association_not_causation(self, repo):
        couples = (
            [Pair("A", 0.6, True, 2.0, 5_000.0, True) for _ in range(12)]
            + [Pair("B", 0.6, True, 6.0, 20_000.0, False) for _ in range(12)]
        )
        pertes = ReliabilityService(repo)._losses(couples)
        assert pertes["comparaison_possible"] is True
        assert pertes["ecart_par_expedition_mad"] == 15_000
        assert "effet causal démontré" in pertes["message_fr"]
