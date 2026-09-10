"""Serveur MCP (transport stdio).

Expose le registre d'outils métier à un client MCP — Claude Desktop, Claude Code,
ou tout autre hôte compatible.

**Authentification.** Le serveur exige un jeton `ATLASAGRI_MCP_TOKEN`, produit
par l'API d'authentification. Il s'exécute donc avec l'identité et le tenant
d'un utilisateur réel, et hérite de ses droits.

C'est le point de sécurité central : sans ce jeton, le serveur refuse de
démarrer plutôt que de tourner sans identité. Un serveur MCP anonyme ayant accès
à la base serait un contournement complet de l'isolation multi-tenant.

Lancement :

    ATLASAGRI_MCP_TOKEN=<jeton> python -m app.mcp_server
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

import app.tools.business_tools  # noqa: F401 - l'import enregistre les outils
from app.core.logging import configure_logging, get_logger
from app.core.security import decode_access_token
from app.db.session import SessionLocal
from app.tools.registry import ToolContext, registry

logger = get_logger(__name__)

SERVER_NAME = "atlasagri"


def _load_request_context():
    token = os.environ.get("ATLASAGRI_MCP_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "ATLASAGRI_MCP_TOKEN est absent.\n"
            "Le serveur MCP refuse de démarrer sans identité : il aurait sinon "
            "accès aux données sans appartenir à aucune organisation.\n"
            "Obtenir un jeton via POST /api/v1/auth/login."
        )
    try:
        return decode_access_token(token)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Jeton MCP invalide ou expiré : {exc}") from exc


def build_server() -> Server:
    request_context = _load_request_context()
    server = Server(SERVER_NAME)

    logger.info(
        "Serveur MCP initialisé",
        context={
            "tenant": request_context.tenant_id,
            "role": request_context.role.value,
            "outils": len(registry.all()),
        },
    )

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """N'expose que les outils que ce rôle peut réellement utiliser."""
        tools: list[Tool] = []
        for definition in registry.all():
            try:
                definition.authorize(request_context)
            except Exception:  # noqa: BLE001 - rôle insuffisant
                continue

            description = definition.description_fr
            if definition.decision_support_fr:
                description += f"\n\nDécision soutenue : {definition.decision_support_fr}"
            if not definition.read_only:
                description += (
                    "\n\nCet outil écrit une proposition en base. Il ne déclenche "
                    "aucune action opérationnelle irréversible."
                )
            tools.append(
                Tool(
                    name=definition.name,
                    description=description,
                    inputSchema=definition.json_schema(),
                )
            )
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        # Une session par appel : un serveur stdio vit longtemps et une session
        # SQLAlchemy conservée trop longtemps finit par servir des données périmées.
        session = SessionLocal()
        try:
            result = await registry.execute(
                name, arguments or {}, ToolContext(session=session, request=request_context)
            )
        finally:
            session.close()

        return [
            TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, indent=2, default=str),
            )
        ]

    return server


async def main() -> None:
    configure_logging()
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(0)
