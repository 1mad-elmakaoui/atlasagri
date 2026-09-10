"""Dépendances FastAPI : authentification, isolation, limitation de débit."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Iterator

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AuthenticationError, RateLimitError
from app.core.security import RequestContext, decode_access_token
from app.db.repositories import TenantRepository
from app.db.session import get_session


def db_session() -> Iterator[Session]:
    yield from get_session()


def current_context(authorization: str = Header(default="")) -> RequestContext:
    """Identité de l'appelant, extraite du jeton porteur."""
    if not authorization.lower().startswith("bearer "):
        raise AuthenticationError("Authentification requise.")
    return decode_access_token(authorization[7:].strip())


def tenant_repository(
    session: Session = Depends(db_session),
    context: RequestContext = Depends(current_context),
) -> TenantRepository:
    return TenantRepository(session, context)


class SlidingWindowRateLimiter:
    """Limitation de débit en mémoire.

    Suffisante pour un déploiement mono-instance. Une installation répartie
    devra la remplacer par un compteur partagé — l'interface est étroite pour
    rendre ce remplacement simple.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, limit: int, window_seconds: int = 60) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            raise RateLimitError(
                f"Limite de {limit} requêtes par minute atteinte. "
                "Merci de réessayer dans un instant."
            )
        hits.append(now)


limiter = SlidingWindowRateLimiter()


def rate_limit(request: Request, context: RequestContext = Depends(current_context)) -> None:
    limiter.check(
        f"{context.tenant_id}:{context.user_id}",
        limit=settings.rate_limit_requests_per_minute,
    )


def agent_rate_limit(context: RequestContext = Depends(current_context)) -> None:
    """Limite dédiée au copilote.

    Chaque question déclenche plusieurs appels de modèle et d'outils : la
    protéger séparément évite qu'un usage intensif du copilote épuise le quota
    des pages courantes.
    """
    limiter.check(
        f"agent:{context.tenant_id}:{context.user_id}",
        limit=settings.agent_rate_limit_requests_per_minute,
    )
