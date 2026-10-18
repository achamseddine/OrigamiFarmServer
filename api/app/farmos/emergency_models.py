"""Farm-data-plane (RLS-protected, tenant-scoped) tables for clinical
decision support and the emergency protocol engine (OrigamiFarmOS
database/CLINICAL-DECISION-SUPPORT-EMERGENCY-PROTOCOLS.md).

Origami recognises urgent patterns and operationalises veterinarian-
approved farm protocols at the point of care. It never invents a drug,
route, dose, contraindication or withdrawal: every medication instruction
lives inside an approved protocol version, approval provenance is
immutable, and when no current approved protocol applies — or the
evidence, eligibility or dose limits say stop — the engine escalates to
the veterinarian and manager rather than improvising.

Tables: the protocol and its versions (approval lives on the version),
trigger rules (signs and measurements), eligibility rules (animal facts),
steps with their medication parameters, and the runtime records — the
assessment with its candidate matches, the run and its steps, each
medication step bound to the administration it produced.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin

STEP_TYPES = ("ASSESS", "NON_DRUG_ACTION", "MEDICATION", "MEASUREMENT", "NOTIFY", "REASSESS", "ESCALATE")
TRIGGER_OPERATORS = (">=", ">", "<=", "<", "==", "!=", "present", "absent")
FAILURE_ACTIONS = ("BLOCK", "REQUIRE_VET", "WARN")
ELIGIBILITY_RULE_TYPES = (
    "species",
    "sex",
    "life_stage",
    "management_profile",
    "weight_kg",
    "weight_known",
    "pregnant",
    "lactating",
    "withdrawal_active",
    "status",
    "age_days",
    "recent_medication",
    "subject_type",
    # authoritative clinical facts (HEALTH-VETERINARY-SCHEMA): open diagnoses, recorded
    # allergies / adverse reactions / conditions, and how fresh the weight is
    "active_diagnosis",
    "allergy",
    "condition",
    "weight_age_days",
)
ELIGIBILITY_RESULTS = ("ELIGIBLE", "WARN", "REQUIRE_VET", "BLOCK")
TRIAGE_LEVELS = ("LOW", "MODERATE", "HIGH", "CRITICAL")
DOSE_RULE_TYPES = ("FIXED", "PER_WEIGHT")


# --------------------------------------------------------------- protocol
class EmergencyProtocol(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "emergency_protocol"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_emergency_protocol_code"),)

    code: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    condition_family_code: Mapped[str] = mapped_column(String(100))
    species_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # animal | group | any — what the protocol is written for.
    subject_scope: Mapped[str] = mapped_column(String(10), default="animal")
    # draft | active | withdrawn
    status: Mapped[str] = mapped_column(String(30), default="draft")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    versions: Mapped[list["EmergencyProtocolVersion"]] = relationship(
        back_populates="protocol", order_by="EmergencyProtocolVersion.version_no"
    )


class EmergencyProtocolVersion(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Operational only after veterinary approval (§3). Approval fields
    are written once and never edited; a change is a new version."""

    __tablename__ = "emergency_protocol_version"
    __table_args__ = (
        UniqueConstraint("emergency_protocol_id", "version_no", name="uq_emergency_protocol_version_no"),
    )

    emergency_protocol_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("emergency_protocol.id"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    protocol_text: Mapped[str | None] = mapped_column(nullable=True)
    minimum_match_confidence: Mapped[float] = mapped_column(Float, default=0.6)
    requires_manager_notification: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_vet_notification: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_pre_action_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    offline_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    reassessment_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # draft | approved | withdrawn
    status: Mapped[str] = mapped_column(String(30), default="draft")
    approved_by_veterinarian_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_reference: Mapped[str | None] = mapped_column(String(150), nullable=True)
    withdrawn_reason: Mapped[str | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    protocol: Mapped[EmergencyProtocol] = relationship(back_populates="versions")
    trigger_rules: Mapped[list["ProtocolTriggerRule"]] = relationship(
        back_populates="protocol_version", cascade="all, delete-orphan", lazy="selectin"
    )
    eligibility_rules: Mapped[list["ProtocolEligibilityRule"]] = relationship(
        back_populates="protocol_version", cascade="all, delete-orphan", lazy="selectin"
    )
    steps: Mapped[list["ProtocolStep"]] = relationship(
        back_populates="protocol_version",
        cascade="all, delete-orphan",
        order_by="ProtocolStep.step_no",
        lazy="selectin",
    )


class ProtocolTriggerRule(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """A structured sign or measurement the matcher scores (§4)."""

    __tablename__ = "protocol_trigger_rule"

    protocol_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("emergency_protocol_version.id"), index=True
    )
    observation_code: Mapped[str] = mapped_column(String(100))
    operator: Mapped[str] = mapped_column(String(20), default="present")
    threshold_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    expected_value_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    danger_sign: Mapped[bool] = mapped_column(Boolean, default=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    protocol_version: Mapped[EmergencyProtocolVersion] = relationship(back_populates="trigger_rules")


class ProtocolEligibilityRule(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """An authoritative-fact check (§5): species, sex, life stage, weight,
    pregnancy/lactation, withdrawal, status, age, recent medication,
    diagnoses, allergies, conditions."""

    __tablename__ = "protocol_eligibility_rule"

    protocol_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("emergency_protocol_version.id"), index=True
    )
    rule_type: Mapped[str] = mapped_column(String(60))
    operator: Mapped[str] = mapped_column(String(20), default="==")
    rule_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    # BLOCK | REQUIRE_VET | WARN
    failure_action: Mapped[str] = mapped_column(String(30), default="WARN")
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)

    protocol_version: Mapped[EmergencyProtocolVersion] = relationship(back_populates="eligibility_rules")


class ProtocolStep(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "protocol_step"
    __table_args__ = (UniqueConstraint("protocol_version_id", "step_no", name="uq_protocol_step_no"),)

    protocol_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("emergency_protocol_version.id"), index=True
    )
    step_no: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    instructions: Mapped[str] = mapped_column()
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    timing_offset_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    protocol_version: Mapped[EmergencyProtocolVersion] = relationship(back_populates="steps")
    medication: Mapped["ProtocolMedicationStep | None"] = relationship(
        back_populates="step", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )


class ProtocolMedicationStep(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """The only place a drug, route and dose rule may come from (§7). The
    engine calculates from these parameters and nothing else."""

    __tablename__ = "protocol_medication_step"
    __table_args__ = (UniqueConstraint("protocol_step_id", name="uq_protocol_medication_step"),)

    protocol_step_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("protocol_step.id"))
    # The medicine product (== its inventory item id).
    medicine_product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    route_code: Mapped[str] = mapped_column(String(40))
    # FIXED | PER_WEIGHT
    dose_rule_type: Mapped[str] = mapped_column(String(40))
    fixed_dose_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    dose_per_weight_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    dose_unit: Mapped[str] = mapped_column(String(30))
    weight_unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    minimum_dose_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum_dose_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    repeat_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    maximum_administrations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Protocol may require a weight no older than this for PER_WEIGHT.
    weight_max_age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    withdrawal_rule_payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    step: Mapped[ProtocolStep] = relationship(back_populates="medication")


# ---------------------------------------------------------------- runtime
class EmergencyAssessment(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """One triage of one subject (§8): the signs used, the animal facts
    used, the candidates, the triage level, the model reference and
    confidence, and why it escalated if it did. Kept whole for audit."""

    __tablename__ = "emergency_assessment"
    __table_args__ = (
        UniqueConstraint("tenant_id", "offline_operation_id", name="uq_emergency_assessment_offline_op"),
        Index("ix_emergency_assessment_status", "status"),
        Index("ix_emergency_assessment_subject", "animal_id", "animal_group_id"),
    )

    animal_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    animal_group_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    health_case_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # worker | observation_hook | reassessment | api | offline
    source: Mapped[str] = mapped_column(String(30), default="worker")
    # Offline execution (§14): the tablet's operation id makes the sync
    # idempotent; the device and the package signature it ran against are
    # kept so a stale package is visible on audit.
    offline_operation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    package_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    offline_discrepancies: Mapped[list] = mapped_column(JSONB, default=list)
    observed_signs: Mapped[list] = mapped_column(JSONB, default=list)
    animal_facts: Mapped[dict] = mapped_column(JSONB, default=dict)
    triage_level: Mapped[str] = mapped_column(String(30), default="LOW")
    ai_model_reference: Mapped[str | None] = mapped_column(String(150), nullable=True)
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # open | protocol_started | escalated | resolved | closed
    status: Mapped[str] = mapped_column(String(30), default="open")
    escalation_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    explanation: Mapped[str] = mapped_column(default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    matches: Mapped[list["ProtocolMatch"]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="ProtocolMatch.match_score.desc()",
        lazy="selectin",
    )


class ProtocolMatch(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "protocol_match"

    emergency_assessment_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("emergency_assessment.id"), index=True
    )
    protocol_version_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_explanation: Mapped[dict] = mapped_column(JSONB, default=dict)
    # ELIGIBLE | WARN | REQUIRE_VET | BLOCK
    eligibility_result: Mapped[str] = mapped_column(String(30), default="ELIGIBLE")
    blocking_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    assessment: Mapped[EmergencyAssessment] = relationship(back_populates="matches")


class EmergencyProtocolRun(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "emergency_protocol_run"
    __table_args__ = (
        UniqueConstraint("tenant_id", "offline_operation_id", name="uq_emergency_run_offline_op"),
        Index("ix_emergency_protocol_run_status", "status"),
    )

    emergency_assessment_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    protocol_version_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    animal_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    animal_group_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    health_case_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # in_progress | awaiting_reassessment | escalated | completed | cancelled
    status: Mapped[str] = mapped_column(String(30), default="in_progress")
    # Offline execution (§14): recorded on the tablet from the signed
    # package, revalidated at sync. What was shown and administered is
    # kept as recorded; any difference the server finds is listed.
    offline: Mapped[bool] = mapped_column(Boolean, default=False)
    offline_operation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    package_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    offline_discrepancies: Mapped[list] = mapped_column(JSONB, default=list)
    manager_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    veterinarian_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_reassessment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    steps: Mapped[list["ProtocolRunStep"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ProtocolRunStep.sequence",
        lazy="selectin",
    )


class ProtocolRunStep(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "protocol_run_step"

    protocol_run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("emergency_protocol_run.id"), index=True
    )
    protocol_step_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("protocol_step.id"))
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    # pending | presented | completed | skipped | blocked
    status: Mapped[str] = mapped_column(String(30), default="pending")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    presented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    medication_administration_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    result_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    skip_reason: Mapped[str | None] = mapped_column(nullable=True)

    run: Mapped[EmergencyProtocolRun] = relationship(back_populates="steps")
    step: Mapped[ProtocolStep] = relationship(lazy="selectin")


EMERGENCY_TABLES = (
    "emergency_protocol",
    "emergency_protocol_version",
    "protocol_trigger_rule",
    "protocol_eligibility_rule",
    "protocol_step",
    "protocol_medication_step",
    "emergency_assessment",
    "protocol_match",
    "emergency_protocol_run",
    "protocol_run_step",
)
