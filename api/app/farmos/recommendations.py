"""Real rules evaluated against real stored data — CONSTITUTION.md: a
recommendation is never generated without persisted evidence. Each rule
inspects this farm's own current state and, if its condition holds,
upserts a Recommendation row (skipping a rule+entity pair that already has
an undecided "generated" row, so refreshing doesn't spam duplicates).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.farmos.finance_models import Expense, Recommendation
from app.farmos.production_models import Crop, CropPlanting
from app.tenant_api.models import Field

FEED_COST_SHARE_THRESHOLD = 0.35
HARVEST_DUE_WINDOW = timedelta(hours=48)


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
    db.add(
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
        )
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
        db.add(
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
            )
        )


def refresh_recommendations(db: Session, tenant_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    _evaluate_feed_cost_insight(db, tenant_id, now)
    _evaluate_harvest_due(db, tenant_id, now)
    db.flush()
