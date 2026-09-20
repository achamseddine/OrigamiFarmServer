"""The invited person's way in.

Public, because the whole point is that whoever holds the link has no
account to authenticate with yet — the token is the credential, and it is
single-use, expiring and hashed at rest (app/tenants/invitations.py).

Two endpoints rather than one: the activation page needs to show whose
account it is and whether the link is still good *before* asking anyone to
type a password, and a page that could only find out by attempting the
change would burn the token to display an error.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.auth.passwords import hash_password
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.common.errors import AppError, ErrorCode
from app.config import get_settings
from app.farmos.security import issue_access_token
from app.tenants.invitations import InvitationError, redeem_invitation
from app.tenants.models import Tenant

router = APIRouter()

MIN_PASSWORD_LENGTH = 8


class InvitationCheckRequest(BaseModel):
    token: str


class InvitationCheckResponse(BaseModel):
    valid: bool
    # Only ever filled in for a valid token, and only with what the holder
    # already knows: their own name and which farm invited them.
    email: str | None = None
    display_name: str | None = None
    tenant_name: str | None = None
    expires_at: datetime | None = None
    message: str | None = None


class AcceptInvitationRequest(BaseModel):
    token: str
    # Eight, not the console's twelve: this is a farm worker typing on a
    # tablet in a field, and a rule that pushes them to write it on the
    # device is worse than a shorter one they can remember.
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class AcceptInvitationResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    email: str
    display_name: str
    tenant_name: str


@router.post("/auth/invitation/check", response_model=InvitationCheckResponse)
def check_invitation(
    payload: InvitationCheckRequest, db: Session = Depends(get_control_db)
) -> InvitationCheckResponse:
    """Reports whether a link still works, without consuming it."""
    try:
        invitation, membership, user = redeem_invitation(db, token=payload.token)
    except InvitationError as exc:
        return InvitationCheckResponse(valid=False, message=str(exc))

    tenant = db.get(Tenant, membership.tenant_id)
    return InvitationCheckResponse(
        valid=True,
        email=user.email,
        display_name=user.display_name,
        tenant_name=tenant.display_name if tenant else None,
        expires_at=invitation.expires_at,
    )


@router.post("/auth/invitation/accept", response_model=AcceptInvitationResponse)
def accept_invitation(
    payload: AcceptInvitationRequest, db: Session = Depends(get_control_db)
) -> AcceptInvitationResponse:
    """Sets the password and signs them straight in.

    Returning a session rather than redirecting to a login screen is
    deliberate: they have just proved they hold the invitation and chosen
    a password, and asking them to type it again immediately is the kind
    of step that loses people on their first day.
    """
    try:
        invitation, membership, user = redeem_invitation(db, token=payload.token)
    except InvitationError as exc:
        raise AppError(ErrorCode.UNAUTHENTICATED, str(exc)) from exc

    now = datetime.now(timezone.utc)
    user.password_hash = hash_password(payload.password)
    # They chose it themselves, so it is not a handed-out password — the
    # same distinction the console tracks for staff accounts.
    user.password_changed_at = now
    invitation.accepted_at = now
    db.flush()

    record_audit_event(
        db,
        actor_id=user.id,
        actor_type=ActorType.TENANT_USER,
        tenant_id=membership.tenant_id,
        action="membership.invitation_accepted",
        entity_type="tenant_membership",
        entity_id=str(membership.id),
        summary=f"{user.email} accepted their invitation and set a password",
    )

    tenant = db.get(Tenant, membership.tenant_id)
    settings = get_settings()
    token = issue_access_token(
        settings, user_id=user.id, tenant_id=membership.tenant_id, email=user.email
    )
    return AcceptInvitationResponse(
        access_token=token,
        email=user.email,
        display_name=user.display_name,
        tenant_name=tenant.display_name if tenant else "",
    )
