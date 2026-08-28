"""GET /priorities — a single ranked "what needs attention" feed the
tablet app's home screen renders directly. It aggregates across whatever
domains currently produce actionable items: open Tasks and unread
Notifications today, joined by later stages (health alerts, low-stock
recommendations, ...) the same way — append a source, don't change the
shape.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.common.db import get_control_db
from app.farmos.deps import AccessContext, check_farm_id, get_access_context, get_farmos_tenant_db
from app.farmos.farm_models import Notification
from app.farmos.schemas import PrioritiesPage, PriorityOut
from app.tenant_api.models import Task

router = APIRouter()

_PRIORITY_WEIGHT = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_OPEN_TASK_STATUSES = ("open", "in_progress")


def build_priorities(db: Session, control_db: Session) -> list[PriorityOut]:
    """Shared with GET /morning-briefing, which shows the same feed
    trimmed to a handful of items alongside the day's KPIs.
    """
    tasks = (
        db.execute(
            select(Task).where(
                Task.deleted_at.is_(None), Task.status.in_(_OPEN_TASK_STATUSES)
            )
        )
        .scalars()
        .all()
    )
    notifications = (
        db.execute(
            select(Notification).where(
                Notification.deleted_at.is_(None), Notification.read_at.is_(None)
            )
        )
        .scalars()
        .all()
    )

    assignee_ids = {task.assigned_to for task in tasks if task.assigned_to is not None}
    names: dict[str, str] = {}
    if assignee_ids:
        rows = control_db.execute(
            select(UserIdentity).where(UserIdentity.id.in_(assignee_ids))
        ).scalars()
        names = {str(user.id): user.display_name for user in rows}

    priorities: list[PriorityOut] = []
    for task in tasks:
        priorities.append(
            PriorityOut(
                id=str(task.id),
                kind="task",
                module_code="tasks",
                notification_type="task",
                title=task.title,
                description=task.description,
                priority=task.priority,
                status=task.status,
                entity_type=task.source_type,
                entity_id=task.source_id,
                source_type=task.source_type,
                source_id=task.source_id,
                due_at=task.due_at,
                assigned_to=str(task.assigned_to) if task.assigned_to else None,
                assigned_to_name=names.get(str(task.assigned_to)) if task.assigned_to else None,
            )
        )
    for note in notifications:
        priorities.append(
            PriorityOut(
                id=str(note.id),
                kind="notification",
                module_code=note.module_code,
                notification_type=note.notification_type,
                title=note.title,
                description=note.description,
                priority=note.priority,
                status=None,
                entity_type=note.entity_type,
                entity_id=note.entity_id,
                source_type=note.source_type,
                source_id=note.source_id,
                due_at=None,
                assigned_to=None,
                assigned_to_name=None,
            )
        )

    priorities.sort(
        key=lambda p: (_PRIORITY_WEIGHT.get(p.priority, 2), p.due_at is None, p.due_at or p.id)
    )
    return priorities


@router.get("/priorities", response_model=PrioritiesPage)
def list_priorities(
    farm_id: str = Query(...),
    limit: int = Query(default=50, le=200),
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> PrioritiesPage:
    check_farm_id(farm_id, access)
    priorities = build_priorities(db, control_db)[:limit]

    counts_by_priority: dict[str, int] = {}
    counts_by_module: dict[str, int] = {}
    for item in priorities:
        counts_by_priority[item.priority] = counts_by_priority.get(item.priority, 0) + 1
        counts_by_module[item.module_code] = counts_by_module.get(item.module_code, 0) + 1

    return PrioritiesPage(
        total=len(priorities),
        counts_by_priority=counts_by_priority,
        counts_by_module=counts_by_module,
        priorities=priorities,
    )
