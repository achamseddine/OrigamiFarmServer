"""Derived metrics for the admin dashboards.

Everything here is computed from data the system already holds — tenant and
device state, entitlements, issued leases, audit volume, and row counts in
each tenant's own farm data. Nothing is estimated or projected, and there
is no separate metering pipeline behind it: usage_meter exists in the
schema but nothing writes to it yet, so it is deliberately not read here
rather than reported as zeroes.

Farm-data counts go through TenantDataRouter one tenant at a time so that
row-level security still scopes every read to that tenant. That is slower
than one cross-tenant query would be, and it is the point: a reporting
path that bypassed RLS would be the one place in this codebase where
tenant isolation depended on remembering a WHERE clause.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.common.tenant_router import TenantDataRouter
from app.tenant_models_registry import TenantBase

# Which farm-data tables stand for which module in the console. A module's
# count is the sum of its tables; tables not listed (join tables, sync
# bookkeeping, per-row cost breakdowns) would inflate the numbers without
# telling anyone anything.
MODULE_TABLES: dict[str, tuple[str, ...]] = {
    "animals": ("animal",),
    "animal_health": ("treatment",),
    "observations": ("observation",),
    "tasks": ("task",),
    "milk_production": ("milk_record",),
    "egg_production": ("egg_record",),
    "produce_harvest": ("harvest_record", "daily_harvest"),
    "agriculture": ("field", "crop", "crop_planting"),
    "inventory": ("inventory_item", "inventory_movement"),
    "sales": ("sale",),
    "expenses": ("expense",),
    "mouneh_production": ("mouneh_production_batch", "mouneh_product", "mouneh_sale"),
    "farm_visits": ("visit_booking", "visit_session", "visitor_profile"),
    "notifications": ("notification",),
    "ai_intelligence": ("recommendation",),
}

_COUNTED_TABLES: tuple[str, ...] = tuple(
    table for tables in MODULE_TABLES.values() for table in tables
)


def _counts_sql() -> str:
    """One UNION ALL over every counted table.

    Table names come from our own MODULE_TABLES and are validated against
    the mapped metadata below, so they are never user input. Soft-deleted
    rows are excluded — a tombstone is not a record the farm still has.
    """
    parts = []
    for table in _COUNTED_TABLES:
        columns = TenantBase.metadata.tables[table].columns
        where = " WHERE deleted_at IS NULL" if "deleted_at" in columns else ""
        activity = "max(created_at)" if "created_at" in columns else "NULL::timestamptz"
        parts.append(
            f"SELECT '{table}' AS source, count(*) AS rows, {activity} AS last_at FROM {table}{where}"
        )
    return " UNION ALL ".join(parts)


def tenant_farm_data_usage(tenant_id: uuid.UUID) -> dict:
    """Row counts and last activity for one tenant's farm data."""
    with TenantDataRouter.session_for(tenant_id) as db:
        rows = db.execute(text(_counts_sql())).all()

    per_table = {row.source: (row.rows, row.last_at) for row in rows}

    by_module: dict[str, int] = {}
    for module, tables in MODULE_TABLES.items():
        by_module[module] = sum(per_table.get(table, (0, None))[0] for table in tables)

    timestamps = [last_at for _, last_at in per_table.values() if last_at is not None]

    return {
        "records_by_module": by_module,
        "total_records": sum(by_module.values()),
        "modules_with_data": sorted(module for module, count in by_module.items() if count > 0),
        "last_activity_at": max(timestamps) if timestamps else None,
    }


def audit_events_per_day(db: Session, days: int) -> list[dict]:
    """Audit volume per day, zero-filled so the series has no gaps."""
    since = datetime.now(timezone.utc) - timedelta(days=days - 1)
    rows = db.execute(
        text(
            """
            SELECT date_trunc('day', created_at) AS day, count(*) AS events
            FROM audit_event
            WHERE created_at >= :since
            GROUP BY 1
            ORDER BY 1
            """
        ),
        {"since": since},
    ).all()
    counted = {row.day.date().isoformat(): row.events for row in rows}

    start = since.date()
    return [
        {
            "day": (day := (start + timedelta(days=offset)).isoformat()),
            "events": counted.get(day, 0),
        }
        for offset in range(days)
    ]


def tenants_created_per_month(db: Session, months: int) -> list[dict]:
    since = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=31 * (months - 1)
    )
    rows = db.execute(
        text(
            """
            SELECT to_char(date_trunc('month', created_at), 'YYYY-MM') AS month, count(*) AS tenants
            FROM tenant
            WHERE created_at >= :since
            GROUP BY 1
            ORDER BY 1
            """
        ),
        {"since": since},
    ).all()
    return [{"month": row.month, "tenants": row.tenants} for row in rows]
