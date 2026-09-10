"""Registre des capacités métier.

Source **unique** des outils exposés à Claude. Le serveur MCP et l'agent
applicatif consomment ce même registre : un outil ajouté ici est immédiatement
disponible aux deux, avec la même validation et le même contrôle d'accès.

L'alternative — définir les outils une fois pour MCP et une fois pour la boucle
d'appel de l'agent — produit deux définitions qui divergent silencieusement.
C'est le genre d'écart qu'on ne découvre qu'en production, quand le modèle
appelle un outil dont le comportement n'est plus celui qu'on croyait.

Trois garanties portées par ce module :

1. **Le tenant n'est jamais un paramètre du modèle.** Il vient du contexte
   d'exécution authentifié. Claude ne peut pas franchir une frontière
   d'organisation, même si un message le lui demandait explicitement.
2. **Les entrées sont validées avant exécution.** Un schéma Pydantic par outil ;
   une entrée non conforme est refusée avec un message exploitable, pas une
   trace technique.
3. **Aucun outil n'exécute d'action irréversible.** Le plus engageant crée une
   recommandation *en attente d'approbation humaine*.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.core.errors import AtlasAgriError, AuthorizationError, ValidationError
from app.core.logging import get_logger
from app.core.security import RequestContext
from app.db.repositories import TenantRepository
from app.domain.enums import UserRole

logger = get_logger(__name__)

InputT = TypeVar("InputT", bound=BaseModel)


@dataclass
class ToolContext:
    """Contexte d'exécution d'un outil.

    Porte l'identité de l'appelant et l'accès aux données. Le dépôt est déjà
    borné au tenant : un outil ne peut pas en sortir sans écrire délibérément
    une requête hors du dépôt.
    """

    session: Session
    request: RequestContext

    @property
    def repo(self) -> TenantRepository:
        return TenantRepository(self.session, self.request)


@dataclass(frozen=True)
class ToolDefinition(Generic[InputT]):
    """Définition complète d'une capacité métier."""

    name: str
    description_fr: str
    input_model: type[InputT]
    handler: Callable[[InputT, ToolContext], Awaitable[dict[str, Any]]]
    allowed_roles: tuple[UserRole, ...] = ()
    # Un outil « lecture seule » ne modifie rien. Les outils d'écriture sont
    # explicitement marqués pour pouvoir les restreindre d'un seul endroit.
    read_only: bool = True
    decision_support_fr: str = field(default="")

    def json_schema(self) -> dict[str, Any]:
        """Schéma JSON destiné à Claude et au protocole MCP."""
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return schema

    def anthropic_spec(self) -> dict[str, Any]:
        """Déclaration au format attendu par l'API Anthropic."""
        description = self.description_fr
        if self.decision_support_fr:
            description = f"{description}\n\nDécision soutenue : {self.decision_support_fr}"
        return {
            "name": self.name,
            "description": description,
            "input_schema": self.json_schema(),
        }

    def authorize(self, request: RequestContext) -> None:
        if self.allowed_roles:
            request.require_role(*self.allowed_roles)


class ToolRegistry:
    """Collection d'outils, avec exécution validée et journalisée."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> ToolDefinition:
        if definition.name in self._tools:
            raise RuntimeError(
                f"Outil déjà enregistré : '{definition.name}'. "
                "Deux outils homonymes rendraient le comportement imprévisible."
            )
        self._tools[definition.name] = definition
        return definition

    def get(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise ValidationError(
                f"Outil inconnu : « {name} ». "
                f"Outils disponibles : {', '.join(sorted(self._tools))}."
            )
        return self._tools[name]

    def all(self) -> list[ToolDefinition]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    def anthropic_specs(self, request: RequestContext) -> list[dict[str, Any]]:
        """Outils réellement utilisables par cet appelant.

        Filtrer ici plutôt qu'au moment de l'appel évite au modèle de tenter un
        outil qu'il n'a pas le droit d'utiliser, puis de devoir interpréter un
        refus.
        """
        usable = []
        for tool in self.all():
            try:
                tool.authorize(request)
            except AuthorizationError:
                continue
            usable.append(tool.anthropic_spec())
        return usable

    async def execute(
        self, name: str, raw_input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        """Exécute un outil : autorisation, validation, appel, journalisation.

        Renvoie toujours une structure exploitable. Une erreur métier devient un
        résultat `{"erreur": ...}` en français plutôt qu'une exception : le
        modèle doit pouvoir expliquer l'échec à l'utilisateur, pas s'interrompre.
        """
        started = time.monotonic()
        tool = self.get(name)

        try:
            tool.authorize(context.request)
        except AuthorizationError as exc:
            _audit(context, name, "DENIED", {"motif": "autorisation"})
            return {"erreur": exc.message_fr, "code": exc.code}

        try:
            payload = tool.input_model.model_validate(raw_input)
        except PydanticValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])} : {e['msg']}" for e in exc.errors()[:5]
            )
            _audit(context, name, "INVALID_INPUT", {"details": details})
            return {
                "erreur": f"Paramètres invalides pour « {name} » : {details}",
                "code": "donnees_invalides",
            }

        try:
            result = await tool.handler(payload, context)
        except AtlasAgriError as exc:
            _audit(context, name, "BUSINESS_ERROR", {"message": exc.message_fr})
            return {"erreur": exc.message_fr, "code": exc.code}
        except Exception as exc:  # noqa: BLE001 - dernier rempart
            logger.exception(
                "Échec inattendu d'un outil",
                context={"outil": name, "tenant": context.request.tenant_id},
            )
            _audit(context, name, "ERROR", {"type": type(exc).__name__})
            return {
                "erreur": (
                    f"L'outil « {name} » n'a pas pu aboutir. "
                    "L'incident a été enregistré."
                ),
                "code": "erreur_interne",
            }

        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        logger.info(
            "Outil exécuté",
            context={
                "outil": name,
                "tenant": context.request.tenant_id,
                "duree_ms": elapsed_ms,
            },
        )
        _audit(context, name, "SUCCESS", {"duree_ms": elapsed_ms})
        return result


def _audit(context: ToolContext, name: str, outcome: str, details: dict[str, Any]) -> None:
    """Trace l'appel. Une trace d'audit ne doit jamais faire échouer l'appel."""
    try:
        context.repo.record_audit(
            action=f"tool:{name}",
            resource_type="MCP_TOOL",
            resource_id=name,
            outcome=outcome,
            details=details,
        )
        context.session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Journal d'audit non enregistré", context={"outil": name})
        context.session.rollback()


registry = ToolRegistry()


def tool(
    *,
    name: str,
    description_fr: str,
    input_model: type[BaseModel],
    allowed_roles: tuple[UserRole, ...] = (),
    read_only: bool = True,
    decision_support_fr: str = "",
) -> Callable:
    """Décorateur d'enregistrement d'un outil."""

    def decorator(handler: Callable[..., Awaitable[dict[str, Any]]]):
        registry.register(
            ToolDefinition(
                name=name,
                description_fr=description_fr,
                input_model=input_model,
                handler=handler,
                allowed_roles=allowed_roles,
                read_only=read_only,
                decision_support_fr=decision_support_fr,
            )
        )
        return handler

    return decorator
