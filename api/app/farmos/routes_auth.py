from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.common.db import get_control_db
from app.common.enums import MembershipStatus
from app.config import get_settings
from app.farmos.deps import AccessContext, get_access_context
from app.farmos.schemas import LoginRequest, LoginResponse, UserProfileOut
from app.farmos.security import issue_access_token, verify_password
from app.tenants.models import TenantMembership

router = APIRouter()


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

    membership = db.execute(
        select(TenantMembership).where(
            TenantMembership.user_id == user.id, TenantMembership.status == MembershipStatus.ACTIVE
        )
    ).scalar_one_or_none()
    if membership is None:
        raise generic_error

    settings = get_settings()
    token = issue_access_token(settings, user_id=user.id, tenant_id=membership.tenant_id, email=user.email)
    return LoginResponse(access_token=token)


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
    return UserProfileOut(
        id=str(access.user_id),
        farm_id=str(access.tenant_id),
        name=access.display_name,
        email=access.email,
        phone=membership.phone,
        role=access.role,
        department=membership.department,
        language=membership.language,
        active=membership.status == MembershipStatus.ACTIVE,
    )
