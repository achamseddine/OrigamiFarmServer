"""The scheduled analytical cycles (FEED-PERFORMANCE-INTELLIGENCE §12 "daily
scheduled feed-performance cycle", MEDICINE-NOTIFICATION-RULES §Scheduled
expiry scan, CLINICAL-DECISION-SUPPORT §12 "schedule reassessment").

Event hooks keep the alerts current between runs; the scheduled cycle is
what guarantees a farm is evaluated at least daily even on a quiet day:
baselines refreshed, persistence checked, recovered conditions resolved,
expiry boundaries crossed, overdue reassessments surfaced.

Mechanics, deliberately plain for a modular monolith: an asyncio task
started by the API's lifespan wakes every `scheduler_interval_minutes`
and, for every active tenant, runs each job whose cadence has elapsed
since its last successful run, in a worker thread with its own
RLS-scoped tenant session on the authority of the farm's owner. Every
run is a `scheduled_job_run` row — when, how triggered, what it did, or
why it failed — and `POST /scheduler/run` runs the same code on demand
as the manager who pressed the button. A job that fails is logged and
does not stop the others; its own writes roll back to a savepoint, the
failed run row stays.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.common.db import ControlSessionLocal
from app.common.enums import MembershipStatus, TenantStatus
from app.common.tenant_router import TenantDataRouter
from app.config import get_settings
from app.farmos.deps import AccessContext
from app.farmos.livestock_service import ensure_utc, now_utc
from app.farmos.platform_models import JOB_CODES, ScheduledJobRun
from app.tenants.models import Tenant, TenantMembership

log = logging.getLogger("origami.scheduler")

# Tenants the background loop evaluates: live farms, and farms in their
# grace period (their data is still theirs to act on).
_RUNNING_STATUSES = (TenantStatus.ACTIVE, TenantStatus.TRIAL, TenantStatus.GRACE)


def _feed_performance_cycle(db: Session, access: AccessContext) -> dict:
    from app.farmos import feed_forecast_service as forecast
    from app.farmos import feed_performance_service as perf

    out = perf.run_cycle(db, access)
    bells = forecast.refresh_bell_signals(db, access)
    return {k: v for k, v in out.items() if k != "assessments"} | {"feed_bell_signals": bells}


def _pharmacy_scan(db: Session, access: AccessContext) -> dict:
    from app.farmos import pharmacy_service as ph

    return ph.evaluate_farm(db, access)


def _emergency_reassessment_check(db: Session, access: AccessContext) -> dict:
    from app.farmos import emergency_service as es

    return es.check_overdue_reassessments(db, access)


# job code -> (cadence, runner)
JOBS: dict[str, tuple[timedelta, Callable[[Session, AccessContext], dict]]] = {
    "feed_performance_cycle": (timedelta(hours=24), _feed_performance_cycle),
    "pharmacy_scan": (timedelta(hours=24), _pharmacy_scan),
    "emergency_reassessment_check": (timedelta(minutes=15), _emergency_reassessment_check),
}
assert tuple(JOBS) == JOB_CODES


def last_run(db: Session, job_code: str, *, successful: bool = True) -> ScheduledJobRun | None:
    stmt = select(ScheduledJobRun).where(ScheduledJobRun.job_code == job_code)
    if successful:
        stmt = stmt.where(ScheduledJobRun.status == "succeeded")
    return db.scalar(stmt.order_by(ScheduledJobRun.started_at.desc()))


def next_due(db: Session, job_code: str) -> datetime:
    last = last_run(db, job_code)
    if last is None:
        return now_utc()
    return (ensure_utc(last.started_at) or now_utc()) + JOBS[job_code][0]


def is_due(db: Session, job_code: str, at: datetime | None = None) -> bool:
    return next_due(db, job_code) <= (at or now_utc())


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def run_job(
    db: Session, access: AccessContext, job_code: str, *, trigger: str = "scheduler"
) -> ScheduledJobRun:
    """Runs one job for the tenant of `access` and records it. The job's
    writes live in a savepoint: a failure rolls them back and keeps only
    the failed run row, so the next run is a clean retry."""
    if job_code not in JOBS:
        raise ValueError(f"Unknown job {job_code}")
    started = now_utc()
    row = ScheduledJobRun(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        job_code=job_code,
        trigger=trigger,
        started_at=started,
        status="running",
        summary_json={},
        run_by=access.user_id,
    )
    try:
        with db.begin_nested():
            summary = JOBS[job_code][1](db, access)
        row.status = "succeeded"
        row.summary_json = _jsonable(summary)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded, none stops the others
        row.status = "failed"
        row.error = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()[-1500:]}"
        log.warning("scheduled job %s failed for tenant %s: %s", job_code, access.tenant_id, exc)
    row.finished_at = now_utc()
    db.add(row)
    db.flush()
    return row


def run_due(
    db: Session,
    access: AccessContext,
    *,
    job_codes: list[str] | None = None,
    force: bool = False,
    trigger: str = "scheduler",
) -> list[ScheduledJobRun]:
    runs = []
    for code in job_codes or list(JOBS):
        if force or is_due(db, code):
            runs.append(run_job(db, access, code, trigger=trigger))
    return runs


def status(db: Session) -> list[dict]:
    out = []
    for code, (cadence, _) in JOBS.items():
        latest = last_run(db, code, successful=False)
        success = last_run(db, code)
        out.append(
            {
                "job_code": code,
                "cadence_minutes": int(cadence.total_seconds() // 60),
                "last_run_at": latest.started_at if latest else None,
                "last_status": latest.status if latest else None,
                "last_error": latest.error if latest else None,
                "last_success_at": success.started_at if success else None,
                "last_summary": success.summary_json if success else {},
                "next_due_at": next_due(db, code),
                "due": is_due(db, code),
            }
        )
    return out


def run_dict(r: ScheduledJobRun) -> dict:
    return {
        "id": str(r.id),
        "job_code": r.job_code,
        "trigger": r.trigger,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "status": r.status,
        "summary": r.summary_json,
        "error": r.error,
    }


# ---------------------------------------------------------------- loop
def system_access_for(tenant_id: uuid.UUID) -> AccessContext | None:
    """The authority the background loop runs under for one farm: its
    owner's membership (then a manager's). A farm with no active owner or
    manager is skipped — the loop never invents an actor."""
    with ControlSessionLocal() as cdb:
        row = cdb.execute(
            select(
                TenantMembership.id,
                TenantMembership.user_id,
                TenantMembership.role,
                UserIdentity.email,
                UserIdentity.display_name,
            )
            .join(UserIdentity, UserIdentity.id == TenantMembership.user_id)
            .where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.status == MembershipStatus.ACTIVE,
                TenantMembership.role.in_(("owner", "manager")),
            )
            .order_by(TenantMembership.role.desc(), TenantMembership.created_at)
        ).first()
    if row is None:
        return None
    return AccessContext(
        membership_id=row[0],
        user_id=row[1],
        tenant_id=tenant_id,
        email=row[3],
        display_name=row[4],
        role=row[2],
        full_access=True,
    )


def tick() -> int:
    """One pass over every live tenant: the due jobs, each farm in its own
    RLS-scoped session that commits on success."""
    with ControlSessionLocal() as cdb:
        tenant_ids = list(cdb.scalars(select(Tenant.id).where(Tenant.status.in_(_RUNNING_STATUSES))))
    ran = 0
    for tenant_id in tenant_ids:
        access = system_access_for(tenant_id)
        if access is None:
            continue
        try:
            with TenantDataRouter.session_for(tenant_id) as db:
                ran += len(run_due(db, access, trigger="scheduler"))
        except Exception:  # noqa: BLE001 - one farm's failure must not stop the others
            log.exception("scheduler tick failed for tenant %s", tenant_id)
    return ran


async def loop(interval_seconds: float) -> None:
    """Sleeps first: the API starts without a burst of analytics, and a
    short-lived process (a test client) never runs a cycle."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            ran = await asyncio.to_thread(tick)
            if ran:
                log.info("scheduler ran %d job(s)", ran)
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")


def start() -> asyncio.Task | None:
    settings = get_settings()
    if not settings.scheduler_enabled or settings.scheduler_interval_minutes <= 0:
        return None
    return asyncio.get_running_loop().create_task(loop(settings.scheduler_interval_minutes * 60))
