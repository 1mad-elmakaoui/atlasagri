"""Agent de collecte des résultats observés.

Ferme la boucle du produit. Sans lui, AtlasAgri annonce des probabilités de
perturbation sans jamais apprendre si elles se sont vérifiées : le système
prédit en permanence et ne se corrige jamais.

Conception reprise de l'article d'Ahmadi et al. : administration
conversationnelle *stateful*, logique d'interaction prédéfinie, capture
structurée des réponses. L'article précise que la « collecte active » désigne
cette administration à état, et non de l'apprentissage actif ou de
l'échantillonnage adaptatif — la même définition s'applique ici.

Le modèle de langage n'administre pas le questionnaire : l'enchaînement est
déterministe (cf. `domain/survey.py`). L'agent conversationnel est la couche qui
tient la session, valide, et écrit un enregistrement immuable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.core.untrusted import sanitize_user_text
from app.db.base import RiskPrediction, Shipment, ShipmentOutcome
from app.db.repositories import TenantRepository
from app.domain.enums import ShipmentStatus
from app.domain.survey import SHIPMENT_OUTCOME_INSTRUMENT, SurveyInstrument
from app.services.shipment_service import ShipmentService

logger = get_logger(__name__)

INSTRUMENT_VERSION = "resultat-expedition-1.0.0"

# Proportion réservée à l'évaluation. 20 % est un compromis courant : assez pour
# mesurer, assez peu pour ne pas priver la calibration de données.
EVALUATION_SHARE = 0.20

# Délai après l'échéance avant de solliciter le terrain. Trop tôt, le camion
# roule encore ; trop tard, les détails se perdent.
COLLECTION_DELAY_HOURS = 2.0


@dataclass(frozen=True)
class PendingCollection:
    """Expédition dont le résultat reste à recueillir."""

    shipment_id: str
    reference: str
    product_fr: str
    origin_fr: str
    destination_fr: str
    sla_deadline_at: datetime
    hours_since_deadline: float
    predicted_probability: float | None
    predicted_level_fr: str | None


class OutcomeCollectionService:
    """Gère les sessions de collecte et écrit les résultats observés."""

    def __init__(
        self,
        repo: TenantRepository,
        *,
        instrument: SurveyInstrument = SHIPMENT_OUTCOME_INSTRUMENT,
    ) -> None:
        self.repo = repo
        self.instrument = instrument
        self.shipments = ShipmentService(repo)

    # --- sollicitation ---

    def pending(self, *, limit: int = 20) -> list[PendingCollection]:
        """Expéditions arrivées à échéance et sans résultat enregistré.

        C'est la file de travail du terrain. Elle est ordonnée par ancienneté :
        un souvenir de trois jours vaut moins qu'un souvenir de trois heures.
        """
        now = datetime.now(UTC)
        deja_collectees = {
            row.shipment_id
            for row in self.repo.outcomes()
            if row.supersedes_id is None or True  # toute réponse existante suffit
        }

        attente: list[PendingCollection] = []
        for shipment in self.repo.shipments():
            if shipment.id in deja_collectees:
                continue
            if shipment.status == ShipmentStatus.CANCELLED.value:
                continue

            echeance = _as_utc(shipment.sla_deadline_at)
            ecoule = (now - echeance).total_seconds() / 3600
            if ecoule < COLLECTION_DELAY_HOURS:
                continue

            prediction = self._latest_prediction(shipment.id)
            contexte = self.shipments.build_context(shipment)
            attente.append(
                PendingCollection(
                    shipment_id=shipment.id,
                    reference=shipment.reference,
                    product_fr=contexte.product_name_fr,
                    origin_fr=contexte.origin_name_fr,
                    destination_fr=contexte.destination_name_fr,
                    sla_deadline_at=echeance,
                    hours_since_deadline=round(ecoule, 1),
                    predicted_probability=(
                        prediction.disruption_probability if prediction else None
                    ),
                    predicted_level_fr=prediction.risk_level if prediction else None,
                )
            )

        attente.sort(key=lambda p: p.hours_since_deadline, reverse=True)
        return attente[:limit]

    # --- session ---

    def start(self, reference: str) -> dict[str, Any]:
        shipment = self.shipments.resolve(reference)
        existing = self._existing_outcome(shipment.id)
        if existing is not None:
            raise ValidationError(
                f"Un résultat a déjà été enregistré pour {shipment.reference} le "
                f"{existing.collected_at:%d/%m/%Y à %H:%M}. "
                "Pour le corriger, enregistrer une nouvelle version : "
                "les réponses initiales ne sont jamais modifiées."
            )
        return self.step(reference, answers={})

    def step(self, reference: str, *, answers: dict[str, Any]) -> dict[str, Any]:
        """Renvoie la question suivante compte tenu des réponses déjà données."""
        shipment = self.shipments.resolve(reference)
        question = self.instrument.next_question(answers)
        repondues, total = self.instrument.progress(answers)

        return {
            "reference": shipment.reference,
            "termine": question is None,
            "question": question.as_payload() if question else None,
            "progression": {"repondues": repondues, "total": total},
            "reponses": answers,
        }

    def answer(
        self, reference: str, *, answers: dict[str, Any], code: str, value: Any
    ) -> dict[str, Any]:
        if isinstance(value, str):
            value = sanitize_user_text(value, max_length=1000)
        mises_a_jour = self.instrument.accept(answers, code, value)
        return self.step(reference, answers=mises_a_jour)

    # --- écriture ---

    def submit(
        self, reference: str, *, answers: dict[str, Any], user_id: str, correcting: bool = False
    ) -> ShipmentOutcome:
        """Enregistre un résultat observé.

        Refuse un questionnaire incomplet : un enregistrement partiel entrerait
        dans la calibration avec des champs manquants qu'il faudrait ensuite
        traiter comme des absences de perturbation, ce qui biaiserait le résultat
        dans le sens rassurant.
        """
        shipment = self.shipments.resolve(reference)

        manquante = self.instrument.next_question(answers)
        if manquante is not None:
            raise ValidationError(
                f"Questionnaire incomplet : « {manquante.text_fr} » reste sans réponse."
            )

        existante = self._existing_outcome(shipment.id)
        if existante is not None and not correcting:
            raise ValidationError(
                f"Un résultat existe déjà pour {shipment.reference}. "
                "Utiliser la correction pour en enregistrer une nouvelle version."
            )

        prediction = self._latest_prediction(shipment.id)
        # La partition est figée ici, à l'écriture, et dérivée de l'identifiant
        # de l'expédition : elle ne peut donc pas être rejouée pour faire
        # basculer un résultat gênant du côté calibration.
        split = _assign_split(shipment.id)

        resultat = ShipmentOutcome(
            tenant_id=self.repo.context.tenant_id,
            shipment_id=shipment.id,
            prediction_id=prediction.id if prediction else None,
            supersedes_id=existante.id if existante else None,
            collected_by_user_id=user_id,
            evaluation_split=split,
            delivered=bool(answers["livree"]),
            non_delivery_reason=answers.get("motif_non_livraison"),
            arrival_delta_hours=answers.get("ecart_arrivee_h"),
            planned_route_followed=answers.get("itineraire_suivi"),
            actual_route_note=answers.get("itineraire_reel_fr"),
            disruption_occurred=answers.get("perturbation"),
            disruption_type=answers.get("type_perturbation"),
            disruption_location=answers.get("lieu_perturbation_fr"),
            disruption_delay_hours=answers.get("retard_perturbation_h"),
            quality_affected=answers.get("qualite_affectee"),
            estimated_loss_mad=answers.get("perte_estimee_mad"),
            comment=answers.get("commentaire_fr"),
            raw_answers=dict(answers),
            instrument_version=INSTRUMENT_VERSION,
        )
        self.repo.add(resultat)
        self.repo.record_audit(
            action="outcome:submit",
            resource_type="SHIPMENT_OUTCOME",
            resource_id=resultat.id,
            details={
                "reference": shipment.reference,
                "partition": split,
                "perturbation": answers.get("perturbation"),
                "correction": correcting,
            },
        )
        self.repo.session.commit()

        logger.info(
            "Résultat d'expédition enregistré",
            context={
                "reference": shipment.reference,
                "partition": split,
                "apparie_a_une_prediction": prediction is not None,
            },
        )
        return resultat

    # --- accès ---

    def _existing_outcome(self, shipment_id: str) -> ShipmentOutcome | None:
        """Version la plus récente, les corrections faisant chaîne."""
        rows = self.repo.outcomes(ShipmentOutcome.shipment_id == shipment_id)
        return max(rows, key=lambda r: r.collected_at) if rows else None

    def _latest_prediction(self, shipment_id: str) -> RiskPrediction | None:
        rows = self.repo.predictions(RiskPrediction.subject_id == shipment_id)
        return max(rows, key=lambda r: r.predicted_at) if rows else None


def _assign_split(shipment_id: str) -> str:
    """Partition déterministe et stable.

    Dérivée d'un condensé de l'identifiant : la même expédition tombe toujours
    du même côté, et la partition ne dépend ni de l'ordre de collecte ni de la
    valeur observée. Tirer au sort à chaque écriture permettrait, en
    ré-enregistrant, de déplacer un résultat vers la partition souhaitée.
    """
    digest = hashlib.sha256(f"split:{shipment_id}".encode()).digest()
    tirage = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return "evaluation" if tirage < EVALUATION_SHARE else "calibration"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def record_prediction(
    repo: TenantRepository,
    *,
    shipment: Shipment,
    reference: str,
    probability: float,
    risk_level: str,
    risk_score: float,
    confidence: str,
    data_state: str,
    route_nodes: list[str],
    exposed_segments: list[dict[str, Any]],
    recommended_option_id: str | None,
    recommended_option_label: str | None,
    engine_version: str,
    minimum_interval_hours: float = 6.0,
) -> RiskPrediction | None:
    """Fige une prédiction, en évitant les doublons rapprochés.

    Chaque consultation du tableau de bord recalcule le risque. Tout persister
    saturerait la table sans rien apprendre de plus : deux prédictions à dix
    minutes d'intervalle portent la même information. On n'écrit donc qu'au-delà
    d'un intervalle minimal, ce qui conserve l'évolution utile — celle qui va du
    signal lointain à la veille du départ.
    """
    now = datetime.now(UTC)
    recentes = repo.predictions(RiskPrediction.subject_id == shipment.id)
    if recentes:
        derniere = max(recentes, key=lambda r: r.predicted_at)
        age = (now - _as_utc(derniere.predicted_at)).total_seconds() / 3600
        if age < minimum_interval_hours:
            return None

    horizon = (_as_utc(shipment.departure_at) - now).total_seconds() / 3600
    prediction = RiskPrediction(
        tenant_id=repo.context.tenant_id,
        subject_type="SHIPMENT",
        subject_id=shipment.id,
        subject_reference=reference,
        horizon_hours=round(horizon, 2),
        disruption_probability=probability,
        risk_level=risk_level,
        risk_score=risk_score,
        confidence=confidence,
        data_state=data_state,
        plan_route_nodes=route_nodes,
        exposed_segments=exposed_segments,
        recommended_option_id=recommended_option_id,
        recommended_option_label=recommended_option_label,
        engine_version=engine_version,
    )
    repo.add(prediction)
    return prediction
