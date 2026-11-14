from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.db import get_control_db
from app.common.enums import MembershipStatus
from app.farmos import duplicates
from app.farmos.deps import AccessContext, check_farm_id, get_farmos_tenant_db, require_permission
from app.farmos.schemas import TaskCreate, TaskOut, TaskUpdate
from app.tenant_api.models import Task
from app.tenants.models import TenantMembership

router = APIRouter()

_OPEN_STATUSES = ("open", "in_progress")


def to_task_out(task: Task) -> TaskOut:
    """See _to_animal_out in routes_animals.py: Task.farm_id is
    SyncedEntityMixin's own column, not the wire's farm_id (=tenant_id).
    """
    return TaskOut(
        id=str(task.id),
        farm_id=str(task.tenant_id),
        title=task.title,
        description=task.description,
        assigned_to=str(task.assigned_to) if task.assigned_to else None,
        due_at=task.due_at,
        priority=task.priority,
        status=task.status,
        source_type=task.source_type,
        source_id=task.source_id,
    )


def _load_task_or_404(db: Session, task_id: str) -> Task:
    try:
        pk = uuid.UUID(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Task not found.") from exc
    task = db.get(Task, pk)
    if task is None or task.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


def _assignee_or_404(control_db: Session, access: AccessContext, assigned_to: str) -> uuid.UUID:
    """A task is assigned to someone on this farm: another farm's user id
    answers like a missing one."""
    try:
        pk = uuid.UUID(assigned_to)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Assignee not found.") from exc
    membership = control_db.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == access.tenant_id,
            TenantMembership.user_id == pk,
            TenantMembership.status == MembershipStatus.ACTIVE,
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=404, detail="Assignee not found.")
    return pk


def _check_assignment_allowed(access: AccessContext, assigned_to: str | None) -> None:
    """Farm managers decide, assign and review; employees see and act on
    their own tasks. Assigning to anyone other than yourself needs
    full_access — a self-assigned reminder (or leaving it unassigned)
    doesn't.
    """
    if assigned_to and assigned_to != str(access.user_id) and not access.full_access:
        raise HTTPException(
            status_code=403, detail="Only a farm manager or owner can assign tasks to someone else."
        )


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    farm_id: str = Query(...),
    status_filter: str | None = Query(default=None),
    assigned_to: str | None = Query(default=None),
    access: AccessContext = Depends(require_permission("tasks", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[TaskOut]:
    check_farm_id(farm_id, access)
    stmt = select(Task).where(Task.deleted_at.is_(None))
    if status_filter:
        stmt = stmt.where(Task.status == status_filter)
    if assigned_to:
        stmt = stmt.where(Task.assigned_to == uuid.UUID(assigned_to))
    rows = db.execute(stmt.order_by(Task.due_at.asc().nulls_last())).scalars().all()
    return [to_task_out(row) for row in rows]


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(
    payload: TaskCreate,
    access: AccessContext = Depends(require_permission("tasks", "create")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> TaskOut:
    check_farm_id(payload.farm_id, access)
    _check_assignment_allowed(access, payload.assigned_to)
    assigned_to = _assignee_or_404(control_db, access, payload.assigned_to) if payload.assigned_to else None
    # An open task with the same title is the same job written down twice —
    # unless the person says it is a second one (app/farmos/duplicates.py).
    twin = db.scalar(
        select(Task).where(
            Task.deleted_at.is_(None),
            Task.status.in_(_OPEN_STATUSES),
            func.lower(Task.title) == payload.title.strip().lower(),
        )
    )
    match = duplicates.guard(
        twin,
        f"An open task named {twin.title if twin else ''} already exists.",
        confirm=payload.confirm_duplicate,
    )
    task = Task(
        tenant_id=access.tenant_id,
        title=payload.title,
        description=payload.description,
        assigned_to=assigned_to,
        due_at=payload.due_at,
        priority=payload.priority,
        source_type=payload.source_type,
        source_id=payload.source_id,
        last_modified_by=access.membership_id,
    )
    db.add(task)
    db.flush()
    duplicates.log_confirmed(
        control_db,
        access,
        entity_type="task",
        entity_id=str(task.id),
        match_id=match.id if match else None,
        what="task",
        module_code="tasks",
    )
    return to_task_out(task)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: str,
    payload: TaskUpdate,
    access: AccessContext = Depends(require_permission("tasks", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> TaskOut:
    """Employees see and act on their own tasks: without full access, only a
    task assigned to you (or to nobody) can be changed."""
    task = _load_task_or_404(db, task_id)
    if not access.full_access and task.assigned_to not in (None, access.user_id):
        raise HTTPException(status_code=403, detail="You can only update a task assigned to you.")
    if payload.assigned_to is not None:
        _check_assignment_allowed(access, payload.assigned_to)
        task.assigned_to = (
            _assignee_or_404(control_db, access, payload.assigned_to) if payload.assigned_to else None
        )
    if payload.status is not None:
        task.status = payload.status
    if payload.priority is not None:
        task.priority = payload.priority
    task.version += 1
    task.last_modified_by = access.membership_id
    db.flush()
    return to_task_out(task)


@router.delete("/tasks/{task_id}", status_code=204, response_model=None)
def delete_task(
    task_id: str,
    access: AccessContext = Depends(require_permission("tasks", "delete")),
    db: Session = Depends(get_farmos_tenant_db),
) -> None:
    task = _load_task_or_404(db, task_id)
    task.deleted_at = datetime.now(timezone.utc)
    task.last_modified_by = access.membership_id
    db.flush()
