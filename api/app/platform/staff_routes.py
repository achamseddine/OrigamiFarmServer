"""Origami staff accounts and the platform roles they hold.

Separate from tenant memberships in every sense: a platform role is
authority over the product, a membership is access to one customer's farm
data, and holding one never implies the other (see TENANCY.md). Managing
staff is restricted to super admins — a commercial or support admin can
use the console, but cannot widen anyone's access, including their own.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.auth.dependencies import require_platform_role
from app.auth.models import UserIdentity
from app.auth.passwords import hash_password
from app.auth.schemas import Identity
from app.common.db import get_control_db
from app.common.enums import ActorType, PlatformRole
from app.common.errors import AppError, ErrorCode
from app.platform.auth_routes import MIN_PASSWORD_LENGTH
from app.tenants.models import PlatformRoleAssignment

router = APIRouter()


class StaffOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    platform_roles: list[str]
    # Whether this account can sign in with a password at all. False for an
    # identity that only ever arrived through OIDC, which the console
    # should not offer a password reset for.
    has_password: bool
    # True while they are still on a password an admin typed for them.
    password_set_by_someone_else: bool


class StaffCreateRequest(BaseModel):
    email: EmailStr
    display_name: str
    platform_role: PlatformRole
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class StaffRoleRequest(BaseModel):
    platform_role: PlatformRole


class StaffPasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


def _staff_out(db: Session, user: UserIdentity) -> StaffOut:
    roles = db.execute(
        select(PlatformRoleAssignment.platform_role).where(PlatformRoleAssignment.user_id == user.id)
    ).scalars().all()
    return StaffOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        platform_roles=list(roles),
        has_password=bool(user.password_hash),
        password_set_by_someone_else=bool(user.password_hash and user.password_changed_at is None),
    )


def _get_staff_or_404(db: Session, user_id: uuid.UUID) -> UserIdentity:
    user = db.get(UserIdentity, user_id)
    if user is None:
        raise AppError(ErrorCode.NOT_FOUND, "No such user")
    return user


@router.get("/staff", response_model=list[StaffOut])
def list_staff(
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(
        require_platform_role(
            PlatformRole.PLATFORM_SUPER_ADMIN,
            PlatformRole.PLATFORM_COMMERCIAL_ADMIN,
            PlatformRole.PLATFORM_SUPPORT_ADMIN,
            PlatformRole.PLATFORM_AUDITOR,
        )
    ),
) -> list[StaffOut]:
    """Everyone holding at least one platform role.

    Readable by any platform role: knowing who else has access is part of
    being able to audit it. Changing it is super-admin only.
    """
    users = db.execute(
        select(UserIdentity)
        .join(PlatformRoleAssignment, PlatformRoleAssignment.user_id == UserIdentity.id)
        .distinct()
        .order_by(UserIdentity.email)
    ).scalars().all()
    return [_staff_out(db, user) for user in users]


@router.post("/staff", response_model=StaffOut, status_code=201)
def create_staff(
    payload: StaffCreateRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(PlatformRole.PLATFORM_SUPER_ADMIN)),
) -> StaffOut:
    """Creates a staff account with an initial password.

    There is no mail delivery in this system, so the password is set here
    and passed on out of band; the new admin changes it via
    POST /platform/v1/auth/change-password.
    """
    email = payload.email.strip()
    existing = db.execute(
        select(UserIdentity).where(func.lower(UserIdentity.email) == email.lower())
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError(
            ErrorCode.CONFLICT,
            "An account with this email already exists — grant it a role instead",
        )

    user = UserIdentity(
        idp_subject=email,
        email=email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()
    db.add(PlatformRoleAssignment(user_id=user.id, platform_role=payload.platform_role.value))
    db.flush()

    record_audit_event(
        db,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        action="platform.staff_created",
        entity_type="user_identity",
        entity_id=str(user.id),
        after={"email": email, "platform_role": payload.platform_role.value},
        summary=f"Created staff account {email} as {payload.platform_role.value}",
    )
    return _staff_out(db, user)


@router.post("/staff/{user_id}/roles", response_model=StaffOut)
def grant_role(
    user_id: uuid.UUID,
    payload: StaffRoleRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(PlatformRole.PLATFORM_SUPER_ADMIN)),
) -> StaffOut:
    user = _get_staff_or_404(db, user_id)
    already = db.execute(
        select(PlatformRoleAssignment).where(
            PlatformRoleAssignment.user_id == user_id,
            PlatformRoleAssignment.platform_role == payload.platform_role.value,
        )
    ).scalar_one_or_none()
    if already is None:
        db.add(PlatformRoleAssignment(user_id=user_id, platform_role=payload.platform_role.value))
        db.flush()
        record_audit_event(
            db,
            actor_id=identity.user_id,
            actor_type=ActorType.PLATFORM_USER,
            action="platform.role_granted",
            entity_type="user_identity",
            entity_id=str(user_id),
            after={"platform_role": payload.platform_role.value},
            summary=f"Granted {payload.platform_role.value} to {user.email}",
        )
    return _staff_out(db, user)


@router.delete("/staff/{user_id}/roles/{platform_role}", response_model=StaffOut)
def revoke_role(
    user_id: uuid.UUID,
    platform_role: PlatformRole,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(PlatformRole.PLATFORM_SUPER_ADMIN)),
) -> StaffOut:
    user = _get_staff_or_404(db, user_id)

    if user_id == identity.user_id and platform_role == PlatformRole.PLATFORM_SUPER_ADMIN:
        # Removing your own super admin is how a platform ends up with
        # nobody who can grant it back.
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "You cannot revoke your own super admin role — ask another super admin",
        )

    assignment = db.execute(
        select(PlatformRoleAssignment).where(
            PlatformRoleAssignment.user_id == user_id,
            PlatformRoleAssignment.platform_role == platform_role.value,
        )
    ).scalar_one_or_none()
    if assignment is not None:
        db.delete(assignment)
        db.flush()
        record_audit_event(
            db,
            actor_id=identity.user_id,
            actor_type=ActorType.PLATFORM_USER,
            action="platform.role_revoked",
            entity_type="user_identity",
            entity_id=str(user_id),
            before={"platform_role": platform_role.value},
            summary=f"Revoked {platform_role.value} from {user.email}",
        )
    return _staff_out(db, user)


@router.post("/staff/{user_id}/password", status_code=204, response_model=None)
def reset_staff_password(
    user_id: uuid.UUID,
    payload: StaffPasswordResetRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(PlatformRole.PLATFORM_SUPER_ADMIN)),
) -> None:
    """Sets someone else's password — the console's answer to a lockout,
    equivalent to re-running scripts/create_platform_admin.py.
    """
    user = _get_staff_or_404(db, user_id)
    user.password_hash = hash_password(payload.new_password)
    # Cleared, not left as it was: whatever they chose before, the password
    # in force now is one this admin knows, and the checklist should say so
    # until they replace it themselves.
    user.password_changed_at = None
    db.flush()
    record_audit_event(
        db,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        action="platform.password_reset",
        entity_type="user_identity",
        entity_id=str(user_id),
        summary=f"Reset the password for {user.email}",
    )
