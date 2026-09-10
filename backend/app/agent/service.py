"""Agent Claude : boucle d'appel d'outils bornée.

L'API Anthropic réelle est utilisée. Le modèle n'est **jamais** simulé : sans
clé, le copilote est désactivé et l'interface l'annonce explicitement. Une
réponse fabriquée localement pour « faire fonctionner la démo » donnerait une
fausse idée de ce que le produit sait faire.

La boucle est bornée (`AGENT_MAX_TOOL_ITERATIONS`). Un agent laissé libre de
boucler consomme du budget et de la latence sans garantie de converger.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from anthropic import APIError, AsyncAnthropic
from sqlalchemy.orm import Session

from app.agent.prompt import RESPONSE_FORMAT_INSTRUCTION, SYSTEM_PROMPT
from app.core.config import settings
from app.core.errors import FeatureDisabledError, ProviderUnavailableError
from app.core.logging import get_logger
from app.core.security import RequestContext
from app.core.untrusted import sanitize_user_text
from app.tools.registry import ToolContext, registry

logger = get_logger(__name__)

# Empêche une réponse d'outil volumineuse de saturer la fenêtre de contexte.
MAX_TOOL_RESULT_CHARS = 12_000

STRUCTURED_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class ToolCallTrace:
    """Trace d'un appel d'outil, restituée à l'interface.

    Sert le panneau « Sources et preuves » : l'utilisateur peut voir quelles
    capacités ont été mobilisées, sans lire de log technique.
    """

    name: str
    input: dict[str, Any]
    succeeded: bool
    error_fr: str | None = None


@dataclass
class AgentAnswer:
    text_fr: str
    structured: dict[str, Any] | None
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    iterations: int = 0
    model: str = ""
    stop_reason: str | None = None


class AgentService:
    """Orchestre Claude au-dessus du registre d'outils."""

    def __init__(self, session: Session, request: RequestContext) -> None:
        self.session = session
        self.request = request
        self._client: AsyncAnthropic | None = None

    @property
    def enabled(self) -> bool:
        return settings.agent_enabled

    @property
    def client(self) -> AsyncAnthropic:
        if not self.enabled:
            raise FeatureDisabledError(
                "Le copilote IA n'est pas configuré sur cette installation : "
                "aucune clé d'API Anthropic n'est renseignée. "
                "Les analyses de risque, les itinéraires et les alternatives "
                "restent disponibles dans le reste de l'application."
            )
        if self._client is None:
            self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._client

    async def ask(
        self, question: str, *, history: list[dict[str, Any]] | None = None
    ) -> AgentAnswer:
        cleaned = sanitize_user_text(question)
        if not cleaned:
            raise FeatureDisabledError("La question est vide.")

        tools = registry.anthropic_specs(self.request)
        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": cleaned})

        traces: list[ToolCallTrace] = []
        iterations = 0
        stop_reason: str | None = None

        while iterations < settings.agent_max_tool_iterations:
            iterations += 1
            try:
                response = await self.client.messages.create(
                    model=settings.anthropic_model,
                    max_tokens=4096,
                    system=f"{SYSTEM_PROMPT}\n\n{RESPONSE_FORMAT_INSTRUCTION}",
                    tools=tools,
                    messages=messages,
                )
            except APIError as exc:
                logger.error("Appel Anthropic en échec", context={"erreur": str(exc)})
                raise ProviderUnavailableError(
                    "Le service d'intelligence artificielle est momentanément "
                    "indisponible. Les analyses déterministes restent accessibles."
                ) from exc

            stop_reason = response.stop_reason
            messages.append({"role": "assistant", "content": response.content})

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                return AgentAnswer(
                    text_fr=_text_of(response.content),
                    structured=_extract_structured(_text_of(response.content)),
                    tool_calls=traces,
                    iterations=iterations,
                    model=settings.anthropic_model,
                    stop_reason=stop_reason,
                )

            results = []
            for block in tool_uses:
                payload = dict(block.input or {})
                outcome = await registry.execute(
                    block.name,
                    payload,
                    ToolContext(session=self.session, request=self.request),
                )
                failed = isinstance(outcome, dict) and "erreur" in outcome
                traces.append(
                    ToolCallTrace(
                        name=block.name,
                        input=payload,
                        succeeded=not failed,
                        error_fr=outcome.get("erreur") if failed else None,
                    )
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": _serialize(outcome),
                        "is_error": failed,
                    }
                )

            messages.append({"role": "user", "content": results})

        # Boucle épuisée : on le dit plutôt que de présenter une réponse
        # partielle comme si elle était complète.
        logger.warning(
            "Boucle d'outils épuisée",
            context={"iterations": iterations, "tenant": self.request.tenant_id},
        )
        return AgentAnswer(
            text_fr=(
                "L'analyse n'a pas pu être menée à son terme dans le nombre "
                "d'étapes autorisé. Les résultats intermédiaires sont "
                "consultables dans les pages Risques et Alternatives."
            ),
            structured=None,
            tool_calls=traces,
            iterations=iterations,
            model=settings.anthropic_model,
            stop_reason="max_iterations",
        )


def _text_of(content: list[Any]) -> str:
    return "\n".join(block.text for block in content if block.type == "text").strip()


def _extract_structured(text: str) -> dict[str, Any] | None:
    """Récupère le bloc JSON de conclusion.

    Un modèle peut oublier le bloc ou le produire mal formé. Dans ce cas on
    renvoie None : l'interface bascule sur un affichage textuel plutôt que de
    montrer une carte de décision à moitié remplie.
    """
    match = STRUCTURED_BLOCK.search(text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.warning("Bloc de conclusion JSON illisible")
        return None
    return parsed if isinstance(parsed, dict) else None


def _serialize(result: dict[str, Any]) -> str:
    payload = json.dumps(result, ensure_ascii=False, default=str)
    if len(payload) > MAX_TOOL_RESULT_CHARS:
        # On tronque explicitement plutôt que de laisser le modèle recevoir un
        # JSON coupé au milieu, qu'il interpréterait comme une donnée manquante.
        return (
            payload[:MAX_TOOL_RESULT_CHARS]
            + '… [résultat tronqué : consulter la page correspondante pour le détail complet]'
        )
    return payload
