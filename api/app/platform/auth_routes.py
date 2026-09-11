"""Password sign-in for the admin console.

Separate from app/auth/routes.py's dev-login, which proves nothing and is
local-only, and from the FarmOS tablet's login, which issues a token
scoped to one farm. This one authenticates an Origami staff account
against user_identity.password_hash and returns the session the console
carries.

Holding a session is not the same as holding platform access: any account
with a password can sign in, and GET /platform/v1/me reports which
platform roles (if any) it actually has. Authorization stays where it has
always been — require_platform_role on each route.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.auth.dependencies import get_identity
from app.auth.models import UserIdentity
from app.auth.passwords import hash_password, verify_password
from app.auth.providers import issue_session_token
from app.auth.schemas import Identity
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.common.errors import AppError, ErrorCode
from app.config import get_settings

router = APIRouter()

# Long enough to be worth having, short enough not to push people toward
# reuse. Length is the only rule: composition rules push users toward
# predictable substitutions without adding real entropy.
MIN_PASSWORD_LENGTH = 12


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: int


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_control_db),
) -> LoginResponse:
    settings = get_settings()

    # Case-insensitive: an email address is not case-sensitive in practice,
    # and letting one fail to match is a support ticket, not security.
    user = db.execute(
        select(UserIdentity).where(func.lower(UserIdentity.email) == payload.email.lower())
    ).scalar_one_or_none()

    if user is None or not user.password_hash or not verify_password(payload.password, user.password_hash):
        # Deliberately identical for "no such account", "account has no
        # password" and "wrong password": distinguishing them tells an
        # attacker which addresses are real.
        raise AppError(ErrorCode.UNAUTHENTICATED, "Incorrect email or password")

    token, expires_at = issue_session_token(
        settings, subject=user.idp_subject, email=user.email, name=user.display_name
    )

    record_audit_event(
        db,
        actor_id=user.id,
        actor_type=ActorType.PLATFORM_USER,
        action="platform.signed_in",
        entity_type="user_identity",
        entity_id=str(user.id),
        ip_address=request.client.host if request.client else None,
        summary=f"{user.email} signed in to the admin console",
    )

    return LoginResponse(access_token=token, expires_at=expires_at)


@router.post("/change-password", status_code=204, response_model=None)
def change_password(
    payload: ChangePasswordRequest,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_control_db),
) -> None:
    user = db.get(UserIdentity, identity.user_id)
    if user is None or not user.password_hash:
        raise AppError(ErrorCode.PERMISSION_DENIED, "This account does not sign in with a password")
    if not verify_password(payload.current_password, user.password_hash):
        raise AppError(ErrorCode.UNAUTHENTICATED, "Current password is incorrect")

    user.password_hash = hash_password(payload.new_password)
    # Only this path sets it: from here on the password in force is one
    # nobody else has ever seen.
    user.password_changed_at = datetime.now(timezone.utc)
    db.flush()

    record_audit_event(
        db,
        actor_id=user.id,
        actor_type=ActorType.PLATFORM_USER,
        action="platform.password_changed",
        entity_type="user_identity",
        entity_id=str(user.id),
        summary=f"{user.email} changed their password",
    )
