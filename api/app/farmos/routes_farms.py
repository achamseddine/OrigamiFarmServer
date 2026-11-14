"""The signed-in user's own farm: its record for the Settings screen and
the farm-level settings a manager changes from the tablet (reference
api/v1/farms.py `GET`/`PATCH /farms/me`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import farm_settings_service
from app.farmos.deps import AccessContext, get_access_context, get_farmos_tenant_db, require_permission
from app.farmos.schemas import FarmOut, FarmSettingsUpdate
from app.tenants.models import Tenant

router = APIRouter()


def _farm_out(control_db: Session, access: AccessContext, settings: dict[str, int]) -> FarmOut:
    tenant = control_db.get(Tenant, access.tenant_id)
    assert tenant is not None  # get_access_context already verified this tenant exists
    return FarmOut(
        id=str(tenant.id),
        name=tenant.display_name,
        country=tenant.country,
        region=tenant.region,
        timezone=tenant.timezone,
        default_currency=tenant.default_currency,
        created_at=tenant.created_at,
        **settings,
    )


@router.get("/farms/me", response_model=FarmOut)
def my_farm(
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> FarmOut:
    """The signed-in user's own farm — the record plus its settings (dry-off
    days, the breeding / milk calendar), for the Settings screen.
    (/farms/{farm_id}/bootstrap, if/when built, returns this plus the whole
    offline-cache payload — far more than a settings display needs, so it
    stays a separate endpoint.)
    """
    return _farm_out(control_db, access, farm_settings_service.calendar(db, access.tenant_id))


@router.patch("/farms/me", response_model=FarmOut)
def update_my_farm(
    payload: FarmSettingsUpdate,
    access: AccessContext = Depends(require_permission("settings", "configure")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> FarmOut:
    """Changes the signed-in user's farm settings. Settings are not history-
    bearing facts, so the row is updated in place — and every change is an
    audit entry with the old value.

    The calendar every plan is drawn from (dry-off, milk peak, breeding wait)
    is configuration, not day-to-day work: it needs Settings *configure*,
    which the owner and manager hold and an employee only when granted."""
    row = farm_settings_service.get_or_create(db, access.tenant_id)
    changes = {
        k: v
        for k, v in payload.model_dump(exclude_unset=True).items()
        if v is not None and getattr(row, k) != v
    }
    start = changes.get("milk_peak_start_days", row.milk_peak_start_days)
    end = changes.get("milk_peak_end_days", row.milk_peak_end_days)
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail="The milk peak window must start before it ends.")
    if changes:
        before = {k: getattr(row, k) for k in changes}
        for k, v in changes.items():
            setattr(row, k, v)
        row.version += 1
        row.last_modified_by = access.membership_id
        db.flush()
        record_audit_event(
            control_db,
            actor_id=access.user_id,
            actor_type=ActorType.TENANT_USER,
            actor_role=access.role,
            tenant_id=access.tenant_id,
            action="farm.settings_changed",
            entity_type="farm",
            entity_id=str(access.tenant_id),
            module_code="settings",
            summary="Changed "
            + ", ".join(f"{k.replace('_', ' ')} {before[k]} → {v}" for k, v in changes.items()),
            changes={k: {"from": before[k], "to": v} for k, v in changes.items()},
            before=before,
            after=changes,
        )
    return _farm_out(control_db, access, farm_settings_service.as_dict(row))
