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
