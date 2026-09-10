"""Copilote IA.

Le copilote est une interface en langage naturel vers le moteur de décision, pas
le produit lui-même. Il n'est jamais simulé : sans clé d'API, l'endpoint répond
qu'il est désactivé, et le reste de l'application continue de fonctionner.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agent.service import AgentService
from app.api.deps import agent_rate_limit, current_context, db_session, tenant_repository
from app.api.schemas import AgentQuestion, AgentResponse
from app.core.config import settings
from app.core.security import RequestContext
from app.core.untrusted import sanitize_user_text
from app.db.base import AgentConversation
from app.db.repositories import TenantRepository

router = APIRouter(prefix="/copilote", tags=["Copilote IA"])


@router.get("/etat")
def status() -> dict:
    """État du copilote, pour que l'interface l'affiche honnêtement."""
    if settings.agent_enabled:
        return {
            "disponible": True,
            "modele": settings.anthropic_model,
            "message_fr": "Le copilote est opérationnel.",
        }
    return {
        "disponible": False,
        "modele": None,
        "message_fr": (
            "Le copilote IA n'est pas configuré sur cette installation "
            "(aucune clé d'API Anthropic renseignée). Les analyses de risque, "
            "les itinéraires, les alternatives et les simulations restent "
            "entièrement disponibles."
        ),
    }


@router.post("/question", response_model=AgentResponse, dependencies=[Depends(agent_rate_limit)])
async def ask(
    payload: AgentQuestion,
    session: Session = Depends(db_session),
    context: RequestContext = Depends(current_context),
    repo: TenantRepository = Depends(tenant_repository),
) -> AgentResponse:
    agent = AgentService(session, context)

    conversation = None
    history: list[dict] = []
    if payload.conversation_id:
        conversation = repo.get(AgentConversation, payload.conversation_id)
        # On ne rejoue que les échanges textuels : réinjecter d'anciens blocs
        # d'appel d'outils ferait référence à des identifiants expirés.
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in conversation.messages
            if isinstance(m.get("content"), str)
        ][-8:]

    answer = await agent.ask(payload.question, history=history)

    if conversation is None:
        conversation = AgentConversation(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            title=sanitize_user_text(payload.question, max_length=120),
            messages=[],
        )
        repo.add(conversation)

    conversation.messages = [
        *conversation.messages,
        {"role": "user", "content": sanitize_user_text(payload.question)},
        {"role": "assistant", "content": answer.text_fr},
    ]
    repo.record_audit(
        action="agent:question",
        resource_type="AGENT_CONVERSATION",
        resource_id=conversation.id,
        details={
            "outils": [t.name for t in answer.tool_calls],
            "iterations": answer.iterations,
        },
    )
    session.commit()

    return AgentResponse(
        reponse_fr=answer.text_fr,
        conclusion=answer.structured,
        outils_utilises=[
            {
                "outil": trace.name,
                "parametres": trace.input,
                "reussi": trace.succeeded,
                "erreur_fr": trace.error_fr,
            }
            for trace in answer.tool_calls
        ],
        iterations=answer.iterations,
        modele=answer.model,
        conversation_id=conversation.id,
    )


@router.get("/conversations")
def conversations(repo: TenantRepository = Depends(tenant_repository)) -> dict:
    rows = sorted(repo.conversations(), key=lambda c: c.updated_at, reverse=True)
    return {
        "conversations": [
            {
                "id": c.id,
                "titre": c.title,
                "messages": c.messages,
                "mis_a_jour_le": c.updated_at,
            }
            for c in rows[:20]
        ]
    }
