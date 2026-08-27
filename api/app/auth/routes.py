from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.auth.providers import issue_dev_session_token
from app.common.db import get_control_db
from app.common.errors import AppError, ErrorCode
from app.config import get_settings

router = APIRouter()


class DevLoginRequest(BaseModel):
    email: EmailStr
    display_name: str = ""


class DevLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/dev-login", response_model=DevLoginResponse)
def dev_login(payload: DevLoginRequest, db: Session = Depends(get_control_db)) -> DevLoginResponse:
    """Local/test-only login that mints an internal session token without a
    live OIDC provider. Disabled outside AUTH_DEV_MODE so it can never be
    reached in staging/production.
    """
    settings = get_settings()
    if not settings.auth_dev_mode:
        raise AppError(ErrorCode.PERMISSION_DENIED, "Dev login is disabled")

    token = issue_dev_session_token(
        settings, subject=payload.email, email=payload.email, name=payload.display_name or payload.email
    )
    return DevLoginResponse(access_token=token)
