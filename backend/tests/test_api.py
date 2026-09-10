"""Tests de l'API : authentification, isolation, contrats de réponse.

Ils passent par le vrai routeur FastAPI, pas par les services directement :
c'est la seule façon de vérifier que l'authentification, l'autorisation et la
gestion des erreurs sont bien câblées.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("WEATHER_PROVIDER", "offline")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from fastapi.testclient import TestClient

from app.db.base import Tenant
from app.db.seed import DEMO_PASSWORD, seed
from app.db.session import SessionLocal, create_all
from app.main import app


@pytest.fixture(scope="module")
def client():
    create_all()
    with SessionLocal() as session:
        if session.query(Tenant).count() == 0:
            seed(session)
    with TestClient(app) as test_client:
        yield test_client


def _token(client, email: str) -> str:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def souss_headers(client):
    return {"Authorization": f"Bearer {_token(client, 'supply@souss-primeurs.ma')}"}


@pytest.fixture(scope="module")
def gharb_headers(client):
    return {"Authorization": f"Bearer {_token(client, 'supply@gharb-agro.ma')}"}


@pytest.fixture(scope="module")
def analyst_headers(client):
    return {"Authorization": f"Bearer {_token(client, 'analyste@souss-primeurs.ma')}"}


class TestAuthentication:
    def test_login_returns_a_token_and_the_user_profile(self, client):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "supply@souss-primeurs.ma", "password": DEMO_PASSWORD},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["access_token"]
        assert body["utilisateur"]["organisation"] == "Souss Primeurs Export"

    def test_wrong_password_and_unknown_account_are_indistinguishable(self, client):
        """Distinguer les deux cas permettrait d'énumérer les comptes existants."""
        wrong = client.post(
            "/api/v1/auth/login",
            json={"email": "supply@souss-primeurs.ma", "password": "mauvais"},
        )
        unknown = client.post(
            "/api/v1/auth/login",
            json={"email": "personne@nulle-part.ma", "password": "mauvais"},
        )
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["message_fr"] == unknown.json()["message_fr"]

    def test_protected_route_requires_a_token(self, client):
        assert client.get("/api/v1/tableau-de-bord").status_code == 401

    def test_forged_token_is_rejected(self, client):
        response = client.get(
            "/api/v1/expeditions", headers={"Authorization": "Bearer pas.un.jeton"}
        )
        assert response.status_code == 401


class TestTenantIsolationOverHttp:
    def test_each_tenant_sees_only_its_own_shipments(
        self, client, souss_headers, gharb_headers
    ):
        souss = client.get("/api/v1/expeditions", headers=souss_headers).json()
        gharb = client.get("/api/v1/expeditions", headers=gharb_headers).json()

        souss_refs = {s["reference"] for s in souss["expeditions"]}
        gharb_refs = {s["reference"] for s in gharb["expeditions"]}

        assert "EXP-1842" in souss_refs
        assert "GHB-0031" in gharb_refs
        assert not (souss_refs & gharb_refs)

    def test_direct_access_to_another_tenant_shipment_is_refused(
        self, client, souss_headers
    ):
        response = client.get("/api/v1/expeditions/GHB-0031", headers=souss_headers)
        assert response.status_code == 404

    def test_map_layers_of_another_tenant_are_refused(self, client, gharb_headers):
        response = client.get("/api/v1/carte/expedition/EXP-1842", headers=gharb_headers)
        assert response.status_code == 404


class TestDashboard:
    def test_overview_exposes_the_decision_essentials(self, client, souss_headers):
        body = client.get("/api/v1/tableau-de-bord", headers=souss_headers).json()
        assert "risque_global" in body
        assert "expeditions_exposees" in body
        assert body["indicateurs"]["expeditions_suivies"] > 0
        assert body["sources"], "L'état des sources externes doit être exposé"

    def test_global_risk_is_the_worst_case_not_an_average(self, client, souss_headers):
        """Une moyenne masquerait l'expédition critique qu'il faut justement voir."""
        body = client.get("/api/v1/tableau-de-bord", headers=souss_headers).json()
        exposed = body["expeditions_exposees"]
        if exposed:
            assert body["risque_global"]["niveau"] == exposed[0]["niveau_risque"]

    def test_regions_report_data_state(self, client, souss_headers):
        body = client.get("/api/v1/tableau-de-bord/regions", headers=souss_headers).json()
        assert body["regions"]
        assert any(r["etat_des_donnees"] == "Simulé" for r in body["regions"])


class TestShipmentDecision:
    def test_detail_returns_ranked_alternatives_and_a_recommendation(
        self, client, souss_headers
    ):
        body = client.get("/api/v1/expeditions/EXP-1842", headers=souss_headers).json()
        assert body["expedition"]["reference"] == "EXP-1842"
        assert body["alternatives"]
        assert body["recommandation"] is not None
        assert body["recommandation"]["recommandee"] is True
        assert body["profil_optimisation"]["ponderations"]

    def test_every_alternative_exposes_its_tradeoffs_and_sources(
        self, client, souss_headers
    ):
        body = client.get("/api/v1/expeditions/EXP-1842", headers=souss_headers).json()
        recommended = body["recommandation"]
        assert recommended["raisons_fr"], "Une recommandation sans raison n'est pas explicable"
        assert recommended["sources"], "Une recommandation sans source n'est pas auditable"
        assert recommended["criteres"], "Les critères de classement doivent être visibles"

    def test_rejected_alternatives_carry_their_reason(self, client, souss_headers):
        body = client.get("/api/v1/expeditions/EXP-1842", headers=souss_headers).json()
        for rejected in body["alternatives_ecartees"]:
            assert rejected["motifs_rejet_fr"]

    def test_map_layers_mark_the_recommended_route(self, client, souss_headers):
        body = client.get("/api/v1/carte/expedition/EXP-1842", headers=souss_headers).json()
        routes = body["itineraires"]["features"]
        assert routes
        assert any(f["properties"]["recommandee"] for f in routes)
        assert all(f["geometry"]["type"] == "LineString" for f in routes)
        assert body["legende"]

    def test_exposed_segments_are_a_separate_layer(self, client, souss_headers):
        body = client.get("/api/v1/carte/expedition/EXP-1842", headers=souss_headers).json()
        for feature in body["troncons_exposes"]["features"]:
            assert feature["properties"]["motifs_fr"]
            assert "passage_prevu_fr" in feature["properties"]


class TestHumanInTheLoop:
    def test_analyst_cannot_decide_on_a_recommendation(
        self, client, souss_headers, analyst_headers
    ):
        listing = client.get("/api/v1/recommandations", headers=souss_headers).json()
        if not listing["recommandations"]:
            pytest.skip("Aucune recommandation enregistrée pour ce test")
        target = listing["recommandations"][0]["id"]
        response = client.post(
            f"/api/v1/recommandations/{target}/decision",
            json={"decision": "ACCEPTED"},
            headers=analyst_headers,
        )
        assert response.status_code == 403

    def test_invalid_decision_lists_the_valid_values(self, client, souss_headers):
        response = client.post(
            "/api/v1/recommandations/inexistante/decision",
            json={"decision": "PEUT_ETRE"},
            headers=souss_headers,
        )
        assert response.status_code == 422
        assert "ACCEPTED" in response.json()["message_fr"]


class TestSimulation:
    def test_scenarios_are_listed(self, client, souss_headers):
        body = client.get("/api/v1/simulations/scenarios", headers=souss_headers).json()
        codes = {s["code"] for s in body["scenarios"]}
        assert {"retard_fournisseur", "route_indisponible", "depart_retarde"} <= codes

    def test_simulation_compares_before_and_after(self, client, souss_headers):
        response = client.post(
            "/api/v1/simulations",
            json={"scenario": "route_indisponible", "shipment_reference": "EXP-1842"},
            headers=souss_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["situation_actuelle"] and body["situation_simulee"]
        assert body["evolution_fr"]
        assert "aucune donnée n'a été modifiée" in body["avertissement_fr"]

    def test_unknown_scenario_is_refused(self, client, souss_headers):
        response = client.post(
            "/api/v1/simulations",
            json={"scenario": "invasion", "shipment_reference": "EXP-1842"},
            headers=souss_headers,
        )
        assert response.status_code == 422


class TestCopilotHonesty:
    def test_copilot_declares_itself_disabled_without_a_key(self, client, souss_headers):
        """Sans clé, le copilote est désactivé — jamais simulé."""
        body = client.get("/api/v1/copilote/etat", headers=souss_headers).json()
        if not body["disponible"]:
            assert "n'est pas configuré" in body["message_fr"]
            assert "restent entièrement disponibles" in body["message_fr"]

    def test_asking_without_a_key_fails_explicitly(self, client, souss_headers):
        state = client.get("/api/v1/copilote/etat", headers=souss_headers).json()
        if state["disponible"]:
            pytest.skip("Une clé est configurée : le cas désactivé n'est pas testable")
        response = client.post(
            "/api/v1/copilote/question",
            json={"question": "Mon transport de tomates est-il à risque ?"},
            headers=souss_headers,
        )
        assert response.status_code == 503
        assert "copilote" in response.json()["message_fr"].lower()


class TestHealth:
    def test_health_reports_provider_state(self, client):
        body = client.get("/api/sante").json()
        assert body["statut"] == "operationnel"
        assert body["sources"]
        # Le mode démonstration doit être visible, pas dissimulé.
        assert any("simul" in s["message_fr"].lower() for s in body["sources"])
