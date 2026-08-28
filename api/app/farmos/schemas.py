"""Pydantic DTOs for the FarmOS tablet contract. Field names match
docs/FARMOS_API.md exactly — this is a fixed external contract, not
free-form internal API design.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserProfileOut(BaseModel):
    id: str
    farm_id: str
    name: str
    email: str | None
    phone: str | None
    role: str
    department: str | None
    language: str
    active: bool


class ModulePermissionOut(BaseModel):
    module_code: str
    can_view: bool
    can_create: bool
    can_edit: bool
    can_delete: bool
    can_approve: bool
    can_export: bool
    can_assign: bool
    can_configure: bool


class EmployeeDetailOut(UserProfileOut):
    job_title: str | None
    employment_status: str
    start_date: str | None
    photo_path: str | None
    working_days: list[str] | None
    working_hours: str | None
    notes: str | None
    permissions: list[ModulePermissionOut] = []
    full_access: bool = False


class ModulePermissionIn(BaseModel):
    module_code: str
    can_view: bool = False
    can_create: bool = False
    can_edit: bool = False
    can_delete: bool = False
    can_approve: bool = False
    can_export: bool = False
    can_assign: bool = False
    can_configure: bool = False


class EmployeeCreate(BaseModel):
    name: str
    password: str
    email: str | None = None
    phone: str | None = None
    role: str = "worker"
    department: str | None = None
    language: str = "en"
    job_title: str | None = None
    employment_status: str = "active"
    start_date: str | None = None
    photo_path: str | None = None
    working_days: list[str] | None = None
    working_hours: str | None = None
    notes: str | None = None
    permissions: list[ModulePermissionIn] = []


class EmployeeUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    role: str | None = None
    department: str | None = None
    language: str | None = None
    job_title: str | None = None
    employment_status: str | None = None
    start_date: str | None = None
    photo_path: str | None = None
    working_days: list[str] | None = None
    working_hours: str | None = None
    notes: str | None = None
    active: bool | None = None
    password: str | None = None


class PermissionSet(BaseModel):
    permissions: list[ModulePermissionIn]


class MyAccessOut(BaseModel):
    user_id: str
    role: str
    full_access: bool
    modules: dict[str, dict[str, bool]]


class ModuleCatalogEntry(BaseModel):
    code: str
    label_en: str
    label_ar: str
    group: str
    license_code: str | None
    licensed_active: bool


class FarmOut(BaseModel):
    id: str
    name: str
    country: str | None
    region: str | None
    timezone: str
    default_currency: str
    created_at: datetime


class ModuleLicenseOut(BaseModel):
    id: str
    farm_id: str
    module_code: str
    status: str
    plan: str
    starts_at: str | None
    expires_at: str | None
    max_users: int | None
    max_products: int | None


# --- Animals -----------------------------------------------------------


class AnimalOut(BaseModel):
    id: str
    farm_id: str
    tag: str
    name: str
    species: str
    breed: str | None
    sex: str | None
    birth_date: datetime | None
    status: str
    location_label: str | None
    health_score: int
    pregnant: bool
    pregnancy_days: int | None
    lactating: bool
    lactation_cycle: int | None
    withdrawal_until: datetime | None
    withdrawal_reason: str | None
    weight_kg: float | None
    group_name: str | None
    photo_path: str | None
    acquisition_date: datetime | None
    acquisition_source: str | None
    sire_tag: str | None
    dam_tag: str | None
    color_markings: str | None
    purchase_cost: float | None
    current_value: float | None
    notes: str | None
    active: bool


class AnimalDetailOut(AnimalOut):
    recent_observations: list[dict] = []
    recent_events: list[dict] = []
    open_recommendations: list[dict] = []


class AnimalCreate(BaseModel):
    tag: str
    name: str
    species: str
    breed: str | None = None
    sex: str | None = None
    birth_date: datetime | None = None
    acquisition_date: datetime | None = None
    acquisition_source: str | None = None
    sire_tag: str | None = None
    dam_tag: str | None = None
    location_label: str | None = None
    group_name: str | None = None
    weight_kg: float | None = None
    color_markings: str | None = None
    photo_path: str | None = None
    status: str = "healthy"
    health_score: int = 100
    pregnant: bool = False
    pregnancy_days: int | None = None
    lactating: bool = False
    lactation_cycle: int | None = None
    purchase_cost: float | None = None
    current_value: float | None = None
    notes: str | None = None


class AnimalUpdate(BaseModel):
    tag: str | None = None
    name: str | None = None
    species: str | None = None
    breed: str | None = None
    sex: str | None = None
    birth_date: datetime | None = None
    acquisition_date: datetime | None = None
    acquisition_source: str | None = None
    sire_tag: str | None = None
    dam_tag: str | None = None
    location_label: str | None = None
    group_name: str | None = None
    weight_kg: float | None = None
    color_markings: str | None = None
    photo_path: str | None = None
    status: str | None = None
    health_score: int | None = None
    pregnant: bool | None = None
    pregnancy_days: int | None = None
    lactating: bool | None = None
    lactation_cycle: int | None = None
    purchase_cost: float | None = None
    current_value: float | None = None
    notes: str | None = None
    active: bool | None = None


class AnimalMove(BaseModel):
    location_label: str


# --- Tasks ---------------------------------------------------------------


class TaskOut(BaseModel):
    id: str
    farm_id: str
    title: str
    description: str | None
    assigned_to: str | None
    due_at: datetime | None
    priority: str
    status: str
    source_type: str | None
    source_id: str | None


class TaskCreate(BaseModel):
    farm_id: str
    title: str
    description: str | None = None
    assigned_to: str | None = None
    due_at: datetime | None = None
    priority: str = "medium"
    source_type: str | None = None
    source_id: str | None = None


class TaskUpdate(BaseModel):
    status: str | None = None
    assigned_to: str | None = None
    priority: str | None = None


# --- Notifications -----------------------------------------------------


class NotificationOut(BaseModel):
    id: str
    module_code: str
    notification_type: str
    title: str
    description: str | None
    priority: str
    entity_type: str | None
    entity_id: str | None
    source_type: str | None
    source_id: str | None
    read_at: datetime | None
    created_at: datetime


class NotificationsPage(BaseModel):
    unread_count: int
    total: int
    notifications: list[NotificationOut]


# --- Priorities ----------------------------------------------------------


class PriorityOut(BaseModel):
    id: str
    kind: str
    module_code: str
    notification_type: str
    title: str
    description: str | None
    priority: str
    status: str | None = None
    entity_type: str | None
    entity_id: str | None
    source_type: str | None
    source_id: str | None
    due_at: datetime | None = None
    assigned_to: str | None = None
    assigned_to_name: str | None = None
    metadata: dict = {}


class PrioritiesPage(BaseModel):
    total: int
    counts_by_priority: dict[str, int]
    counts_by_module: dict[str, int]
    priorities: list[PriorityOut]


# --- Reports ---------------------------------------------------------------


class MorningBriefingOut(BaseModel):
    date: str
    farm_name: str
    manager_name: str | None
    kpis: dict[str, int | float]
    priorities: list[PriorityOut]
    tasks: list[TaskOut]


class AmountByCategory(BaseModel):
    category: str
    amount: float


class AmountByProductType(BaseModel):
    product_type: str
    amount: float


class AmountByProductLabel(BaseModel):
    product_label: str
    amount: float


class DailySummaryOut(BaseModel):
    date: str
    revenue_today: float
    expenses_today: float
    gross_margin: float
    cash_collected: float
    pending_payments: float
    sales_breakdown: list[AmountByProductType]
    expense_breakdown: list[AmountByCategory]
    top_selling_products: list[AmountByProductLabel]
    business_insights: list[str]


# --- Animal health / observations ---------------------------------------


class TreatmentOut(BaseModel):
    id: str
    entity_type: str
    entity_id: str
    diagnosis: str | None
    medication: str
    dose: str
    route: str
    start_at: datetime
    end_at: datetime | None
    withdrawal_until: datetime | None
    vet_id: str | None
    responsible_user_id: str
    status: str
    cost: float | None
    notes: str | None


class TreatmentCreate(BaseModel):
    entity_type: str
    entity_id: str
    medication: str
    dose: str
    route: str
    responsible_user_id: str
    diagnosis: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    withdrawal_until: datetime | None = None
    vet_id: str | None = None
    cost: float | None = None
    notes: str | None = None


class ObservationOut(BaseModel):
    id: str
    farm_id: str
    entity_type: str
    entity_id: str
    observation_type: str
    quality: str
    confidence: float
    value_numeric: float | None
    value_text: str | None
    unit: str | None
    severity: str | None
    observed_at: datetime
    observer_id: str
    notes: str | None


class ObservationCreate(BaseModel):
    farm_id: str
    entity_type: str
    entity_id: str
    observation_type: str
    quality: str = "human_observed"
    value_numeric: float | None = None
    value_text: str | None = None
    unit: str | None = None
    severity: str | None = None
    observed_at: datetime | None = None
    observer_id: str
    notes: str | None = None


# --- Feed & inventory ------------------------------------------------------


class InventoryItemOut(BaseModel):
    id: str
    farm_id: str
    name: str
    category: str | None
    unit: str
    current_qty: float
    reorder_level: float
    supplier_label: str | None
    unit_cost: float | None
    last_purchase: datetime | None


class FeedTransactionCreate(BaseModel):
    item_id: str
    direction: str
    quantity: float
    unit: str | None = None
    reason: str | None = None
    linked_entity_type: str | None = None
    linked_entity_id: str | None = None
    allow_negative: bool = False


# --- Production --------------------------------------------------------


class EggRecordOut(BaseModel):
    id: str
    flock_id: str
    total_eggs: int
    sellable_eggs: int
    broken_eggs: int
    consumed: int
    hatched: int
    wasted: int
    recorded_at: datetime


class EggRecordCreate(BaseModel):
    flock_id: str
    total_eggs: int
    sellable_eggs: int = 0
    broken_eggs: int = 0
    consumed: int = 0
    hatched: int = 0
    wasted: int = 0
    recorded_at: datetime | None = None


class FieldOut(BaseModel):
    id: str
    name: str
    crop_type: str | None
    area_value: float | None
    area_unit: str | None
    stage: str | None
    expected_harvest_date: datetime | None
    est_yield_kg: float | None
    field_code: str | None
    location_label: str | None
    soil_type: str | None
    irrigation_method: str | None
    status: str = "active"
    notes: str | None


class FieldCreate(BaseModel):
    name: str
    field_code: str | None = None
    area_value: float | None = None
    area_unit: str | None = "m2"
    location_label: str | None = None
    soil_type: str | None = None
    irrigation_method: str | None = None
    status: str = "active"
    notes: str | None = None


class FieldUpdate(BaseModel):
    name: str | None = None
    field_code: str | None = None
    area_value: float | None = None
    area_unit: str | None = None
    location_label: str | None = None
    soil_type: str | None = None
    irrigation_method: str | None = None
    status: str | None = None
    notes: str | None = None
    crop_type: str | None = None
    stage: str | None = None
    expected_harvest_date: datetime | None = None
    est_yield_kg: float | None = None


class HarvestRecordOut(BaseModel):
    id: str
    field_id: str
    product_name: str
    quantity: float
    unit: str
    waste_qty: float
    destination: str | None
    recorded_at: datetime


class HarvestRecordCreate(BaseModel):
    field_id: str
    product_name: str
    quantity: float
    unit: str = "kg"
    waste_qty: float = 0
    destination: str | None = None
    recorded_at: datetime | None = None


class MilkRecordOut(BaseModel):
    id: str
    animal_id: str
    session: str
    liters: float
    quality_status: str
    destination: str
    recorded_at: datetime
    recorded_by: str | None
    under_withdrawal_warning: bool = False


class MilkRecordCreate(BaseModel):
    animal_id: str
    session: str
    liters: float
    destination: str = "stored"
    quality_status: str = "normal"
    recorded_at: datetime | None = None
    recorded_by: str | None = None


# --- Agriculture ---------------------------------------------------------


class CropOut(BaseModel):
    id: str
    name: str
    category: str | None
    default_cycle_days: int | None
    active: bool


class CropCreate(BaseModel):
    name: str
    category: str | None = None
    default_cycle_days: int | None = None


class CropPlantingOut(BaseModel):
    id: str
    field_id: str
    crop_id: str
    variety: str | None
    planted_area: float | None
    area_unit: str | None
    planted_date: datetime | None
    expected_harvest_date: datetime | None
    expected_yield_kg: float | None
    stage: str
    status: str
    notes: str | None
    created_at: datetime


class CropPlantingCreate(BaseModel):
    field_id: str
    crop_id: str
    variety: str | None = None
    planted_area: float | None = None
    area_unit: str | None = "m2"
    planted_date: datetime | None = None
    expected_harvest_date: datetime | None = None
    expected_yield_kg: float | None = None
    stage: str = "planted"
    notes: str | None = None


class CropPlantingUpdate(BaseModel):
    variety: str | None = None
    planted_area: float | None = None
    expected_harvest_date: datetime | None = None
    expected_yield_kg: float | None = None
    stage: str | None = None
    status: str | None = None
    notes: str | None = None


class DailyHarvestOut(BaseModel):
    id: str
    field_id: str
    product_name: str
    total_quantity: float
    sellable_quantity: float
    waste_quantity: float
    unit: str
    recorded_at: datetime
    inventory_item_id: str | None
    inventory_qty_after: float | None


class ExpenseOut(BaseModel):
    id: str
    farm_id: str
    supplier_id: str | None
    category: str
    amount: float
    currency: str
    linked_entity_type: str | None
    linked_entity_id: str | None
    incurred_at: datetime


class SaleOut(BaseModel):
    id: str
    farm_id: str
    customer_id: str | None
    product_type: str
    product_label: str | None
    quantity: float | None
    unit: str | None
    amount: float
    currency: str
    payment_status: str
    sold_at: datetime


class AuditEventOut(BaseModel):
    id: str
    user_id: str
    user_name: str | None
    action: str
    entity_type: str
    entity_id: str
    module_code: str | None
    summary: str | None
    changes_json: dict | None
    metadata_json: dict = {}
    device: str | None
    timestamp: datetime


class EvidenceItem(BaseModel):
    label: str
    value: str


class RecommendationOut(BaseModel):
    id: str
    farm_id: str
    category: str
    priority: str
    title: str
    entity_type: str | None
    entity_id: str | None
    entity_label: str | None
    confidence: float
    rationale: str
    suggested_action: str
    status: str
    rule_id: str | None
    generated_at: datetime
    evidence: list[EvidenceItem] = []


class RecommendationDecision(BaseModel):
    decision: str
    decided_by: str
    note: str | None = None


class DailyHarvestCreate(BaseModel):
    field_id: str
    planting_id: str | None = None
    crop_id: str | None = None
    product_name: str | None = None
    total_quantity: float
    sellable_quantity: float | None = None
    waste_quantity: float = 0
    unit: str = "kg"
    destination: str | None = None
    recorded_at: datetime | None = None
    notes: str | None = None
