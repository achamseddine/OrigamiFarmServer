from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.auth.models import UserIdentity
from app.auth.passwords import hash_password, verify_password
from app.common.db import get_control_db
from app.common.enums import ActorType, DeviceStatus, MembershipStatus
from app.config import get_settings
from app.devices.models import Device
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

    _record_device(db, payload, membership)

    settings = get_settings()
    token = issue_access_token(settings, user_id=user.id, tenant_id=membership.tenant_id, email=user.email)
    return LoginResponse(access_token=token, user=_profile_of(user, membership))


def _record_device(db: Session, payload: LoginRequest, membership: TenantMembership) -> None:
    """Note the tablet somebody just signed in on.

    Devices used to arrive by being paired: an admin generated a key, the
    customer typed it in, and the device existed because it had been
    permitted. Nothing is permitted now, so a device only exists if it
    says so — which is exactly the honest version of the same list. It
    answers "which tablets is this customer using", not "which tablets may
    they use", and that first question is the one anybody actually asked.

    Failures here are swallowed deliberately. A worker standing in a field
    at six in the morning must not be locked out because bookkeeping about
    their hardware went wrong.
    """
    if not payload.installation_id:
        return

    now = datetime.now(timezone.utc)
    try:
        device = db.execute(
            select(Device).where(Device.installation_id == payload.installation_id)
        ).scalar_one_or_none()

        if device is None:
            db.add(
                Device(
                    tenant_id=membership.tenant_id,
                    farm_id=membership.default_farm_id,
                    installation_id=payload.installation_id,
                    display_name=payload.device_name or "Tablet",
                    app_version=payload.app_version or "0.0.0",
                    status=DeviceStatus.ACTIVE,
                    activated_at=now,
                    last_seen_at=now,
                )
            )
        else:
            # A revoked device stays revoked: revoking is how an operator
            # says a tablet has been lost, and a sign-in on it is the
            # least convincing possible argument for undoing that.
            if device.status != DeviceStatus.REVOKED:
                device.status = DeviceStatus.ACTIVE
                device.tenant_id = membership.tenant_id
            device.last_seen_at = now
            if payload.device_name:
                device.display_name = payload.device_name
            if payload.app_version:
                device.app_version = payload.app_version
        db.flush()
    except SQLAlchemyError:
        # Two tablets racing on a first sign-in, or any other write
        # problem. The session is poisoned, so roll the failed statement
        # back and carry on issuing the token.
        db.rollback()


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
