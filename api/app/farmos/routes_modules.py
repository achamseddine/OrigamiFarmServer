"""GET /modules and POST /modules/{module_code}/activate.

Both used to read and write `tenant_entitlement`: a farm's Mouneh screen
was locked until somebody activated the licence, and the row that said so
was the thing the tablet checked.

Origami is one subscription covering the whole product, so there is no
licence to hold or withhold and nothing writes those rows any more. These
two endpoints stay because a shipped app calls them by name — the paths
are pinned in docs/FARMOS_API.md — and they now report every module as
active. Deleting them would lock every installed tablet out of Mouneh and
Farm Visits, which is the opposite of what "every module is included"
means.

Activation is still refused to a worker. Not because it changes anything
— it cannot — but because a route that quietly accepts a call it used to
reject teaches a client the wrong thing about who may do what.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.db import get_control_db
from app.farmos.deps import AccessContext, check_farm_id, require_owner_or_manager
from app.farmos.schemas import ModuleLicenseOut, ModuleLicenseUpdate
from app.plans.licensing_map import MODULE_LICENCES
from app.plans.models import ModuleCatalog

router = APIRouter()

# The plan every module reports itself as being sold under. One name, so a
# client showing it has something honest to print.
INCLUDED_PLAN = "origami"


def _included(tenant_id, module_code: str) -> ModuleLicenseOut:
    """A licence the customer has by virtue of being a customer.

    The id is the module code rather than a uuid: there is no row to name,
    and an invented uuid would look like one that could be looked up.
    """
    return ModuleLicenseOut(
        id=module_code,
        farm_id=str(tenant_id),
        module_code=module_code,
        status="active",
        plan=INCLUDED_PLAN,
        starts_at=None,
        expires_at=None,
        max_users=None,
        max_products=None,
    )


def _licence_codes(db: Session) -> list[str]:
    """Every distinct licence code the product has.

    Read from the catalog where it has rows, falling back to the static map
    so a deployment whose catalog has not been seeded still answers with
    the add-ons the app asks about by name.
    """
    codes = {
        code
        for code in db.execute(select(ModuleCatalog.license_code)).scalars()
        if code
    }
    return sorted(codes | set(MODULE_LICENCES.values()))


@router.get("/modules", response_model=list[ModuleLicenseOut])
def list_modules(
    access: AccessContext = Depends(require_owner_or_manager),
    db: Session = Depends(get_control_db),
) -> list[ModuleLicenseOut]:
    return [_included(access.tenant_id, code) for code in _licence_codes(db)]


@router.post("/modules/{module_code}/activate", response_model=ModuleLicenseOut)
def activate_module(
    module_code: str,
    _payload: ModuleLicenseUpdate,
    farm_id: str | None = Query(default=None),
    access: AccessContext = Depends(require_owner_or_manager),
    db: Session = Depends(get_control_db),
) -> ModuleLicenseOut:
    """Already active. Answers so, rather than 404ing a shipped app."""
    if farm_id is not None:
        check_farm_id(farm_id, access)
    # Touching the catalog keeps the old behaviour of learning a module
    # code the server had not seen, which is how the two add-ons got their
    # rows in the first place.
    if db.get(ModuleCatalog, module_code) is None:
        db.add(
            ModuleCatalog(
                module_code=module_code,
                name_en=module_code.replace("_", " ").title(),
                name_ar=module_code,
            )
        )
        db.flush()
    return _included(access.tenant_id, module_code)
