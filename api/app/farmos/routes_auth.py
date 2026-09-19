from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.auth.models import UserIdentity
from app.auth.passwords import hash_password, verify_password
from app.common.db import get_control_db
from app.common.enums import ActorType, MembershipStatus
from app.config import get_settings
from app.farmos.deps import AccessContext, get_access_context
from app.farmos.schemas import LoginRequest, LoginResponse, UserProfileOut
from app.farmos.security import issue_access_token
from app.tenants.models import TenantMembership

router = APIRouter()


def _profile_of(user: UserIdentity, membership: TenantMembership) -> UserProfileOut:
    """The profile shape both /auth/login and /auth/me return.

    One function because the tablet app takes its profile from whichever
    of the two it happened to call — login on a fresh sign-in, /auth/me on
    a relaunch — and two constructions that drift apart would give the
    same person a different name or role depending on how they got in.
    """
    return UserProfileOut(
        id=str(user.id),
        # The tablet contract calls the tenant "farm_id": one customer is
        # one farm from the app's point of view.
        farm_id=str(membership.tenant_id),
        name=user.display_name,
        email=user.email,
        phone=membership.phone,
        role=membership.role,
        department=membership.department,
        language=membership.language,
        active=membership.status == MembershipStatus.ACTIVE,
    )


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_control_db)) -> LoginResponse:
    """Not one of the 92 verified endpoints (the reference app's call sites
    only exercise GET /auth/me — this app never re-sends a stored
    password), but the contract's own build order lists it as required to
    open the app at all, so its shape follows the same conventions as
    everything else here.
    """
    generic_error = HTTPException(status_code=401, detail="Incorrect email or password.")

    user = db.execute(select(UserIdentity).where(UserIdentity.email == payload.email)).scalar_one_or_none()
    if user is None or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise generic_error

    # `.first()` on an ordered query, not `.scalar_one_or_none()`: one
    # person can hold an active membership in more than one tenant, and
    # that is ordinary rather than exceptional — an operator running two
    # farms under one email, or an admin who re-created a customer while
    # testing and left the old one in place. Asking for exactly one row
    # there raised MultipleResultsFound, which reached the tablet as a 500
    # and read as "the password is wrong" to everyone looking at it.
    #
    # The tablet contract has no way to choose between them (one login per
    # device, no tenant picker, unlike the console's X-Membership-Id), so
    # the newest wins: the membership created last is the customer whoever
    # is signing in was most recently set up on, which is what an admin
    # reading out a freshly issued password expects to happen.
    membership = (
        db.execute(
            select(TenantMembership)
            .where(
                TenantMembership.user_id == user.id,
                TenantMembership.status == MembershipStatus.ACTIVE,
            )
            .order_by(TenantMembership.created_at.desc(), TenantMembership.id.desc())
        )
        .scalars()
        .first()
    )
    if membership is None:
        raise generic_error

    settings = get_settings()
    token = issue_access_token(settings, user_id=user.id, tenant_id=membership.tenant_id, email=user.email)
    return LoginResponse(access_token=token, user=_profile_of(user, membership))


@router.get("/auth/me", response_model=UserProfileOut)
def me(
    access: AccessContext = Depends(get_access_context), db: Session = Depends(get_control_db)
) -> UserProfileOut:
    """Restores a session from the stored token on relaunch — re-validates
    the bearer token and returns the current profile, without re-sending a
    password. See app/farmos/deps.py:get_access_context for what "restore"
    actually checks (membership status, farm/tenant status) on every call.
    """
    membership = db.get(TenantMembership, access.membership_id)
    assert membership is not None  # get_access_context already verified this membership exists
    user = db.get(UserIdentity, access.user_id)
    assert user is not None  # likewise — the context was built from this user
    return _profile_of(user, membership)


class ChangeMyPasswordRequest(BaseModel):
    current_password: str
    # Eight, matching the invitation flow: this is a farm worker on a
    # tablet, and a longer rule pushes them to write it on the device.
    new_password: str = Field(min_length=8)


@router.post("/auth/change-password", status_code=204, response_model=None)
def change_my_password(
    payload: ChangeMyPasswordRequest,
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_control_db),
) -> None:
    """Lets a farm user replace their own password.

    Added because the alternative made a promise the product could not
    keep: an admin can now set a password for an owner directly, which is
    the workable answer when there is no mail server, but without this
    that owner would be stuck forever with a password somebody else chose
    and read down a phone line. Setting one for somebody is only
    acceptable if they can take it back.
    """
    user = db.get(UserIdentity, access.user_id)
    if user is None or not user.password_hash:
        raise HTTPException(status_code=403, detail="This account does not sign in with a password.")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Your current password is not correct.")

    user.password_hash = hash_password(payload.new_password)
    # Their own choice now, not one handed to them — the same distinction
    # the console tracks for staff accounts.
    user.password_changed_at = datetime.now(timezone.utc)
    db.flush()

    record_audit_event(
        db,
        actor_id=user.id,
        actor_type=ActorType.TENANT_USER,
        tenant_id=access.tenant_id,
        action="tenant_user.password_changed",
        entity_type="user_identity",
        entity_id=str(user.id),
        summary=f"{user.email} changed their own password",
    )
