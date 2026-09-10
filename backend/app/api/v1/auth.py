"""Authentification."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_context, db_session
from app.api.schemas import LoginRequest, LoginResponse
from app.core.config import settings
from app.core.errors import AuthenticationError
from app.core.logging import get_logger
from app.core.security import RequestContext, create_access_token, verify_password
from app.db.base import AuditLog, Tenant, User
from app.domain.enums import UserRole

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentification"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, session: Session = Depends(db_session)) -> LoginResponse:
    user = session.query(User).filter(User.email == payload.email.lower()).first()

    # Message identique dans les deux cas : distinguer « compte inconnu » de
    # « mot de passe incorrect » permettrait d'énumérer les comptes existants.
    invalid = AuthenticationError("Identifiants invalides.")

    if user is None or not user.is_active:
        raise invalid
    if not verify_password(payload.password, user.hashed_password):
        session.add(
            AuditLog(
                tenant_id=user.tenant_id,
                user_id=user.id,
                action="auth:login",
                resource_type="USER",
                resource_id=user.id,
                outcome="FAILURE",
                details={"motif": "mot de passe invalide"},
            )
        )
        session.commit()
        raise invalid

    user.last_login_at = datetime.now(UTC)
    session.add(
        AuditLog(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action="auth:login",
            resource_type="USER",
            resource_id=user.id,
            outcome="SUCCESS",
            details={},
        )
    )
    session.commit()

    tenant = session.get(Tenant, user.tenant_id)
    role = UserRole(user.role)

    return LoginResponse(
        access_token=create_access_token(
            user_id=user.id, tenant_id=user.tenant_id, role=role, email=user.email
        ),
        expires_in_minutes=settings.access_token_ttl_minutes,
        utilisateur={
            "id": user.id,
            "nom": user.full_name,
            "email": user.email,
            "role": role.value,
            "role_fr": role.label_fr,
            "organisation": tenant.name if tenant else None,
            "organisation_slug": tenant.slug if tenant else None,
        },
    )


@router.get("/moi")
def me(context: RequestContext = Depends(current_context)) -> dict:
    return {
        "id": context.user_id,
        "email": context.email,
        "role": context.role.value,
        "role_fr": context.role.label_fr,
        "peut_decider": context.can_decide,
    }
