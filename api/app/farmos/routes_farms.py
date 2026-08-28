from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.db import get_control_db
from app.farmos.deps import AccessContext, get_access_context
from app.farmos.schemas import FarmOut
from app.tenants.models import Tenant

router = APIRouter()


@router.get("/farms/me", response_model=FarmOut)
def my_farm(
    access: AccessContext = Depends(get_access_context), db: Session = Depends(get_control_db)
) -> FarmOut:
    """The signed-in user's own farm — just the record, for the Settings
    screen. (/farms/{farm_id}/bootstrap, if/when built, returns this plus
    the whole offline-cache payload — far more than a settings display
    needs, so it stays a separate endpoint.)
    """
    tenant = db.get(Tenant, access.tenant_id)
    assert tenant is not None  # get_access_context already verified this tenant exists
    return FarmOut(
        id=str(tenant.id),
        name=tenant.display_name,
        country=tenant.country,
        region=tenant.region,
        timezone=tenant.timezone,
        default_currency=tenant.default_currency,
        created_at=tenant.created_at,
    )
