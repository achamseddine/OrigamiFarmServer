from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.common.enums import (
    EntitlementStatus,
    OnboardingStatus,
    SubscriptionStatus,
    TenantRole,
    TenantStatus,
)


class PlatformMeOut(BaseModel):
    """Who the console is signed in as, and what platform access they hold.

    platform_roles is empty for a perfectly valid identity that simply has
    no Origami staff role — the console shows that as "no platform access"
    rather than letting every subsequent call fail with a bare 403.
    """

    user_id: uuid.UUID
    email: str
    display_name: str
    platform_roles: list[str]


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
    currency: str = "USD"
    # Optional, and left unset rather than zeroed when unknown: an unpriced
    # plan is excluded from revenue rather than counted as free.
    monthly_price_cents: int | None = Field(default=None, ge=0)
    annual_price_cents: int | None = Field(default=None, ge=0)


class PlanUpdateRequest(BaseModel):
    name: str | None = None
    status: str | None = None
    limits: dict | None = None
    currency: str | None = None
    monthly_price_cents: int | None = Field(default=None, ge=0)
    annual_price_cents: int | None = Field(default=None, ge=0)


class PlanOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    status: str
    limits: dict
    currency: str
    monthly_price_cents: int | None
    annual_price_cents: int | None

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
    # From the joined user_identity — a list of bare UUIDs is unusable in
    # the console, which needs to show who these people are.
    email: str
    display_name: str
    # The tablet contract's own free-text job title, distinct from
    # tenant_role above (see app/tenants/models.py).
    role: str
    default_farm_id: uuid.UUID | None = None
    has_password: bool


class MembershipStatusChangeRequest(BaseModel):
    active: bool
    reason: str | None = None


class LicenseLeaseOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    device_name: str | None
    issued_at: datetime
    expires_at: datetime
    policy_version: int
    modules: list[str]
    revoked_at: datetime | None

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
