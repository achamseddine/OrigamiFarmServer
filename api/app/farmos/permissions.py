"""The fixed permission-grid vocabulary shared by app/farmos/deps.py
(authorization) and the employee/access serializers (what gets shown).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.tenants.models import MembershipModulePermission

ACTIONS: tuple[str, ...] = (
    "view",
    "create",
    "edit",
    "delete",
    "approve",
    "export",
    "assign",
    "configure",
)

# 20 module codes recognized by the permission grid (GET /modules/catalog).
MODULE_CODES: tuple[str, ...] = (
    "agriculture",
    "ai_intelligence",
    "animal_health",
    "animals",
    "egg_production",
    "employees",
    "expenses",
    "farm_visits",
    "feed_nutrition",
    "finance",
    "inventory",
    "milk_production",
    "morning_operations",
    "mouneh_inventory",
    "mouneh_production",
    "produce_harvest",
    "reports",
    "sales",
    "settings",
    "tasks",
)


def permissions_grid(db: Session, membership_id: uuid.UUID) -> dict[str, dict[str, bool]]:
    """One key per module the membership has at least one permission row
    for — a module with zero rows is simply absent, not present-and-false.
    """
    rows = db.execute(
        select(MembershipModulePermission).where(
            MembershipModulePermission.membership_id == membership_id
        )
    ).scalars().all()
    grid: dict[str, dict[str, bool]] = {}
    for row in rows:
        grid.setdefault(row.module_code, dict.fromkeys(ACTIONS, False))
        if row.permission_code in ACTIONS:
            grid[row.module_code][row.permission_code] = True
    return grid


def full_access_grid() -> dict[str, dict[str, bool]]:
    return {module: dict.fromkeys(ACTIONS, True) for module in MODULE_CODES}
