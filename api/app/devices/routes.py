from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.auth.dependencies import get_tenant_context
from app.auth.schemas import TenantContext
from app.common.db import get_control_db
from app.common.enums import ActorType, DeviceActivationStatus, DevicePlatform, DeviceStatus
from app.common.errors import AppError, ErrorCode
from app.config import get_settings
from app.devices.lease import issue_license_lease
from app.devices.models import Device, DeviceActivation, LicenseLease
from app.devices.service import (
    active_modules_for_tenant,
    farm_ids_for_device,
    hash_activation_code,
    permission_profile_hash_for,
)
from app.tenants.models import Tenant

router = APIRouter()


class ActivateDeviceRequest(BaseModel):
    activation_code: str
    installation_id: str
    display_name: str
    platform: DevicePlatform = DevicePlatform.ANDROID
    app_version: str = "0.0.0"


class LeaseResponse(BaseModel):
    lease_id: uuid.UUID
    lease: str
    expires_at: datetime
    modules: list[str]
    farm_ids: list[uuid.UUID]


class ActivateDeviceResponse(BaseModel):
    device_id: uuid.UUID
    tenant_id: uuid.UUID
    company_code: str
    tenant_display_name: str
    farm_id: uuid.UUID | None
    lease: LeaseResponse


def _issue_and_record_lease(db: Session, device: Device) -> LeaseResponse:
    settings = get_settings()
    modules = active_modules_for_tenant(db, device.tenant_id)
    farm_ids = farm_ids_for_device(db, device)
    profile_hash = permission_profile_hash_for(modules, farm_ids)

    lease_id, token, expires_at = issue_license_lease(
        settings,
        tenant_id=device.tenant_id,
        device_id=device.id,
        farm_ids=farm_ids,
        modules=modules,
        permission_profile_hash=profile_hash,
    )
    db.add(
        LicenseLease(
            id=lease_id,
            tenant_id=device.tenant_id,
            device_id=device.id,
            issued_at=datetime.now(timezone.utc),
            expires_at=expires_at,
            policy_version=settings.license_lease_policy_version,
            modules=modules,
            farm_ids=[str(f) for f in farm_ids],
            permission_profile_hash=profile_hash,
        )
    )
    db.flush()
    return LeaseResponse(
        lease_id=lease_id, lease=token, expires_at=expires_at, modules=modules, farm_ids=farm_ids
    )


@router.post("/device/activate", response_model=ActivateDeviceResponse)
def activate_device(
    payload: ActivateDeviceRequest, db: Session = Depends(get_control_db)
) -> ActivateDeviceResponse:
    """No bearer token required: the one-time activation code itself is the
    credential. See LICENSE_ENTITLEMENTS.md for the activation lifecycle.
    """
    code_hash = hash_activation_code(payload.activation_code)
    activation_row = db.execute(
        select(DeviceActivation).where(DeviceActivation.code_hash == code_hash)
    ).scalar_one_or_none()
    if activation_row is None:
        raise AppError(ErrorCode.ACTIVATION_CODE_INVALID)

    if activation_row.status == DeviceActivationStatus.USED:
        raise AppError(ErrorCode.ACTIVATION_CODE_ALREADY_USED)
    if activation_row.status == DeviceActivationStatus.REVOKED:
        raise AppError(ErrorCode.ACTIVATION_CODE_INVALID)

    now = datetime.now(timezone.utc)
    if activation_row.status == DeviceActivationStatus.EXPIRED or activation_row.expires_at <= now:
        activation_row.status = DeviceActivationStatus.EXPIRED
        db.flush()
        raise AppError(ErrorCode.ACTIVATION_CODE_EXPIRED)

    tenant = db.get(Tenant, activation_row.tenant_id)
    if tenant is None:
        raise AppError(ErrorCode.ACTIVATION_CODE_INVALID)

    device = Device(
        tenant_id=activation_row.tenant_id,
        farm_id=activation_row.farm_id,
        installation_id=payload.installation_id,
        display_name=payload.display_name,
        platform=payload.platform,
        app_version=payload.app_version,
        status=DeviceStatus.ACTIVE,
        activated_at=now,
        last_seen_at=now,
    )
    db.add(device)
    try:
        db.flush()
    except IntegrityError as exc:
        raise AppError(
            ErrorCode.CONFLICT, "A device with this installation_id is already registered"
        ) from exc

    activation_row.status = DeviceActivationStatus.USED
    activation_row.used_at = now
    activation_row.used_by_device_id = device.id
    db.flush()

    record_audit_event(
        db,
        actor_id=None,
        actor_type=ActorType.DEVICE,
        tenant_id=tenant.id,
        action="device.activated",
        entity_type="device",
        entity_id=str(device.id),
        after={"installation_id": device.installation_id, "platform": device.platform.value},
    )

    lease = _issue_and_record_lease(db, device)

    return ActivateDeviceResponse(
        device_id=device.id,
        tenant_id=tenant.id,
        company_code=tenant.company_code,
        tenant_display_name=tenant.display_name,
        farm_id=device.farm_id,
        lease=lease,
    )


@router.post("/license/refresh", response_model=LeaseResponse)
def refresh_license(
    tenant_context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_control_db),
) -> LeaseResponse:
    if tenant_context.device_id is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "X-Device-Id header is required")

    device = db.get(Device, tenant_context.device_id)
    if device is None:
        raise AppError(ErrorCode.DEVICE_NOT_FOUND)
    if device.status != DeviceStatus.ACTIVE:
        raise AppError(ErrorCode.DEVICE_REVOKED)

    device.last_seen_at = datetime.now(timezone.utc)
    db.flush()
    return _issue_and_record_lease(db, device)
