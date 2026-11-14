"""Work responsibilities (Settings > Employees > Responsibilities) — Stage 11.
What an employee looks after and the duties it means; assigning duties
grants the module access they need. Manager endpoints under
`/employees/{employee_id}/responsibilities` (Employees view / assign) and
the signed-in person's own list at `GET /me/responsibilities`.

The employee is a tenant membership (see app/farmos/routes_employees_admin.py);
the responsibility rows are tenant data under RLS.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.common.db import get_control_db
from app.farmos import responsibility_service as responsibilities
from app.farmos.deps import AccessContext, get_access_context, get_farmos_tenant_db, require_permission
from app.farmos.responsibility_schemas import ResponsibilityEndIn, ResponsibilityIn
from app.farmos.routes_employees_admin import _load_membership_or_404
from app.tenants.models import TenantMembership

router = APIRouter()

_view = require_permission("employees", "view")
_assign = require_permission("employees", "assign")


def _employee(
    control_db: Session, access: AccessContext, employee_id: str
) -> tuple[TenantMembership, UserIdentity]:
    membership = _load_membership_or_404(control_db, access, employee_id)
    user = control_db.get(UserIdentity, membership.user_id)
    assert user is not None  # every membership has a backing UserIdentity
    return membership, user


@router.get("/employees/{employee_id}/responsibilities")
def list_responsibilities(
    employee_id: str,
    include_ended: bool = False,
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> list[dict]:
    _membership, user = _employee(control_db, access, employee_id)
    return [
        responsibilities.to_dict(db, r)
        for r in responsibilities.of_user(db, user.id, active_only=not include_ended)
    ]


@router.post("/employees/{employee_id}/responsibilities", status_code=201)
def add_responsibility(
    employee_id: str,
    payload: ResponsibilityIn,
    access: AccessContext = Depends(_assign),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Makes the employee responsible for an animal / group / species / field
    with these duties, and gives them the module access the duties need."""
    membership, user = _employee(control_db, access, employee_id)
    row, granted = responsibilities.assign(
        db,
        control_db,
        access,
        membership=membership,
        employee=user,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id,
        duties=payload.duties,
        notes=payload.notes,
    )
    return {**responsibilities.to_dict(db, row), "modules_granted": granted}


@router.post("/employees/{employee_id}/responsibilities/{responsibility_id}/end")
def end_responsibility(
    employee_id: str,
    responsibility_id: str,
    payload: ResponsibilityEndIn,
    access: AccessContext = Depends(_assign),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    _membership, user = _employee(control_db, access, employee_id)
    row = responsibilities.get_or_404(db, responsibility_id, user.id)
    responsibilities.end(db, control_db, access, row, reason=payload.reason)
    return responsibilities.to_dict(db, row)


@router.get("/me/responsibilities")
def my_responsibilities(
    access: AccessContext = Depends(get_access_context), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    """The signed-in employee's own animals, groups and fields and their
    duties — what their day is built from."""
    return [
        responsibilities.to_dict(db, r, with_animals=True)
        for r in responsibilities.of_user(db, access.user_id)
    ]
