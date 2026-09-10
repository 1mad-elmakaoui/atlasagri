"""Tests de l'adaptateur Copernicus.

Ce qui est protégé ici n'est pas la connexion au service — elle ne peut pas
être rejouée hors ligne — mais la **règle qui décide si une acquisition est
exploitable**. C'est là que se situe le risque produit : un NDVI mesuré sous
une couverture nuageuse a l'apparence d'une valeur normale et n'en est pas une.
Le laisser passer afficherait une parcelle en bonne santé alors que le
satellite n'a vu que le sommet des nuages.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("WEATHER_PROVIDER", "offline")

from app.core.errors import FeatureDisabledError
from app.providers.satellite.copernicus import (
    MINIMUM_VALID_FRACTION,
    CopernicusSatelliteProvider,
    _latest_usable,
)
from app.providers.satellite.providers import UnavailableSatelliteProvider


def _acquisition(
    jour: str, ndvi: float, ndwi: float, *, pixels: int = 12100, sans_donnee: int = 0
) -> dict:
    """Un intervalle tel que le renvoie l'API statistique Sentinel Hub."""
    stats = {"mean": ndvi, "sampleCount": pixels, "noDataCount": sans_donnee}
    stats_ndwi = {"mean": ndwi, "sampleCount": pixels, "noDataCount": sans_donnee}
    return {
        "interval": {"from": f"{jour}T00:00:00Z", "to": f"{jour}T23:59:59Z"},
        "outputs": {
            "ndvi": {"bands": {"B0": {"stats": stats}}},
            "ndwi": {"bands": {"B0": {"stats": stats_ndwi}}},
        },
    }


class TestSelectionAcquisition:
    def test_the_most_recent_usable_acquisition_wins(self):
        payload = {
            "data": [
                _acquisition("2026-08-10", 0.58, -0.21, sans_donnee=400),
                _acquisition("2026-08-15", 0.62, -0.18, sans_donnee=1450),
            ]
        }
        retenu = _latest_usable(payload)
        assert str(retenu["date"]) == "2026-08-15"
        assert retenu["ndvi"] == pytest.approx(0.62)

    def test_a_cloudy_acquisition_is_skipped_not_averaged(self):
        """Le cas qui compte : la plus récente est inexploitable.

        On redescend vers une acquisition plus ancienne mais dégagée, plutôt
        que de moyenner les deux — une moyenne mélangerait une mesure du sol
        avec une mesure de nuage.
        """
        payload = {
            "data": [
                # 90 % de pixels masqués : sous le seuil d'exploitabilité.
                _acquisition("2026-08-20", 0.31, -0.05, sans_donnee=10_890),
                _acquisition("2026-08-15", 0.62, -0.18, sans_donnee=1_450),
            ]
        }
        retenu = _latest_usable(payload)
        assert str(retenu["date"]) == "2026-08-15"
        assert retenu["valid_fraction"] > MINIMUM_VALID_FRACTION

    def test_fully_clouded_window_returns_nothing(self):
        payload = {"data": [_acquisition("2026-08-20", 0.30, 0.0, pixels=100, sans_donnee=95)]}
        assert _latest_usable(payload) is None

    def test_interval_without_satellite_pass_is_ignored(self):
        """Les jours sans passage satellite arrivent avec des sorties vides."""
        payload = {
            "data": [
                {"interval": {"from": "2026-08-19T00:00:00Z"}, "outputs": {}},
                _acquisition("2026-08-12", 0.55, -0.20, pixels=100, sans_donnee=5),
            ]
        }
        assert str(_latest_usable(payload)["date"]) == "2026-08-12"

    def test_empty_payload_returns_nothing(self):
        assert _latest_usable({}) is None
        assert _latest_usable({"data": []}) is None


class TestConfiguration:
    def test_provider_refuses_to_start_without_credentials(self):
        """Sans identifiants, on échoue à la construction.

        Construire un fournisseur qui échouera à chaque appel reporterait
        l'erreur au moment le plus coûteux : devant l'utilisateur.
        """
        with pytest.raises(FeatureDisabledError):
            CopernicusSatelliteProvider()

    async def test_default_provider_states_unavailability_with_remediation(self):
        from app.domain.geo import Coordinates

        reponse = await UnavailableSatelliteProvider().get_vegetation(
            Coordinates(latitude=30.42, longitude=-9.60)
        )
        assert reponse.available is False
        assert "COPERNICUS_CLIENT_ID" in reponse.remediation_fr


class TestTraductionEnObservation:
    """Le chemin complet, sans réseau : réponse brute → observation exploitable.

    L'échange HTTP lui-même n'est pas rejouable hors ligne. Ce qui est testé
    ici, c'est la traduction — la partie où une erreur passerait inaperçue en
    production parce que la valeur produite resterait plausible.
    """

    def _provider(self, monkeypatch) -> CopernicusSatelliteProvider:
        from app.core.config import settings

        monkeypatch.setattr(settings, "copernicus_client_id", "identifiant-de-test")
        monkeypatch.setattr(settings, "copernicus_client_secret", "secret-de-test")
        return CopernicusSatelliteProvider()

    async def test_payload_becomes_a_dated_derived_observation(self, monkeypatch):
        from app.domain.enums import DataState
        from app.domain.geo import Coordinates
        from app.providers.satellite.interface import VegetationObservation

        provider = self._provider(monkeypatch)

        async def _reponse(*_args, **_kwargs):
            return {
                "data": [
                    _acquisition("2026-08-20", 0.28, -0.02, sans_donnee=11_500),
                    _acquisition("2026-08-15", 0.64, -0.17, sans_donnee=1_210),
                ]
            }

        monkeypatch.setattr(provider, "_statistics", _reponse)
        observation = await provider.get_vegetation(
            Coordinates(latitude=30.42, longitude=-9.60)
        )

        assert isinstance(observation, VegetationObservation)
        assert observation.acquired_on.isoformat() == "2026-08-15"
        assert observation.ndvi == 0.64
        # NDVI est calculé à partir de bandes mesurées : dérivé, pas observé.
        assert observation.state is DataState.DERIVED
        assert observation.masked_share_percent == pytest.approx(10.0, abs=0.5)
        assert observation.source.label_fr.startswith("Copernicus")

    async def test_unreachable_service_degrades_without_inventing_a_value(
        self, monkeypatch
    ):
        from app.core.errors import ProviderUnavailableError
        from app.domain.geo import Coordinates
        from app.providers.satellite.interface import SatelliteAvailability

        provider = self._provider(monkeypatch)

        async def _panne(*_args, **_kwargs):
            raise ProviderUnavailableError("API statistique Copernicus injoignable.")

        monkeypatch.setattr(provider, "_statistics", _panne)
        reponse = await provider.get_vegetation(
            Coordinates(latitude=30.42, longitude=-9.60)
        )

        assert isinstance(reponse, SatelliteAvailability)
        assert reponse.available is False
        assert "injoignable" in reponse.reason_fr
