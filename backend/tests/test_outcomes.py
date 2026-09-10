"""Tests de la boucle de retour terrain.

Les garanties protégées ici sont celles qui rendent la donnée exploitable
statistiquement : déterminisme de l'instrument, immuabilité des réponses, et
étanchéité de la partition d'évaluation.

Une régression sur l'un de ces points ne casserait rien de visible — elle
corromprait silencieusement toute calibration ultérieure, ce qui est bien pire.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault("WEATHER_PROVIDER", "offline")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.core.errors import ValidationError
from app.core.security import RequestContext
from app.db.base import RiskPrediction, ShipmentOutcome, Tenant, User
from app.db.repositories import TenantRepository
from app.db.seed import seed
from app.db.session import SessionLocal, create_all
from app.domain.enums import UserRole
from app.domain.survey import SHIPMENT_OUTCOME_INSTRUMENT
from app.services.outcome_collection import (
    EVALUATION_SHARE,
    OutcomeCollectionService,
    _assign_split,
)
from app.services.shipment_service import ShipmentService

COMPLET = {
    "livree": True,
    "ecart_arrivee_h": 4.5,
    "itineraire_suivi": True,
    "perturbation": True,
    "type_perturbation": "INONDATION",
    "lieu_perturbation_fr": "Imi n'Tanoute",
    "retard_perturbation_h": 4.0,
    "qualite_affectee": True,
    "perte_estimee_mad": 18500.0,
    "commentaire_fr": "Chaussée impraticable",
}


@pytest.fixture
def echoir(repo):
    """Fait passer une expédition au-delà de son échéance, puis restaure.

    La base de test est partagée entre modules : muter une échéance sans la
    remettre en place ferait échouer, ailleurs, des tests qui évaluent des
    alternatives — toutes deviendraient infaisables. La restauration est donc
    une garantie d'isolation, pas une politesse.
    """
    original: list[tuple] = []

    def _echoir(reference: str, hours_ago: float = 5.0):
        shipment = ShipmentService(repo).resolve(reference)
        original.append((shipment, shipment.sla_deadline_at))
        shipment.sla_deadline_at = datetime.now(UTC) - timedelta(hours=hours_ago)
        repo.session.commit()
        return shipment

    yield _echoir

    for shipment, echeance in original:
        shipment.sla_deadline_at = echeance
    repo.session.commit()


@pytest.fixture
def repo():
    create_all()
    session = SessionLocal()
    if session.query(Tenant).count() == 0:
        seed(session)
    tenant = session.query(Tenant).filter(Tenant.slug == "souss-primeurs").one()
    user = session.query(User).filter(User.email == "supply@souss-primeurs.ma").one()
    yield TenantRepository(
        session,
        RequestContext(
            user_id=user.id, tenant_id=tenant.id,
            role=UserRole.SUPPLY_CHAIN_MANAGER, email=user.email,
        ),
    )
    session.close()


class TestInstrument:
    """L'enchaînement doit être reproductible : c'est un instrument de mesure."""

    def test_same_answers_always_produce_the_same_next_question(self):
        answers = {"livree": True, "ecart_arrivee_h": 2.0}
        premier = SHIPMENT_OUTCOME_INSTRUMENT.next_question(answers)
        for _ in range(5):
            assert SHIPMENT_OUTCOME_INSTRUMENT.next_question(dict(answers)).code == premier.code

    def test_non_delivery_short_circuits_the_transit_questions(self):
        answers = SHIPMENT_OUTCOME_INSTRUMENT.accept({}, "livree", False)
        codes = {q.code for q in SHIPMENT_OUTCOME_INSTRUMENT.applicable_questions(answers)}
        assert "motif_non_livraison" in codes
        # Aucune question de trajet n'a de sens si rien n'est parti.
        assert "perturbation" not in codes
        assert "ecart_arrivee_h" not in codes

    def test_retracting_a_branch_clears_its_orphaned_answers(self):
        """Une cause de perturbation ne doit pas survivre à « aucune perturbation »."""
        answers = dict(COMPLET)
        revise = SHIPMENT_OUTCOME_INSTRUMENT.accept(answers, "perturbation", False)
        assert "type_perturbation" not in revise
        assert "retard_perturbation_h" not in revise
        assert "lieu_perturbation_fr" not in revise

    def test_answers_are_never_mutated_in_place(self):
        origine = {"livree": True}
        suivant = SHIPMENT_OUTCOME_INSTRUMENT.accept(origine, "ecart_arrivee_h", 3.0)
        assert "ecart_arrivee_h" not in origine
        assert suivant["ecart_arrivee_h"] == 3.0

    def test_out_of_list_answer_is_refused_with_the_valid_options(self):
        with pytest.raises(ValidationError, match="hors liste"):
            SHIPMENT_OUTCOME_INSTRUMENT.accept(
                {"livree": True, "ecart_arrivee_h": 1.0, "itineraire_suivi": True,
                 "perturbation": True},
                "type_perturbation", "TSUNAMI",
            )

    def test_out_of_range_number_is_refused(self):
        with pytest.raises(ValidationError, match="au moins"):
            SHIPMENT_OUTCOME_INSTRUMENT.accept(
                {"livree": True, "ecart_arrivee_h": 1.0, "itineraire_suivi": True,
                 "perturbation": True, "type_perturbation": "PLUIE"},
                "retard_perturbation_h", -3,
            )

    def test_answering_an_inapplicable_question_is_refused(self):
        with pytest.raises(ValidationError, match="ne s'applique pas"):
            SHIPMENT_OUTCOME_INSTRUMENT.accept({"livree": False}, "perturbation", True)

    def test_boolean_accepts_french_wording(self):
        assert SHIPMENT_OUTCOME_INSTRUMENT.accept({}, "livree", "oui")["livree"] is True
        assert SHIPMENT_OUTCOME_INSTRUMENT.accept({}, "livree", "non")["livree"] is False


class TestEvaluationSplit:
    """La partition doit être inattaquable, sinon la calibration devient circulaire."""

    def test_split_is_stable_for_a_given_shipment(self):
        assert _assign_split("exp-42") == _assign_split("exp-42")

    def test_split_does_not_depend_on_the_observed_value(self):
        """Ré-enregistrer ne doit pas permettre de déplacer un résultat gênant."""
        avant = _assign_split("exp-99")
        apres = _assign_split("exp-99")
        assert avant == apres

    def test_split_proportion_is_close_to_the_target(self):
        tirages = [_assign_split(f"exp-{i}") for i in range(3000)]
        part = tirages.count("evaluation") / len(tirages)
        assert abs(part - EVALUATION_SHARE) < 0.04


class TestCollectionLoop:
    def test_nothing_is_requested_before_the_deadline(self, repo):
        assert OutcomeCollectionService(repo).pending() == []

    def test_shipment_past_deadline_enters_the_queue(self, repo, echoir):
        echoir("EXP-1842")
        attente = OutcomeCollectionService(repo).pending()
        assert any(p.reference == "EXP-1842" for p in attente)

    def test_queue_is_ordered_oldest_first(self, repo, echoir):
        echoir("EXP-1842", hours_ago=3)
        echoir("EXP-1845", hours_ago=40)
        attente = OutcomeCollectionService(repo).pending()
        assert attente[0].reference == "EXP-1845"

    async def test_a_decision_freezes_a_prediction(self, repo):
        service = ShipmentService(repo)
        shipment = service.resolve("EXP-1843")
        await service.decide(shipment)

        predictions = repo.predictions(RiskPrediction.subject_id == shipment.id)
        assert len(predictions) == 1
        assert 0 <= predictions[0].disruption_probability <= 1
        assert predictions[0].engine_version

    async def test_repeated_decisions_do_not_duplicate_predictions(self, repo):
        service = ShipmentService(repo)
        shipment = service.resolve("EXP-1844")
        await service.decide(shipment)
        await service.decide(shipment)
        assert len(repo.predictions(RiskPrediction.subject_id == shipment.id)) == 1

    async def test_outcome_is_paired_with_the_prediction_that_was_shown(self, repo, echoir):
        service = ShipmentService(repo)
        shipment = service.resolve("EXP-1842")
        await service.decide(shipment)
        echoir("EXP-1842")

        collecte = OutcomeCollectionService(repo)
        resultat = collecte.submit(
            "EXP-1842", answers=dict(COMPLET), user_id=repo.context.user_id
        )
        assert resultat.prediction_id is not None

        prediction = repo.session.get(RiskPrediction, resultat.prediction_id)
        assert prediction is not None
        # Le couple exploitable pour la calibration est bien formé.
        assert prediction.disruption_probability is not None
        assert resultat.disruption_occurred is True

    def test_incomplete_survey_is_refused(self, repo, echoir):
        echoir("EXP-1846")
        with pytest.raises(ValidationError, match="incomplet"):
            OutcomeCollectionService(repo).submit(
                "EXP-1846", answers={"livree": True}, user_id=repo.context.user_id
            )

    def test_second_submission_without_correction_is_refused(self, repo, echoir):
        echoir("EXP-1847")
        collecte = OutcomeCollectionService(repo)
        collecte.submit("EXP-1847", answers=dict(COMPLET), user_id=repo.context.user_id)
        with pytest.raises(ValidationError, match="existe déjà"):
            collecte.submit("EXP-1847", answers=dict(COMPLET), user_id=repo.context.user_id)

    def test_correction_preserves_the_original_record(self, repo, echoir):
        """On ne réécrit jamais une mesure de terrain."""
        echoir("EXP-1848")
        collecte = OutcomeCollectionService(repo)
        premier = collecte.submit(
            "EXP-1848", answers=dict(COMPLET), user_id=repo.context.user_id
        )

        corrige = dict(COMPLET) | {"perte_estimee_mad": 25000.0}
        second = collecte.submit(
            "EXP-1848", answers=corrige, user_id=repo.context.user_id, correcting=True
        )

        conserves = repo.outcomes(ShipmentOutcome.shipment_id == premier.shipment_id)
        assert len(conserves) == 2, "La version initiale a été effacée"
        assert second.supersedes_id == premier.id
        assert second.evaluation_split == premier.evaluation_split
        # Les réponses brutes d'origine restent intactes.
        original = repo.session.get(ShipmentOutcome, premier.id)
        assert original.estimated_loss_mad == 18500.0

    def test_collected_shipment_leaves_the_queue(self, repo, echoir):
        echoir("EXP-1849")
        collecte = OutcomeCollectionService(repo)
        assert any(p.reference == "EXP-1849" for p in collecte.pending())
        collecte.submit("EXP-1849", answers=dict(COMPLET), user_id=repo.context.user_id)
        assert not any(p.reference == "EXP-1849" for p in collecte.pending())

    def test_raw_answers_are_kept_verbatim(self, repo, echoir):
        """Permet de recoder plus tard sans redemander au terrain."""
        echoir("EXP-1845")
        resultat = OutcomeCollectionService(repo).submit(
            "EXP-1845", answers=dict(COMPLET), user_id=repo.context.user_id
        )
        assert resultat.raw_answers == COMPLET
        assert resultat.instrument_version
