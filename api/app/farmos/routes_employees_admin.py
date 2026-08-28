"""Employee CRUD and Set Employee Permissions — Stage 4. See
app/farmos/routes_employees.py for GET /me/access and GET /modules/catalog
(Stage 1), which this module shares the "employees" permission module
with.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.auth.models import UserIdentity
from app.common.db import get_control_db
from app.common.enums import ActorType, MembershipStatus, TenantRole
from app.farmos.deps import AccessContext, is_full_access_role, require_permission
from app.farmos.permissions import ACTIONS, full_access_grid, permissions_grid
from app.farmos.schemas import (
    EmployeeCreate,
    EmployeeDetailOut,
    EmployeeUpdate,
    ModulePermissionOut,
    PermissionSet,
)
from app.farmos.security import hash_password
from app.tenants.models import MembershipModulePermission, TenantMembership

router = APIRouter()

_ACTION_TO_FIELD = {action: f"can_{action}" for action in ACTIONS}


def _to_employee_out(db: Session, user: UserIdentity, membership: TenantMembership) -> EmployeeDetailOut:
    full_access = is_full_access_role(membership.role)
    grid = full_access_grid() if full_access else permissions_grid(db, membership.id)
    permissions = [
        ModulePermissionOut(module_code=module_code, **{f"can_{a}": v for a, v in actions.items()})
        for module_code, actions in grid.items()
    ]
    return EmployeeDetailOut(
        id=str(user.id),
        farm_id=str(membership.tenant_id),
        name=user.display_name,
        email=user.email,
        phone=membership.phone,
        role=membership.role,
        department=membership.department,
        language=membership.language,
        active=membership.status == MembershipStatus.ACTIVE,
        job_title=membership.job_title,
        employment_status=membership.employment_status,
        start_date=membership.start_date.date().isoformat() if membership.start_date else None,
        photo_path=membership.photo_path,
        working_days=membership.working_days,
        working_hours=membership.working_hours,
        notes=membership.notes,
        permissions=permissions,
        full_access=full_access,
    )


def _apply_permissions(db: Session, membership_id: uuid.UUID, permissions: list) -> None:
    db.execute(
        MembershipModulePermission.__table__.delete().where(
            MembershipModulePermission.membership_id == membership_id
        )
    )
    for entry in permissions:
        for action in ACTIONS:
            if getattr(entry, f"can_{action}"):
                db.add(
                    MembershipModulePermission(
                        membership_id=membership_id,
                        module_code=entry.module_code,
                        permission_code=action,
                    )
                )


@router.get("/employees", response_model=list[EmployeeDetailOut])
def list_employees(
    include_inactive: bool = Query(default=False),
    access: AccessContext = Depends(require_permission("employees", "view")),
    db: Session = Depends(get_control_db),
) -> list[EmployeeDetailOut]:
    stmt = select(TenantMembership).where(TenantMembership.tenant_id == access.tenant_id)
    if not include_inactive:
        stmt = stmt.where(TenantMembership.status == MembershipStatus.ACTIVE)
    memberships = db.execute(stmt.order_by(TenantMembership.created_at)).scalars().all()
    out = []
    for membership in memberships:
        user = db.get(UserIdentity, membership.user_id)
        if user is not None:
            out.append(_to_employee_out(db, user, membership))
    return out


@router.post("/employees", response_model=EmployeeDetailOut, status_code=201)
def create_employee(
    payload: EmployeeCreate,
    access: AccessContext = Depends(require_permission("employees", "create")),
    db: Session = Depends(get_control_db),
) -> EmployeeDetailOut:
    user = UserIdentity(
        idp_subject=payload.email or f"farmos:{uuid.uuid4()}",
        email=payload.email or f"{uuid.uuid4()}@no-email.origami.local",
        display_name=payload.name,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()

    membership = TenantMembership(
        tenant_id=access.tenant_id,
        user_id=user.id,
        status=MembershipStatus.ACTIVE,
        tenant_role=TenantRole.TENANT_OWNER if payload.role == "owner" else TenantRole.EMPLOYEE,
        role=payload.role,
        phone=payload.phone,
        department=payload.department,
        language=payload.language,
        job_title=payload.job_title,
        employment_status=payload.employment_status,
        start_date=datetime.fromisoformat(payload.start_date) if payload.start_date else None,
        photo_path=payload.photo_path,
        working_days=payload.working_days,
        working_hours=payload.working_hours,
        notes=payload.notes,
    )
    db.add(membership)
    db.flush()
    _apply_permissions(db, membership.id, payload.permissions)
    db.flush()

    record_audit_event(
        db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action="employee.created",
        entity_type="employee",
        entity_id=str(user.id),
        module_code="employees",
        summary=f"Added {payload.name} as {payload.role}",
    )
    return _to_employee_out(db, user, membership)


def _load_membership_or_404(db: Session, access: AccessContext, employee_id: str) -> TenantMembership:
    try:
        user_id = uuid.UUID(employee_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Employee not found.") from exc
    membership = db.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == access.tenant_id, TenantMembership.user_id == user_id
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=404, detail="Employee not found.")
    return membership


@router.patch("/employees/{employee_id}", response_model=EmployeeDetailOut)
def update_employee(
    employee_id: str,
    payload: EmployeeUpdate,
    access: AccessContext = Depends(require_permission("employees", "edit")),
    db: Session = Depends(get_control_db),
) -> EmployeeDetailOut:
    membership = _load_membership_or_404(db, access, employee_id)
    user = db.get(UserIdentity, membership.user_id)
    assert user is not None  # every membership has a backing UserIdentity

    changes: dict[str, dict[str, object]] = {}
    fields = payload.model_dump(
        exclude_unset=True, exclude={"password", "active", "name", "email", "start_date"}
    )
    for key, value in fields.items():
        before = getattr(membership, key)
        if before != value:
            changes[key] = {"from": before, "to": value}
        setattr(membership, key, value)
    if "start_date" in payload.model_fields_set:
        parsed = datetime.fromisoformat(payload.start_date) if payload.start_date else None
        if membership.start_date != parsed:
            changes["start_date"] = {"from": str(membership.start_date), "to": payload.start_date}
        membership.start_date = parsed
    if payload.name is not None and payload.name != user.display_name:
        changes["name"] = {"from": user.display_name, "to": payload.name}
        user.display_name = payload.name
    if payload.email is not None and payload.email != user.email:
        changes["email"] = {"from": user.email, "to": payload.email}
        user.email = payload.email
    if payload.active is not None:
        new_status = MembershipStatus.ACTIVE if payload.active else MembershipStatus.INACTIVE
        if membership.status != new_status:
            changes["active"] = {"from": membership.status == MembershipStatus.ACTIVE, "to": payload.active}
        membership.status = new_status
    if payload.password:
        user.password_hash = hash_password(payload.password)

    db.flush()
    if changes:
        record_audit_event(
            db,
            actor_id=access.user_id,
            actor_type=ActorType.TENANT_USER,
            actor_role=access.role,
            tenant_id=access.tenant_id,
            action="employee.updated",
            entity_type="employee",
            entity_id=str(user.id),
            module_code="employees",
            summary=f"Updated {user.display_name}",
            changes=changes,
        )
    return _to_employee_out(db, user, membership)


@router.delete("/employees/{employee_id}", status_code=204, response_model=None)
def deactivate_employee(
    employee_id: str,
    access: AccessContext = Depends(require_permission("employees", "delete")),
    db: Session = Depends(get_control_db),
) -> None:
    """Deactivates rather than deletes: an employee's name is attached to
    every record they ever entered, and history is never silently deleted
    — that applies to who did the work too.
    """
    membership = _load_membership_or_404(db, access, employee_id)
    membership.status = MembershipStatus.INACTIVE
    db.flush()
    record_audit_event(
        db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action="employee.deactivated",
        entity_type="employee",
        entity_id=employee_id,
        module_code="employees",
        summary="Deactivated an employee",
    )


@router.put("/employees/{employee_id}/permissions", response_model=EmployeeDetailOut)
def set_employee_permissions(
    employee_id: str,
    payload: PermissionSet,
    access: AccessContext = Depends(require_permission("employees", "configure")),
    db: Session = Depends(get_control_db),
) -> EmployeeDetailOut:
    """Replaces the employee's whole responsibility set — send every
    module they should hold; anything omitted is revoked.
    """
    membership = _load_membership_or_404(db, access, employee_id)
    user = db.get(UserIdentity, membership.user_id)
    assert user is not None
    _apply_permissions(db, membership.id, payload.permissions)
    db.flush()
    record_audit_event(
        db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action="employee.permissions_set",
        entity_type="employee",
        entity_id=str(user.id),
        module_code="employees",
        summary=f"Updated permissions for {user.display_name}",
        metadata={"module_count": len(payload.permissions)},
    )
    return _to_employee_out(db, user, membership)
