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


class ModuleLicenseUpdate(BaseModel):
    status: str = "active"
    plan: str = "mouneh_addon"
    expires_at: str | None = None
    max_users: int | None = None
    max_products: int | None = None


# --- Mouneh --------------------------------------------------------------


class RawMaterialOut(BaseModel):
    id: str
    farm_id: str
    name: str
    category: str
    source_type: str
    inventory_item_id: str | None
    unit: str
    default_unit_cost: float
    stock_tracking_enabled: bool
    current_stock: float
    loss_percent_default: float
    active: bool


class RawMaterialCreate(BaseModel):
    name: str
    category: str = "raw_material"
    source_type: str = "purchased"
    inventory_item_id: str | None = None
    unit: str
    default_unit_cost: float = 0
    stock_tracking_enabled: bool = True
    current_stock: float = 0
    loss_percent_default: float = 0


class CostComponentOut(BaseModel):
    id: str
    product_id: str | None
    batch_id: str | None
    label: str
    cost_type: str
    calculation_method: str
    quantity: float | None
    unit_cost: float | None
    amount: float | None
    allocation_basis: str | None


class CostComponentCreate(BaseModel):
    label: str
    cost_type: str
    calculation_method: str = "fixed_amount"
    quantity: float | None = None
    unit_cost: float | None = None
    amount: float | None = None
    allocation_basis: str | None = None


class RecipeItemOut(BaseModel):
    id: str
    material_id: str
    material_type: str
    quantity: float
    unit: str
    loss_percent: float
    is_optional: bool


class RecipeItemCreate(BaseModel):
    material_id: str
    material_type: str = "raw_material"
    quantity: float
    unit: str
    loss_percent: float = 0
    is_optional: bool = False


class RecipeOut(BaseModel):
    id: str
    product_id: str
    version: int
    effective_from: datetime
    basis_quantity: float
    basis_unit: str
    active: bool
    notes: str | None
    items: list[RecipeItemOut] = []
    cost_components: list[CostComponentOut] = []


class RecipeCreate(BaseModel):
    basis_quantity: float
    basis_unit: str
    notes: str | None = None
    items: list[RecipeItemCreate]
    cost_components: list[CostComponentCreate] = []


class MounehProductOut(BaseModel):
    id: str
    farm_id: str
    name: str
    category: str
    photo_path: str | None
    output_unit: str
    custom_output_unit_label: str | None
    default_batch_size: float
    shelf_life_days: int | None
    warehouse_rules: str | None
    low_stock_threshold: float | None
    target_price: float | None
    wholesale_price: float | None
    target_margin_pct: float | None
    status: str
    created_at: datetime


class MounehProductDetailOut(MounehProductOut):
    active_recipe: RecipeOut | None = None


class MounehProductCreate(BaseModel):
    name: str
    category: str = "general"
    photo_path: str | None = None
    output_unit: str
    custom_output_unit_label: str | None = None
    default_batch_size: float = 1
    shelf_life_days: int | None = None
    warehouse_rules: str | None = None
    low_stock_threshold: float | None = None
    target_price: float | None = None
    wholesale_price: float | None = None
    target_margin_pct: float | None = None


class BatchInputConsumptionOut(BaseModel):
    id: str
    material_id: str
    planned_qty: float
    actual_qty: float | None
    unit_cost: float
    total_cost: float | None


class ProductionBatchOut(BaseModel):
    id: str
    farm_id: str
    product_id: str
    recipe_version_id: str
    batch_code: str
    planned_qty: float
    actual_output_qty: float | None
    waste_qty: float
    damaged_qty: float
    quality_status: str
    expiry_date: datetime | None
    warehouse_location: str | None
    status: str
    planned_unit_cost: float | None
    planned_total_cost: float | None
    actual_unit_cost: float | None
    actual_total_cost: float | None
    labor_hours: float | None
    started_at: datetime
    completed_at: datetime | None
    notes: str | None
    consumptions: list[BatchInputConsumptionOut] = []


class ProductionBatchCreate(BaseModel):
    product_id: str
    batch_code: str | None = None
    planned_qty: float
    warehouse_location: str | None = None
    notes: str | None = None


class BatchCompleteRequest(BaseModel):
    actual_output_qty: float
    waste_qty: float = 0
    damaged_qty: float = 0
    quality_status: str = "good"
    expiry_date: datetime | None = None
    warehouse_location: str | None = None
    labor_hours: float | None = None
    extra_cost_components: list[CostComponentCreate] = []


class BatchConsumptionLine(BaseModel):
    material_id: str
    actual_qty: float
    unit_cost: float | None = None


class BatchConsumeRequest(BaseModel):
    lines: list[BatchConsumptionLine]
    allow_negative: bool = False


class FinishedGoodsStockOut(BaseModel):
    id: str
    batch_id: str
    product_id: str
    quantity_produced: float
    quantity_available: float
    quantity_reserved: float
    quantity_sold: float
    quantity_damaged: float
    quantity_expired: float
    unit_cost: float
    expiry_date: datetime | None
    warehouse_location: str | None


class MounehSaleOut(BaseModel):
    id: str
    farm_id: str
    product_id: str
    batch_id: str
    finished_goods_stock_id: str
    quantity: float
    unit_price: float
    discount: float
    customer_id: str | None
    channel: str
    cost_per_unit: float
    revenue: float
    margin: float
    sold_at: datetime


class MounehSaleCreate(BaseModel):
    product_id: str
    finished_goods_stock_id: str | None = None
    quantity: float
    unit_price: float
    discount: float = 0
    customer_id: str | None = None
    channel: str = "retail"


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


# --- Farm Visits -----------------------------------------------------------


class VisitModuleStatusOut(BaseModel):
    module_code: str
    status: str
    active: bool
    features: dict[str, bool]


class VisitActivityOut(BaseModel):
    id: str
    farm_id: str
    name: str
    activity_type: str
    price: float
    capacity_per_slot: int
    duration_minutes: int | None
    requires_staff_role: str | None
    requires_animal_id: str | None
    welfare_limit_json: dict | None
    active: bool


class VisitActivityCreate(BaseModel):
    name: str
    activity_type: str = "other"
    price: float = 0
    capacity_per_slot: int = 1
    duration_minutes: int | None = None
    requires_staff_role: str | None = None
    requires_animal_id: str | None = None
    welfare_limit_json: dict | None = None
    active: bool = True


class VisitPackageOut(BaseModel):
    id: str
    farm_id: str
    name: str
    description: str | None
    base_price: float
    currency: str
    duration_minutes: int | None
    included_items_json: dict
    active: bool


class VisitPackageCreate(BaseModel):
    name: str
    description: str | None = None
    base_price: float = 0
    currency: str = "USD"
    duration_minutes: int | None = None
    included_items_json: dict = {}
    active: bool = True


class VisitorProfileOut(BaseModel):
    id: str
    farm_id: str
    full_name: str
    phone: str | None
    email: str | None
    preferred_language: str
    notes: str | None
    consent_marketing: bool


class VisitorProfileCreate(BaseModel):
    full_name: str
    phone: str | None = None
    email: str | None = None
    preferred_language: str = "en"
    notes: str | None = None
    consent_marketing: bool = False


class VisitSessionOut(BaseModel):
    id: str
    farm_id: str
    date: str
    start_time: str
    end_time: str
    capacity: int
    status: str
    weather_note: str | None
    expected_staff_cost: float | None


class VisitSessionCreate(BaseModel):
    date: str
    start_time: str
    end_time: str
    capacity: int
    weather_note: str | None = None
    expected_staff_cost: float | None = None


class VisitSessionUpdate(BaseModel):
    capacity: int | None = None
    status: str | None = None
    weather_note: str | None = None
    expected_staff_cost: float | None = None


class BookingActivityOut(BaseModel):
    id: str
    activity_id: str
    quantity: int
    unit_price: float
    total_price: float


class BookingActivitySelection(BaseModel):
    activity_id: str
    quantity: int = 1


class VisitBookingOut(BaseModel):
    id: str
    farm_id: str
    visitor_id: str
    session_id: str
    package_id: str
    status: str
    adults: int
    children: int
    total_amount: float
    deposit_amount: float
    balance_due: float
    source: str
    payment_method: str | None
    notes: str | None
    confirmed_at: datetime | None
    checked_in_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    activities: list[BookingActivityOut] = []


class VisitBookingCreate(BaseModel):
    visitor_id: str | None = None
    visitor: VisitorProfileCreate | None = None
    session_id: str
    package_id: str
    adults: int = 1
    children: int = 0
    activities: list[BookingActivitySelection] = []
    deposit_amount: float = 0
    source: str = "manual"
    payment_method: str | None = None
    notes: str | None = None
    idempotency_key: str | None = None


class OpeningCalendarDayOut(BaseModel):
    id: str
    weekday: int
    is_open: bool
    open_time: str | None
    close_time: str | None
    default_capacity: int
    notes: str | None


class OpeningCalendarDayUpsert(BaseModel):
    weekday: int
    is_open: bool = False
    open_time: str | None = None
    close_time: str | None = None
    default_capacity: int = 0
    notes: str | None = None


class VisitCostOut(BaseModel):
    id: str
    session_id: str
    category: str
    description: str | None
    amount: float
    allocation_method: str


class VisitCostCreate(BaseModel):
    session_id: str
    category: str
    description: str | None = None
    amount: float
    allocation_method: str = "per_session"


class VisitIncidentOut(BaseModel):
    id: str
    session_id: str
    booking_id: str | None
    incident_type: str
    severity: str
    description: str
    action_taken: str | None
    created_at: datetime


class VisitIncidentCreate(BaseModel):
    session_id: str
    booking_id: str | None = None
    incident_type: str
    severity: str = "low"
    description: str
    action_taken: str | None = None


class VisitStaffRosterOut(BaseModel):
    id: str
    session_id: str
    worker_id: str
    role: str
    start_time: str
    end_time: str
    hourly_rate: float
    total_cost: float | None


class VisitStaffRosterCreate(BaseModel):
    session_id: str
    worker_id: str
    role: str
    start_time: str
    end_time: str
    hourly_rate: float = 0


class VisitorFeedbackOut(BaseModel):
    id: str
    booking_id: str
    rating: int
    comments: str | None
    would_return: bool | None
    submitted_at: datetime


class VisitorFeedbackCreate(BaseModel):
    booking_id: str
    rating: int
    comments: str | None = None
    would_return: bool | None = None


class RetailSaleLine(BaseModel):
    item_type: str  # "finished_goods" | "inventory_item"
    item_id: str
    quantity: float
    unit_price: float


class VisitRetailSaleOut(BaseModel):
    id: str
    booking_id: str | None
    visitor_id: str | None
    sale_id: str
    channel: str
    total_amount: float


class VisitRetailSaleCreate(BaseModel):
    booking_id: str | None = None
    visitor_id: str | None = None
    channel: str = "farm_shop"
    payment_status: str = "paid"
    lines: list[RetailSaleLine]
    notes: str | None = None
