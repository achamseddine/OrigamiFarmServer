"""The "employees" group. GET /me/access and GET /modules/catalog land in
Stage 1 (nothing renders without them — see docs/FARMOS_API.md); the
employee CRUD + Set Employee Permissions endpoints are Stage 4 and are
added alongside app/farmos/routes_employees_admin.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.db import get_control_db
from app.farmos.deps import AccessContext, get_access_context
from app.farmos.permissions import MODULE_CODES, full_access_grid
from app.farmos.schemas import ModuleCatalogEntry, MyAccessOut
from app.plans.models import ModuleCatalog

router = APIRouter()


@router.get("/me/access", response_model=MyAccessOut)
def my_access(access: AccessContext = Depends(get_access_context)) -> MyAccessOut:
    """Drives the whole app: navigation is built from this, and every
    Add/Edit/Delete button is shown or hidden by it. The server enforces
    the identical rules on every write route — this response is what the
    app draws, never the security boundary itself.
    """
    modules = full_access_grid() if access.full_access else access.permissions
    return MyAccessOut(
        user_id=str(access.user_id), role=access.role, full_access=access.full_access, modules=modules
    )


@router.get("/modules/catalog", response_model=list[ModuleCatalogEntry])
def module_catalog(
    _access: AccessContext = Depends(get_access_context), db: Session = Depends(get_control_db)
) -> list[ModuleCatalogEntry]:
    """Every module the product has, with whether this farm's own licence
    for it is active. Any signed-in user may read it — it describes the
    product, not this farm's data.
    """
    rows = (
        db.execute(select(ModuleCatalog).where(ModuleCatalog.module_code.in_(MODULE_CODES))).scalars().all()
    )

    return [
        ModuleCatalogEntry(
            code=row.module_code,
            label_en=row.name_en,
            label_ar=row.name_ar,
            group=row.group,
            license_code=row.license_code,
            # Always. Origami is sold as one subscription covering the
            # whole product, so there is no such thing as a customer who
            # has paid and is missing a module — and a per-module check
            # here could only ever produce a farm that cannot reach
            # something it is entitled to.
            #
            # license_code is still reported: the app shows it, and it
            # stays meaningful as a description of which part of the
            # product a screen belongs to.
            licensed_active=True,
        )
        for row in rows
    ]
