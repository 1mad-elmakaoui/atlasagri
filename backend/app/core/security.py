"""Authentification, autorisation et contexte d'exécution.

Le point important est `RequestContext` : il porte le tenant et le rôle de
l'appelant et il est **injecté** dans tous les services et tous les outils MCP.

Le `tenant_id` n'est jamais un paramètre fourni par l'appelant ni par le modèle
de langage. Claude ne peut donc pas franchir une frontière d'organisation, même
si on le lui demandait explicitement dans un message.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings
from app.core.errors import AuthenticationError, AuthorizationError
from app.domain.enums import UserRole

ALGORITHM = "HS256"
BCRYPT_ROUNDS = 12


def _prepare(plain: str) -> bytes:
    """Pré-condensé SHA-256 avant bcrypt.

    bcrypt ignore silencieusement tout ce qui dépasse 72 octets : deux mots de
    passe longs partageant leurs 72 premiers octets seraient équivalents. Le
    pré-condensé supprime cette limite sans affaiblir l'algorithme, et l'encodage
    base64 évite les octets nuls que bcrypt rejette.
    """
    return base64.b64encode(hashlib.sha256(plain.encode("utf-8")).digest())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_prepare(plain), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Empreinte corrompue ou format inattendu : on refuse, sans détailler.
        return False


@dataclass(frozen=True)
class RequestContext:
    """Identité effective d'un appel. Source unique de vérité pour l'autorisation."""

    user_id: str
    tenant_id: str
    role: UserRole
    email: str

    def require_role(self, *allowed: UserRole) -> None:
        if self.role is UserRole.ADMIN:
            return
        if self.role not in allowed:
            raise AuthorizationError(
                "Votre rôle ne permet pas cette action. "
                f"Rôles autorisés : {', '.join(r.label_fr for r in allowed)}."
            )

    @property
    def can_decide(self) -> bool:
        """Peut accepter ou rejeter une recommandation à impact opérationnel."""
        return self.role in (
            UserRole.ADMIN,
            UserRole.SUPPLY_CHAIN_MANAGER,
            UserRole.OPERATIONS_MANAGER,
        )


def create_access_token(*, user_id: str, tenant_id: str, role: UserRole, email: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "tenant": tenant_id,
        "role": role.value,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.effective_secret_key(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> RequestContext:
    try:
        payload: dict[str, Any] = jwt.decode(
            token, settings.effective_secret_key(), algorithms=[ALGORITHM]
        )
    except JWTError as exc:
        raise AuthenticationError(
            "Session invalide ou expirée. Merci de vous reconnecter."
        ) from exc

    try:
        return RequestContext(
            user_id=payload["sub"],
            tenant_id=payload["tenant"],
            role=UserRole(payload["role"]),
            email=payload["email"],
        )
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Session invalide. Merci de vous reconnecter.") from exc
