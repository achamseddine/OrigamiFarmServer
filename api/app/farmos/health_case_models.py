"""Farm-data-plane (RLS-protected, tenant-scoped) tables for health cases
(OrigamiFarmOS database/HEALTH-VETERINARY-SCHEMA.md).

Observation is not diagnosis; diagnosis is not treatment; a treatment is
not an administration. These tables hold the clinical frame the existing
`observation`, `treatment` and `medication_administration` rows attach to:

* `health_case` — one episode for one animal or group, opened by a
  veterinarian / manager or by the emergency engine when it escalates or
  starts a protocol; closed with an outcome, never deleted.
* `diagnosis` — what the veterinarian concluded, with a certainty and a
  status; a revised diagnosis supersedes, never overwrites.
* `treatment_plan` — the prescription frame: who prescribed, when, with
  what instructions; the `treatment` rows are its actions.

The animal's standing conditions and dated weights were ported in stage 6
(app/farmos/livestock_models.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin

CASE_STATUSES = ("open", "monitoring", "resolved", "closed")
CASE_SEVERITIES = ("low", "moderate", "high", "critical")
CERTAINTIES = ("suspected", "presumptive", "confirmed", "ruled_out")
DIAGNOSIS_STATUSES = ("active", "resolved", "superseded")
PLAN_STATUSES = ("prescribed", "active", "completed", "cancelled")


class HealthCase(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "health_case"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_code", name="uq_health_case_code"),
        Index("ix_health_case_subject", "animal_id", "animal_group_id"),
    )

    animal_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    animal_group_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    case_code: Mapped[str] = mapped_column(String(100))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="open")
    severity_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    primary_problem_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # emergency_assessment | emergency_run | manual | observation
    source_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    diagnoses: Mapped[list["Diagnosis"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="Diagnosis.diagnosed_at"
    )
    plans: Mapped[list["TreatmentPlan"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="TreatmentPlan.prescribed_at"
    )


class Diagnosis(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "diagnosis"

    health_case_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("health_case.id"), index=True
    )
    diagnosis_code: Mapped[str] = mapped_column(String(120))
    diagnosis_text: Mapped[str | None] = mapped_column(String(250), nullable=True)
    certainty_code: Mapped[str] = mapped_column(String(30), default="suspected")
    diagnosed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    diagnosed_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    supersedes_diagnosis_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)

    case: Mapped[HealthCase] = relationship(back_populates="diagnoses")


class TreatmentPlan(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "treatment_plan"

    health_case_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("health_case.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="prescribed")
    prescribed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prescribed_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    instructions: Mapped[str | None] = mapped_column(nullable=True)
    # The protocol run that produced this plan, when the prescription came
    # from a veterinarian-approved emergency protocol.
    source_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    case: Mapped[HealthCase] = relationship(back_populates="plans")


HEALTH_CASE_TABLES = ("health_case", "diagnosis", "treatment_plan")
