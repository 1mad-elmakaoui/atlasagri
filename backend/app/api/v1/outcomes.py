"""Collecte des résultats observés et fiabilité des prédictions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import current_context, rate_limit, tenant_repository
from app.core.security import RequestContext
from app.db.repositories import TenantRepository
from app.services.outcome_collection import OutcomeCollectionService

router = APIRouter(prefix="/resultats", tags=["Retour terrain"], dependencies=[Depends(rate_limit)])


class SurveyStep(BaseModel):
    shipment_reference: str
    answers: dict[str, Any] = Field(default_factory=dict)


class SurveyAnswer(SurveyStep):
    code: str
    value: Any = None


class SurveySubmission(SurveyStep):
    correcting: bool = False


@router.get("/en-attente")
def pending(
    limit: int = 20, repo: TenantRepository = Depends(tenant_repository)
) -> dict:
    """File de travail : expéditions à échéance sans retour terrain."""
    attente = OutcomeCollectionService(repo).pending(limit=limit)
    return {
        "en_attente": [
            {
                "shipment_id": p.shipment_id,
                "reference": p.reference,
                "produit": p.product_fr,
                "origine": p.origin_fr,
                "destination": p.destination_fr,
                "echeance": p.sla_deadline_at,
                "heures_depuis_echeance": p.hours_since_deadline,
                "probabilite_annoncee": p.predicted_probability,
                "niveau_annonce": p.predicted_level_fr,
            }
            for p in attente
        ],
        "nombre": len(attente),
    }


@router.post("/question")
def next_question(
    payload: SurveyStep, repo: TenantRepository = Depends(tenant_repository)
) -> dict:
    return OutcomeCollectionService(repo).step(
        payload.shipment_reference, answers=payload.answers
    )


@router.post("/reponse")
def answer(
    payload: SurveyAnswer, repo: TenantRepository = Depends(tenant_repository)
) -> dict:
    return OutcomeCollectionService(repo).answer(
        payload.shipment_reference,
        answers=payload.answers,
        code=payload.code,
        value=payload.value,
    )


@router.post("")
def submit(
    payload: SurveySubmission,
    repo: TenantRepository = Depends(tenant_repository),
    context: RequestContext = Depends(current_context),
) -> dict:
    resultat = OutcomeCollectionService(repo).submit(
        payload.shipment_reference,
        answers=payload.answers,
        user_id=context.user_id,
        correcting=payload.correcting,
    )
    return {
        "id": resultat.id,
        "reference": payload.shipment_reference,
        "partition": resultat.evaluation_split,
        "apparie_a_une_prediction": resultat.prediction_id is not None,
        "message_fr": (
            "Retour terrain enregistré. Merci : c'est cette donnée qui permet "
            "au système de se corriger."
        ),
    }


@router.get("/fiabilite")
def reliability(repo: TenantRepository = Depends(tenant_repository)) -> dict:
    """Confrontation des prédictions passées aux résultats observés.

    Mesurée sur la seule partition d'évaluation : mesurer sur les données ayant
    servi à calibrer produirait un score flatteur et faux.
    """
    from app.services.reliability import ReliabilityService

    return ReliabilityService(repo).evaluate()


@router.get("/calibration")
def calibration_status(repo: TenantRepository = Depends(tenant_repository)) -> dict:
    """Quels seuils reposent sur l'historique local, lesquels restent provisoires."""
    from app.domain.morocco import REGIONS

    rows = repo.calibrated_thresholds()
    return {
        "seuils": [
            {
                "region": REGIONS[r.region_code].name_fr
                if r.region_code in REGIONS
                else r.region_code,
                "region_code": r.region_code,
                "culture": r.crop_code,
                "variable": r.variable,
                "modere": round(r.moderate_at, 1),
                "eleve": round(r.high_at, 1),
                "critique": round(r.critical_at, 1) if r.critical_at is not None else None,
                "unite": r.unit,
                "fenetre": r.calibration_window,
                "echantillon": r.sample_size,
                "quantiles": [r.low_quantile, r.high_quantile],
                "calibre_le": r.calibrated_on,
            }
            for r in sorted(rows, key=lambda x: (x.region_code, x.crop_code or "", x.variable))
        ],
        "nombre": len(rows),
        "note_fr": (
            "Les couples région/culture absents de cette liste reposent sur des "
            "seuils provisoires, signalés comme tels sur chaque évaluation."
        ),
    }


@router.get("/historique")
def history(repo: TenantRepository = Depends(tenant_repository)) -> dict:
    """Résultats déjà recueillis, les plus récents d'abord."""
    from app.db.base import Shipment

    rows = sorted(repo.outcomes(), key=lambda r: r.collected_at, reverse=True)
    lignes = []
    for row in rows:
        shipment = repo.session.get(Shipment, row.shipment_id)
        lignes.append(
            {
                "id": row.id,
                "reference": shipment.reference if shipment else "—",
                "recueilli_le": row.collected_at,
                "partition": row.evaluation_split,
                "livree": row.delivered,
                "ecart_arrivee_h": row.arrival_delta_hours,
                "perturbation": row.disruption_occurred,
                "type_perturbation": row.disruption_type,
                "lieu": row.disruption_location,
                "retard_h": row.disruption_delay_hours,
                "qualite_affectee": row.quality_affected,
                "perte_mad": row.estimated_loss_mad,
                "commentaire": row.comment,
                "corrige_une_version_anterieure": row.supersedes_id is not None,
            }
        )
    return {"resultats": lignes, "nombre": len(lignes)}
