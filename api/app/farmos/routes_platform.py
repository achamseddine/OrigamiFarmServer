"""Platform masters: units of measure, farm assets, notification routes
and the scheduled analytical cycles (OrigamiFarmOS FOUNDATION-SCHEMA uom,
ASSET-MAINTENANCE-SCHEMA §2, MEDICINE-NOTIFICATION-RULES §Notification
behavior, FEED-PERFORMANCE-INTELLIGENCE §12).

Units and routes are configuration (Settings configure); assets are
operational records (inventory or feed create / edit); the scheduler's
status is readable by anyone who holds Settings, and running it on demand
is an owner's or manager's action.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import asset_service as assets
from app.farmos import routing_service as routing
from app.farmos import scheduler_service as scheduler
from app.farmos import uom_service
from app.farmos.deps import (
    AccessContext,
    get_access_context,
    get_farmos_tenant_db,
    require_any_permission,
    require_owner_or_manager,
)
from app.farmos.feed_catalog import FeedError
from app.farmos.platform_models import FarmAsset, ScheduledJobRun
from app.farmos.platform_schemas import AssetIn, AssetPatch, RoutesSet, SchedulerRunIn, UnitIn

router = APIRouter()

_configure = require_any_permission(("settings", "configure"), ("settings", "edit"))
_settings_view = require_any_permission(
    ("settings", "view"), ("inventory", "view"), ("feed_nutrition", "view"), ("animal_health", "view")
)
_asset_write = require_any_permission(
    ("inventory", "create"), ("inventory", "edit"), ("feed_nutrition", "create"), ("feed_nutrition", "edit")
)


def _audit(
    control_db: Session,
    access: AccessContext,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    summary: str,
    module_code: str = "settings",
    changes: dict | None = None,
) -> None:
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        module_code=module_code,
        summary=summary,
        changes=changes,
    )


# ---------------------------------------------------------------- units
@router.get("/units-of-measure")
def list_units(
    include_inactive: bool = False,
    _access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    """The unit table every unit column references: built-in plus the
    farm's own."""
    return uom_service.list_units(db, include_inactive=include_inactive)


@router.post("/units-of-measure", status_code=201)
def create_unit(
    payload: UnitIn,
    access: AccessContext = Depends(_configure),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    row = uom_service.create_unit(db, access, **payload.model_dump())
    _audit(
        control_db,
        access,
        action="unit_of_measure.created",
        entity_type="unit_of_measure",
        entity_id=str(row.id),
        summary=f"Added unit {row.code} ({row.dimension})",
        changes={"code": row.code, "dimension": row.dimension, "base_unit_code": row.base_unit_code},
    )
    return uom_service.unit_dict(row)


# --------------------------------------------------------------- assets
@router.get("/assets")
def list_assets(
    asset_type: str | None = None,
    include_retired: bool = False,
    _access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(FarmAsset).where(FarmAsset.deleted_at.is_(None))
    if asset_type:
        stmt = stmt.where(FarmAsset.asset_type == asset_type)
    if not include_retired:
        stmt = stmt.where(FarmAsset.status != "retired")
    return [assets.asset_dict(a) for a in db.scalars(stmt.order_by(FarmAsset.name))]


@router.post("/assets", status_code=201)
def create_asset(
    payload: AssetIn,
    access: AccessContext = Depends(_asset_write),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """A durable resource (ASSET-MAINTENANCE §2): the mixer a numbered mix
    names, a cooling tank, a milking machine."""
    row = assets.create_asset(db, access, **payload.model_dump())
    _audit(
        control_db,
        access,
        action="farm_asset.registered",
        entity_type="farm_asset",
        entity_id=str(row.id),
        summary=f"Registered {row.asset_type.replace('_', ' ')} {row.name} ({row.code})",
        module_code="inventory",
    )
    return assets.asset_dict(row)


@router.get("/assets/{asset_id}")
def get_asset(
    asset_id: str,
    _access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    return assets.asset_dict(assets.get_asset(db, asset_id))


@router.patch("/assets/{asset_id}")
def update_asset(
    asset_id: str,
    payload: AssetPatch,
    access: AccessContext = Depends(_asset_write),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    asset = assets.get_asset(db, asset_id)
    before = asset.status
    changes = payload.model_dump(exclude_unset=True)
    assets.update_asset(db, access, asset, changes)
    action = "farm_asset.updated"
    if changes.get("status") and changes["status"] != before:
        action = {
            "out_of_service": "farm_asset.out_of_service",
            "active": "farm_asset.returned_to_service",
            "retired": "farm_asset.retired",
        }.get(changes["status"], "farm_asset.updated")
    _audit(
        control_db,
        access,
        action=action,
        entity_type="farm_asset",
        entity_id=str(asset.id),
        summary=f"Updated {asset.name}: {', '.join(sorted(changes)) or 'no change'}",
        module_code="inventory",
        changes={"changed": sorted(changes), "status": {"from": before, "to": asset.status}},
    )
    return assets.asset_dict(asset)


# --------------------------------------------------------- notification routes
@router.get("/notification-routes")
def list_routes(
    access: AccessContext = Depends(_settings_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    """Who each route reaches: the farm's configuration and the built-in
    default it falls back to, with the effective recipients resolved."""
    return routing.routes_overview(db, access.tenant_id)


@router.put("/notification-routes/{route_code}")
def set_routes(
    route_code: str,
    payload: RoutesSet,
    access: AccessContext = Depends(_configure),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    rows = routing.set_routes(db, access, route_code, [e.model_dump() for e in payload.entries])
    _audit(
        control_db,
        access,
        action="notification_routes.changed",
        entity_type="notification_route",
        entity_id=route_code,
        summary=f"Routed {route_code} to "
        + (", ".join(r.role or str(r.user_id) for r in rows) if rows else "the built-in default"),
        changes={
            "route_code": route_code,
            "entries": [{"role": r.role, "user_id": str(r.user_id) if r.user_id else None} for r in rows],
        },
    )
    return routing.route_dict(db, access.tenant_id, route_code)


# ------------------------------------------------------------- scheduler
@router.get("/scheduler/jobs")
def scheduler_jobs(
    _access: AccessContext = Depends(_settings_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    """The scheduled cycles (feed performance, pharmacy scan, reassessment
    check): cadence, last run, last result, next due."""
    return scheduler.status(db)


@router.get("/scheduler/runs")
def scheduler_runs(
    job_code: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    _access: AccessContext = Depends(_settings_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(ScheduledJobRun)
    if job_code:
        stmt = stmt.where(ScheduledJobRun.job_code == job_code)
    return [
        scheduler.run_dict(r)
        for r in db.scalars(stmt.order_by(ScheduledJobRun.started_at.desc()).limit(limit))
    ]


@router.post("/scheduler/run")
def scheduler_run(
    payload: SchedulerRunIn | None = None,
    access: AccessContext = Depends(require_owner_or_manager),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Runs the cycles now (all, or the ones named). `force=false` runs
    only what is due."""
    p = payload or SchedulerRunIn()
    unknown = [c for c in (p.job_codes or []) if c not in scheduler.JOBS]
    if unknown:
        raise FeedError(f"Unknown job(s): {', '.join(unknown)}. Known: {', '.join(scheduler.JOBS)}.")
    runs = scheduler.run_due(db, access, job_codes=p.job_codes, force=p.force, trigger="manual")
    if runs:
        _audit(
            control_db,
            access,
            action="scheduler.run",
            entity_type="scheduled_job_run",
            entity_id=str(runs[-1].id),
            summary="Ran " + ", ".join(f"{r.job_code} ({r.status})" for r in runs),
            changes={"runs": [{"job_code": r.job_code, "status": r.status} for r in runs]},
        )
    return {
        "ran": len(runs),
        "runs": [
            {
                "id": str(r.id),
                "job_code": r.job_code,
                "status": r.status,
                "summary": r.summary_json,
                "error": r.error,
            }
            for r in runs
        ],
        "jobs": scheduler.status(db),
    }
