from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.common.enums import (
    EntitlementStatus,
    OnboardingStatus,
    SubscriptionStatus,
    TenantRole,
    TenantStatus,
)


class TenantCreateRequest(BaseModel):
    company_code: str
    legal_name: str
    display_name: str
    country: str
    timezone: str = "UTC"
    default_currency: str = "USD"


class TenantUpdateRequest(BaseModel):
    legal_name: str | None = None
    display_name: str | None = None
    country: str | None = None
    timezone: str | None = None
    default_currency: str | None = None
    onboarding_status: OnboardingStatus | None = None


class TenantStatusChangeRequest(BaseModel):
    status: TenantStatus
    reason: str


class TenantOut(BaseModel):
    id: uuid.UUID
    company_code: str
    legal_name: str
    display_name: str
    country: str
    timezone: str
    default_currency: str
    status: TenantStatus
    onboarding_status: OnboardingStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class TenantListOut(BaseModel):
    items: list[TenantOut]
    total: int
    limit: int
    offset: int


class FarmCreateRequest(BaseModel):
    farm_code: str
    name: str
    location_metadata: dict = {}
    timezone_override: str | None = None


class FarmOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    farm_code: str
    name: str
    active: bool

    model_config = {"from_attributes": True}


class PlanCreateRequest(BaseModel):
    code: str
    name: str
    limits: dict = {}


class PlanOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    status: str
    limits: dict

    model_config = {"from_attributes": True}


class ModuleCreateRequest(BaseModel):
    module_code: str
    name_en: str
    name_ar: str
    description: str = ""
    dependencies: list[str] = []
    trial_allowed: bool = True


class ModuleOut(BaseModel):
    module_code: str
    name_en: str
    name_ar: str
    description: str
    dependencies: list[str]
    active: bool

    model_config = {"from_attributes": True}


class SubscriptionUpsertRequest(BaseModel):
    plan_id: uuid.UUID
    billing_cycle: str = "MONTHLY"
    starts_at: datetime
    renews_at: datetime | None = None
    grace_until: datetime | None = None


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    plan_id: uuid.UUID
    status: SubscriptionStatus
    billing_cycle: str
    starts_at: datetime
    renews_at: datetime | None
    ends_at: datetime | None
    grace_until: datetime | None

    model_config = {"from_attributes": True}


class EntitlementActivateRequest(BaseModel):
    reason: str
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    configuration: dict = {}
    trial: bool = False


class EntitlementDeactivateRequest(BaseModel):
    reason: str
    effective_until: datetime | None = None


class EntitlementOut(BaseModel):
    module_code: str
    status: EntitlementStatus
    effective_from: datetime
    effective_until: datetime | None
    reason: str | None

    model_config = {"from_attributes": True}


class DeviceActivationCreateRequest(BaseModel):
    farm_id: uuid.UUID | None = None
    ttl_hours: int = 24


class DeviceActivationCreateResponse(BaseModel):
    activation_id: uuid.UUID
    activation_code: str
    expires_at: datetime


class DeviceOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    farm_id: uuid.UUID | None
    installation_id: str
    display_name: str
    status: str
    last_seen_at: datetime | None
    last_sync_at: datetime | None

    model_config = {"from_attributes": True}


class DeviceRevokeRequest(BaseModel):
    reason: str


class MembershipInviteRequest(BaseModel):
    email: EmailStr
    display_name: str
    tenant_role: TenantRole
    default_farm_id: uuid.UUID | None = None


class MembershipOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    tenant_role: TenantRole
    status: str

    model_config = {"from_attributes": True}


class AuditEventOut(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_type: str
    tenant_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
