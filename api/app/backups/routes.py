from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.auth.dependencies import require_platform_role
from app.auth.schemas import Identity
from app.backups.models import BackupJob, TenantExport
from app.common.db import get_control_db
from app.common.enums import ActorType, JobStatus, PlatformRole
from app.common.errors import AppError, ErrorCode

router = APIRouter()

_STAFF = (PlatformRole.PLATFORM_SUPER_ADMIN, PlatformRole.PLATFORM_COMMERCIAL_ADMIN)
_READ_ROLES = (*_STAFF, PlatformRole.PLATFORM_SUPPORT_ADMIN, PlatformRole.PLATFORM_AUDITOR)


class BackupJobOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    job_type: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None

    model_config = {"from_attributes": True}


class TenantExportCreateRequest(BaseModel):
    reason: str


class TenantExportOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    status: str
    storage_key: str | None
    download_url_expires_at: datetime | None
    reason: str | None

    model_config = {"from_attributes": True}


@router.get("/tenants/{tenant_id}/backups", response_model=list[BackupJobOut])
def list_tenant_backups(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_READ_ROLES)),
) -> list[BackupJob]:
    return list(
        db.execute(select(BackupJob).where(BackupJob.tenant_id == tenant_id)).scalars().all()
    )


@router.get("/backups", response_model=list[BackupJobOut])
def list_all_backups(
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_READ_ROLES)),
) -> list[BackupJob]:
    """Platform-wide database backup status (tenant_id is null for these
    rows) plus every tenant snapshot job, newest first.
    """
    return list(db.execute(select(BackupJob).order_by(BackupJob.created_at.desc())).scalars().all())


@router.post("/tenants/{tenant_id}/exports", response_model=TenantExportOut, status_code=201)
def request_tenant_export(
    tenant_id: uuid.UUID,
    payload: TenantExportCreateRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> TenantExport:
    """Creates the export request record. A background worker (see
    workers/) picks this up, writes the package to protected object
    storage, and issues an expiring download link — never generated
    synchronously in the request/response cycle.
    """
    export = TenantExport(
        tenant_id=tenant_id,
        requested_by=identity.user_id,
        status=JobStatus.PENDING,
        reason=payload.reason,
    )
    db.add(export)
    db.flush()
    record_audit_event(
        db,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        tenant_id=tenant_id,
        action="tenant_export.requested",
        entity_type="tenant_export",
        entity_id=str(export.id),
        reason=payload.reason,
    )
    return export


@router.get("/tenants/{tenant_id}/exports", response_model=list[TenantExportOut])
def list_tenant_exports(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_READ_ROLES)),
) -> list[TenantExport]:
    return list(
        db.execute(select(TenantExport).where(TenantExport.tenant_id == tenant_id)).scalars().all()
    )


class RestoreRequest(BaseModel):
    reason: str
    confirm_tenant_code: str


@router.post("/tenants/{tenant_id}/restore-requests", status_code=201)
def request_restore(
    tenant_id: uuid.UUID,
    payload: RestoreRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(PlatformRole.PLATFORM_SUPER_ADMIN)),
) -> dict:
    """Deliberately not a one-click action: requires super-admin, an
    explicit reason, and re-typing the tenant's company_code. Creates an
    audited request only — actual restore execution is a separate,
    manually-triggered operational runbook (see docs/deployment notes),
    never a synchronous destructive call from this endpoint.
    """
    from app.tenants.models import Tenant

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise AppError(ErrorCode.NOT_FOUND)
    if payload.confirm_tenant_code != tenant.company_code:
        raise AppError(ErrorCode.VALIDATION_ERROR, "confirm_tenant_code does not match")

    record_audit_event(
        db,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        tenant_id=tenant_id,
        action="restore.requested",
        entity_type="tenant",
        entity_id=str(tenant_id),
        reason=payload.reason,
    )
    return {"status": "REQUEST_RECORDED", "tenant_id": str(tenant_id)}
