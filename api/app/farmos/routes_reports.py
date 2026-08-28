"""GET /morning-briefing — the tablet app's home-screen summary: today's
date, a handful of top priorities, today's tasks, and a few headline KPIs.
KPIs from domains not built yet (milk/eggs — Stage 3) honestly report 0
rather than being guessed or omitted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.common.db import get_control_db
from app.common.enums import MembershipStatus
from app.farmos.deps import AccessContext, check_farm_id, get_access_context, get_farmos_tenant_db
from app.farmos.routes_priorities import build_priorities
from app.farmos.routes_tasks import to_task_out
from app.farmos.schemas import MorningBriefingOut
from app.tenant_api.models import Animal, Field, Task
from app.tenants.models import Tenant, TenantMembership

router = APIRouter()

_OPEN_TASK_STATUSES = ("open", "in_progress")
_MANAGER_ROLE_RANK = {"owner": 0, "manager": 1}


@router.get("/morning-briefing", response_model=MorningBriefingOut)
def morning_briefing(
    farm_id: str = Query(...),
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> MorningBriefingOut:
    check_farm_id(farm_id, access)

    tenant = control_db.get(Tenant, access.tenant_id)
    assert tenant is not None  # AccessContext already resolved this same tenant

    managers = control_db.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == access.tenant_id,
            TenantMembership.status == MembershipStatus.ACTIVE,
            TenantMembership.role.in_(("owner", "manager")),
        )
    ).scalars().all()
    manager_name = None
    if managers:
        best = min(managers, key=lambda m: _MANAGER_ROLE_RANK.get(m.role, 99))
        user = control_db.get(UserIdentity, best.user_id)
        manager_name = user.display_name if user else None

    now = datetime.now(timezone.utc)
    animal_count = db.execute(
        select(func.count()).select_from(Animal).where(
            Animal.deleted_at.is_(None), Animal.active.is_(True)
        )
    ).scalar_one()
    active_fields = db.execute(
        select(func.count()).select_from(Field).where(Field.deleted_at.is_(None))
    ).scalar_one()

    todays_tasks = list(
        db.execute(
            select(Task)
            .where(
                Task.deleted_at.is_(None),
                Task.status.in_(_OPEN_TASK_STATUSES),
                Task.due_at.is_not(None),
                Task.due_at <= now,
            )
            .order_by(Task.due_at.asc())
        )
        .scalars()
        .all()
    )
    tasks_due = len(todays_tasks)

    priorities = build_priorities(db, control_db)
    open_alerts = sum(1 for p in priorities if p.priority in ("critical", "high"))

    return MorningBriefingOut(
        date=now.date().isoformat(),
        farm_name=tenant.display_name,
        manager_name=manager_name,
        kpis={
            "animals": animal_count,
            "milk_today_l": 0,
            "eggs_today": 0,
            "active_fields": active_fields,
            "tasks_due": tasks_due,
            "open_alerts": open_alerts,
        },
        priorities=priorities[:5],
        tasks=[to_task_out(t) for t in todays_tasks[:10]],
    )
