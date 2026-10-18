"""Feed Performance Intelligence (OrigamiFarmOS
database/FEED-PERFORMANCE-INTELLIGENCE.md): an analytical layer over the
canonical feed facts. It reads lots, batches, feeding events, milk, egg,
weight and health records and writes only its own projections — monitors,
exposure windows, assessments, scores and alerts. Nothing here is a
second feed, stock, production or health truth; every row is rebuildable
from them (CLAUDE.md: no shadow feed / production truth).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin

METRICS = ("milk_l_per_day", "eggs_per_day", "weight_gain_kg_per_day")
METRIC_UNITS = {
    "milk_l_per_day": "L/head/day",
    "eggs_per_day": "eggs/day",
    "weight_gain_kg_per_day": "kg/head/day",
}
BASELINE_METHODS = (
    "rolling_subject",
    "previous_period",
    "lactation_adjusted",
    "peer_group",
    "production_curve",
)
LIKELIHOODS = ("LOW", "MODERATE", "HIGH", "INSUFFICIENT_EVIDENCE")
ASSESSMENT_STATUSES = ("normal", "anomaly", "insufficient")
ALERT_TYPES = (
    "PRODUCTION_DECLINE_AFTER_FEED_CHANGE",
    "FEED_BATCH_UNDERPERFORMANCE",
    "FORMULA_COMPLIANCE_DEVIATION",
    "INGREDIENT_LOT_PERFORMANCE_ANOMALY",
    "SUPPLIER_LOT_PERFORMANCE_ANOMALY",
    "INTAKE_OR_REFUSAL_ANOMALY",
    "FEED_COST_PER_OUTPUT_INCREASE",
    "FEED_PERFORMANCE_IMPROVEMENT",
)
ALERT_STATUSES = ("open", "acknowledged", "resolved")


class FeedPerformanceMonitor(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """What the farm continuously evaluates: one subject, one production
    metric, one explicit baseline method and the thresholds that decide
    when a change is worth a person's attention (§3)."""

    __tablename__ = "feed_performance_monitor"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "subject_type", "subject_id", "production_metric_code", name="uq_feed_perf_monitor"
        ),
    )

    subject_type: Mapped[str] = mapped_column(String(10))  # animal | group
    subject_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    production_metric_code: Mapped[str] = mapped_column(String(60), default="milk_l_per_day")
    baseline_method_code: Mapped[str] = mapped_column(String(60), default="rolling_subject")
    baseline_window_days: Mapped[int] = mapped_column(Integer, default=14)
    evaluation_window_days: Mapped[int] = mapped_column(Integer, default=3)
    evaluation_frequency_code: Mapped[str] = mapped_column(String(30), default="daily")
    minimum_exposure_days: Mapped[float] = mapped_column(Float, default=2.0)
    minimum_observations: Mapped[int] = mapped_column(Integer, default=3)
    alert_threshold_percent: Mapped[float] = mapped_column(Float, default=5.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    configuration_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class FeedExposureWindow(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """A reproducible projection of feeding events (§4): one row per
    subject × product × lot, from the first issue to the last, with the
    lineage snapshot (mix, supplier, ingredient lots) frozen at the time
    it was built. Rebuilt whole; never edited."""

    __tablename__ = "feed_exposure_window"
    __table_args__ = (
        Index("ix_feed_exposure_subject", "tenant_id", "subject_type", "subject_id"),
        Index("ix_feed_exposure_batch", "feed_batch_id"),
    )

    subject_type: Mapped[str] = mapped_column(String(10))
    subject_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    feed_product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    lot_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    feed_batch_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    exposure_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exposure_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    offered_quantity: Mapped[float] = mapped_column(Float, default=0)
    consumed_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    feeding_event_count: Mapped[int] = mapped_column(Integer, default=0)
    lineage_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FeedPerformanceAssessment(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """One evaluation of one monitor (§5): the explicit baseline, what was
    observed, the variance, the feed-related likelihood, the confidence,
    and the evidence and confounders it rests on. Versioned by the model
    reference; never overwritten by a later evaluation."""

    __tablename__ = "feed_performance_assessment"
    __table_args__ = (Index("ix_feed_perf_assessment_monitor", "monitor_id", "evaluated_at"),)

    monitor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    baseline_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    variance_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    variance_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    # LOW | MODERATE | HIGH | INSUFFICIENT_EVIDENCE — an association, never a diagnosis
    feed_related_likelihood: Mapped[str] = mapped_column(String(30), default="INSUFFICIENT_EVIDENCE")
    confidence_score: Mapped[float] = mapped_column(Float, default=0)
    model_reference: Mapped[str] = mapped_column(String(150))
    model_version: Mapped[str] = mapped_column(String(100))
    evidence_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    confounders_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    explanation: Mapped[str] = mapped_column(Text)
    # normal | anomaly | insufficient
    status: Mapped[str] = mapped_column(String(30), default="normal")
    trigger: Mapped[str] = mapped_column(String(40), default="cycle")
    evaluated_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class FeedBatchPerformanceScore(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """A rebuildable score card per numbered mix (§7) for the manager's
    dashboard. Explanatory only: it never changes a lot's status."""

    __tablename__ = "feed_batch_performance_score"
    __table_args__ = (UniqueConstraint("tenant_id", "feed_batch_id", name="uq_feed_batch_perf_score"),)

    feed_batch_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exposed_head_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exposure_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    formula_compliance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    intake_response_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    production_response_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    health_signal_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    consistency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    economic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, default=0)
    evidence_json: Mapped[dict] = mapped_column(JSONB, default=dict)


class SupplierFeedPerformance(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """Supplier × ingredient, from that supplier's actual lots and what
    happened downstream of them (§9). Ingredient-specific on purpose: a
    supplier's barley and its premix are never one number. Suppliers are
    the ids and labels the lots carry — there is no supplier master yet."""

    __tablename__ = "supplier_feed_performance"
    __table_args__ = (Index("ix_supplier_feed_perf_tenant", "tenant_id", "supplier_label"),)

    supplier_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    supplier_label: Mapped[str] = mapped_column(String(200))
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    feed_product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    evaluation_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evaluation_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lot_count: Mapped[int] = mapped_column(Integer, default=0)
    feed_batch_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purchase_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_consistency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    downstream_performance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    incident_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0)
    methodology_code: Mapped[str] = mapped_column(String(60))
    methodology_version: Mapped[str] = mapped_column(String(40))
    evidence_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FeedPerformanceAlert(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A persistent, deduplicated finding (§11). It stays open while the
    condition holds, is acknowledged by a person, and resolves itself when
    the condition clears — acknowledging never resolves it."""

    __tablename__ = "feed_performance_alert"
    __table_args__ = (
        Index("ix_feed_perf_alert_dedup", "tenant_id", "deduplication_key"),
        Index("ix_feed_perf_alert_status", "tenant_id", "status"),
    )

    assessment_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    feed_batch_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    feed_product_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    subject_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    alert_type: Mapped[str] = mapped_column(String(60))
    # critical | high | medium | low | info — the bell's priorities
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(String(200))
    explanation: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # open | acknowledged | resolved
    status: Mapped[str] = mapped_column(String(30), default="open")
    deduplication_key: Mapped[str] = mapped_column(String(255))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set only when a person closed it with a reason; an automatic resolution
    # (the condition cleared) leaves it null.
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
