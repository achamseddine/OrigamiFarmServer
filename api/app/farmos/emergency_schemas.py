"""Request bodies for the emergency protocol and health case APIs
(OrigamiFarmOS database/CLINICAL-DECISION-SUPPORT-EMERGENCY-PROTOCOLS.md,
database/HEALTH-VETERINARY-SCHEMA.md). Responses are the services' dicts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TriggerRuleIn(BaseModel):
    observation_code: str
    operator: str = "present"
    threshold_numeric: float | None = None
    threshold_unit: str | None = None
    expected_value_code: str | None = None
    required: bool = False
    weight: float = 1.0
    danger_sign: bool = False
    label: str | None = None


class EligibilityRuleIn(BaseModel):
    rule_type: str
    operator: str = "=="
    rule_payload: dict = Field(default_factory=dict)
    failure_action: str = "WARN"
    message: str | None = None


class MedicationStepIn(BaseModel):
    medicine_product_id: str
    route_code: str
    dose_rule_type: str
    fixed_dose_quantity: float | None = None
    dose_per_weight_quantity: float | None = None
    dose_unit: str
    weight_unit: str | None = None
    minimum_dose_quantity: float | None = None
    maximum_dose_quantity: float | None = None
    repeat_interval_minutes: int | None = None
    maximum_administrations: int | None = None
    weight_max_age_days: int | None = None
    withdrawal_rule_payload: dict = Field(default_factory=dict)


class ProtocolStepIn(BaseModel):
    step_no: int
    step_type: str
    title: str
    instructions: str
    required: bool = True
    requires_confirmation: bool = True
    timing_offset_minutes: int | None = None
    medication: MedicationStepIn | None = None


class ProtocolVersionIn(BaseModel):
    protocol_text: str | None = None
    minimum_match_confidence: float = 0.6
    requires_manager_notification: bool = True
    requires_vet_notification: bool = True
    requires_pre_action_confirmation: bool = True
    offline_eligible: bool = False
    reassessment_minutes: int | None = None
    trigger_rules: list[TriggerRuleIn] = Field(default_factory=list)
    eligibility_rules: list[EligibilityRuleIn] = Field(default_factory=list)
    steps: list[ProtocolStepIn] = Field(default_factory=list)


class ProtocolIn(BaseModel):
    code: str
    title: str
    condition_family_code: str
    species_code: str | None = None
    subject_scope: str = "animal"
    version: ProtocolVersionIn


class ApproveVersionIn(BaseModel):
    approval_reference: str
    effective_from: datetime | None = None


class WithdrawVersionIn(BaseModel):
    reason: str


class SignIn(BaseModel):
    code: str
    value_numeric: float | None = None
    value_text: str | None = None
    unit: str | None = None
    present: bool = True


class AssessmentIn(BaseModel):
    """`POST /emergency/assessments` — the worker's signs and measurements.
    Recent observations on the subject are added as evidence."""

    subject_type: str
    subject_id: str
    signs: list[SignIn] = Field(default_factory=list)
    source: str = "worker"
    notes: str | None = None


class StartRunIn(BaseModel):
    match_id: str | None = None
    confirmed: bool = True


class PrepareStepIn(BaseModel):
    weight_kg: float | None = None
    head_count: int | None = None


class ConfirmStepIn(BaseModel):
    weight_kg: float | None = None
    head_count: int | None = None
    lot_id: str | None = None
    result: dict = Field(default_factory=dict)
    notes: str | None = None


class SkipStepIn(BaseModel):
    reason: str


class ReassessIn(BaseModel):
    signs: list[SignIn] = Field(default_factory=list)
    notes: str | None = None


class EscalateIn(BaseModel):
    reason: str


class ResolveRunIn(BaseModel):
    outcome: str


class OfflineShownIn(BaseModel):
    """What the tablet showed from the signed package (§14)."""

    protocol_version_id: str | None = None
    match_score: float | None = None
    triage_level: str | None = None
    escalated: bool = False
    escalation_reasons: list[str] = Field(default_factory=list)


class OfflineMedicationIn(BaseModel):
    dose_quantity: float | None = None
    dose_unit: str | None = None
    lot_id: str | None = None
    weight_used_kg: float | None = None
    head_count: int | None = None


class OfflineStepIn(BaseModel):
    protocol_step_id: str
    status: str = "completed"  # completed | skipped
    occurred_at: datetime | None = None
    result: dict = Field(default_factory=dict)
    medication: OfflineMedicationIn | None = None
    skip_reason: str | None = None
    notes: str | None = None


class OfflineRunIn(BaseModel):
    """`POST /emergency/offline-runs` — an emergency executed on the tablet
    from the offline package, submitted for revalidation. `operation_id`
    makes the sync idempotent."""

    operation_id: str
    device_id: str | None = None
    package_signature: str | None = None
    subject_type: str
    subject_id: str
    signs: list[SignIn] = Field(default_factory=list)
    occurred_at: datetime | None = None
    shown: OfflineShownIn = Field(default_factory=OfflineShownIn)
    steps: list[OfflineStepIn] = Field(default_factory=list)
    notes: str | None = None


# ------------------------------------------------------------ health cases
class HealthCaseIn(BaseModel):
    subject_type: str
    subject_id: str
    severity_code: str | None = None
    primary_problem_code: str | None = None
    responsible_user_id: str | None = None
    notes: str | None = None
    opened_at: datetime | None = None


class HealthCasePatch(BaseModel):
    status: str | None = None
    severity_code: str | None = None
    primary_problem_code: str | None = None
    responsible_user_id: str | None = None
    notes: str | None = None


class HealthCaseClose(BaseModel):
    outcome: str
    status: str = "resolved"


class DiagnosisIn(BaseModel):
    """`POST /health/cases/{id}/diagnoses` — a diagnostic role only."""

    diagnosis_code: str
    diagnosis_text: str | None = None
    certainty_code: str = "suspected"
    supersedes_diagnosis_id: str | None = None
    diagnosed_at: datetime | None = None
    notes: str | None = None


class TreatmentPlanIn(BaseModel):
    instructions: str | None = None
