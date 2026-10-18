"""Feed Performance Intelligence (OrigamiFarmOS
database/FEED-PERFORMANCE-INTELLIGENCE.md), the deterministic increment,
ported one-to-one from the reference backend onto the tenant data plane.

What it does, and only this:

- connects what was bought (supplier lot), what was mixed (numbered mix,
  actual components), what was fed (feeding events through the output
  lot) and what the animals produced afterwards (milk / eggs / growth);
- compares production in an evaluation window against an **explicit
  baseline** the monitor names — rolling subject, comparable previous
  period, lactation-stage-adjusted, matched peer group or an approved
  expected production curve — and says how big and how persistent the
  change is;
- lists the feed changes that preceded it, with their lineage, and the
  confounders it could and could not check (§6); missing context lowers
  the confidence, it is never read as "no change";
- scores mixes and suppliers from the same facts, ingredient by
  ingredient (§8, §9, §11);
- puts an effective-value frame on the money (§10);
- raises persistent, deduplicated alerts with suggested actions, puts
  them on the farm's bell and resolves them when the condition clears;
- is re-run by the hooks §12 names and by the scheduled cycle.

What it never does: write a milk, feed, stock, health or supplier fact;
change a formula, a program, a lot's status or a supplier's standing; or
call an association a cause. Every assessment carries its model
reference, evidence window, baseline method actually applied,
confounders and confidence so a person can check it.

Hooks run inside a savepoint: an analytics failure is logged and never
costs the farm the milk record or the feeding event that triggered it.
"""

from __future__ import annotations

import logging
import math
import statistics
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, literal_column, or_, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.common.db import ControlSessionLocal
from app.farmos import feed_batch_service as batches
from app.farmos import feed_inventory_service as inv
from app.farmos import feed_models as fm
from app.farmos import feeding_program_service as programs
from app.farmos import routing_service as routing
from app.farmos.deps import AccessContext
from app.farmos.feed_catalog import FeedError
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.feed_performance_models import (
    ALERT_TYPES,
    BASELINE_METHODS,
    METRIC_UNITS,
    METRICS,
    FeedBatchPerformanceScore,
    FeedExposureWindow,
    FeedPerformanceAlert,
    FeedPerformanceAssessment,
    FeedPerformanceMonitor,
    SupplierFeedPerformance,
)
from app.farmos.finance_models import Sale
from app.farmos.health_case_models import HealthCase
from app.farmos.livestock_models import AnimalGroup, AnimalWeightRecord
from app.farmos.livestock_service import ensure_utc, now_utc
from app.farmos.production_models import EggRecord, MilkRecord, Observation, Treatment
from app.tenant_api.models import Animal, Task

log = logging.getLogger("origami.feed_performance")

MODEL_REFERENCE = "origami.feed_performance.deterministic"
MODEL_VERSION = "2.0"
MODULE_CODE = "feed_nutrition"
REVIEW_TASK = "feed_performance_review"
BELL_SOURCE = "feed_performance_alert"
OPEN_CASE_STATUSES = ("open", "monitoring")
GROUP_ENTITY_TYPES = ("flock", "group")
COMPLIANCE_TOLERANCE_PCT = 5.0  # a component more than this off target is a manufacturing deviation
CHANGE_LOOKBACK_DAYS = 7  # a lot introduced this long before the period counts as a feed change
ONGOING_GAP_DAYS = 2  # an exposure with no issue for this long is over
COST_INCREASE_ALERT_PCT = 10.0
REFUSALS_FOR_ALERT = 2
HEAT_STRESS_C = 30.0
UNDERPERFORMANCE_SCORE = 0.4  # production response below this is weak (0.5 = no change)
MIN_CONFIDENCE_FOR_ALERT = 0.5
# Relative lactation-curve shape (Wood: t^b · e^(−c·t)); the scale cancels
# because only the ratio between two days in milk is used.
LACTATION_CURVE_DEFAULTS = {
    "cow": (0.19, 0.0036),
    "goat": (0.15, 0.005),
    "sheep": (0.12, 0.008),
    "buffalo": (0.19, 0.0036),
}
ENVIRONMENT_OBSERVATIONS = (
    "ambient_temperature_c",
    "ambient_temperature",
    "temperature_ambient",
    "heat_stress",
    "thi",
    "barn_temperature_c",
)
MOVEMENT_FIELDS = ("group_name", "location_label")


class PerformanceError(FeedError):
    pass


_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


# ---------------------------------------------------------------- helpers
def _as_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _start_of(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _day(column):
    """The UTC calendar day of a timestamptz column — a literal zone, so the
    SELECT and GROUP BY render the identical expression."""
    return func.date(func.timezone(literal_column("'UTC'"), column))


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def _f(value) -> float:
    return float(value or 0)


def _s(value: uuid.UUID | None) -> str | None:
    return str(value) if value else None


def _subject(db: Session, subject_type: str, subject_id: uuid.UUID | str | None):
    row = programs.subject_row(db, subject_type, subject_id)
    if row is None or row.deleted_at is not None:
        return None
    return row


def _subject_animals(db: Session, subject_type: str, subject) -> list[Animal]:
    if subject is None:
        return []
    if subject_type == "animal":
        return [subject]
    return programs.animals_in_group(db, subject)


def get_monitor(db: Session, monitor_id: str | uuid.UUID | None) -> FeedPerformanceMonitor:
    pk = parse_uuid(monitor_id)
    m = db.get(FeedPerformanceMonitor, pk) if pk else None
    if m is None or m.deleted_at is not None:
        raise PerformanceError("Monitor not found", 404)
    return m


def get_alert(db: Session, alert_id: str | uuid.UUID | None) -> FeedPerformanceAlert:
    pk = parse_uuid(alert_id)
    a = db.get(FeedPerformanceAlert, pk) if pk else None
    if a is None or a.deleted_at is not None:
        raise PerformanceError("Alert not found", 404)
    return a


def _validate_monitor(subject_type: str, *, metric: str, method: str, configuration: dict | None) -> None:
    if metric not in METRICS:
        raise PerformanceError(f"production_metric_code must be one of {list(METRICS)}")
    if method not in BASELINE_METHODS:
        raise PerformanceError(f"baseline_method_code must be one of {list(BASELINE_METHODS)}")
    if metric == "eggs_per_day" and subject_type != "group":
        raise PerformanceError("Eggs are recorded per flock; monitor the group, not an animal.")
    if method == "lactation_adjusted" and metric != "milk_l_per_day":
        raise PerformanceError("A lactation-adjusted baseline applies to milk.")
    if method == "production_curve":
        curve = (configuration or {}).get("expected_curve")
        if not isinstance(curve, list) or not curve:
            raise PerformanceError(
                "A production-curve baseline needs configuration.expected_curve: a list of "
                "{from_day, to_day, value} bands over days in milk (or age in days)."
            )
        for band in curve:
            if not isinstance(band, dict) or band.get("value") is None or band.get("from_day") is None:
                raise PerformanceError("Each expected_curve band needs from_day, to_day and value.")


# ---------------------------------------------------------------- monitors
def create_monitor(
    db: Session,
    access: AccessContext,
    *,
    subject_type: str,
    subject_id: str,
    production_metric_code: str = "milk_l_per_day",
    baseline_method_code: str = "rolling_subject",
    baseline_window_days: int = 14,
    evaluation_window_days: int = 3,
    evaluation_frequency_code: str = "daily",
    minimum_exposure_days: float = 2.0,
    minimum_observations: int = 3,
    alert_threshold_percent: float = 5.0,
    active: bool = True,
    configuration: dict | None = None,
) -> FeedPerformanceMonitor:
    """Watches one animal or group on one metric against one explicit
    baseline method. The same subject and metric twice returns the
    existing monitor."""
    if subject_type not in ("animal", "group"):
        raise PerformanceError("subject_type must be animal or group")
    subject = _subject(db, subject_type, subject_id)
    if subject is None:
        raise PerformanceError("Subject not found", 404)
    _validate_monitor(
        subject_type, metric=production_metric_code, method=baseline_method_code, configuration=configuration
    )
    if baseline_window_days < 2 or evaluation_window_days < 1 or alert_threshold_percent <= 0:
        raise PerformanceError("Windows must be at least a day and the threshold above zero.")
    existing = db.scalar(
        select(FeedPerformanceMonitor).where(
            FeedPerformanceMonitor.subject_type == subject_type,
            FeedPerformanceMonitor.subject_id == subject.id,
            FeedPerformanceMonitor.production_metric_code == production_metric_code,
            FeedPerformanceMonitor.deleted_at.is_(None),
        )
    )
    if existing is not None:
        return existing
    row = FeedPerformanceMonitor(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        subject_type=subject_type,
        subject_id=subject.id,
        production_metric_code=production_metric_code,
        baseline_method_code=baseline_method_code,
        baseline_window_days=baseline_window_days,
        evaluation_window_days=evaluation_window_days,
        evaluation_frequency_code=evaluation_frequency_code,
        minimum_exposure_days=minimum_exposure_days,
        minimum_observations=minimum_observations,
        alert_threshold_percent=alert_threshold_percent,
        active=active,
        configuration_json=configuration or {},
        created_by=access.user_id,
    )
    db.add(row)
    db.flush()
    return row


_PATCHABLE = {
    "baseline_method_code",
    "baseline_window_days",
    "evaluation_window_days",
    "evaluation_frequency_code",
    "minimum_exposure_days",
    "minimum_observations",
    "alert_threshold_percent",
    "active",
    "configuration_json",
}


def update_monitor(
    db: Session, access: AccessContext, monitor: FeedPerformanceMonitor, changes: dict
) -> FeedPerformanceMonitor:
    for k, v in changes.items():
        if k == "configuration":
            k = "configuration_json"
        if k in _PATCHABLE and v is not None:
            setattr(monitor, k, v)
    _validate_monitor(
        monitor.subject_type,
        metric=monitor.production_metric_code,
        method=monitor.baseline_method_code,
        configuration=monitor.configuration_json,
    )
    if monitor.baseline_window_days < 2 or monitor.evaluation_window_days < 1:
        raise PerformanceError("Windows must be at least a day and the threshold above zero.")
    monitor.version += 1
    monitor.last_modified_by = access.membership_id
    monitor.updated_at = now_utc()
    return monitor


def ensure_default_monitors(db: Session, access: AccessContext) -> list[FeedPerformanceMonitor]:
    """A monitor for every group that produces: milk for herds whose
    members have milk records, eggs for flocks with egg records. Explicit
    per-animal and growth monitors are the farm's to add."""
    created = []
    for group in db.scalars(select(AnimalGroup).where(AnimalGroup.deleted_at.is_(None))):
        members = programs.animals_in_group(db, group)
        has_milk = bool(members) and bool(
            db.scalar(
                select(func.count(MilkRecord.id)).where(
                    MilkRecord.animal_id.in_([m.id for m in members]), MilkRecord.deleted_at.is_(None)
                )
            )
        )
        has_eggs = bool(
            db.scalar(
                select(func.count(EggRecord.id)).where(
                    EggRecord.flock_id.in_((str(group.id), group.name)), EggRecord.deleted_at.is_(None)
                )
            )
        )
        if has_milk:
            created.append(
                create_monitor(
                    db,
                    access,
                    subject_type="group",
                    subject_id=str(group.id),
                    production_metric_code="milk_l_per_day",
                )
            )
        if has_eggs:
            created.append(
                create_monitor(
                    db,
                    access,
                    subject_type="group",
                    subject_id=str(group.id),
                    production_metric_code="eggs_per_day",
                )
            )
    return created


# ------------------------------------------------------------- production
def daily_values(
    db: Session, subject_type: str, subject, metric: str, start: date, end: date
) -> dict[date, float]:
    """Production per day between the dates, inclusive. Milk is litres per
    milked head per day (a herd of 3 and a herd of 30 compare on the same
    scale); eggs are the flock's eggs per day; growth is the average daily
    gain between consecutive dated weights, on the day of the later
    weight. A day with no record is absent — missing is not zero."""
    lo, hi = _start_of(start), _start_of(end + timedelta(days=1))
    out: dict[date, float] = {}
    if metric == "milk_l_per_day":
        ids = [a.id for a in _subject_animals(db, subject_type, subject)]
        if not ids:
            return out
        rows = db.execute(
            select(
                _day(MilkRecord.recorded_at),
                func.sum(MilkRecord.liters),
                func.count(func.distinct(MilkRecord.animal_id)),
            )
            .where(
                MilkRecord.animal_id.in_(ids),
                MilkRecord.deleted_at.is_(None),
                MilkRecord.recorded_at >= lo,
                MilkRecord.recorded_at < hi,
            )
            .group_by(_day(MilkRecord.recorded_at))
        ).all()
        for d, total, heads in rows:
            if heads:
                out[_as_date(d)] = round(float(total) / int(heads), 3)
    elif metric == "eggs_per_day" and subject_type == "group" and subject is not None:
        rows = db.execute(
            select(_day(EggRecord.recorded_at), func.sum(EggRecord.total_eggs))
            .where(
                EggRecord.flock_id.in_((str(subject.id), subject.name)),
                EggRecord.deleted_at.is_(None),
                EggRecord.recorded_at >= lo,
                EggRecord.recorded_at < hi,
            )
            .group_by(_day(EggRecord.recorded_at))
        ).all()
        for d, total in rows:
            out[_as_date(d)] = float(total or 0)
    elif metric == "weight_gain_kg_per_day":
        per_day: dict[date, list[float]] = {}
        for a in _subject_animals(db, subject_type, subject):
            records = db.scalars(
                select(AnimalWeightRecord)
                .where(
                    AnimalWeightRecord.animal_id == a.id,
                    AnimalWeightRecord.deleted_at.is_(None),
                    AnimalWeightRecord.measured_at < hi,
                )
                .order_by(AnimalWeightRecord.measured_at)
            ).all()
            for prev, cur in zip(records, records[1:]):
                cur_at, prev_at = ensure_utc(cur.measured_at), ensure_utc(prev.measured_at)
                if cur_at is None or prev_at is None:
                    continue
                day = cur_at.date()
                if day < start:
                    continue
                span = (cur_at - prev_at).total_seconds() / 86400
                if span <= 0:
                    continue
                per_day.setdefault(day, []).append((float(cur.weight_kg) - float(prev.weight_kg)) / span)
        for day, gains in per_day.items():
            out[day] = round(statistics.fmean(gains), 4)
    return out


def _milked_heads(db: Session, subject_type: str, subject, start: date, end: date) -> set[uuid.UUID]:
    ids = [a.id for a in _subject_animals(db, subject_type, subject)]
    if not ids:
        return set()
    return set(
        db.scalars(
            select(func.distinct(MilkRecord.animal_id)).where(
                MilkRecord.animal_id.in_(ids),
                MilkRecord.deleted_at.is_(None),
                MilkRecord.recorded_at >= _start_of(start),
                MilkRecord.recorded_at < _start_of(end + timedelta(days=1)),
            )
        )
    )


# --------------------------------------------------------------- exposure
def _lineage(db: Session, lot: fm.FeedLot | None, product: fm.FeedProduct | None) -> dict:
    if lot is None:
        return {"feed_product": product.name if product else None, "lot": None}
    batch = db.get(fm.FeedBatch, lot.feed_batch_id) if lot.feed_batch_id else None
    ingredients = []
    if batch is not None:
        for c in batch.components:
            src = db.get(fm.FeedLot, c.lot_id) if c.lot_id else None
            comp = db.get(fm.FeedProduct, c.feed_product_id)
            ingredients.append(
                {
                    "feed_product_id": str(c.feed_product_id),
                    "product_name": comp.name if comp else None,
                    "lot_id": _s(c.lot_id),
                    "lot_code": src.lot_code if src else None,
                    "supplier_id": _s(src.supplier_id) if src else None,
                    "supplier_label": src.supplier_label if src else None,
                    "actual_quantity": c.actual_quantity,
                    "target_quantity": c.target_quantity,
                }
            )
    return {
        "feed_product": product.name if product else None,
        "lot_id": str(lot.id),
        "lot_code": lot.lot_code,
        "source_type": lot.source_type,
        "supplier_id": _s(lot.supplier_id),
        "supplier_label": lot.supplier_label,
        "mix": None
        if batch is None
        else {
            "batch_id": str(batch.id),
            "mix_number": batch.mix_number,
            "mix_code": batch.mix_code,
            "formula_version_id": _s(batch.formula_version_id),
            "production_date": batch.production_date.isoformat() if batch.production_date else None,
            "operator_id": _s(batch.operator_id or batch.mixed_by),
            "mixer_asset_id": _s(batch.mixer_asset_id),
        },
        "ingredient_lots": ingredients,
    }


def rebuild_exposures(db: Session, access: AccessContext) -> list[FeedExposureWindow]:
    """Projects the feeding events into exposure windows (§4): one per
    subject × product × lot, first issue to last. Rebuilt in full so it can
    never drift from the events it summarises."""
    db.execute(delete(FeedExposureWindow))
    events = db.scalars(
        select(fm.FeedingEvent)
        .where(
            fm.FeedingEvent.status == "recorded",
            fm.FeedingEvent.event_type.in_(("offered", "delivered")),
            fm.FeedingEvent.deleted_at.is_(None),
        )
        .order_by(fm.FeedingEvent.occurred_at)
    ).all()
    agg: dict[tuple, dict] = {}
    for e in events:
        at = ensure_utc(e.occurred_at) or now_utc()
        for c in e.components:
            key = (e.subject_type, e.subject_id, c.feed_product_id, c.lot_id)
            w = agg.setdefault(
                key,
                {
                    "start": at,
                    "end": at,
                    "offered": 0.0,
                    "consumed": 0.0,
                    "consumed_known": False,
                    "count": 0,
                    "batch_id": c.batch_id,
                },
            )
            w["start"] = min(w["start"], at)
            w["end"] = max(w["end"], at)
            w["offered"] += c.quantity_offered
            if c.quantity_consumed is not None:
                w["consumed"] += c.quantity_consumed
                w["consumed_known"] = True
            w["count"] += 1
            w["batch_id"] = w["batch_id"] or c.batch_id
    rows = []
    at_now = now_utc()
    cutoff = at_now - timedelta(days=ONGOING_GAP_DAYS)
    for (stype, sid, pid, lot_id), w in agg.items():
        lot = db.get(fm.FeedLot, lot_id) if lot_id else None
        product = db.get(fm.FeedProduct, pid)
        row = FeedExposureWindow(
            tenant_id=access.tenant_id,
            last_modified_by=access.membership_id,
            subject_type=stype,
            subject_id=sid,
            feed_product_id=pid,
            lot_id=lot_id,
            feed_batch_id=w["batch_id"] or (lot.feed_batch_id if lot else None),
            exposure_start=w["start"],
            exposure_end=None if w["end"] >= cutoff else w["end"],
            offered_quantity=round(w["offered"], 3),
            consumed_estimate=round(w["consumed"], 3) if w["consumed_known"] else None,
            feeding_event_count=w["count"],
            lineage_json=_lineage(db, lot, product),
            generated_at=at_now,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def exposures_for(
    db: Session, subject_type: str | None = None, subject_id: uuid.UUID | str | None = None
) -> list[FeedExposureWindow]:
    stmt = select(FeedExposureWindow)
    if subject_type and subject_id:
        pk = parse_uuid(subject_id)
        if pk is None:
            return []
        stmt = stmt.where(
            FeedExposureWindow.subject_type == subject_type, FeedExposureWindow.subject_id == pk
        )
    return list(db.scalars(stmt.order_by(FeedExposureWindow.exposure_start.desc())))


def _feed_changes(
    db: Session, subject_type: str, subject, group, since: datetime, until: datetime
) -> list[dict]:
    """Lots first issued to the subject (or its group) in the lookback: the
    candidate feed changes an assessment lists, with their lineage."""
    keys = [(subject_type, subject.id)] + ([("group", group.id)] if group is not None else [])
    changes = []
    for stype, sid in keys:
        for w in exposures_for(db, stype, sid):
            start = ensure_utc(w.exposure_start) or until
            if start < since or start > until:
                continue
            lineage = w.lineage_json or {}
            mix = lineage.get("mix") or {}
            batch = db.get(fm.FeedBatch, w.feed_batch_id) if w.feed_batch_id else None
            compliance = None
            if batch is not None and batch.status in ("completed", "quarantined"):
                devs = [
                    abs(v["variance_pct"])
                    for v in batches.batch_variance(batch)
                    if v.get("variance_pct") is not None
                ]
                compliance = round(max(devs), 2) if devs else None
            end = ensure_utc(w.exposure_end) if w.exposure_end else None
            exposed_until = min(until, end) if end is not None else until
            changes.append(
                {
                    "subject_type": stype,
                    "subject_id": str(sid),
                    "feed_product_id": str(w.feed_product_id),
                    "product_name": lineage.get("feed_product"),
                    "lot_id": _s(w.lot_id),
                    "lot_code": lineage.get("lot_code"),
                    "supplier_label": lineage.get("supplier_label"),
                    "mix_code": mix.get("mix_code"),
                    "mix_number": mix.get("mix_number"),
                    "batch_id": _s(w.feed_batch_id),
                    "formula_version_id": mix.get("formula_version_id"),
                    "ingredient_lots": [
                        {k: i.get(k) for k in ("product_name", "lot_id", "lot_code", "supplier_label")}
                        for i in lineage.get("ingredient_lots", [])
                    ],
                    "introduced_at": start.isoformat(),
                    "days_of_exposure": round((exposed_until - start).total_seconds() / 86400, 1),
                    "offered_quantity": w.offered_quantity,
                    "worst_component_deviation_pct": compliance,
                }
            )
    changes.sort(key=lambda c: str(c["introduced_at"]))
    return changes


# --------------------------------------------------------------- baselines
def _age_days(animals: list[Animal], at: datetime) -> float | None:
    ages = [
        (at - (ensure_utc(a.birth_date) or at)).total_seconds() / 86400
        for a in animals
        if a.birth_date is not None
    ]
    return round(statistics.fmean(ages), 1) if ages else None


def _wood(t: float, b: float, c: float) -> float:
    return max(t, 1.0) ** b * math.exp(-c * max(t, 1.0))


def _curve_value(curve: list[dict], day: float) -> float | None:
    for band in curve:
        lo, hi = float(band.get("from_day", 0)), band.get("to_day")
        if day >= lo and (hi is None or day <= float(hi)):
            return float(band["value"])
    return None


def _peers(db: Session, monitor: FeedPerformanceMonitor, subject):
    """Matched peers: same farm, species, management profile and kind of
    subject; for animals, the same lactation state."""
    if monitor.subject_type == "animal":
        rows = db.scalars(
            select(Animal).where(
                Animal.active.is_(True),
                Animal.deleted_at.is_(None),
                Animal.species == subject.species,
                Animal.id != subject.id,
                Animal.management_profile == subject.management_profile,
                Animal.lactating == subject.lactating,
            )
        ).all()
        return [("animal", a) for a in rows]
    groups = db.scalars(
        select(AnimalGroup).where(
            AnimalGroup.deleted_at.is_(None),
            AnimalGroup.species == subject.species,
            AnimalGroup.id != subject.id,
            AnimalGroup.management_profile == subject.management_profile,
        )
    ).all()
    return [("group", g) for g in groups]


def _days_in_milk(monitor: FeedPerformanceMonitor, animals: list[Animal], at: datetime) -> float | None:
    """The animal record carries no calving date yet, so days in milk is
    known only when the monitor's configuration states it
    (`configuration.days_in_milk_at` = an ISO date the lactation started);
    otherwise it is missing context, and recorded as such."""
    started = (monitor.configuration_json or {}).get("lactation_started_at")
    if not started:
        return None
    try:
        start = datetime.fromisoformat(str(started))
    except ValueError:
        return None
    start = ensure_utc(start) or at
    return round(max((at - start).total_seconds() / 86400, 0.0), 1)


def _baseline(
    db: Session,
    monitor: FeedPerformanceMonitor,
    subject,
    animals: list[Animal],
    period_start: date,
    end_day: date,
    baseline_start: date,
    baseline_end: date,
    at: datetime,
) -> tuple[dict[date, float], dict]:
    """The explicit baseline series (§13) and how it was built. A method
    whose inputs are missing falls back to the rolling subject baseline
    and says so — the fallback is recorded, never silent."""
    method = monitor.baseline_method_code
    rolling = daily_values(
        db, monitor.subject_type, subject, monitor.production_metric_code, baseline_start, baseline_end
    )
    details: dict = {
        "method_requested": method,
        "method_applied": method,
        "window": {"start": baseline_start.isoformat(), "end": baseline_end.isoformat()},
    }
    if method in ("rolling_subject", "previous_period"):
        return rolling, details
    if method == "lactation_adjusted":
        dim_eval = _days_in_milk(
            monitor, animals, _start_of(end_day) - timedelta(days=(end_day - period_start).days / 2)
        )
        if dim_eval is None or not rolling:
            details.update(
                {
                    "method_applied": "rolling_subject",
                    "fallback_reason": "days in milk unknown (no lactation start recorded for the monitor)",
                }
            )
            return rolling, details
        shift = ((period_start - baseline_start).days + (end_day - baseline_end).days) / 2
        dim_base = max(dim_eval - shift, 1.0)
        defaults = LACTATION_CURVE_DEFAULTS.get(subject.species, (0.15, 0.005))
        curve_cfg = (monitor.configuration_json or {}).get("lactation_curve", {})
        b, c = float(curve_cfg.get("b", defaults[0])), float(curve_cfg.get("c", defaults[1]))
        ratio = _wood(dim_eval, b, c) / _wood(dim_base, b, c)
        details.update(
            {
                "days_in_milk_evaluation": dim_eval,
                "days_in_milk_baseline": round(dim_base, 1),
                "curve": {"b": b, "c": c},
                "expected_ratio": round(ratio, 4),
            }
        )
        return {d: round(v * ratio, 4) for d, v in rolling.items()}, details
    if method == "peer_group":
        peers = _peers(db, monitor, subject)
        series: dict[date, list[float]] = {}
        for stype, peer in peers:
            for d, v in daily_values(
                db, stype, peer, monitor.production_metric_code, period_start, end_day
            ).items():
                series.setdefault(d, []).append(v)
        usable = {d: round(statistics.fmean(vs), 4) for d, vs in series.items() if len(vs) >= 2}
        if len(usable) < monitor.minimum_observations:
            details.update(
                {
                    "method_applied": "rolling_subject",
                    "fallback_reason": (
                        f"fewer than two matched peers reporting on {monitor.minimum_observations} day(s)"
                    ),
                    "peers_considered": len(peers),
                }
            )
            return rolling, details
        details.update(
            {
                "peers": [{"subject_type": st, "subject_id": str(p.id), "name": p.name} for st, p in peers],
                "window": {"start": period_start.isoformat(), "end": end_day.isoformat()},
                "note": (
                    "the peers' production over the same days; " "farm-wide effects (weather, milking) cancel"
                ),
            }
        )
        return usable, details
    if method == "production_curve":
        curve = (monitor.configuration_json or {}).get("expected_curve") or []
        milk = monitor.production_metric_code == "milk_l_per_day"
        day = _days_in_milk(monitor, animals, at) if milk else _age_days(animals, at)
        value = _curve_value(curve, day) if day is not None else None
        if value is None:
            details.update(
                {
                    "method_applied": "rolling_subject",
                    "fallback_reason": (
                        "no expected value: days in milk / age unknown or outside the approved curve"
                    ),
                }
            )
            return rolling, details
        details.update(
            {
                "curve_day": day,
                "expected_value": value,
                "basis": "days_in_milk" if milk else "age_days",
                "window": {"start": period_start.isoformat(), "end": end_day.isoformat()},
            }
        )
        return {
            period_start + timedelta(days=i): value for i in range((end_day - period_start).days + 1)
        }, details
    return rolling, details


# ------------------------------------------------------------ confounders
def _movements(tenant_id: uuid.UUID, ids: list[uuid.UUID], lo: datetime, hi: datetime) -> int:
    """Group or location changes recorded on the animals: the audit trail
    (control database) is the server's record of who moved what."""
    if not ids:
        return 0
    with ControlSessionLocal() as cdb:
        rows = cdb.execute(
            select(AuditEvent.changes_json).where(
                AuditEvent.tenant_id == tenant_id,
                AuditEvent.entity_type == "animal",
                AuditEvent.entity_id.in_([str(i) for i in ids]),
                AuditEvent.action == "animal.updated",
                AuditEvent.created_at >= lo,
                AuditEvent.created_at < hi,
            )
        ).all()
    return sum(1 for (changes,) in rows if changes and set(MOVEMENT_FIELDS) & set(changes))


def _feedings_per_day(db: Session, keys: list[tuple[str, uuid.UUID]], start: date, end: date) -> float | None:
    """Feedings per fed day: the pattern of the days the subject was fed
    (two meals or one), not whether a new feed started — that is the
    feed change itself, listed separately."""
    total = 0
    days: set[date] = set()
    for stype, sid in keys:
        for (occurred,) in db.execute(
            select(fm.FeedingEvent.occurred_at).where(
                fm.FeedingEvent.subject_type == stype,
                fm.FeedingEvent.subject_id == sid,
                fm.FeedingEvent.status == "recorded",
                fm.FeedingEvent.deleted_at.is_(None),
                fm.FeedingEvent.event_type.in_(("offered", "delivered")),
                fm.FeedingEvent.occurred_at >= _start_of(start),
                fm.FeedingEvent.occurred_at < _start_of(end + timedelta(days=1)),
            )
        ).all():
            total += 1
            days.add((ensure_utc(occurred) or now_utc()).date())
    return round(total / len(days), 2) if days else None


def _count_observations(db: Session, entity_types: tuple[str, ...], entity_ids: list[str], lo, hi) -> int:
    if not entity_ids:
        return 0
    return (
        db.scalar(
            select(func.count(Observation.id)).where(
                Observation.entity_type.in_(entity_types),
                Observation.entity_id.in_(entity_ids),
                Observation.deleted_at.is_(None),
                Observation.observed_at >= lo,
                Observation.observed_at < hi,
            )
        )
        or 0
    )


def _count_treatments(db: Session, entity_types: tuple[str, ...], entity_ids: list[str], lo, hi) -> int:
    if not entity_ids:
        return 0
    return (
        db.scalar(
            select(func.count(Treatment.id)).where(
                Treatment.entity_type.in_(entity_types),
                Treatment.entity_id.in_(entity_ids),
                Treatment.deleted_at.is_(None),
                Treatment.start_at >= lo,
                Treatment.start_at < hi,
            )
        )
        or 0
    )


def _confounders(
    db: Session,
    monitor: FeedPerformanceMonitor,
    subject_type: str,
    subject,
    group,
    period_start: date,
    period_end: date,
    baseline_start: date,
    baseline_end: date,
    observed: dict[date, float],
    changes: list[dict],
) -> dict:
    """What else could explain the change, from the facts this server
    holds (§6). Anything it cannot see is named as missing, so the
    confidence can drop for it rather than the gap passing as 'nothing
    changed'."""
    animals = _subject_animals(db, subject_type, subject)
    ids = [a.id for a in animals]
    sids = [str(i) for i in ids]
    lo = _start_of(period_start - timedelta(days=CHANGE_LOOKBACK_DAYS))
    hi = _start_of(period_end + timedelta(days=1))
    at = _start_of(period_end)
    keys = [(subject_type, subject.id)] + ([("group", group.id)] if group is not None else [])
    milk = monitor.production_metric_code == "milk_l_per_day"

    health = _count_observations(db, ("animal",), sids, lo, hi) + _count_treatments(
        db, ("animal",), sids, lo, hi
    )
    cases = 0
    if ids:
        cases += (
            db.scalar(
                select(func.count(HealthCase.id)).where(
                    HealthCase.animal_id.in_(ids),
                    HealthCase.status.in_(OPEN_CASE_STATUSES),
                    HealthCase.deleted_at.is_(None),
                )
            )
            or 0
        )
    if subject_type == "group":
        health += _count_observations(db, GROUP_ENTITY_TYPES, [str(subject.id)], lo, hi)
        health += _count_treatments(db, GROUP_ENTITY_TYPES, [str(subject.id)], lo, hi)
        cases += (
            db.scalar(
                select(func.count(HealthCase.id)).where(
                    HealthCase.animal_group_id == subject.id,
                    HealthCase.status.in_(OPEN_CASE_STATUSES),
                    HealthCase.deleted_at.is_(None),
                )
            )
            or 0
        )
    withdrawal = sum(
        1 for a in animals if a.withdrawal_until and (ensure_utc(a.withdrawal_until) or lo) >= lo
    )
    lactating = sum(1 for a in animals if a.lactating)
    pregnant = sum(1 for a in animals if a.pregnant)
    membership = "unavailable"
    if subject_type == "group" and animals:
        before = _milked_heads(db, subject_type, subject, baseline_start, baseline_end)
        after = _milked_heads(db, subject_type, subject, period_start, period_end)
        membership = (
            "changed"
            if before and after and before != after
            else ("stable" if before and after else "unavailable")
        )
    refusals = 0
    for stype, sid in keys:
        refusals += (
            db.scalar(
                select(func.count(fm.FeedingEvent.id)).where(
                    fm.FeedingEvent.subject_type == stype,
                    fm.FeedingEvent.subject_id == sid,
                    fm.FeedingEvent.event_type == "refusal",
                    fm.FeedingEvent.status == "recorded",
                    fm.FeedingEvent.deleted_at.is_(None),
                    fm.FeedingEvent.occurred_at >= _start_of(period_start),
                    fm.FeedingEvent.occurred_at < hi,
                )
            )
            or 0
        )

    # Lactation stage and reproductive context. The animal record carries
    # parity (lactation_cycle) but no calving / dry-off dates yet.
    days_in_milk = _days_in_milk(monitor, animals, at) if milk else None
    parities = [a.lactation_cycle for a in animals if a.lactation_cycle]
    parity = round(statistics.fmean(parities), 1) if parities else None
    recent_calving: int | None = None if milk else 0
    recent_dry_off: int | None = None if milk else 0
    # Movement and management
    movements = _movements(monitor.tenant_id, ids, lo, hi)
    management = (
        db.scalar(
            select(func.count(fm.FeedingAssignment.id)).where(
                or_(
                    *[
                        (fm.FeedingAssignment.subject_type == st) & (fm.FeedingAssignment.subject_id == sid)
                        for st, sid in keys
                    ]
                ),
                fm.FeedingAssignment.deleted_at.is_(None),
                fm.FeedingAssignment.created_at >= lo,
                fm.FeedingAssignment.created_at < hi,
            )
        )
        or 0
    )
    # Feeding timing / frequency
    eval_fpd = _feedings_per_day(db, keys, period_start, period_end)
    base_fpd = _feedings_per_day(db, keys, baseline_start, baseline_end)
    timing_changed = eval_fpd is not None and base_fpd is not None and abs(eval_fpd - base_fpd) >= 0.5
    # Formula version in use and ingredient lot changes
    versions_now = sorted({c["formula_version_id"] for c in changes if c.get("formula_version_id")})
    earlier_versions: set[str] = set()
    for stype, sid in keys:
        for w in exposures_for(db, stype, sid):
            if (ensure_utc(w.exposure_start) or hi) < lo and (w.lineage_json or {}).get("mix"):
                earlier_versions.add(w.lineage_json["mix"].get("formula_version_id"))
    formula_version_changed = (
        bool(versions_now) and bool(earlier_versions) and set(versions_now) != earlier_versions
    )
    worst_deviation = max(
        (
            c["worst_component_deviation_pct"]
            for c in changes
            if c.get("worst_component_deviation_pct") is not None
        ),
        default=None,
    )
    # Laboratory analysis on the lots / mixes introduced
    lot_ids = [parse_uuid(c["lot_id"]) for c in changes if c.get("lot_id")] + [
        parse_uuid(i["lot_id"]) for c in changes for i in c.get("ingredient_lots", []) if i.get("lot_id")
    ]
    batch_ids = [parse_uuid(c["batch_id"]) for c in changes if c.get("batch_id")]
    lot_ids = [x for x in lot_ids if x is not None]
    batch_ids = [x for x in batch_ids if x is not None]
    lab = None
    if lot_ids or batch_ids:
        conds = []
        if lot_ids:
            conds.append(
                (fm.FeedNutrientProfile.subject_type == "feed_lot")
                & fm.FeedNutrientProfile.subject_id.in_(lot_ids)
            )
        if batch_ids:
            conds.append(
                (fm.FeedNutrientProfile.subject_type == "feed_batch")
                & fm.FeedNutrientProfile.subject_id.in_(batch_ids)
            )
        lab = (
            db.scalar(
                select(func.count(fm.FeedNutrientProfile.id)).where(
                    fm.FeedNutrientProfile.source_type == "lab", or_(*conds)
                )
            )
            or 0
        )
        lab = lab or None
    # Environment: ambient temperature / heat-stress observations recorded on the farm
    env_rows = db.scalars(
        select(Observation).where(
            Observation.observation_type.in_(ENVIRONMENT_OBSERVATIONS),
            Observation.deleted_at.is_(None),
            Observation.observed_at >= _start_of(period_start),
            Observation.observed_at < hi,
        )
    ).all()
    env_max = max((float(o.value_numeric) for o in env_rows if o.value_numeric is not None), default=None)
    environment = (
        None
        if not env_rows
        else {
            "observations": len(env_rows),
            "max_temperature_c": env_max,
            "heat_stress": bool(env_max is not None and env_max >= HEAT_STRESS_C),
        }
    )
    # Milking-system / measurement anomalies
    milking_anomalies = 0
    if milk and ids:
        milking_anomalies = (
            db.scalar(
                select(func.count(MilkRecord.id)).where(
                    MilkRecord.animal_id.in_(ids),
                    MilkRecord.deleted_at.is_(None),
                    MilkRecord.recorded_at >= _start_of(period_start),
                    MilkRecord.recorded_at < hi,
                    MilkRecord.quality_status != "normal",
                )
            )
            or 0
        )
        sessions_eval = (
            db.scalar(
                select(func.count(func.distinct(MilkRecord.session))).where(
                    MilkRecord.animal_id.in_(ids),
                    MilkRecord.deleted_at.is_(None),
                    MilkRecord.recorded_at >= _start_of(period_start),
                    MilkRecord.recorded_at < hi,
                )
            )
            or 0
        )
        sessions_base = (
            db.scalar(
                select(func.count(func.distinct(MilkRecord.session))).where(
                    MilkRecord.animal_id.in_(ids),
                    MilkRecord.deleted_at.is_(None),
                    MilkRecord.recorded_at >= _start_of(baseline_start),
                    MilkRecord.recorded_at < _start_of(baseline_end + timedelta(days=1)),
                )
            )
            or 0
        )
        if sessions_eval and sessions_base and sessions_eval != sessions_base:
            milking_anomalies += 1
    # Live weight change across the baseline + evaluation span
    weight_change = None
    if ids:
        deltas = []
        for aid in ids:
            recs = db.scalars(
                select(AnimalWeightRecord)
                .where(
                    AnimalWeightRecord.animal_id == aid,
                    AnimalWeightRecord.deleted_at.is_(None),
                    AnimalWeightRecord.measured_at >= _start_of(baseline_start),
                    AnimalWeightRecord.measured_at < hi,
                )
                .order_by(AnimalWeightRecord.measured_at)
            ).all()
            if len(recs) >= 2:
                deltas.append(float(recs[-1].weight_kg) - float(recs[0].weight_kg))
        weight_change = round(statistics.fmean(deltas), 2) if deltas else None

    missing = []
    if milk:
        if days_in_milk is None:
            missing.append("days_in_milk")
        if parity is None:
            missing.append("parity")
        missing.append("calving_and_dry_off_dates")
    if environment is None:
        missing.append("environment")
    if lab is None:
        missing.append("lab_analysis")
    if weight_change is None:
        missing.append("weight_change")
    missing += ["body_condition", "milk_components"]
    if membership == "unavailable":
        missing.append("group_membership")
    return {
        "health_events": health,
        "active_health_cases": cases,
        "animals_under_withdrawal": withdrawal,
        "lactating_heads": lactating,
        "pregnant_heads": pregnant,
        "head_count": len(animals),
        "milked_heads_change": membership,
        "refusal_events": refusals,
        "observation_days": len(observed),
        "days_in_milk": days_in_milk,
        "parity": parity,
        "recent_calving": recent_calving,
        "recent_dry_off": recent_dry_off,
        "movements": movements,
        "management_changes": management,
        "feeding_timing": {
            "evaluation_per_day": eval_fpd,
            "baseline_per_day": base_fpd,
            "changed": timing_changed,
        },
        "formula_version_changed": formula_version_changed,
        "formula_versions_in_use": versions_now,
        "worst_component_deviation_pct": worst_deviation,
        "ingredient_lot_changes": len(changes),
        "lab_analyses_on_changes": lab,
        "environment": environment,
        "milking_anomalies": milking_anomalies,
        "weight_change_kg": weight_change,
        "missing_context": missing,
    }


# ------------------------------------------------------- suggested actions
def _action(code: str, label: str, target_type: str | None, target_id: str | None) -> dict:
    return {"code": code, "label": label, "target_type": target_type, "target_id": target_id}


def _suggested_actions(
    status: str,
    likelihood: str,
    pct: float | None,
    changes: list[dict],
    confounders: dict,
    subject_type: str,
    subject_id: str,
) -> list[dict]:
    """§15: a structured list the manager can act on, each pointing at
    the record it concerns. Recommendations only — nothing here changes a
    formula, a program, a lot or a supplier."""
    actions: list[dict] = []
    if status == "insufficient":
        return [
            _action(
                "record_more_observations",
                "Record production daily so the baseline and the evaluation window both have enough days.",
                subject_type,
                subject_id,
            )
        ]
    if status == "anomaly" and (pct or 0) < 0:
        for c in changes:
            if c.get("batch_id"):
                actions.append(
                    _action(
                        "inspect_mix",
                        f"Inspect {c.get('mix_code') or c.get('product_name')} "
                        "(smell, moisture, mould, mixing)",
                        "feed_batch",
                        c["batch_id"],
                    )
                )
            elif c.get("lot_id"):
                actions.append(
                    _action(
                        "inspect_lot",
                        f"Inspect lot {c.get('lot_code')} of {c.get('product_name')}",
                        "feed_lot",
                        c["lot_id"],
                    )
                )
            for i in c.get("ingredient_lots", []):
                if i.get("lot_id"):
                    label = f"Inspect {i.get('product_name')} lot {i.get('lot_code')}"
                    if i.get("supplier_label"):
                        label += f" from {i['supplier_label']}"
                    actions.append(_action("inspect_ingredient_lot", label, "feed_lot", i["lot_id"]))
        actions.append(
            _action(
                "compare_intake",
                "Compare feed offered against eaten and record refusals",
                subject_type,
                subject_id,
            )
        )
        worst = confounders.get("worst_component_deviation_pct")
        if worst and worst > COMPLIANCE_TOLERANCE_PCT:
            actions.append(
                _action(
                    "review_formula_compliance",
                    "A mix was off its formula: separate the mixing deviation from any ingredient question",
                    "feed_batch",
                    next((c["batch_id"] for c in changes if c.get("batch_id")), None),
                )
            )
        if likelihood in ("MODERATE", "HIGH") and changes:
            target = next((c for c in reversed(changes) if c.get("batch_id") or c.get("lot_id")), None)
            actions.append(
                _action(
                    "sample_lab_analysis",
                    "Sample for laboratory analysis if the decline persists",
                    "feed_batch" if target and target.get("batch_id") else "feed_lot",
                    (target.get("batch_id") or target.get("lot_id")) if target else None,
                )
            )
        if confounders.get("health_events") or confounders.get("active_health_cases"):
            actions.append(
                _action(
                    "check_health",
                    "Check the animals with health events or open cases before reading the change as feed",
                    subject_type,
                    subject_id,
                )
            )
        if confounders.get("milking_anomalies"):
            actions.append(
                _action(
                    "check_milking_system",
                    "Check the milking records and equipment: abnormal milk or a changed session "
                    "pattern was recorded",
                    subject_type,
                    subject_id,
                )
            )
        if (confounders.get("environment") or {}).get("heat_stress"):
            actions.append(
                _action(
                    "mitigate_heat_stress",
                    "Heat stress was recorded: shade, water and ventilation before any ration change",
                    subject_type,
                    subject_id,
                )
            )
        actions.append(
            _action(
                "review_ration",
                "Review the ration with the responsible nutrition / veterinary professional — "
                "nothing changes until a person decides",
                subject_type,
                subject_id,
            )
        )
    elif status == "anomaly":
        actions.append(
            _action(
                "document_change",
                "Production improved after a feed change: note what changed so it can be "
                "repeated deliberately",
                subject_type,
                subject_id,
            )
        )
    seen: set[tuple] = set()
    out = []
    for a in actions:
        key = (a["code"], a.get("target_id"))
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


# --------------------------------------------------------------- evaluate
def evaluate_monitor(
    db: Session,
    access: AccessContext,
    monitor: FeedPerformanceMonitor,
    *,
    as_of: datetime | None = None,
    trigger: str = "cycle",
) -> FeedPerformanceAssessment:
    """One assessment (§5): explicit baseline window and method, evaluation
    window, variance, persistence, the feed changes that preceded it, the
    confounders checked, suggested actions, a likelihood label and a
    confidence. Writes the assessment, raises or resolves the matching
    alert, and nothing else."""
    at = ensure_utc(as_of) or now_utc()
    end_day = at.date()
    period_start = end_day - timedelta(days=monitor.evaluation_window_days - 1)
    baseline_end = period_start - timedelta(days=1)
    baseline_days = (
        monitor.evaluation_window_days
        if monitor.baseline_method_code == "previous_period"
        else monitor.baseline_window_days
    )
    baseline_start = baseline_end - timedelta(days=baseline_days - 1)

    subject = _subject(db, monitor.subject_type, monitor.subject_id)
    if subject is None:
        raise PerformanceError("The monitor's subject no longer exists.", 404)
    group = programs.group_for_animal(db, subject) if monitor.subject_type == "animal" else None
    animals = _subject_animals(db, monitor.subject_type, subject)
    observed = daily_values(
        db, monitor.subject_type, subject, monitor.production_metric_code, period_start, end_day
    )
    baseline, baseline_details = _baseline(
        db, monitor, subject, animals, period_start, end_day, baseline_start, baseline_end, at
    )
    changes = _feed_changes(
        db,
        monitor.subject_type,
        subject,
        group,
        _start_of(period_start - timedelta(days=CHANGE_LOOKBACK_DAYS)),
        at,
    )
    confounders = _confounders(
        db,
        monitor,
        monitor.subject_type,
        subject,
        group,
        period_start,
        end_day,
        baseline_start,
        baseline_end,
        observed,
        changes,
    )
    unit = METRIC_UNITS[monitor.production_metric_code]
    method_applied = baseline_details["method_applied"]
    evidence: dict[str, Any] = {
        "metric": monitor.production_metric_code,
        "unit": unit,
        "baseline_method": monitor.baseline_method_code,
        "baseline_method_applied": method_applied,
        "baseline_details": baseline_details,
        "trigger": trigger,
        "baseline_window": {
            "start": baseline_start.isoformat(),
            "end": baseline_end.isoformat(),
            "observation_days": len(baseline),
        },
        "evaluation_window": {
            "start": period_start.isoformat(),
            "end": end_day.isoformat(),
            "observation_days": len(observed),
        },
        "daily_baseline": {d.isoformat(): v for d, v in sorted(baseline.items())},
        "daily_observed": {d.isoformat(): v for d, v in sorted(observed.items())},
        "feed_changes": changes,
        "threshold_pct": monitor.alert_threshold_percent,
        "minimum_observations": monitor.minimum_observations,
    }
    name = subject.name
    min_obs = monitor.minimum_observations
    threshold = monitor.alert_threshold_percent
    baseline_value = _mean(baseline.values())
    observed_value = _mean(observed.values())

    if len(observed) < min_obs or len(baseline) < min_obs or baseline_value is None or observed_value is None:
        status, likelihood, confidence = "insufficient", "INSUFFICIENT_EVIDENCE", 0.2
        variance = pct = None
        explanation = (
            f"{name}: not enough evidence to assess feed performance — {len(observed)} observation day(s) "
            f"in the evaluation window and {len(baseline)} in the baseline; at least {min_obs} of each are "
            "needed. Missing data is treated as missing, not as no change."
        )
    else:
        variance = round(observed_value - baseline_value, 4)
        pct = round(variance / baseline_value * 100, 2) if baseline_value else None
        below = sum(
            1 for v in observed.values() if baseline_value and v < baseline_value * (1 - threshold / 100)
        )
        above = sum(
            1 for v in observed.values() if baseline_value and v > baseline_value * (1 + threshold / 100)
        )
        persistent_down = pct is not None and pct <= -threshold and below >= max(2, len(observed) - 1)
        persistent_up = pct is not None and pct >= threshold and above >= max(2, len(observed) - 1)
        anomaly = persistent_down or persistent_up
        exposed_enough = [c for c in changes if c["days_of_exposure"] >= (monitor.minimum_exposure_days or 0)]
        clean = (
            confounders["health_events"] == 0
            and confounders["active_health_cases"] == 0
            and confounders["milked_heads_change"] != "changed"
            and confounders["refusal_events"] == 0
            and not confounders["recent_calving"]
            and not confounders["recent_dry_off"]
            and confounders["milking_anomalies"] == 0
            and not confounders["feeding_timing"]["changed"]
        )
        if anomaly and exposed_enough:
            likelihood = "HIGH" if clean and len(exposed_enough) == 1 else "MODERATE"
        else:
            likelihood = "LOW"
        status = "anomaly" if anomaly else "normal"
        confidence = 0.95
        confidence -= min(0.3, 0.04 * len(confounders["missing_context"]))
        if confounders["health_events"] or confounders["active_health_cases"]:
            confidence -= 0.1
        if confounders["milked_heads_change"] == "changed":
            confidence -= 0.1
        if confounders["recent_calving"] or confounders["recent_dry_off"]:
            confidence -= 0.08
        if confounders["movements"]:
            confidence -= 0.05
        if confounders["feeding_timing"]["changed"]:
            confidence -= 0.05
        if confounders["milking_anomalies"]:
            confidence -= 0.05
        if method_applied != monitor.baseline_method_code:
            confidence -= 0.05
        if len(observed) < 2 * min_obs:
            confidence -= 0.1
        if len(baseline) < 2 * min_obs:
            confidence -= 0.05
        confidence = round(_clamp(confidence, 0.1, 0.95), 2)
        direction = "below" if (pct or 0) < 0 else "above"
        method_text = method_applied.replace("_", " ")
        lines = [
            f"{name}: {monitor.production_metric_code.replace('_', ' ')} is {abs(pct or 0):.1f}% "
            f"{direction} the {method_text} baseline ({observed_value:.2f} vs {baseline_value:.2f} {unit}) "
            f"over {len(observed)} day(s)"
            + (", and persistently so." if anomaly else "; within the threshold.")
        ]
        if method_applied != monitor.baseline_method_code:
            lines.append(
                f"The {monitor.baseline_method_code.replace('_', ' ')} baseline could not be built "
                f"({baseline_details.get('fallback_reason')}); the rolling subject baseline was used instead."
            )
        if exposed_enough:
            c = exposed_enough[-1]
            what = c["mix_code"] or c["lot_code"] or c["product_name"]
            lines.append(
                f"Relevant change: {c['product_name']} {what} introduced {c['days_of_exposure']:.0f} day(s) "
                "before the end of the period"
                + (f" from {c['supplier_label']}" if c.get("supplier_label") else "")
                + "."
            )
            if c["ingredient_lots"]:
                parts = [
                    f"{i['product_name']} lot {i['lot_code']}"
                    + (f" ({i['supplier_label']})" if i.get("supplier_label") else "")
                    for i in c["ingredient_lots"]
                    if i.get("lot_code")
                ]
                if parts:
                    lines.append("It contains " + ", ".join(parts) + ".")
            if c["worst_component_deviation_pct"] is not None:
                lines.append(
                    "Formula compliance: largest component deviation "
                    f"{c['worst_component_deviation_pct']:.1f}%"
                    + (
                        " — a manufacturing deviation, to be separated from any ingredient question."
                        if c["worst_component_deviation_pct"] > COMPLIANCE_TOLERANCE_PCT
                        else " (within tolerance)."
                    )
                )
        elif changes:
            lines.append(
                "A feed change exists but the exposure is shorter than the monitor's minimum, "
                "so it is not counted as explanatory yet."
            )
        else:
            lines.append("No feed change recorded in the lookback window.")
        checks = [
            f"health events among the animals: {confounders['health_events']}",
            f"open health cases: {confounders['active_health_cases']}",
            f"milked heads: {confounders['milked_heads_change']}",
            f"refusals: {confounders['refusal_events']}",
            f"lactating {confounders['lactating_heads']}/{confounders['head_count']}, "
            f"pregnant {confounders['pregnant_heads']}",
        ]
        if confounders["days_in_milk"] is not None:
            checks.append(
                f"days in milk {confounders['days_in_milk']:.0f}"
                + (f", parity {confounders['parity']:g}" if confounders["parity"] is not None else "")
            )
        checks.append(f"movements: {confounders['movements']}")
        checks.append(f"feeding program changes: {confounders['management_changes']}")
        ft = confounders["feeding_timing"]
        checks.append(
            "feeding frequency: "
            + ("changed" if ft["changed"] else "stable")
            + (
                f" ({ft['baseline_per_day']} → {ft['evaluation_per_day']} per day)"
                if ft["evaluation_per_day"] and ft["baseline_per_day"]
                else ""
            )
        )
        if confounders["formula_version_changed"]:
            checks.append("formula version: changed")
        if confounders["lab_analyses_on_changes"]:
            checks.append(f"lab analyses on the lots introduced: {confounders['lab_analyses_on_changes']}")
        if confounders["environment"]:
            checks.append(
                f"environment: max {confounders['environment']['max_temperature_c']} °C"
                + (" (heat stress)" if confounders["environment"]["heat_stress"] else "")
            )
        if confounders["milking_anomalies"]:
            checks.append(f"milking anomalies: {confounders['milking_anomalies']}")
        if confounders["weight_change_kg"] is not None:
            checks.append(f"weight change {confounders['weight_change_kg']:+.1f} kg")
        lines.append(
            "Checks: "
            + "; ".join(checks)
            + ". Not available: "
            + ", ".join(confounders["missing_context"]).replace("_", " ")
            + "."
        )
        lines.append(
            f"Feed-related likelihood {likelihood} (an association, not a cause), "
            f"confidence {confidence:.2f}."
        )
        explanation = " ".join(lines)

    actions = _suggested_actions(
        status, likelihood, pct, changes, confounders, monitor.subject_type, str(monitor.subject_id)
    )
    evidence["suggested_actions"] = actions
    assessment = FeedPerformanceAssessment(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        monitor_id=monitor.id,
        evaluated_at=at,
        period_start=_start_of(period_start),
        period_end=_start_of(end_day + timedelta(days=1)),
        baseline_value=baseline_value,
        observed_value=observed_value,
        variance_value=variance,
        variance_percent=pct,
        feed_related_likelihood=likelihood,
        confidence_score=confidence,
        model_reference=MODEL_REFERENCE,
        model_version=MODEL_VERSION,
        evidence_json=evidence,
        confounders_json=confounders,
        explanation=explanation,
        status=status,
        trigger=trigger,
        evaluated_by=access.user_id,
    )
    db.add(assessment)
    db.flush()

    # Alerts: raised for a persistent change with a feed change behind it,
    # kept while it holds, resolved when it clears.
    decline_key = f"{monitor.id}|PRODUCTION_DECLINE_AFTER_FEED_CHANGE"
    improve_key = f"{monitor.id}|FEED_PERFORMANCE_IMPROVEMENT"
    if status == "anomaly" and likelihood in ("MODERATE", "HIGH"):
        declining = (pct or 0) < 0
        alert_type = "PRODUCTION_DECLINE_AFTER_FEED_CHANGE" if declining else "FEED_PERFORMANCE_IMPROVEMENT"
        key = decline_key if declining else improve_key
        change = next(
            (c for c in reversed(changes) if c["days_of_exposure"] >= (monitor.minimum_exposure_days or 0)),
            None,
        )
        severity = ("high" if likelihood == "HIGH" else "medium") if declining else "info"
        upsert_alert(
            db,
            access,
            alert_type=alert_type,
            severity=severity,
            title=f"{'Production decline' if declining else 'Production improvement'} "
            f"after feed change — {name}",
            explanation=explanation,
            evidence={
                "assessment_id": str(assessment.id),
                "change": change,
                "variance_pct": pct,
                "suggested_actions": actions,
                "confounders": confounders,
            },
            confidence=confidence,
            dedup_key=key,
            assessment_id=assessment.id,
            feed_batch_id=parse_uuid(change["batch_id"]) if change else None,
            feed_product_id=parse_uuid(change["feed_product_id"]) if change else None,
            subject_type=monitor.subject_type,
            subject_id=monitor.subject_id,
            review_task=(likelihood == "HIGH" and declining),
        )
        resolve_alerts(
            db, access, dedup_key=improve_key if declining else decline_key, reason="condition reversed"
        )
    elif status == "normal":
        resolve_alerts(db, access, dedup_key=decline_key, reason="production back within the threshold")
        resolve_alerts(db, access, dedup_key=improve_key, reason="production back within the threshold")
    return assessment


# ------------------------------------------------------------------ alerts
def _alert_entity(alert: FeedPerformanceAlert) -> tuple[str, str]:
    if alert.feed_batch_id:
        return "feed_batch", str(alert.feed_batch_id)
    if alert.subject_type and alert.subject_id:
        return alert.subject_type, str(alert.subject_id)
    if alert.feed_product_id:
        return "feed_product", str(alert.feed_product_id)
    return "farm", str(alert.tenant_id)


def _bell(db: Session, access: AccessContext, alert: FeedPerformanceAlert) -> None:
    """Open alerts sit on the farm's bell under the feed module; an
    acknowledged or resolved one leaves it (the alert itself stays listed)."""
    entity_type, entity_id = _alert_entity(alert)
    routing.notify_bell(
        db,
        access.tenant_id,
        module_code=MODULE_CODE,
        notification_type=f"feed_performance_{alert.alert_type.lower()}",
        title=alert.title,
        description=alert.explanation[:300],
        priority=alert.severity,
        entity_type=entity_type,
        entity_id=entity_id,
        source_type=BELL_SOURCE,
        source_id=str(alert.id),
        actor=access.membership_id,
    )


def upsert_alert(
    db: Session,
    access: AccessContext,
    *,
    alert_type: str,
    severity: str,
    title: str,
    explanation: str,
    evidence: dict,
    confidence: float | None,
    dedup_key: str,
    assessment_id: uuid.UUID | None = None,
    feed_batch_id: uuid.UUID | None = None,
    feed_product_id: uuid.UUID | None = None,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    review_task: bool = False,
) -> tuple[FeedPerformanceAlert, bool]:
    """One open alert per condition (§11): a repeat evaluation refreshes
    it instead of raising a twin. Returns (alert, created)."""
    if alert_type not in ALERT_TYPES:
        raise PerformanceError(f"alert_type must be one of {list(ALERT_TYPES)}")
    at = now_utc()
    latest = db.scalar(
        select(FeedPerformanceAlert)
        .where(FeedPerformanceAlert.deduplication_key == dedup_key, FeedPerformanceAlert.deleted_at.is_(None))
        .order_by(FeedPerformanceAlert.detected_at.desc())
    )
    existing = latest if latest is not None and latest.status != "resolved" else None
    if (
        existing is None
        and latest is not None
        and latest.resolved_by is not None
        and _SEVERITY_RANK.get(severity, 0) <= _SEVERITY_RANK.get(latest.severity, 0)
    ):
        # A person closed this very condition with a reason; it is not raised
        # again until the finding is worse than the one they explained.
        return latest, False
    if existing is not None:
        existing.explanation = explanation
        existing.evidence_json = evidence
        existing.confidence_score = confidence
        existing.severity = severity
        existing.last_seen_at = at
        existing.assessment_id = assessment_id or existing.assessment_id
        existing.last_modified_by = access.membership_id
        if existing.status == "open":
            _bell(db, access, existing)
        return existing, False
    alert = FeedPerformanceAlert(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        assessment_id=assessment_id,
        feed_batch_id=feed_batch_id,
        feed_product_id=feed_product_id,
        subject_type=subject_type,
        subject_id=subject_id,
        alert_type=alert_type,
        severity=severity,
        detected_at=at,
        last_seen_at=at,
        title=title[:200],
        explanation=explanation,
        evidence_json=evidence,
        confidence_score=confidence,
        status="open",
        deduplication_key=dedup_key,
    )
    db.add(alert)
    db.flush()
    _bell(db, access, alert)
    if review_task:
        reviewer = routing.primary_recipient(access.tenant_id, "feed_performance", db=db)
        task = Task(
            tenant_id=access.tenant_id,
            last_modified_by=access.membership_id,
            title=f"Review feed performance: {title[:160]}",
            description=explanation[:1500],
            assigned_to=reviewer.user_id if reviewer else None,
            due_at=at + timedelta(days=1),
            priority="high",
            status="open",
            source_type=REVIEW_TASK,
            source_id=str(alert.id),
        )
        db.add(task)
        db.flush()
    return alert, True


def resolve_alerts(
    db: Session, access: AccessContext, *, dedup_key: str, reason: str
) -> list[FeedPerformanceAlert]:
    rows = db.scalars(
        select(FeedPerformanceAlert).where(
            FeedPerformanceAlert.deduplication_key == dedup_key,
            FeedPerformanceAlert.status != "resolved",
            FeedPerformanceAlert.deleted_at.is_(None),
        )
    ).all()
    at = now_utc()
    for a in rows:
        a.status = "resolved"
        a.resolved_at = at
        a.resolution_note = reason
        a.last_modified_by = access.membership_id
        routing.clear_bell(db, source_type=BELL_SOURCE, source_id=str(a.id), at=at)
        for t in db.scalars(
            select(Task).where(
                Task.source_type == REVIEW_TASK,
                Task.source_id == str(a.id),
                Task.status == "open",
                Task.deleted_at.is_(None),
            )
        ):
            t.status = "done"
            t.last_modified_by = access.membership_id
    return list(rows)


def acknowledge_alert(
    db: Session, access: AccessContext, alert: FeedPerformanceAlert
) -> FeedPerformanceAlert:
    """Records that a person saw it. The condition is untouched — the
    alert resolves only when the evidence does (§11)."""
    if alert.status == "resolved":
        raise PerformanceError("This alert is already resolved.")
    at = now_utc()
    alert.status = "acknowledged"
    alert.acknowledged_by = access.user_id
    alert.acknowledged_at = at
    alert.last_modified_by = access.membership_id
    routing.clear_bell(db, source_type=BELL_SOURCE, source_id=str(alert.id), at=at)
    return alert


def resolve_alert_manually(
    db: Session, access: AccessContext, alert: FeedPerformanceAlert, *, note: str | None
) -> FeedPerformanceAlert:
    if alert.status == "resolved":
        raise PerformanceError("This alert is already resolved.")
    if not note or not note.strip():
        raise PerformanceError("Say why this alert is being closed — the evidence has not changed.")
    at = now_utc()
    alert.status = "resolved"
    alert.resolved_at = at
    alert.resolved_by = access.user_id
    alert.resolution_note = note.strip()
    alert.last_modified_by = access.membership_id
    routing.clear_bell(db, source_type=BELL_SOURCE, source_id=str(alert.id), at=at)
    for t in db.scalars(
        select(Task).where(
            Task.source_type == REVIEW_TASK,
            Task.source_id == str(alert.id),
            Task.status == "open",
            Task.deleted_at.is_(None),
        )
    ):
        t.status = "done"
        t.last_modified_by = access.membership_id
    return alert


# ---------------------------------------------------------- batch scores
def _product_name(db: Session, product_id: uuid.UUID | str | None) -> str | None:
    pk = parse_uuid(product_id)
    p = db.get(fm.FeedProduct, pk) if pk else None
    return p.name if p else None


def score_batch(db: Session, access: AccessContext, batch: fm.FeedBatch) -> FeedBatchPerformanceScore | None:
    """The numbered mix's score card (§7, §8): formula compliance from
    actual vs target, intake from refusals, production response from the
    assessments of the animals it was fed to, health signal from their
    health events during the exposure, consistency from the day-to-day
    spread, economics against the farm's other mixes of the same feed.
    A dimension with no evidence is null and lowers the confidence; it is
    never scored as perfect. A weak, confident card raises
    FEED_BATCH_UNDERPERFORMANCE; a manufacturing deviation raises its own
    alert, kept apart from any ingredient question."""
    if batch.status not in ("completed", "quarantined"):
        return None
    usage = batches.mix_usage(db, batch)
    variance = usage["variance"]
    devs = [abs(v["variance_pct"]) for v in variance if v.get("variance_pct") is not None]
    compliance = (
        round(_clamp(1 - (statistics.fmean(devs) / (2 * COMPLIANCE_TOLERANCE_PCT))), 4) if devs else None
    )
    issued, refused = usage["issued_quantity"], usage["refused_quantity"]
    intake = round(_clamp(1 - refused / issued), 4) if issued > 0 else None
    first_use = ensure_utc(usage["first_use_at"]) if usage["first_use_at"] else None
    last_use = ensure_utc(usage["last_use_at"]) if usage["last_use_at"] else None
    exposure_days = usage["days_used"]

    responses: list[float] = []
    health_events = 0
    daily_series: list[list[float]] = []
    exposed_heads = 0
    for s in usage["exposed_subjects"]:
        subject = _subject(db, s["subject_type"], s["subject_id"])
        if subject is None:
            continue
        animals = _subject_animals(db, s["subject_type"], subject)
        exposed_heads += len(animals) if animals else (getattr(subject, "count", 1) or 1)
        monitors = db.scalars(
            select(FeedPerformanceMonitor).where(
                FeedPerformanceMonitor.subject_type == s["subject_type"],
                FeedPerformanceMonitor.subject_id == subject.id,
                FeedPerformanceMonitor.deleted_at.is_(None),
            )
        ).all()
        for m in monitors:
            latest = db.scalar(
                select(FeedPerformanceAssessment)
                .where(
                    FeedPerformanceAssessment.monitor_id == m.id,
                    FeedPerformanceAssessment.status != "insufficient",
                    FeedPerformanceAssessment.period_end
                    >= (first_use or ensure_utc(batch.started_at) or now_utc()),
                )
                .order_by(FeedPerformanceAssessment.evaluated_at.desc())
            )
            if latest is not None and latest.variance_percent is not None:
                responses.append(latest.variance_percent)
            if first_use is not None:
                series = daily_values(
                    db,
                    s["subject_type"],
                    subject,
                    m.production_metric_code,
                    first_use.date(),
                    (last_use or now_utc()).date(),
                )
                if len(series) >= 3:
                    daily_series.append(list(series.values()))
        if animals and first_use is not None:
            sids = [str(a.id) for a in animals]
            hi = (last_use or now_utc()) + timedelta(days=1)
            health_events += _count_observations(db, ("animal",), sids, first_use, hi)
            health_events += _count_treatments(db, ("animal",), sids, first_use, hi)
    production = round(_clamp(0.5 + statistics.fmean(responses) / 20), 4) if responses else None
    health = (
        None
        if not usage["exposed_subjects"] or first_use is None
        else (1.0 if health_events == 0 else (0.7 if health_events <= 2 else 0.4))
    )
    consistency = None
    cvs = []
    for series_values in daily_series:
        m_ = statistics.fmean(series_values)
        if m_:
            cvs.append(statistics.pstdev(series_values) / m_)
    if cvs:
        consistency = round(_clamp(1 - 2 * statistics.fmean(cvs)), 4)
    economic = None
    peers = [
        float(c)
        for c in db.scalars(
            select(fm.FeedBatch.unit_cost).where(
                fm.FeedBatch.feed_product_id == batch.feed_product_id,
                fm.FeedBatch.status.in_(("completed", "quarantined")),
                fm.FeedBatch.unit_cost.is_not(None),
                fm.FeedBatch.id != batch.id,
                fm.FeedBatch.deleted_at.is_(None),
            )
        ).all()
        if c is not None
    ]
    if batch.unit_cost is not None and peers:
        avg = statistics.fmean(peers)
        economic = round(_clamp(1 - (batch.unit_cost - avg) / avg), 4) if avg else None
    dims = {
        "formula_compliance": compliance,
        "intake_response": intake,
        "production_response": production,
        "health_signal": health,
        "consistency": consistency,
        "economic": economic,
    }
    available = [v for v in dims.values() if v is not None]
    overall = round(statistics.fmean(available), 4) if available else None
    confidence = round(_clamp((len(available) / len(dims)) * min(1.0, 0.3 + (exposure_days or 0) / 7)), 2)
    dm_cost = _cost_per_kg_dm(db, batch)
    evidence = {
        "mix_code": batch.mix_code,
        "dimensions_available": [k for k, v in dims.items() if v is not None],
        "component_deviations_pct": {v["feed_product_id"]: v.get("variance_pct") for v in variance},
        "worst_component_deviation_pct": max(devs) if devs else None,
        "issued_quantity": issued,
        "refused_quantity": refused,
        "production_responses_pct": responses,
        "health_events_during_exposure": health_events,
        "daily_series_count": len(daily_series),
        "peer_unit_costs": peers,
        "unit_cost": batch.unit_cost,
        "cost_per_kg_dm": dm_cost,
        "exposed_subjects": [s["subject_id"] for s in usage["exposed_subjects"]],
        "operator_id": _s(batch.operator_id or batch.mixed_by),
        "mixer_asset_id": _s(batch.mixer_asset_id),
        "model_reference": MODEL_REFERENCE,
        "model_version": MODEL_VERSION,
    }
    row = db.scalar(
        select(FeedBatchPerformanceScore).where(FeedBatchPerformanceScore.feed_batch_id == batch.id)
    )
    if row is None:
        row = FeedBatchPerformanceScore(
            tenant_id=access.tenant_id, last_modified_by=access.membership_id, feed_batch_id=batch.id
        )
        db.add(row)
    row.evaluated_at = now_utc()
    row.exposed_head_count = exposed_heads or None
    row.exposure_days = float(exposure_days) if exposure_days else None
    row.formula_compliance_score = compliance
    row.intake_response_score = intake
    row.production_response_score = production
    row.health_signal_score = health
    row.consistency_score = consistency
    row.economic_score = economic
    row.overall_score = overall
    row.confidence_score = confidence
    row.evidence_json = evidence
    row.last_modified_by = access.membership_id
    db.flush()
    # Manufacturing deviation is its own finding, kept apart from any
    # question about an ingredient's quality (§8).
    worst = max(devs) if devs else None
    key = f"{batch.id}|FORMULA_COMPLIANCE_DEVIATION"
    if worst is not None and worst > COMPLIANCE_TOLERANCE_PCT:
        offenders = [
            f"{_product_name(db, v['feed_product_id'])} {v['variance_pct']:+.1f}%"
            for v in variance
            if v.get("variance_pct") is not None and abs(v["variance_pct"]) > COMPLIANCE_TOLERANCE_PCT
        ]
        upsert_alert(
            db,
            access,
            alert_type="FORMULA_COMPLIANCE_DEVIATION",
            severity="high" if worst > 2 * COMPLIANCE_TOLERANCE_PCT else "medium",
            title=f"Formula deviation in {batch.mix_code}",
            explanation=(
                f"{batch.mix_code} was mixed off its formula: {', '.join(offenders)} against target "
                f"(tolerance ±{COMPLIANCE_TOLERANCE_PCT:.0f}%). This is a manufacturing deviation; any "
                "production change after this mix should be read with it in mind before an ingredient "
                "or supplier is questioned."
            ),
            evidence={
                "component_deviations_pct": evidence["component_deviations_pct"],
                "tolerance_pct": COMPLIANCE_TOLERANCE_PCT,
                "suggested_actions": [
                    _action(
                        "review_formula_compliance",
                        "Check the weighing and mixing record of this mix",
                        "feed_batch",
                        str(batch.id),
                    )
                ],
            },
            confidence=0.95,
            dedup_key=key,
            feed_batch_id=batch.id,
            feed_product_id=batch.feed_product_id,
        )
    # A weak, confident card: the mix itself is associated with weaker
    # production downstream (§11 FEED_BATCH_UNDERPERFORMANCE).
    under_key = f"{batch.id}|FEED_BATCH_UNDERPERFORMANCE"
    if (
        production is not None
        and production < UNDERPERFORMANCE_SCORE
        and overall is not None
        and overall < 0.5
        and confidence >= MIN_CONFIDENCE_FOR_ALERT
        and (exposure_days or 0) >= 2
    ):
        mean_resp = statistics.fmean(responses)
        caveat = (
            " The mix was also off its formula, so manufacturing is a candidate explanation "
            "before any ingredient."
            if compliance is not None and compliance < 0.5
            else ""
        )
        upsert_alert(
            db,
            access,
            alert_type="FEED_BATCH_UNDERPERFORMANCE",
            severity="high" if production < 0.3 else "medium",
            title=f"{batch.mix_code} is associated with weaker production",
            explanation=(
                f"Animals fed {batch.mix_code} show a production response of {mean_resp:+.1f}% against their "
                f"baselines over {exposure_days} day(s) of use (overall score {overall:.2f}, confidence "
                f"{confidence:.2f}). Health events during the exposure: {health_events}.{caveat} "
                "An association, not a cause: inspect the mix and its ingredient lots, compare intake, "
                "and review the ration with the responsible professional."
            ),
            evidence={
                "score": dict(dims),
                "production_responses_pct": responses,
                "exposure_days": exposure_days,
                "suggested_actions": [
                    _action("inspect_mix", f"Inspect {batch.mix_code}", "feed_batch", str(batch.id)),
                    _action(
                        "compare_intake",
                        "Compare offered against eaten on the exposed animals",
                        "feed_batch",
                        str(batch.id),
                    ),
                    _action(
                        "sample_lab_analysis",
                        "Sample the mix for laboratory analysis",
                        "feed_batch",
                        str(batch.id),
                    ),
                ],
            },
            confidence=confidence,
            dedup_key=under_key,
            feed_batch_id=batch.id,
            feed_product_id=batch.feed_product_id,
        )
    else:
        resolve_alerts(
            db, access, dedup_key=under_key, reason="the mix's production response is no longer weak"
        )
    return row


def score_all_batches(db: Session, access: AccessContext) -> list[FeedBatchPerformanceScore]:
    rows = []
    for b in db.scalars(
        select(fm.FeedBatch).where(
            fm.FeedBatch.status.in_(("completed", "quarantined")), fm.FeedBatch.deleted_at.is_(None)
        )
    ):
        r = score_batch(db, access, b)
        if r is not None:
            rows.append(r)
    return rows


def _open_alerts_for_batch(db: Session, batch_id: uuid.UUID) -> list[FeedPerformanceAlert]:
    return list(
        db.scalars(
            select(FeedPerformanceAlert).where(
                FeedPerformanceAlert.feed_batch_id == batch_id,
                FeedPerformanceAlert.status != "resolved",
                FeedPerformanceAlert.deleted_at.is_(None),
            )
        )
    )


def batch_score_dict(db: Session, row: FeedBatchPerformanceScore) -> dict:
    batch = db.get(fm.FeedBatch, row.feed_batch_id)
    alerts = _open_alerts_for_batch(db, row.feed_batch_id)
    return {
        "feed_batch_id": str(row.feed_batch_id),
        "mix_number": batch.mix_number if batch else None,
        "mix_code": batch.mix_code if batch else None,
        "batch_code": batch.batch_code if batch else None,
        "product_name": _product_name(db, batch.feed_product_id) if batch else None,
        "status": batch.status if batch else None,
        "produced_at": batch.produced_at if batch else None,
        "evaluated_at": row.evaluated_at,
        "exposed_head_count": row.exposed_head_count,
        "exposure_days": row.exposure_days,
        "formula_compliance_score": row.formula_compliance_score,
        "intake_response_score": row.intake_response_score,
        "production_response_score": row.production_response_score,
        "health_signal_score": row.health_signal_score,
        "consistency_score": row.consistency_score,
        "economic_score": row.economic_score,
        "overall_score": row.overall_score,
        "confidence_score": row.confidence_score,
        "evidence": row.evidence_json,
        "open_alerts": [
            {
                "id": str(a.id),
                "alert_type": a.alert_type,
                "severity": a.severity,
                "status": a.status,
                "title": a.title,
            }
            for a in alerts
        ],
    }


def batch_performance_summary(db: Session, batch: fm.FeedBatch) -> dict | None:
    """What `GET /feed-mixes/{n}` shows under `performance`: the score card's
    headline and the open alerts on the mix, or None before any cycle."""
    row = db.scalar(
        select(FeedBatchPerformanceScore).where(FeedBatchPerformanceScore.feed_batch_id == batch.id)
    )
    if row is None:
        alerts = _open_alerts_for_batch(db, batch.id)
        if not alerts:
            return None
        return {
            "overall_score": None,
            "confidence_score": None,
            "open_alerts": [
                {"id": str(a.id), "alert_type": a.alert_type, "severity": a.severity, "title": a.title}
                for a in alerts
            ],
        }
    d = batch_score_dict(db, row)
    return {
        k: d[k]
        for k in (
            "overall_score",
            "confidence_score",
            "formula_compliance_score",
            "production_response_score",
            "intake_response_score",
            "health_signal_score",
            "economic_score",
            "open_alerts",
            "evaluated_at",
        )
    }


# ---------------------------------------------------- ingredient lots (§8)
def ingredient_lot_performance(
    db: Session, access: AccessContext, *, write_alerts: bool = True
) -> list[dict]:
    """Does one purchased ingredient lot travel with weaker production
    through the mixes that used it, compared with the farm's other mixes
    of the same feed that did not? Drill-down to the mixes is kept; a
    manufacturing deviation on those mixes is stated, not hidden (§8).
    The cycle raises and resolves the alerts (`write_alerts`); a read of
    the findings does not."""
    scores = {s.feed_batch_id: s for s in db.scalars(select(FeedBatchPerformanceScore))}
    if not scores:
        return []
    batch_by_id = {b.id: b for b in db.scalars(select(fm.FeedBatch).where(fm.FeedBatch.id.in_(list(scores))))}
    using: dict[uuid.UUID, set[uuid.UUID]] = {}
    for comp in db.scalars(
        select(fm.FeedBatchComponent).where(
            fm.FeedBatchComponent.batch_id.in_(list(scores)), fm.FeedBatchComponent.lot_id.is_not(None)
        )
    ):
        if comp.lot_id is not None:
            using.setdefault(comp.lot_id, set()).add(comp.batch_id)
    findings = []
    for lot_id, batch_ids in using.items():
        lot = db.get(fm.FeedLot, lot_id)
        if lot is None or lot.source_type != "purchased":
            continue
        resp = [r for b in batch_ids if (r := scores[b].production_response_score) is not None]
        key = f"{lot.id}|INGREDIENT_LOT_PERFORMANCE_ANOMALY"
        if not resp:
            if write_alerts:
                resolve_alerts(db, access, dedup_key=key, reason="no production evidence on the mixes")
            else:
                # The read lists the lot without a verdict: no mix made from it
                # has a production reading yet.
                findings.append(
                    {
                        "lot_id": str(lot.id),
                        "lot_code": lot.lot_code,
                        "feed_product_id": str(lot.feed_product_id),
                        "product_name": _product_name(db, lot.feed_product_id),
                        "supplier_label": lot.supplier_label,
                        "mixes": [batch_by_id[b].mix_code for b in batch_ids if b in batch_by_id],
                        "production_response_score": None,
                        "other_mixes_score": None,
                        "manufacturing_deviation": False,
                        "weak": False,
                    }
                )
            continue
        lot_mean = statistics.fmean(resp)
        products = {batch_by_id[b].feed_product_id for b in batch_ids if b in batch_by_id}
        others = [
            s.production_response_score
            for bid, s in scores.items()
            if bid not in batch_ids
            and s.production_response_score is not None
            and batch_by_id.get(bid) is not None
            and batch_by_id[bid].feed_product_id in products
        ]
        others_mean = statistics.fmean(others) if others else None
        compliance = [c for b in batch_ids if (c := scores[b].formula_compliance_score) is not None]
        deviated = bool(compliance) and min(compliance) < 0.5
        weak = lot_mean < UNDERPERFORMANCE_SCORE and (others_mean is None or others_mean >= lot_mean + 0.1)
        mix_codes = [batch_by_id[b].mix_code for b in batch_ids if b in batch_by_id]
        finding: dict[str, Any] = {
            "lot_id": str(lot.id),
            "lot_code": lot.lot_code,
            "feed_product_id": str(lot.feed_product_id),
            "product_name": _product_name(db, lot.feed_product_id),
            "supplier_label": lot.supplier_label,
            "mixes": mix_codes,
            "production_response_score": round(lot_mean, 4),
            "other_mixes_score": round(others_mean, 4) if others_mean is not None else None,
            "manufacturing_deviation": deviated,
            "weak": weak,
        }
        findings.append(finding)
        if not write_alerts:
            continue
        if weak:
            pname = finding["product_name"] or "Ingredient"
            confidence = round(
                _clamp(
                    0.4
                    + 0.15 * min(len(batch_ids), 3)
                    - (0.2 if deviated else 0)
                    + (0.1 if others_mean is not None else 0)
                ),
                2,
            )
            mixes = ", ".join(mix_codes)
            upsert_alert(
                db,
                access,
                alert_type="INGREDIENT_LOT_PERFORMANCE_ANOMALY",
                severity="medium",
                title=f"{pname} lot {lot.lot_code} travels with weaker production",
                explanation=(
                    f"The mixes containing {pname} lot {lot.lot_code}"
                    + (f" from {lot.supplier_label}" if lot.supplier_label else "")
                    + f" ({mixes}) score {lot_mean:.2f} on production response"
                    + (
                        f" against {others_mean:.2f} for mixes of the same feed without it"
                        if others_mean is not None
                        else ""
                    )
                    + "."
                    + (
                        " Those mixes were also off their formula, so manufacturing must be ruled out first; "
                        "this is not clean evidence against the ingredient."
                        if deviated
                        else ""
                    )
                    + " An association, not a cause: inspect the lot, check its declared and laboratory "
                    "analysis, and compare the supplier's other lots."
                ),
                evidence={
                    "finding": finding,
                    "suggested_actions": [
                        _action(
                            "inspect_ingredient_lot", f"Inspect lot {lot.lot_code}", "feed_lot", str(lot.id)
                        ),
                        _action(
                            "sample_lab_analysis",
                            "Send a sample of the lot for laboratory analysis",
                            "feed_lot",
                            str(lot.id),
                        ),
                        _action(
                            "compare_supplier_lots",
                            "Compare with the supplier's other lots of the same ingredient",
                            "feed_product",
                            str(lot.feed_product_id),
                        ),
                    ],
                },
                confidence=confidence,
                dedup_key=key,
                feed_product_id=lot.feed_product_id,
            )
        else:
            resolve_alerts(db, access, dedup_key=key, reason="the lot's mixes no longer stand out")
    return findings


# --------------------------------------------------------------- suppliers
def _declared_dm(db: Session, product: fm.FeedProduct | None) -> float | None:
    if product is None:
        return None
    profile = batches.latest_profile(db, "feed_product", product.id, basis="as_fed")
    if profile is None:
        return None
    for v in profile.values:
        if v.nutrient_code.upper() == "DM" and v.value:
            return float(v.value)
    return None


def _cost_per_kg_dm(db: Session, batch: fm.FeedBatch) -> float | None:
    """Dry-matter-adjusted cost of a mix (§10): the unit cost over the
    formula's calculated dry matter, when the components declare one."""
    if batch.unit_cost is None or not batch.formula_version_id:
        return None
    version = db.get(fm.FeedFormulaVersion, batch.formula_version_id)
    if version is None:
        return None
    calc = batches.formula_nutrients(db, version, basis="as_fed")
    dm = next((v["value"] for v in calc["values"] if v["nutrient_code"].upper() == "DM"), None)
    if not dm:
        return None
    return round(batch.unit_cost / (dm / 100), 6)


def supplier_performance(
    db: Session, access: AccessContext, *, days: int = 90
) -> list[SupplierFeedPerformance]:
    """Supplier × ingredient from the supplier's lots in the period (§9):
    what was bought and at what cost, how many mixes used it, incidents on
    those lots, and the production response of the mixes downstream of
    them, with a dry-matter-adjusted cost and an effective-value frame
    (§10). Rebuilt whole; different ingredients from one supplier stay
    separate rows. Weak, confident downstream evidence raises
    SUPPLIER_LOT_PERFORMANCE_ANOMALY."""
    at = now_utc()
    since = at - timedelta(days=days)
    db.execute(delete(SupplierFeedPerformance))
    lots = db.scalars(
        select(fm.FeedLot).where(
            fm.FeedLot.source_type == "purchased",
            fm.FeedLot.received_at >= since,
            fm.FeedLot.deleted_at.is_(None),
        )
    ).all()
    groups: dict[tuple, list[fm.FeedLot]] = {}
    for lot in lots:
        label = lot.supplier_label or (str(lot.supplier_id) if lot.supplier_id else None)
        if not label:
            continue
        groups.setdefault((lot.supplier_id, label, lot.feed_product_id), []).append(lot)
    cost_by_product: dict[uuid.UUID, list[float]] = {}
    for (_, _, product_id), lot_rows in groups.items():
        for lt in lot_rows:
            if lt.unit_cost is not None:
                cost_by_product.setdefault(product_id, []).append(lt.unit_cost)
    rows = []
    for (supplier_id, label, product_id), lot_rows in groups.items():
        product = db.get(fm.FeedProduct, product_id)
        if product is None:
            continue
        lot_ids = [lt.id for lt in lot_rows]
        batch_ids = set(
            db.scalars(
                select(fm.FeedBatchComponent.batch_id).where(fm.FeedBatchComponent.lot_id.in_(lot_ids))
            )
        )
        qty = sum(lt.accepted_quantity or 0 for lt in lot_rows)
        costed = [
            (lt.accepted_quantity or 0, lt.unit_cost)
            for lt in lot_rows
            if lt.unit_cost is not None and lt.accepted_quantity
        ]
        avg_cost = round(sum(q * c for q, c in costed) / sum(q for q, _ in costed), 4) if costed else None
        incidents = sum(
            1
            for lt in lot_rows
            if lt.status in ("quarantined", "blocked", "recalled") or (lt.rejected_quantity or 0) > 0
        )
        scores = [
            db.scalar(select(FeedBatchPerformanceScore).where(FeedBatchPerformanceScore.feed_batch_id == b))
            for b in batch_ids
        ]
        downstream = _mean(s.production_response_score for s in scores if s is not None)
        overall_mix = _mean(s.overall_score for s in scores if s is not None)
        profiles = db.scalars(
            select(fm.FeedNutrientProfile).where(
                fm.FeedNutrientProfile.subject_type == "feed_lot",
                fm.FeedNutrientProfile.subject_id.in_(lot_ids),
                fm.FeedNutrientProfile.source_type == "lab",
            )
        ).all()
        quality = None
        cp: list[float] = []
        for p in profiles:
            for v in p.values:
                if v.nutrient_code.upper() in ("CP", "CRUDE_PROTEIN") and v.value is not None:
                    cp.append(float(v.value))
        if len(cp) >= 2 and statistics.fmean(cp):
            quality = round(_clamp(1 - 3 * statistics.pstdev(cp) / statistics.fmean(cp)), 4)
        confidence = round(
            _clamp(min(1.0, len(lot_rows) / 3) * (0.4 + 0.6 * (1 if downstream is not None else 0))), 2
        )
        dm = _declared_dm(db, product)
        cost_per_kg_dm = round(avg_cost / (dm / 100), 6) if avg_cost is not None and dm else None
        farm_avg = statistics.fmean(cost_by_product[product_id]) if cost_by_product.get(product_id) else None
        price_score = (
            round(_clamp(0.5 + (farm_avg - avg_cost) / farm_avg), 4)
            if avg_cost is not None and farm_avg
            else None
        )
        effective = (
            round(_clamp(0.5 * price_score + 0.5 * downstream), 4)
            if price_score is not None and downstream is not None
            else None
        )
        row = SupplierFeedPerformance(
            tenant_id=access.tenant_id,
            last_modified_by=access.membership_id,
            supplier_id=supplier_id,
            supplier_label=label,
            inventory_item_id=product.inventory_item_id,
            feed_product_id=product_id,
            evaluation_start=since,
            evaluation_end=at,
            lot_count=len(lot_rows),
            feed_batch_count=len(batch_ids),
            purchase_quantity=round(qty, 3),
            average_unit_cost=avg_cost,
            quality_consistency_score=quality,
            downstream_performance_score=downstream,
            incident_count=incidents,
            confidence_score=confidence,
            methodology_code="deterministic_lot_lineage",
            methodology_version=MODEL_VERSION,
            evidence_json={
                "lots": [
                    {
                        "lot_id": str(lt.id),
                        "lot_code": lt.lot_code,
                        "status": lt.status,
                        "accepted_quantity": lt.accepted_quantity,
                        "rejected_quantity": lt.rejected_quantity,
                        "unit_cost": lt.unit_cost,
                        "received_at": lt.received_at.isoformat() if lt.received_at else None,
                    }
                    for lt in lot_rows
                ],
                "batches": sorted(str(b) for b in batch_ids),
                "downstream_overall_score": overall_mix,
                "lab_profiles": len(profiles),
                "unit": product.unit,
                "declared_dm_pct": dm,
                "cost_per_kg_dm": cost_per_kg_dm,
                "farm_average_unit_cost": round(farm_avg, 4) if farm_avg else None,
                "price_score": price_score,
                "effective_value_score": effective,
            },
            generated_at=at,
        )
        db.add(row)
        rows.append(row)
        key = f"supplier:{supplier_id or label}:{product_id}|SUPPLIER_LOT_PERFORMANCE_ANOMALY"
        if (
            downstream is not None
            and downstream < UNDERPERFORMANCE_SCORE
            and len(batch_ids) >= 2
            and confidence >= MIN_CONFIDENCE_FOR_ALERT
        ):
            db.flush()
            upsert_alert(
                db,
                access,
                alert_type="SUPPLIER_LOT_PERFORMANCE_ANOMALY",
                severity="medium",
                title=f"{label}'s {product.name} lots travel with weaker production",
                explanation=(
                    f"{len(lot_rows)} lot(s) of {product.name} from {label} went into "
                    f"{len(batch_ids)} mix(es) "
                    f"whose production response averages {downstream:.2f} (0.5 is no change), at "
                    f"{avg_cost if avg_cost is not None else '—'} per {product.unit}"
                    + (f", {cost_per_kg_dm} per kg DM" if cost_per_kg_dm else "")
                    + f". Confidence {confidence:.2f}. An association, not a cause, and never by itself a "
                    "reason to block the supplier: inspect the lots, check analyses and compare with the "
                    "farm's other sources before any purchasing decision."
                ),
                evidence={
                    "supplier_row_id": str(row.id),
                    "lots": [lt.lot_code for lt in lot_rows],
                    "batches": sorted(str(b) for b in batch_ids),
                    "downstream_performance_score": downstream,
                    "effective_value_score": effective,
                    "suggested_actions": [
                        _action(
                            "compare_supplier_lots",
                            f"Compare {label}'s lots with other sources of {product.name}",
                            "feed_product",
                            str(product_id),
                        ),
                        _action(
                            "sample_lab_analysis",
                            "Send the supplier's current lot for laboratory analysis",
                            "feed_product",
                            str(product_id),
                        ),
                        _action(
                            "review_purchasing",
                            "Review purchasing with the manager — an analytical score never blocks "
                            "a supplier by itself",
                            "feed_product",
                            str(product_id),
                        ),
                    ],
                },
                confidence=confidence,
                dedup_key=key,
                feed_product_id=product_id,
            )
        else:
            resolve_alerts(
                db, access, dedup_key=key, reason="the supplier's lots no longer stand out downstream"
            )
    db.flush()
    return rows


def supplier_row_dict(db: Session, r: SupplierFeedPerformance) -> dict:
    ev = r.evidence_json or {}
    return {
        "id": str(r.id),
        "supplier_id": _s(r.supplier_id),
        "supplier_label": r.supplier_label,
        "feed_product_id": str(r.feed_product_id),
        "product_name": _product_name(db, r.feed_product_id),
        "inventory_item_id": str(r.inventory_item_id),
        "evaluation_start": r.evaluation_start,
        "evaluation_end": r.evaluation_end,
        "lot_count": r.lot_count,
        "feed_batch_count": r.feed_batch_count,
        "purchase_quantity": r.purchase_quantity,
        "unit": ev.get("unit"),
        "average_unit_cost": r.average_unit_cost,
        "cost_per_kg_dm": ev.get("cost_per_kg_dm"),
        "effective_value_score": ev.get("effective_value_score"),
        "quality_consistency_score": r.quality_consistency_score,
        "downstream_performance_score": r.downstream_performance_score,
        "incident_count": r.incident_count,
        "confidence_score": r.confidence_score,
        "methodology_code": r.methodology_code,
        "methodology_version": r.methodology_version,
        "evidence": r.evidence_json,
        "generated_at": r.generated_at,
    }


# ------------------------------------------- cost, value and intake checks
def _milk_litres(db: Session, start: datetime, end: datetime) -> float:
    return _f(
        db.scalar(
            select(func.sum(MilkRecord.liters)).where(
                MilkRecord.deleted_at.is_(None), MilkRecord.recorded_at >= start, MilkRecord.recorded_at < end
            )
        )
    )


def _cost_per_litre(db: Session, start: datetime, end: datetime) -> float | None:
    cost = _f(
        db.scalar(
            select(func.sum(fm.FeedingEvent.total_cost)).where(
                fm.FeedingEvent.status == "recorded",
                fm.FeedingEvent.deleted_at.is_(None),
                fm.FeedingEvent.occurred_at >= start,
                fm.FeedingEvent.occurred_at < end,
            )
        )
    )
    litres = _milk_litres(db, start, end)
    if not litres or not cost:
        return None
    return round(cost / litres, 4)


def effective_feed_value(db: Session, *, days: int = 7) -> dict:
    """§10: purchase price alone is insufficient. For the last `days`:
    feed cost, cost per litre, cost per head per day, milk revenue (at the
    farm's own recent selling price) less feed cost, and the dry-matter-
    adjusted cost of the mixes in use. Every figure names what it lacks."""
    end = now_utc()
    start = end - timedelta(days=days)
    events = db.scalars(
        select(fm.FeedingEvent).where(
            fm.FeedingEvent.status == "recorded",
            fm.FeedingEvent.deleted_at.is_(None),
            fm.FeedingEvent.event_type.in_(("offered", "delivered")),
            fm.FeedingEvent.occurred_at >= start,
            fm.FeedingEvent.occurred_at < end,
        )
    ).all()
    cost = round(sum(e.total_cost or 0 for e in events), 4)
    head_days: dict[tuple, int] = {}
    for e in events:
        key = ((ensure_utc(e.occurred_at) or end).date(), e.subject_type, e.subject_id)
        head_days[key] = max(head_days.get(key, 0), e.head_count or 1)
    hd = sum(head_days.values())
    litres = _milk_litres(db, start, end)
    sales = db.scalars(
        select(Sale).where(
            Sale.product_type == "milk",
            Sale.deleted_at.is_(None),
            Sale.sold_at >= end - timedelta(days=90),
            Sale.quantity.is_not(None),
            Sale.quantity > 0,
        )
    ).all()
    price = round(sum(_f(s.amount) for s in sales) / sum(_f(s.quantity) for s in sales), 4) if sales else None
    revenue = round(litres * price, 2) if price is not None and litres else None
    mixes = []
    for b in db.scalars(
        select(fm.FeedBatch)
        .where(
            fm.FeedBatch.status.in_(("completed", "quarantined")),
            fm.FeedBatch.unit_cost.is_not(None),
            fm.FeedBatch.deleted_at.is_(None),
        )
        .order_by(fm.FeedBatch.mix_number.desc())
        .limit(10)
    ):
        mixes.append(
            {
                "batch_id": str(b.id),
                "mix_code": b.mix_code,
                "unit_cost": b.unit_cost,
                "unit": b.unit,
                "cost_per_kg_dm": _cost_per_kg_dm(db, b),
            }
        )
    missing = []
    if not litres:
        missing.append("milk volume")
    if price is None:
        missing.append("milk selling price (no milk sale with a quantity in the last 90 days)")
    if not hd:
        missing.append("head-days (no feeding events with head counts)")
    if not any(m["cost_per_kg_dm"] for m in mixes):
        missing.append("dry matter of the mixes (declare DM on the ingredients)")
    return {
        "window_days": days,
        "feed_cost": cost,
        "milk_litres": round(litres, 3),
        "cost_per_litre": round(cost / litres, 4) if litres and cost else None,
        "head_days": hd,
        "cost_per_head_day": round(cost / hd, 4) if hd and cost else None,
        "milk_price_per_litre": price,
        "milk_revenue": revenue,
        "revenue_less_feed_cost": round(revenue - cost, 2) if revenue is not None else None,
        "mixes": mixes,
        "missing": missing,
        "note": (
            "Effective value combines commercial cost with downstream performance; see the supplier rows' "
            "effective_value_score. Nutrient-adjusted cost needs laboratory analyses."
        ),
    }


def check_cost_efficiency(db: Session, access: AccessContext) -> dict:
    """Feed cost per litre, this week against last (§10). A rise beyond the
    threshold is an alert with both figures; a fall resolves it."""
    end = now_utc()
    this = _cost_per_litre(db, end - timedelta(days=7), end)
    prev = _cost_per_litre(db, end - timedelta(days=14), end - timedelta(days=7))
    change = round((this - prev) / prev * 100, 2) if this is not None and prev else None
    value = effective_feed_value(db)
    key = "farm|FEED_COST_PER_OUTPUT_INCREASE"
    if change is not None and this is not None and prev is not None and change > COST_INCREASE_ALERT_PCT:
        upsert_alert(
            db,
            access,
            alert_type="FEED_COST_PER_OUTPUT_INCREASE",
            severity="medium",
            title="Feed cost per litre is rising",
            explanation=(
                f"Feed cost per litre of milk is {this:.3f} this week against {prev:.3f} last week "
                f"({change:+.1f}%). Check the mixes and lots introduced this week and the milk recorded "
                "against them."
            ),
            evidence={
                "cost_per_litre_this_week": this,
                "cost_per_litre_last_week": prev,
                "change_pct": change,
                "effective_feed_value": value,
                "suggested_actions": [
                    _action(
                        "review_mix_costs",
                        "Compare the unit cost and DM-adjusted cost of this week's mixes",
                        "farm",
                        str(access.tenant_id),
                    ),
                    _action(
                        "review_production",
                        "Check whether milk fell or feed cost rose",
                        "farm",
                        str(access.tenant_id),
                    ),
                ],
            },
            confidence=0.7,
            dedup_key=key,
        )
    elif change is not None:
        resolve_alerts(db, access, dedup_key=key, reason="cost per litre back within range")
    return {
        "cost_per_litre_this_week": this,
        "cost_per_litre_last_week": prev,
        "change_pct": change,
        "effective_feed_value": value,
    }


def check_intake(db: Session, access: AccessContext) -> list[FeedPerformanceAlert]:
    since = now_utc() - timedelta(days=3)
    rows = db.execute(
        select(fm.FeedingEvent.subject_type, fm.FeedingEvent.subject_id, func.count(fm.FeedingEvent.id))
        .where(
            fm.FeedingEvent.event_type == "refusal",
            fm.FeedingEvent.status == "recorded",
            fm.FeedingEvent.deleted_at.is_(None),
            fm.FeedingEvent.occurred_at >= since,
        )
        .group_by(fm.FeedingEvent.subject_type, fm.FeedingEvent.subject_id)
    ).all()
    out = []
    for stype, sid, count in rows:
        key = f"{stype}:{sid}|INTAKE_OR_REFUSAL_ANOMALY"
        if count < REFUSALS_FOR_ALERT:
            resolve_alerts(db, access, dedup_key=key, reason="refusals have stopped")
            continue
        subject = _subject(db, stype, sid)
        name = getattr(subject, "name", str(sid))
        alert, _ = upsert_alert(
            db,
            access,
            alert_type="INTAKE_OR_REFUSAL_ANOMALY",
            severity="medium",
            title=f"Repeated refusals — {name}",
            explanation=(
                f"{name} refused feed {count} times in the last 3 days. Compare offered against eaten and "
                "check the lots being issued before reading any production change as feed quality."
            ),
            evidence={
                "refusals_3d": count,
                "suggested_actions": [
                    _action("compare_intake", "Compare offered against eaten", stype, str(sid)),
                    _action("inspect_lot", "Inspect the lots being issued", stype, str(sid)),
                ],
            },
            confidence=0.8,
            dedup_key=key,
            subject_type=stype,
            subject_id=sid,
        )
        out.append(alert)
    return out


# ------------------------------------------------------------------ cycle
def run_cycle(
    db: Session, access: AccessContext, *, as_of: datetime | None = None, monitor_id: str | None = None
) -> dict:
    """The daily feed-performance cycle (§12): default monitors, fresh
    exposure projection, every active monitor evaluated, mixes, ingredient
    lots and suppliers scored, cost and intake checked. Idempotent —
    alerts are deduplicated and projections rebuilt."""
    ensure_default_monitors(db, access)
    rebuild_exposures(db, access)
    stmt = select(FeedPerformanceMonitor).where(
        FeedPerformanceMonitor.active.is_(True), FeedPerformanceMonitor.deleted_at.is_(None)
    )
    if monitor_id:
        pk = parse_uuid(monitor_id)
        if pk is None:
            raise PerformanceError("Monitor not found", 404)
        stmt = stmt.where(FeedPerformanceMonitor.id == pk)
    assessments = [
        evaluate_monitor(db, access, m, as_of=as_of, trigger="cycle")
        for m in db.scalars(stmt.order_by(FeedPerformanceMonitor.created_at))
    ]
    scores = score_all_batches(db, access)
    lots = ingredient_lot_performance(db, access)
    suppliers = supplier_performance(db, access)
    cost = check_cost_efficiency(db, access)
    intake = check_intake(db, access)
    open_alerts = (
        db.scalar(
            select(func.count(FeedPerformanceAlert.id)).where(
                FeedPerformanceAlert.status != "resolved", FeedPerformanceAlert.deleted_at.is_(None)
            )
        )
        or 0
    )
    return {
        "evaluated_at": ensure_utc(as_of) or now_utc(),
        "monitors": len(assessments),
        "assessments": [assessment_dict(db, a) for a in assessments],
        "anomalies": sum(1 for a in assessments if a.status == "anomaly"),
        "insufficient": sum(1 for a in assessments if a.status == "insufficient"),
        "batches_scored": len(scores),
        "ingredient_lots_evaluated": len(lots),
        "ingredient_lot_anomalies": sum(1 for f in lots if f["weak"]),
        "suppliers": len(suppliers),
        "cost_efficiency": cost,
        "intake_alerts": len(intake),
        "open_alerts": open_alerts,
    }


# ---------------------------------------------------------- hooks & views
def _monitors_for(db: Session, subject_type: str, subject) -> list[FeedPerformanceMonitor]:
    keys = [(subject_type, subject.id)]
    if subject_type == "animal":
        group = programs.group_for_animal(db, subject)
        if group is not None:
            keys.append(("group", group.id))
    monitors: list[FeedPerformanceMonitor] = []
    for stype, sid in keys:
        monitors += db.scalars(
            select(FeedPerformanceMonitor).where(
                FeedPerformanceMonitor.subject_type == stype,
                FeedPerformanceMonitor.subject_id == sid,
                FeedPerformanceMonitor.active.is_(True),
                FeedPerformanceMonitor.deleted_at.is_(None),
            )
        ).all()
    return monitors


def _safely(db: Session, what: str, fn: Callable[[], object]) -> None:
    """Hooks never cost the farm the record that triggered them: the
    analytics run inside a savepoint, and a failure is logged and rolled
    back to it."""
    try:
        with db.begin_nested():
            fn()
    except Exception:  # noqa: BLE001 - analytics must not break a milk record or a feeding event
        log.exception("feed performance hook %s failed", what)


def _reevaluate(db: Session, access: AccessContext, subject_type: str, subject, *, trigger: str) -> int:
    monitors = _monitors_for(db, subject_type, subject)
    if not monitors:
        return 0
    rebuild_exposures(db, access)
    for m in monitors:
        evaluate_monitor(db, access, m, trigger=trigger)
    return len(monitors)


def on_batch_completed(db: Session, access: AccessContext, batch: fm.FeedBatch) -> None:
    _safely(db, "batch_completed", lambda: score_batch(db, access, batch))


def on_production_recorded(db: Session, access: AccessContext, subject_type: str, subject) -> None:
    """After a milk or egg record: re-evaluate the monitors of the subject
    and of its group. Cheap, and it keeps alerts current between cycles."""
    _safely(
        db,
        "production_recorded",
        lambda: _reevaluate(db, access, subject_type, subject, trigger="production_recorded"),
    )


def on_feeding_recorded(db: Session, access: AccessContext, subject_type: str, subject) -> None:
    """§12 FeedingEventRecorded: a new issue (or a refusal) re-evaluates
    the subject's monitors, so a feed change is on record the day it starts."""
    _safely(
        db,
        "feeding_recorded",
        lambda: _reevaluate(db, access, subject_type, subject, trigger="feeding_recorded"),
    )


def on_context_changed(
    db: Session, access: AccessContext, subject_type: str, subject, *, trigger: str
) -> None:
    """§12 FeedingProgramAssigned/changed, HealthCaseOpened/Closed and
    lifecycle / group / weight changes: the confounders moved, so the
    reading is taken again."""
    _safely(db, trigger, lambda: _reevaluate(db, access, subject_type, subject, trigger=trigger))


def _lot_status_changed(db: Session, access: AccessContext, lot: fm.FeedLot) -> None:
    batch_ids = set(
        db.scalars(select(fm.FeedBatchComponent.batch_id).where(fm.FeedBatchComponent.lot_id == lot.id))
    )
    if lot.feed_batch_id:
        batch_ids.add(lot.feed_batch_id)
    output_lots = [lot.id]
    for bid in batch_ids:
        batch = db.get(fm.FeedBatch, bid)
        if batch is None:
            continue
        if batch.status in ("completed", "quarantined"):
            score_batch(db, access, batch)
        if batch.output_lot_id:
            output_lots.append(batch.output_lot_id)
    subjects = set(
        db.execute(
            select(fm.FeedingEvent.subject_type, fm.FeedingEvent.subject_id)
            .join(fm.FeedingEventComponent, fm.FeedingEventComponent.event_id == fm.FeedingEvent.id)
            .where(fm.FeedingEventComponent.lot_id.in_(output_lots), fm.FeedingEvent.status == "recorded")
        ).all()
    )
    rebuilt = False
    for stype, sid in subjects:
        subject = _subject(db, stype, sid)
        if subject is None:
            continue
        monitors = _monitors_for(db, stype, subject)
        if not monitors:
            continue
        if not rebuilt:
            rebuild_exposures(db, access)
            rebuilt = True
        for m in monitors:
            evaluate_monitor(db, access, m, trigger="lot_status_changed")
    if lot.source_type == "purchased":
        supplier_performance(db, access)


def on_lot_status_changed(db: Session, access: AccessContext, lot: fm.FeedLot) -> None:
    """§12 ingredient / output lot change: re-score the mixes that used
    the lot (or came out of it) and re-evaluate the subjects that
    received those mixes — the lot's standing is a supplier incident and
    an exposure fact at once."""
    _safely(db, "lot_status_changed", lambda: _lot_status_changed(db, access, lot))


def on_receipt(db: Session, access: AccessContext) -> None:
    """§12 supplier receipt or cost update: the supplier rows and the cost
    picture are refreshed."""

    def _run() -> None:
        supplier_performance(db, access)
        check_cost_efficiency(db, access)

    _safely(db, "receipt", _run)


def monitor_dict(db: Session, m: FeedPerformanceMonitor) -> dict:
    subject = _subject(db, m.subject_type, m.subject_id)
    latest = db.scalar(
        select(FeedPerformanceAssessment)
        .where(FeedPerformanceAssessment.monitor_id == m.id)
        .order_by(FeedPerformanceAssessment.evaluated_at.desc())
    )
    return {
        "id": str(m.id),
        "farm_id": str(m.tenant_id),
        "subject_type": m.subject_type,
        "subject_id": str(m.subject_id),
        "subject_name": getattr(subject, "name", None),
        "species": getattr(subject, "species", None),
        "production_metric_code": m.production_metric_code,
        "baseline_method_code": m.baseline_method_code,
        "baseline_window_days": m.baseline_window_days,
        "evaluation_window_days": m.evaluation_window_days,
        "evaluation_frequency_code": m.evaluation_frequency_code,
        "minimum_exposure_days": m.minimum_exposure_days,
        "minimum_observations": m.minimum_observations,
        "alert_threshold_percent": m.alert_threshold_percent,
        "active": m.active,
        "configuration": m.configuration_json,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "latest_assessment": assessment_dict(db, latest) if latest else None,
    }


def assessment_dict(db: Session, a: FeedPerformanceAssessment) -> dict:
    monitor = db.get(FeedPerformanceMonitor, a.monitor_id)
    subject = _subject(db, monitor.subject_type, monitor.subject_id) if monitor else None
    return {
        "id": str(a.id),
        "monitor_id": str(a.monitor_id),
        "subject_type": monitor.subject_type if monitor else None,
        "subject_id": str(monitor.subject_id) if monitor else None,
        "subject_name": getattr(subject, "name", None),
        "production_metric_code": monitor.production_metric_code if monitor else None,
        "evaluated_at": a.evaluated_at,
        "period_start": a.period_start,
        "period_end": a.period_end,
        "baseline_value": a.baseline_value,
        "observed_value": a.observed_value,
        "variance_value": a.variance_value,
        "variance_percent": a.variance_percent,
        "feed_related_likelihood": a.feed_related_likelihood,
        "confidence_score": a.confidence_score,
        "model_reference": a.model_reference,
        "model_version": a.model_version,
        "evidence": a.evidence_json,
        "confounders": a.confounders_json,
        "explanation": a.explanation,
        "status": a.status,
        "trigger": a.trigger,
        "baseline_method_applied": (a.evidence_json or {}).get("baseline_method_applied"),
        "suggested_actions": (a.evidence_json or {}).get("suggested_actions", []),
    }


def alert_dict(db: Session, a: FeedPerformanceAlert, names: routing.NameCache | None = None) -> dict:
    batch = db.get(fm.FeedBatch, a.feed_batch_id) if a.feed_batch_id else None
    subject = _subject(db, a.subject_type, a.subject_id) if a.subject_type and a.subject_id else None
    return {
        "id": str(a.id),
        "farm_id": str(a.tenant_id),
        "assessment_id": _s(a.assessment_id),
        "feed_batch_id": _s(a.feed_batch_id),
        "mix_code": batch.mix_code if batch else None,
        "feed_product_id": _s(a.feed_product_id),
        "product_name": _product_name(db, a.feed_product_id) if a.feed_product_id else None,
        "subject_type": a.subject_type,
        "subject_id": _s(a.subject_id),
        "subject_name": getattr(subject, "name", None),
        "alert_type": a.alert_type,
        "severity": a.severity,
        "detected_at": a.detected_at,
        "last_seen_at": a.last_seen_at,
        "title": a.title,
        "explanation": a.explanation,
        "evidence": a.evidence_json,
        "confidence_score": a.confidence_score,
        "suggested_actions": (a.evidence_json or {}).get("suggested_actions", []),
        "status": a.status,
        "deduplication_key": a.deduplication_key,
        "acknowledged_by": _s(a.acknowledged_by),
        "acknowledged_by_name": names.get(a.acknowledged_by) if names and a.acknowledged_by else None,
        "acknowledged_at": a.acknowledged_at,
        "resolved_at": a.resolved_at,
        "resolved_by": _s(a.resolved_by),
        "resolved_by_name": names.get(a.resolved_by) if names and a.resolved_by else None,
        "resolution_note": a.resolution_note,
    }


def exposure_dict(db: Session, w: FeedExposureWindow) -> dict:
    subject = _subject(db, w.subject_type, w.subject_id)
    return {
        "id": str(w.id),
        "subject_type": w.subject_type,
        "subject_id": str(w.subject_id),
        "subject_name": getattr(subject, "name", None),
        "feed_product_id": str(w.feed_product_id),
        "lot_id": _s(w.lot_id),
        "feed_batch_id": _s(w.feed_batch_id),
        "exposure_start": w.exposure_start,
        "exposure_end": w.exposure_end,
        "offered_quantity": w.offered_quantity,
        "consumed_estimate": w.consumed_estimate,
        "feeding_event_count": w.feeding_event_count,
        "lineage": w.lineage_json,
        "generated_at": w.generated_at,
    }


def summary(db: Session) -> dict:
    alerts = db.scalars(
        select(FeedPerformanceAlert).where(
            FeedPerformanceAlert.status != "resolved", FeedPerformanceAlert.deleted_at.is_(None)
        )
    ).all()
    monitors = db.scalar(
        select(func.count(FeedPerformanceMonitor.id)).where(
            FeedPerformanceMonitor.active.is_(True), FeedPerformanceMonitor.deleted_at.is_(None)
        )
    )
    last = db.scalar(select(func.max(FeedPerformanceAssessment.evaluated_at)))
    by_type: dict[str, int] = {}
    for a in alerts:
        by_type[a.alert_type] = by_type.get(a.alert_type, 0) + 1
    return {
        "open_alerts": len(alerts),
        "alerts_by_type": by_type,
        "monitors": monitors or 0,
        "last_evaluated_at": last,
        "batches_scored": db.scalar(select(func.count(FeedBatchPerformanceScore.id))) or 0,
        "suppliers_scored": db.scalar(select(func.count(SupplierFeedPerformance.id))) or 0,
        "effective_feed_value": effective_feed_value(db),
        "baseline_methods": list(BASELINE_METHODS),
        "metrics": list(METRICS),
        "model_reference": MODEL_REFERENCE,
        "model_version": MODEL_VERSION,
    }


# Re-exported for the router's product lookups.
get_product = inv.get_product
