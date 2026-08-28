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
    permissions: list[ModulePermissionOut]
    full_access: bool


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
