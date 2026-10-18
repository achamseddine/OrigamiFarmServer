"""Feed Performance Intelligence API (OrigamiFarmOS
database/FEED-PERFORMANCE-INTELLIGENCE.md).

Monitors say what is watched; the cycle evaluates them against explicit
baselines; assessments, mix scores, supplier rows and alerts are what it
produces. Reads need the feed module; running the cycle, editing a
monitor or acknowledging an alert need edit; closing an alert by hand
needs approve, with a reason.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import feed_performance_service as perf
from app.farmos.deps import AccessContext, get_farmos_tenant_db, require_permission
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.feed_performance_models import (
    FeedBatchPerformanceScore,
    FeedPerformanceAlert,
    FeedPerformanceAssessment,
    FeedPerformanceMonitor,
    SupplierFeedPerformance,
)
from app.farmos.livestock_service import ensure_utc
from app.farmos.platform_schemas import (
    FeedPerformanceAlertClose,
    FeedPerformanceMonitorIn,
    FeedPerformanceMonitorPatch,
)
from app.farmos.routing_service import NameCache

router = APIRouter(prefix="/feed-performance")

_view = require_permission("feed_nutrition", "view")
_edit = require_permission("feed_nutrition", "edit")
_approve = require_permission("feed_nutrition", "approve")


def _audit(
    control_db: Session,
    access: AccessContext,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    summary: str,
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
        module_code="feed_nutrition",
        summary=summary,
        changes=changes,
    )


@router.get("/summary")
def performance_summary(
    _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    """Counts for the Performance tab and the bell: open alerts by type,
    active monitors, when the cycle last ran, and the model it ran."""
    return perf.summary(db)


@router.post("/evaluate")
def evaluate(
    as_of: datetime | None = None,
    monitor_id: str | None = None,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Runs the feed-performance cycle now (§12): exposure projection,
    every active monitor against its baseline, mix and supplier scores,
    cost and intake checks. `as_of` evaluates as of an earlier day."""
    out = perf.run_cycle(db, access, as_of=ensure_utc(as_of) if as_of else None, monitor_id=monitor_id)
    _audit(
        control_db,
        access,
        action="feed_performance.cycle_run",
        entity_type="farm",
        entity_id=str(access.tenant_id),
        summary=(
            f"Feed performance cycle: {out['monitors']} monitor(s), {out['anomalies']} anomaly(ies), "
            f"{out['open_alerts']} open alert(s)"
        ),
    )
    return out


# --------------------------------------------------------------- monitors
@router.get("/monitors")
def list_monitors(
    include_inactive: bool = False,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(FeedPerformanceMonitor).where(FeedPerformanceMonitor.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(FeedPerformanceMonitor.active.is_(True))
    return [perf.monitor_dict(db, m) for m in db.scalars(stmt.order_by(FeedPerformanceMonitor.created_at))]


@router.post("/monitors", status_code=201)
def create_monitor(
    payload: FeedPerformanceMonitorIn,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Watches one animal or group on one metric against one explicit
    baseline method. The same subject and metric twice returns the
    existing monitor."""
    m = perf.create_monitor(db, access, **payload.model_dump())
    _audit(
        control_db,
        access,
        action="feed_performance.monitor_created",
        entity_type=m.subject_type,
        entity_id=str(m.subject_id),
        summary=f"Monitoring {m.production_metric_code} against the {m.baseline_method_code} baseline",
        changes={
            "monitor_id": str(m.id),
            "metric": m.production_metric_code,
            "baseline": m.baseline_method_code,
            "threshold_pct": m.alert_threshold_percent,
        },
    )
    return perf.monitor_dict(db, m)


@router.patch("/monitors/{monitor_id}")
def update_monitor(
    monitor_id: str,
    payload: FeedPerformanceMonitorPatch,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    m = perf.get_monitor(db, monitor_id)
    changes = payload.model_dump(exclude_unset=True)
    perf.update_monitor(db, access, m, changes)
    _audit(
        control_db,
        access,
        action="feed_performance.monitor_updated",
        entity_type=m.subject_type,
        entity_id=str(m.subject_id),
        summary=f"Monitor {m.production_metric_code}: changed {', '.join(sorted(changes)) or 'nothing'}",
        changes={"monitor_id": str(m.id), "changed": sorted(changes)},
    )
    return perf.monitor_dict(db, m)


@router.post("/monitors/{monitor_id}/evaluate")
def evaluate_monitor(
    monitor_id: str,
    as_of: datetime | None = None,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    m = perf.get_monitor(db, monitor_id)
    perf.rebuild_exposures(db, access)
    a = perf.evaluate_monitor(db, access, m, as_of=ensure_utc(as_of) if as_of else None, trigger="manual")
    return perf.assessment_dict(db, a)


# ------------------------------------------------------------ assessments
@router.get("/assessments")
def list_assessments(
    monitor_id: str | None = None,
    subject_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(FeedPerformanceAssessment)
    if monitor_id:
        pk = parse_uuid(monitor_id)
        if pk is None:
            return []
        stmt = stmt.where(FeedPerformanceAssessment.monitor_id == pk)
    if subject_id:
        spk = parse_uuid(subject_id)
        if spk is None:
            return []
        ids = db.scalars(
            select(FeedPerformanceMonitor.id).where(FeedPerformanceMonitor.subject_id == spk)
        ).all()
        stmt = stmt.where(FeedPerformanceAssessment.monitor_id.in_(ids))
    rows = db.scalars(stmt.order_by(FeedPerformanceAssessment.evaluated_at.desc()).limit(limit))
    return [perf.assessment_dict(db, a) for a in rows]


@router.get("/exposures")
def list_exposures(
    subject_type: str | None = None,
    subject_id: str | None = None,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    """The exposure projection: which lot and mix each animal or group
    actually received, from when to when, with its supplier lineage."""
    return [perf.exposure_dict(db, w) for w in perf.exposures_for(db, subject_type, subject_id)]


# ------------------------------------------------------------------ alerts
@router.get("/alerts")
def list_alerts(
    alert_status: str | None = Query("open", alias="status"),
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    """`status=open` (default) returns open and acknowledged alerts;
    `status=all` includes resolved ones."""
    stmt = select(FeedPerformanceAlert).where(FeedPerformanceAlert.deleted_at.is_(None))
    if alert_status == "open":
        stmt = stmt.where(FeedPerformanceAlert.status != "resolved")
    elif alert_status and alert_status != "all":
        stmt = stmt.where(FeedPerformanceAlert.status == alert_status)
    names = NameCache()
    return [
        perf.alert_dict(db, a, names)
        for a in db.scalars(stmt.order_by(FeedPerformanceAlert.detected_at.desc()))
    ]


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: str,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    a = perf.acknowledge_alert(db, access, perf.get_alert(db, alert_id))
    _audit(
        control_db,
        access,
        action="feed_performance.alert_acknowledged",
        entity_type="feed_performance_alert",
        entity_id=str(a.id),
        summary=f"Acknowledged: {a.title}",
    )
    return perf.alert_dict(db, a, NameCache())


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: str,
    payload: FeedPerformanceAlertClose,
    access: AccessContext = Depends(_approve),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Closes an alert by hand, with a reason — for a condition a person has
    explained (an authorised substitution, a measurement fault)."""
    a = perf.resolve_alert_manually(db, access, perf.get_alert(db, alert_id), note=payload.note)
    _audit(
        control_db,
        access,
        action="feed_performance.alert_resolved",
        entity_type="feed_performance_alert",
        entity_id=str(a.id),
        summary=f"Closed by hand: {a.title} — {a.resolution_note}",
        changes={"note": a.resolution_note},
    )
    return perf.alert_dict(db, a, NameCache())


# -------------------------------------------------------- scores & suppliers
@router.get("/batches")
def list_batch_scores(
    _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    rows = db.scalars(select(FeedBatchPerformanceScore)).all()
    out = [perf.batch_score_dict(db, r) for r in rows]
    out.sort(key=lambda d: -(d["mix_number"] or 0))
    return out


@router.get("/batches/{batch_id}")
def batch_score(
    batch_id: str, _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    pk = parse_uuid(batch_id)
    row = (
        db.scalar(select(FeedBatchPerformanceScore).where(FeedBatchPerformanceScore.feed_batch_id == pk))
        if pk
        else None
    )
    if row is None:
        raise perf.PerformanceError("No performance score for this mix yet — run the cycle.", 404)
    return perf.batch_score_dict(db, row)


@router.get("/suppliers")
def list_supplier_performance(
    _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    rows = db.scalars(
        select(SupplierFeedPerformance).order_by(
            SupplierFeedPerformance.supplier_label, SupplierFeedPerformance.feed_product_id
        )
    ).all()
    return [perf.supplier_row_dict(db, r) for r in rows]


@router.get("/ingredient-lots")
def list_ingredient_lot_performance(
    access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    """Purchased ingredient lots against the production response of the
    mixes that used them (§8): the lot, its supplier, the mixes, the mean
    response beside the farm's other mixes of the same feed, and whether a
    manufacturing deviation on those mixes clouds the comparison. Read
    from the last cycle's scores; the cycle itself raises the alerts."""
    return perf.ingredient_lot_performance(db, access, write_alerts=False)


@router.get("/effective-feed-value")
def get_effective_feed_value(
    days: int = Query(7, ge=1, le=90),
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    """§10 — purchase price alone is insufficient: feed cost over the
    window, cost per litre and per head-day, milk revenue at the farm's own
    recent selling price less feed cost, and the dry-matter-adjusted cost
    of the mixes in use. Every figure that cannot be computed is named in
    `missing` rather than guessed."""
    return perf.effective_feed_value(db, days=days)
