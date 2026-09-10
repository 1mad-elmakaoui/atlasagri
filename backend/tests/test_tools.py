"""Tests de la couche outils : validation, autorisation, isolation, honnêteté.

L'enjeu principal est de garantir que le modèle de langage ne peut ni franchir
une frontière d'organisation, ni faire enregistrer une décision portant sur une
option qui n'a jamais été évaluée.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("WEATHER_PROVIDER", "offline")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.tools.business_tools  # noqa: F401 - enregistre les outils
from app.core.security import RequestContext
from app.db.base import Recommendation, Shipment, Tenant, User
from app.db.seed import seed
from app.db.session import SessionLocal, create_all
from app.domain.enums import UserRole
from app.tools.registry import ToolContext, registry


@pytest.fixture(scope="module")
def database():
    create_all()
    session = SessionLocal()
    if session.query(Tenant).count() == 0:
        seed(session)
    yield session
    session.close()


def _context(database, *, slug: str, role: UserRole) -> ToolContext:
    tenant = database.query(Tenant).filter(Tenant.slug == slug).one()
    user = database.query(User).filter(User.tenant_id == tenant.id).first()
    return ToolContext(
        session=database,
        request=RequestContext(
            user_id=user.id, tenant_id=tenant.id, role=role, email=user.email
        ),
    )


@pytest.fixture
def souss(database):
    return _context(database, slug="souss-primeurs", role=UserRole.SUPPLY_CHAIN_MANAGER)


@pytest.fixture
def gharb(database):
    return _context(database, slug="gharb-agro", role=UserRole.SUPPLY_CHAIN_MANAGER)


class TestRegistry:
    def test_every_tool_declares_a_schema_and_a_french_description(self):
        for tool in registry.all():
            assert tool.description_fr.strip(), f"{tool.name} sans description"
            schema = tool.json_schema()
            assert schema.get("type") == "object", f"{tool.name} sans schéma d'objet"

    def test_tenant_is_never_a_model_supplied_parameter(self):
        """Le tenant vient du contexte authentifié, jamais du modèle.

        S'il devenait un paramètre d'entrée, Claude pourrait le renseigner et
        franchir la frontière entre organisations.
        """
        for tool in registry.all():
            properties = tool.json_schema().get("properties", {})
            forbidden = {"tenant_id", "tenant", "organisation_id", "user_id"}
            assert not (forbidden & set(properties)), (
                f"{tool.name} accepte un paramètre d'identité côté modèle"
            )

    def test_write_tools_are_restricted_to_deciders(self):
        for tool in registry.all():
            if not tool.read_only:
                assert tool.allowed_roles, (
                    f"{tool.name} écrit en base sans restriction de rôle"
                )

    def test_unknown_tool_is_reported_clearly(self):
        with pytest.raises(Exception, match="Outil inconnu"):
            registry.get("outil_qui_nexiste_pas")


class TestValidationAndAuthorization:
    async def test_invalid_input_is_refused_with_a_readable_message(self, souss):
        result = await registry.execute(
            "get_crop_risk", {"crop_code": "TOMATE", "horizon_hours": 9999}, souss
        )
        assert result["code"] == "donnees_invalides"
        assert "horizon_hours" in result["erreur"]

    async def test_analyst_cannot_create_a_recommendation(self, database):
        analyst = _context(database, slug="souss-primeurs", role=UserRole.ANALYST)
        result = await registry.execute(
            "create_recommendation",
            {
                "shipment_reference": "EXP-1842",
                "recommended_option_id": "plan-actuel",
                "title": "Tentative",
                "summary_fr": "Tentative",
            },
            analyst,
        )
        assert result["code"] == "acces_refuse"

    async def test_analyst_does_not_even_see_write_tools(self, database):
        analyst = _context(database, slug="souss-primeurs", role=UserRole.ANALYST)
        names = {spec["name"] for spec in registry.anthropic_specs(analyst.request)}
        assert "create_recommendation" not in names
        assert "get_route_risk" in names


class TestTenantIsolation:
    async def test_shipment_of_another_tenant_is_not_reachable(self, souss):
        """« GHB-0031 » appartient à Gharb Agro : Souss ne doit pas la voir."""
        result = await registry.execute(
            "get_shipment", {"shipment_reference": "GHB-0031"}, souss
        )
        assert result["code"] == "introuvable"

    async def test_each_tenant_sees_only_its_own_inventory(self, souss, gharb):
        souss_stock = await registry.execute("get_inventory_status", {}, souss)
        gharb_stock = await registry.execute("get_inventory_status", {}, gharb)

        souss_products = {row["produit"] for row in souss_stock["stocks"]}
        gharb_products = {row["produit"] for row in gharb_stock["stocks"]}

        assert souss_products and gharb_products
        assert not (souss_products & gharb_products)
        assert "Fraise du Loukkos" in gharb_products
        assert "Fraise du Loukkos" not in souss_products

    async def test_isolation_error_is_indistinguishable_from_absence(self, souss):
        """Confirmer l'existence d'une ressource d'un autre tenant serait déjà une fuite."""
        existing_other_tenant = await registry.execute(
            "get_shipment", {"shipment_reference": "GHB-0031"}, souss
        )
        never_existed = await registry.execute(
            "get_shipment", {"shipment_reference": "EXP-9999"}, souss
        )
        assert existing_other_tenant["code"] == never_existed["code"] == "introuvable"


class TestDecisionTools:
    async def test_route_risk_reports_exposed_segments_with_timing(self, souss):
        result = await registry.execute(
            "get_route_risk", {"shipment_reference": "EXP-1842"}, souss
        )
        assert "erreur" not in result
        assert result["reference"] == "EXP-1842"
        assert "passage_prevu" in str(result["troncons_exposes"])

    async def test_disruption_probability_carries_its_caveat(self, souss):
        result = await registry.execute(
            "get_route_risk", {"shipment_reference": "EXP-1842"}, souss
        )
        assert "pas calibré" in result["avertissement_probabilite_fr"]

    async def test_alternatives_are_ranked_and_rejects_are_explained(self, souss):
        result = await registry.execute(
            "generate_alternatives", {"shipment_reference": "EXP-1842"}, souss
        )
        assert result["options"], "Aucune option produite"
        assert result["options"][0]["rang"] == 1
        assert result["option_recommandee"]
        for rejected in result["options_ecartees"]:
            assert rejected["motifs_rejet"], "Une option écartée sans motif"

    async def test_simulated_data_is_declared(self, souss):
        result = await registry.execute(
            "get_route_risk", {"shipment_reference": "EXP-1842"}, souss
        )
        assert result["etat_des_donnees"] == "Simulé"

    async def test_satellite_declares_unavailability_instead_of_inventing_ndvi(self, souss):
        result = await registry.execute(
            "get_satellite_observation", {"region_code": "SOUSS_MASSA"}, souss
        )
        assert result["disponible"] is False
        assert result.get("ndvi") is None
        assert "Copernicus" in result["motif_fr"]

    async def test_recommendation_rejects_an_option_that_was_never_evaluated(self, souss):
        """Garde-fou central : le modèle ne peut pas faire enregistrer une option inventée."""
        result = await registry.execute(
            "create_recommendation",
            {
                "shipment_reference": "EXP-1842",
                "recommended_option_id": "itineraire-imaginaire",
                "title": "Test",
                "summary_fr": "Test",
            },
            souss,
        )
        assert result["code"] == "introuvable"
        assert "ne fait pas partie des options évaluées" in result["erreur"]

    async def test_recommendation_stores_engine_values_not_model_values(self, souss):
        alternatives = await registry.execute(
            "generate_alternatives", {"shipment_reference": "EXP-1842"}, souss
        )
        created = await registry.execute(
            "create_recommendation",
            {
                "shipment_reference": "EXP-1842",
                "recommended_option_id": alternatives["option_recommandee"],
                "title": "Décaler le départ",
                "summary_fr": "Résumé de la proposition",
            },
            souss,
        )
        assert created["statut"] == "Proposée"

        record = souss.session.get(Recommendation, created["recommandation_id"])
        assert record is not None
        # Les valeurs d'impact viennent du moteur : elles sont vérifiables.
        assert "probabilite_perturbation" in record.expected_impact
        assert record.alternatives_considered
        assert record.data_sources
        assert record.status == "PROPOSED", "Une recommandation ne doit jamais s'auto-appliquer"

    async def test_simulation_changes_nothing_in_the_database(self, souss):
        before = souss.session.query(Shipment).count()
        result = await registry.execute(
            "simulate_scenario",
            {"scenario": "route_indisponible", "shipment_reference": "EXP-1842"},
            souss,
        )
        assert "situation_actuelle" in result and "situation_simulee" in result
        assert souss.session.query(Shipment).count() == before

    async def test_unknown_scenario_lists_the_valid_ones(self, souss):
        result = await registry.execute(
            "simulate_scenario",
            {"scenario": "invasion_de_criquets", "shipment_reference": "EXP-1842"},
            souss,
        )
        assert "Scénarios disponibles" in result["erreur"]
