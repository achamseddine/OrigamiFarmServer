"""Issuing and redeeming a tenant user's invitation.

The gap this closes: POST /tenants/{id}/memberships created an identity
with no password and recorded an audit event saying "invited", which was
aspirational — nothing was sent, and the person had no way to sign in to
anything. An owner handed a brand-new farm could not open the tablet app.

The token is generated here, hashed before storage, and returned to the
caller exactly once. Everything about that mirrors device activation
(app/devices/service.py), for the same reason: a database dump must not
hand anybody a way into a tenant's data.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.common.enums import MembershipStatus
from app.tenants.models import MembershipInvitation, TenantMembership

# Long enough that a link mailed on Friday still works on Monday, short
# enough that a forwarded email is not a permanent back door.
DEFAULT_TTL_HOURS = 168


def generate_invitation_token() -> str:
    # 32 bytes: this is a bearer credential that sets a password, not a
    # code somebody retypes off a screen like a device activation.
    return secrets.token_urlsafe(32)


def hash_invitation_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invitation_url(base_url: str, token: str) -> str:
    """Where the invited person goes. The console serves /welcome.

    Not /activate, which is what this was: the product already had "device
    activation codes" for pairing a tablet, and two different credentials
    both called activation is a trap somebody walks into. The old path
    still forwards here so links already sent keep working.
    """
    return f"{base_url.rstrip('/')}/welcome/?token={token}"


@dataclass
class IssuedInvitation:
    invitation: MembershipInvitation
    # Plaintext, available only on the call that created it.
    token: str
    url: str


def issue_invitation(
    db: Session,
    *,
    membership: TenantMembership,
    base_url: str,
    invited_by: uuid.UUID | None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> IssuedInvitation:
    """Creates an invitation, superseding any that is still outstanding.

    Superseding rather than accumulating is what makes "resend" safe: an
    admin who clicks it twice has one working link, not two, so a link
    that leaked from an old email stops working the moment a new one is
    sent.
    """
    now = datetime.now(timezone.utc)
    outstanding = db.execute(
        select(MembershipInvitation).where(
            MembershipInvitation.membership_id == membership.id,
            MembershipInvitation.accepted_at.is_(None),
            MembershipInvitation.revoked_at.is_(None),
        )
    ).scalars().all()
    for previous in outstanding:
        previous.revoked_at = now

    token = generate_invitation_token()
    invitation = MembershipInvitation(
        tenant_id=membership.tenant_id,
        membership_id=membership.id,
        token_hash=hash_invitation_token(token),
        expires_at=now + timedelta(hours=ttl_hours),
        invited_by=invited_by,
    )
    db.add(invitation)
    db.flush()
    return IssuedInvitation(
        invitation=invitation, token=token, url=invitation_url(base_url, token)
    )


class InvitationError(Exception):
    """Why a token cannot be redeemed, in words the invited person can read."""


def redeem_invitation(
    db: Session, *, token: str
) -> tuple[MembershipInvitation, TenantMembership, UserIdentity]:
    """Resolves a token to the person it was issued for, or explains why not.

    Every rejection below is deliberately specific. This endpoint is not a
    login: the holder of the link is the person being identified, so
    telling them "this link has expired" rather than a blanket failure
    costs no secret and saves a support call. An unknown token still says
    only that it is invalid.
    """
    invitation = db.execute(
        select(MembershipInvitation).where(
            MembershipInvitation.token_hash == hash_invitation_token(token)
        )
    ).scalar_one_or_none()
    if invitation is None:
        raise InvitationError("This invitation link is not valid.")
    if invitation.accepted_at is not None:
        raise InvitationError(
            "This invitation has already been used. Sign in with the password you set, "
            "or ask for a new invitation."
        )
    if invitation.revoked_at is not None:
        raise InvitationError(
            "This invitation was replaced by a newer one. Use the most recent link you were sent."
        )
    if invitation.expires_at <= datetime.now(timezone.utc):
        raise InvitationError("This invitation has expired. Ask for a new one.")

    membership = db.get(TenantMembership, invitation.membership_id)
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        raise InvitationError("This account is no longer active. Contact your administrator.")

    user = db.get(UserIdentity, membership.user_id)
    if user is None:
        raise InvitationError("This invitation link is not valid.")

    return invitation, membership, user
