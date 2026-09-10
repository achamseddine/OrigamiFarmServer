"""Real rules evaluated against real stored data — CONSTITUTION.md: a
recommendation is never generated without persisted evidence. Each rule
inspects this farm's own current state and, if its condition holds,
upserts a Recommendation row (skipping a rule+entity pair that already has
an undecided "generated" row, so refreshing doesn't spam duplicates) and a
paired Notification row, so the same real, evidence-backed alert also
shows up in the notification inbox and GET /priorities — nothing else in
this codebase ever creates a Notification, so without this the inbox
stayed empty no matter what happened on the farm.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.farmos.farm_models import Notification
from app.farmos.finance_models import Expense, Recommendation
from app.farmos.production_models import Crop, CropPlanting, EggRecord
from app.tenant_api.models import Field, InventoryItem

FEED_COST_SHARE_THRESHOLD = 0.35
HARVEST_DUE_WINDOW = timedelta(hours=48)
EGG_DROP_THRESHOLD = 0.20

# Recommendation.category -> the permission-grid module code its paired
# notification belongs under (see app/farmos/permissions.py MODULE_CODES).
_CATEGORY_MODULE_CODE = {
    "finance": "finance",
    "harvest": "produce_harvest",
    "feed": "feed_nutrition",
    "egg": "egg_production",
    "health": "animal_health",
}


def _has_undecided(db: Session, rule_id: str, entity_id: str | None) -> bool:
    stmt = select(Recommendation).where(
        Recommendation.deleted_at.is_(None),
        Recommendation.rule_id == rule_id,
        Recommendation.status == "generated",
    )
    stmt = stmt.where(Recommendation.entity_id == entity_id) if entity_id else stmt.where(
        Recommendation.entity_id.is_(None)
    )
    return db.execute(stmt).first() is not None


def _add_recommendation(db: Session, tenant_id: uuid.UUID, recommendation: Recommendation) -> None:
    """The only place a Recommendation gets created — every rule below
    calls this so every generated recommendation also raises a
    notification, with no rule able to forget the second half.
    """
    db.add(recommendation)
    db.flush()  # assigns recommendation.id so the notification can link back to it
    db.add(
        Notification(
            tenant_id=tenant_id,
            module_code=_CATEGORY_MODULE_CODE.get(recommendation.category, recommendation.category),
            notification_type=recommendation.rule_id or recommendation.category,
            title=recommendation.title,
            description=recommendation.rationale,
            priority=recommendation.priority if recommendation.priority != "info" else "low",
            entity_type=recommendation.entity_type,
            entity_id=recommendation.entity_id,
            source_type="recommendation",
            source_id=str(recommendation.id),
        )
    )


def _evaluate_feed_cost_insight(db: Session, tenant_id: uuid.UUID, now: datetime) -> None:
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.execute(
        select(Expense.category, func.sum(Expense.amount))
        .where(Expense.deleted_at.is_(None), Expense.incurred_at >= today_start)
        .group_by(Expense.category)
    ).all()
    if not rows:
        return
    total = sum(float(amount) for _category, amount in rows)
    if total <= 0:
        return
    feed_total = sum(float(amount) for category, amount in rows if category == "feed")
    share = feed_total / total
    if share <= FEED_COST_SHARE_THRESHOLD:
        return
    if _has_undecided(db, "RULE-FEED-COST-INSIGHT", None):
        return
    _add_recommendation(
        db,
        tenant_id,
        Recommendation(
            tenant_id=tenant_id,
            category="finance",
            priority="info",
            title="Feed cost share is high",
            entity_type=None,
            entity_id=None,
            entity_label="Feed expenses",
            confidence=0.8,
            rationale=(
                f"Feed represents {share:.1%} of total expenses today, above the "
                f"{FEED_COST_SHARE_THRESHOLD:.0%} attention threshold used for supplier/usage review."
            ),
            suggested_action="Review feed usage per group and compare current supplier pricing.",
            status="generated",
            rule_id="RULE-FEED-COST-INSIGHT",
            generated_at=now,
            evidence=[{"label": "Feed share of expenses", "value": f"{share:.1%}"}],
        ),
    )


def _evaluate_harvest_due(db: Session, tenant_id: uuid.UUID, now: datetime) -> None:
    due_by = now + HARVEST_DUE_WINDOW
    plantings = db.execute(
        select(CropPlanting).where(
            CropPlanting.deleted_at.is_(None),
            CropPlanting.status == "active",
            CropPlanting.stage != "harvested",
            CropPlanting.expected_harvest_date.is_not(None),
            CropPlanting.expected_harvest_date <= due_by,
        )
    ).scalars().all()
    for planting in plantings:
        entity_id = str(planting.field_id)
        if _has_undecided(db, "RULE-HARVEST-DUE", entity_id):
            continue
        field = db.get(Field, planting.field_id)
        crop = db.get(Crop, planting.crop_id)
        field_name = field.name if field else "Unknown field"
        crop_name = crop.name if crop else "crop"
        entity_label = f"{field_name} — {crop_name}"
        days = max((planting.expected_harvest_date - now).days, 0)
        yield_note = (
            f" (~{float(planting.expected_yield_kg):.0f} kg expected)"
            if planting.expected_yield_kg is not None
            else ""
        )
        _add_recommendation(
            db,
            tenant_id,
            Recommendation(
                tenant_id=tenant_id,
                category="harvest",
                priority="low",
                title=f"{crop_name} ready in {days} day(s)",
                entity_type="field",
                entity_id=entity_id,
                entity_label=entity_label,
                confidence=0.9,
                rationale=(
                    f"{entity_label} is expected to be ready for harvest within the next "
                    f"48 hours{yield_note}."
                ),
                suggested_action=f"Schedule the harvest crew for {entity_label}.",
                status="generated",
                rule_id="RULE-HARVEST-DUE",
                generated_at=now,
                evidence=[{"label": "Expected harvest", "value": "Within 48 hours"}],
            ),
        )


def _evaluate_low_feed_stock(db: Session, tenant_id: uuid.UUID, now: datetime) -> None:
    """Any active inventory item at or below its own reorder_level — the
    same "should not go negative without explicit override" stock model
    routes_feed.py already enforces, just read the other way: not empty
    yet, but low enough that reordering now avoids running out.
    """
    items = db.execute(
        select(InventoryItem).where(
            InventoryItem.deleted_at.is_(None),
            InventoryItem.reorder_level > 0,
            InventoryItem.current_qty <= InventoryItem.reorder_level,
        )
    ).scalars().all()
    for item in items:
        entity_id = str(item.id)
        if _has_undecided(db, "RULE-LOW-FEED", entity_id):
            continue
        current_qty = float(item.current_qty)
        reorder_level = float(item.reorder_level)
        _add_recommendation(
            db,
            tenant_id,
            Recommendation(
                tenant_id=tenant_id,
                category="feed",
                priority="high" if current_qty <= 0 else "medium",
                title=f"Low feed: {item.name}",
                entity_type="inventory_item",
                entity_id=entity_id,
                entity_label=item.name,
                confidence=0.95,
                rationale=(
                    f"{item.name} stock ({current_qty:g} {item.unit}) has fallen to or below its "
                    f"reorder level of {reorder_level:g} {item.unit}."
                ),
                suggested_action=f"Place a reorder for {item.name} soon.",
                status="generated",
                rule_id="RULE-LOW-FEED",
                generated_at=now,
                evidence=[
                    {"label": "Current stock", "value": f"{current_qty:g} {item.unit}"},
                    {"label": "Reorder level", "value": f"{reorder_level:g} {item.unit}"},
                ],
            ),
        )


def _evaluate_egg_production_drop(db: Session, tenant_id: uuid.UUID, now: datetime) -> None:
    """Per flock_id, this week's sellable-egg total vs the preceding week's
    — a drop past EGG_DROP_THRESHOLD is worth a look even with no other
    signal, the same way the tablet app's demo egg-drop scenario works.
    """
    this_week_start = now - timedelta(days=7)
    last_week_start = now - timedelta(days=14)
    rows = db.execute(
        select(EggRecord.flock_id, EggRecord.sellable_eggs, EggRecord.recorded_at).where(
            EggRecord.deleted_at.is_(None), EggRecord.recorded_at >= last_week_start
        )
    ).all()
    totals: dict[str, dict[str, int]] = {}
    for flock_id, sellable_eggs, recorded_at in rows:
        bucket = totals.setdefault(flock_id, {"this_week": 0, "last_week": 0})
        if recorded_at >= this_week_start:
            bucket["this_week"] += sellable_eggs
        else:
            bucket["last_week"] += sellable_eggs

    for flock_id, bucket in totals.items():
        last_week_total = bucket["last_week"]
        this_week_total = bucket["this_week"]
        if last_week_total <= 0:
            continue
        drop = (last_week_total - this_week_total) / last_week_total
        if drop <= EGG_DROP_THRESHOLD:
            continue
        if _has_undecided(db, "RULE-EGG-DROP", flock_id):
            continue
        _add_recommendation(
            db,
            tenant_id,
            Recommendation(
                tenant_id=tenant_id,
                category="egg",
                priority="medium",
                title=f"Egg production down {drop:.0%}",
                entity_type="flock",
                entity_id=flock_id,
                entity_label=flock_id,
                confidence=0.74,
                rationale=(
                    f"Egg output for {flock_id} is down {drop:.0%} versus last week "
                    f"({this_week_total} vs {last_week_total}), which exceeds the "
                    f"{EGG_DROP_THRESHOLD:.0%} investigation threshold."
                ),
                suggested_action=f"Investigate feed intake and conditions for {flock_id} today.",
                status="generated",
                rule_id="RULE-EGG-DROP",
                generated_at=now,
                evidence=[
                    {
                        "label": "Production",
                        "value": f"{this_week_total} vs {last_week_total} last week",
                    },
                    {"label": "Threshold", "value": f">{EGG_DROP_THRESHOLD:.0%} drop triggers review"},
                ],
            ),
        )


def refresh_recommendations(db: Session, tenant_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    _evaluate_feed_cost_insight(db, tenant_id, now)
    _evaluate_harvest_due(db, tenant_id, now)
    _evaluate_low_feed_stock(db, tenant_id, now)
    _evaluate_egg_production_drop(db, tenant_id, now)
    db.flush()
