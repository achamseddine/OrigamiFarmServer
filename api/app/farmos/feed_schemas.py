"""Pydantic shapes for the generic feed architecture — the Stage 7 part of
the FarmOS tablet contract (docs/FARMOS_API.md). Field names match the
reference backend's OpenAPI document exactly; kept beside
app/farmos/schemas.py because the feed domain alone is as large as the
rest of the contract.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------- products
class FeedProductCreate(BaseModel):
    code: str | None = None
    name: str
    name_ar: str | None = None
    unit: str = "kg"
    source_type: str = "purchased"  # purchased | farm_produced
    is_ingredient: bool = True
    is_feedable: bool = False
    category: str | None = None
    unit_cost: float | None = None
    supplier_id: str | None = None
    supplier_label: str | None = None
    reorder_level: float = 0
    opening_quantity: float = 0
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be empty")
        return v.strip()


class FeedProductUpdate(BaseModel):
    name: str | None = None
    name_ar: str | None = None
    category: str | None = None
    is_ingredient: bool | None = None
    is_feedable: bool | None = None
    default_unit_cost: float | None = None
    status: str | None = None
    notes: str | None = None


class FeedLotOut(BaseModel):
    id: str
    feed_product_id: str
    lot_code: str
    source_type: str
    supplier_id: str | None = None
    supplier_label: str | None = None
    feed_batch_id: str | None = None
    received_at: datetime
    expiry_date: datetime | None = None
    ordered_quantity: float | None = None
    received_quantity: float
    accepted_quantity: float
    rejected_quantity: float
    quantity_on_hand: float
    unit: str
    unit_cost: float | None = None
    status: str
    reference: str | None = None
    notes: str | None = None


class FeedAvailabilityOut(BaseModel):
    feed_product_id: str
    unit: str
    on_hand: float
    unusable: float
    unusable_by_status: dict[str, float] = {}
    reserved: float
    available: float
    lots: list[dict] = []


class FeedProductOut(BaseModel):
    id: str
    farm_id: str
    code: str
    name: str
    name_ar: str | None = None
    source_type: str
    is_ingredient: bool
    is_feedable: bool
    category: str | None = None
    unit: str
    inventory_item_id: str
    default_unit_cost: float | None = None
    status: str
    notes: str | None = None
    availability: FeedAvailabilityOut
    policy_names: list[str] = []
    has_active_formula: bool = False


class LotReceive(BaseModel):
    feed_product_id: str
    quantity: float = Field(gt=0)
    unit: str | None = None
    unit_cost: float | None = None
    supplier_id: str | None = None
    supplier_label: str | None = None
    lot_code: str | None = None
    expiry_date: datetime | None = None
    ordered_quantity: float | None = None
    rejected_quantity: float = Field(0, ge=0)
    reference: str | None = None
    notes: str | None = None
    received_at: datetime | None = None


class LotStatusUpdate(BaseModel):
    status: str
    reason: str | None = None


class AdjustmentCreate(BaseModel):
    feed_product_id: str
    lot_id: str | None = None
    delta: float
    reason: str = "adjustment"  # adjustment | waste | return | count_correction
    explanation: str


# ----------------------------------------------------------------- policy
class PolicyRuleIn(BaseModel):
    effect: str  # allow | block | limit
    species_code: str | None = None
    management_profile: str | None = None
    life_stage: str | None = None
    reproductive_state: str | None = None
    max_inclusion_pct: float | None = None
    min_age_days: int | None = None
    max_age_days: int | None = None
    reason: str | None = None


class PolicySet(BaseModel):
    name: str
    rules: list[PolicyRuleIn]
    requires_approved_formula: bool = False
    cross_species_transfer_allowed: bool = True
    lot_id: str | None = None
    notes: str | None = None


class PolicyOut(BaseModel):
    id: str
    feed_product_id: str | None
    lot_id: str | None
    name: str
    requires_approved_formula: bool
    cross_species_transfer_allowed: bool
    status: str
    effective_from: datetime | None
    effective_to: datetime | None
    notes: str | None = None
    rules: list[PolicyRuleIn]


class PolicyValidate(BaseModel):
    """Either name the subject, or describe the use."""

    subject_type: str | None = None
    subject_id: str | None = None
    species_code: str | None = None
    management_profile: str | None = None
    life_stage: str | None = None
    reproductive_state: str | None = None
    age_days: int | None = None
    inclusion_pct: float | None = None
    lot_id: str | None = None
    through_formula: bool = False


class PolicyDecisionOut(BaseModel):
    allowed: bool
    reasons: list[str]
    max_inclusion_pct: float | None = None
    requires_approved_formula: bool
    cross_species_transfer_allowed: bool
    policies: list[str]


# --------------------------------------------------------------- formulas
class FormulaComponentIn(BaseModel):
    feed_product_id: str
    target_quantity: float = Field(gt=0)
    unit: str | None = None
    target_percentage: float | None = None


class FormulaCreate(BaseModel):
    code: str | None = None
    name: str
    feed_product_id: str
    species_code: str | None = None
    description: str | None = None
    batch_size: float = Field(gt=0)
    unit: str = "kg"
    components: list[FormulaComponentIn]
    notes: str | None = None
    activate: bool = True


class FormulaVersionCreate(BaseModel):
    batch_size: float = Field(gt=0)
    unit: str | None = None
    components: list[FormulaComponentIn]
    notes: str | None = None
    activate: bool = True


class FormulaComponentOut(BaseModel):
    id: str
    feed_product_id: str
    product_name: str | None = None
    target_quantity: float
    unit: str
    target_percentage: float | None = None
    sort_order: int


class FormulaVersionOut(BaseModel):
    id: str
    formula_id: str
    version: int
    status: str
    batch_size: float
    unit: str
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    locked: bool
    notes: str | None = None
    created_at: datetime
    components: list[FormulaComponentOut]
    planned_cost: float | None = None


class FormulaOut(BaseModel):
    id: str
    farm_id: str
    code: str
    name: str
    feed_product_id: str
    product_name: str | None = None
    species_code: str | None = None
    status: str
    description: str | None = None
    active_version: int | None = None
    versions: list[FormulaVersionOut]


# ---------------------------------------------------------------- batches
class BatchStart(BaseModel):
    formula_id: str | None = None
    formula_version_id: str | None = None
    batch_code: str | None = None
    target_quantity: float = Field(gt=0)
    unit: str | None = None
    notes: str | None = None
    # §20: intended use is metadata validated by the usage policy, never a
    # separate table or numbering sequence.
    intended_species_code: str | None = None
    intended_management_profile: str | None = None
    # A farm asset (GET /assets), not free text; the operator defaults to
    # the account opening the batch.
    mixer_asset_id: str | None = None
    operator_id: str | None = None
    production_date: datetime | None = None


class MixSettingsOut(BaseModel):
    farm_id: str
    code_format: str
    last_number: int
    next_number: int
    next_code: str


class MixSettingsUpdate(BaseModel):
    code_format: str


class BatchActualIn(BaseModel):
    feed_product_id: str
    actual_quantity: float = Field(ge=0)
    unit: str | None = None
    lot_id: str | None = None


class BatchComplete(BaseModel):
    actuals: list[BatchActualIn]
    actual_quantity: float = Field(gt=0)
    produced_at: datetime | None = None
    lot_code: str | None = None
    expiry_date: datetime | None = None
    notes: str | None = None
    allow_negative: bool = False
    operator_id: str | None = None
    mixer_asset_id: str | None = None


class BatchQuarantine(BaseModel):
    reason: str


class BatchComponentOut(BaseModel):
    id: str
    feed_product_id: str
    product_name: str | None = None
    lot_id: str | None = None
    lot_code: str | None = None
    target_quantity: float | None = None
    actual_quantity: float | None = None
    unit: str
    unit_cost: float | None = None
    cost: float | None = None


class BatchOut(BaseModel):
    id: str
    farm_id: str
    feed_product_id: str
    product_name: str | None = None
    formula_version_id: str | None = None
    formula_code: str | None = None
    formula_version: int | None = None
    batch_code: str
    mix_number: int
    mix_code: str
    intended_species_code: str | None = None
    intended_management_profile: str | None = None
    production_date: datetime
    use_by_date: datetime | None = None
    operator_id: str | None = None
    operator_name: str | None = None
    mixer_asset_id: str | None = None
    mixer_asset_name: str | None = None
    status: str
    target_quantity: float | None = None
    actual_quantity: float | None = None
    unit: str
    planned_cost: float | None = None
    actual_cost: float | None = None
    unit_cost: float | None = None
    output_lot_id: str | None = None
    output_lot_code: str | None = None
    started_at: datetime
    produced_at: datetime | None = None
    mixed_by: str | None = None
    notes: str | None = None
    components: list[BatchComponentOut]
    variance: list[dict] = []


# -------------------------------------------------------------- nutrients
class NutrientOut(BaseModel):
    code: str
    name_en: str
    name_ar: str
    unit: str
    category: str
    sort_order: int


class NutrientValueIn(BaseModel):
    nutrient_code: str
    value: float
    unit: str | None = None


class NutrientProfileCreate(BaseModel):
    subject_type: str  # feed_product | formula_version | feed_lot | feed_batch
    subject_id: str
    basis: str = "as_fed"
    source_type: str = "declared"  # declared | calculated | lab
    reference: str | None = None
    effective_at: datetime | None = None
    values: list[NutrientValueIn]


class NutrientProfileOut(BaseModel):
    id: str
    subject_type: str
    subject_id: str
    basis: str
    source_type: str
    reference: str | None = None
    effective_at: datetime
    values: list[dict]


# --------------------------------------------------------------- programs
class ProgramComponentIn(BaseModel):
    feed_product_id: str
    quantity_per_head: float = Field(gt=0)
    unit: str | None = None
    frequency: str = "per_day"  # per_day | per_feeding
    timing: str | None = None
    notes: str | None = None


class ProgramRuleIn(BaseModel):
    species_code: str | None = None
    sex: str | None = None
    life_stage: str | None = None
    management_profile: str | None = None
    reproductive_state: str | None = None  # pregnant | open
    lactation_state: str | None = None  # lactating | dry
    production_metric: str | None = None  # milk_l_per_day | eggs_per_day
    production_min: float | None = None
    production_max: float | None = None
    weight_min: float | None = None
    weight_max: float | None = None
    age_min_days: int | None = None
    age_max_days: int | None = None
    priority: int = 0
    notes: str | None = None


class ProgramCreate(BaseModel):
    code: str | None = None
    name: str
    name_ar: str | None = None
    category: str | None = None
    description: str | None = None
    feedings_per_day: int = Field(2, ge=1, le=12)
    components: list[ProgramComponentIn]
    rules: list[ProgramRuleIn] = []
    notes: str | None = None
    activate: bool = True


class ProgramVersionCreate(BaseModel):
    feedings_per_day: int | None = Field(None, ge=1, le=12)
    components: list[ProgramComponentIn]
    rules: list[ProgramRuleIn] = []
    notes: str | None = None
    activate: bool = True


class ProgramComponentOut(BaseModel):
    id: str
    feed_product_id: str
    product_name: str | None = None
    quantity_per_head: float
    unit: str
    frequency: str
    timing: str | None = None
    notes: str | None = None
    daily_per_head: float


class ProgramRuleOut(ProgramRuleIn):
    id: str


class ProgramVersionOut(BaseModel):
    id: str
    program_id: str
    version: int
    status: str
    feedings_per_day: int
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    locked: bool
    notes: str | None = None
    components: list[ProgramComponentOut]
    rules: list[ProgramRuleOut]


class ProgramOut(BaseModel):
    id: str
    farm_id: str
    code: str
    name: str
    name_ar: str | None = None
    category: str | None = None
    status: str
    description: str | None = None
    active_version: int | None = None
    versions: list[ProgramVersionOut]
    subjects_assigned: int = 0


class ProgramResolveRequest(BaseModel):
    """Either a real subject, or a described one."""

    subject_type: str | None = None
    subject_id: str | None = None
    species_code: str | None = None
    sex: str | None = None
    life_stage: str | None = None
    management_profile: str | None = None
    reproductive_state: str | None = None
    lactation_state: str | None = None
    production: dict[str, float] = {}
    weight_kg: float | None = None
    age_days: int | None = None
    head_count: int = 1


# ------------------------------------------------------------ assignments
class AssignmentCreate(BaseModel):
    assignment_type: str = "explicit"  # explicit | supplement | override | restriction
    program_id: str | None = None
    program_version_id: str | None = None
    feed_product_id: str | None = None
    quantity_per_head: float | None = None
    unit: str | None = None
    reason: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class AssignmentOut(BaseModel):
    id: str
    farm_id: str
    subject_type: str
    subject_id: str
    program_version_id: str | None = None
    program_id: str | None = None
    program_name: str | None = None
    program_version: int | None = None
    assignment_type: str
    feed_product_id: str | None = None
    product_name: str | None = None
    quantity_per_head: float | None = None
    unit: str | None = None
    reason: str | None = None
    valid_from: datetime
    valid_to: datetime | None = None
    status: str
    created_by: str | None = None
    created_at: datetime
    ended_at: datetime | None = None


# ----------------------------------------------------------------- events
class FeedingComponentIn(BaseModel):
    feed_product_id: str
    quantity_offered: float = Field(gt=0)
    quantity_consumed: float | None = Field(None, ge=0)
    unit: str | None = None
    lot_id: str | None = None
    batch_id: str | None = None


class FeedingEventCreate(BaseModel):
    subject_type: str
    subject_id: str
    event_type: str = "offered"  # offered | delivered | consumed_estimate | refusal
    occurred_at: datetime | None = None
    head_count: int | None = Field(None, ge=0)
    notes: str | None = None
    components: list[FeedingComponentIn]
    allow_negative: bool = False


class FeedingEventReverse(BaseModel):
    reason: str


class FeedingEventOut(BaseModel):
    id: str
    farm_id: str
    subject_type: str
    subject_id: str
    program_version_id: str | None = None
    occurred_at: datetime
    event_type: str
    head_count: int | None = None
    recorded_by: str | None = None
    notes: str | None = None
    status: str
    reversal_of_id: str | None = None
    total_cost: float | None = None
    created_at: datetime
    components: list[dict]


# ------------------------------------------------------------ allocations
class AllocationCreate(BaseModel):
    feed_product_id: str
    quantity: float = Field(gt=0)
    unit: str | None = None
    lot_id: str | None = None
    species_code: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    feeding_program_id: str | None = None
    location_label: str | None = None
    cost_centre: str | None = None
    purpose: str | None = None
    transferable: bool = False


class AllocationTransfer(BaseModel):
    quantity: float | None = Field(None, gt=0)
    species_code: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    feeding_program_id: str | None = None
    purpose: str | None = None


class AllocationOut(BaseModel):
    id: str
    farm_id: str
    feed_product_id: str
    product_name: str | None = None
    lot_id: str | None = None
    species_code: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    feeding_program_id: str | None = None
    location_label: str | None = None
    cost_centre: str | None = None
    purpose: str | None = None
    allocated_quantity: float
    consumed_quantity: float
    remaining_quantity: float
    unit: str
    transferable: bool
    status: str
    created_by: str | None = None
    created_at: datetime
    released_at: datetime | None = None


# ----------------------------------------------------------- replenishment
class ReorderPolicyUpsert(BaseModel):
    feed_product_id: str
    location_label: str | None = None
    minimum_stock: float | None = None
    reorder_point: float | None = None
    safety_stock: float | None = None
    preferred_reorder_quantity: float | None = None
    maximum_stock: float | None = None
    lead_time_days: int | None = None
    cover_margin_days: int = 3
    variance_threshold_pct: float = 5.0
    active: bool = True


class ReorderPolicyOut(ReorderPolicyUpsert):
    id: str
    farm_id: str
    unit: str


class ReorderAcknowledge(BaseModel):
    quantity: float | None = None
    note: str | None = None
    assigned_to: str | None = None


# --------------------------------------------------------- reconciliation
class ReconciliationCreate(BaseModel):
    feed_product_id: str
    lot_id: str | None = None
    period_from: datetime
    period_to: datetime
    counted_closing_quantity: float | None = None
    explanation: str | None = None


class ReconciliationClose(BaseModel):
    post_adjustment: bool = False
    explanation: str | None = None


class ReconciliationOut(BaseModel):
    id: str
    farm_id: str
    feed_product_id: str
    product_name: str | None = None
    lot_id: str | None = None
    period_from: datetime
    period_to: datetime
    opening_quantity: float
    received_quantity: float
    issued_to_batches: float
    issued_to_feeding: float
    other_issued_quantity: float
    waste_quantity: float
    returned_quantity: float
    adjustment_quantity: float
    expected_closing_quantity: float
    counted_closing_quantity: float | None = None
    variance_quantity: float | None = None
    variance_pct: float | None = None
    unit: str
    status: str
    explanation: str | None = None
    created_at: datetime
    closed_at: datetime | None = None
