from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.auth.dependencies import require_platform_role
from app.auth.schemas import Identity
from app.common.db import get_control_db
from app.common.enums import ActorType, PlatformRole
from app.common.errors import AppError, ErrorCode
from app.support.models import SupportSession

router = APIRouter()

_SUPPORT_ROLES = (PlatformRole.PLATFORM_SUPER_ADMIN, PlatformRole.PLATFORM_SUPPORT_ADMIN)


class SupportSessionCreateRequest(BaseModel):
    reason: str
    scope: list[str] = []
    ttl_minutes: int = 60
    case_id: uuid.UUID | None = None


class SupportSessionOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    support_user_id: uuid.UUID
    reason: str
    scope: list[str]
    starts_at: datetime
    expires_at: datetime
    ended_at: datetime | None
    is_active: bool

    model_config = {"from_attributes": True}

    @classmethod
    def from_row(cls, row: SupportSession) -> "SupportSessionOut":
        return cls(
            id=row.id,
            tenant_id=row.tenant_id,
            support_user_id=row.support_user_id,
            reason=row.reason,
            scope=row.scope,
            starts_at=row.starts_at,
            expires_at=row.expires_at,
            ended_at=row.ended_at,
            is_active=row.is_active(now=datetime.now(timezone.utc)),
        )


@router.post(
    "/tenants/{tenant_id}/support-sessions", response_model=SupportSessionOut, status_code=201
)
def start_support_session(
    tenant_id: uuid.UUID,
    payload: SupportSessionCreateRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_SUPPORT_ROLES)),
) -> SupportSessionOut:
    """Time-boxed, audited elevated access — never a standing grant. See
    SECURITY.md, Support Session Design.
    """
    now = datetime.now(timezone.utc)
    session = SupportSession(
        tenant_id=tenant_id,
        support_user_id=identity.user_id,
        case_id=payload.case_id,
        reason=payload.reason,
        scope=payload.scope,
        starts_at=now,
        expires_at=now + timedelta(minutes=payload.ttl_minutes),
    )
    db.add(session)
    db.flush()
    record_audit_event(
        db,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        tenant_id=tenant_id,
        action="support_session.started",
        entity_type="support_session",
        entity_id=str(session.id),
        reason=payload.reason,
        after={"scope": payload.scope, "expires_at": session.expires_at.isoformat()},
    )
    return SupportSessionOut.from_row(session)


@router.post("/support-sessions/{session_id}/end", response_model=SupportSessionOut)
def end_support_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_SUPPORT_ROLES)),
) -> SupportSessionOut:
    session = db.get(SupportSession, session_id)
    if session is None:
        raise AppError(ErrorCode.NOT_FOUND)
    if session.ended_at is None:
        session.ended_at = datetime.now(timezone.utc)
        db.flush()
        record_audit_event(
            db,
            actor_id=identity.user_id,
            actor_type=ActorType.PLATFORM_USER,
            tenant_id=session.tenant_id,
            action="support_session.ended",
            entity_type="support_session",
            entity_id=str(session.id),
        )
    return SupportSessionOut.from_row(session)


@router.get("/tenants/{tenant_id}/support-sessions", response_model=list[SupportSessionOut])
def list_support_sessions(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(
        require_platform_role(*_SUPPORT_ROLES, PlatformRole.PLATFORM_AUDITOR)
    ),
) -> list[SupportSessionOut]:
    rows = db.execute(
        select(SupportSession).where(SupportSession.tenant_id == tenant_id)
    ).scalars().all()
    return [SupportSessionOut.from_row(r) for r in rows]
