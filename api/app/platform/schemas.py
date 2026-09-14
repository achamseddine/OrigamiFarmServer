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
    # True while the password in force is one somebody else typed — the
    # admin who created the account, or one who reset it. False for an
    # identity that signs in through OIDC and has no password at all.
    password_set_by_someone_else: bool = False


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
    # What the plan sells. A plan with none is a price with no contents —
    # legal, since the modules can be chosen afterwards, but it grants a
    # subscriber nothing until they are.
    module_codes: list[str] = []


class PlanModulesRequest(BaseModel):
    """The complete set of modules a plan includes — not an addition to it."""

    module_codes: list[str]


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
    # Always sent with the plan: a price without its contents cannot be
    # read as an offer.
    module_codes: list[str] = []

    model_config = {"from_attributes": True}


class LicenceOut(BaseModel):
    """A licence code and what holding it opens in the tablet app.

    The console builds its plan picker from these rather than from the
    module list, because only a code some module actually points at gates
    anything — offering the rest is how a plan ends up selling nothing.
    """

    license_code: str
    name: str
    # Tablet module codes this licence unlocks, with their display names.
    unlocks: list[str]
    unlocks_labels: list[str]
    # True for the paid add-ons; the rest are ordinary parts of a plan.
    is_addon: bool
    # How many tenants hold it today.
    tenants_licensed: int


class LicenceIssueRequest(BaseModel):
    """How long each half of the pack stays good for."""

    # A week by default: a customer who gets the email on Friday should
    # still be able to act on it when they are next at the farm office.
    key_ttl_hours: int = Field(default=168, ge=1, le=8760)
    invitation_ttl_hours: int = Field(default=168, ge=1, le=720)
    # Pin the key to one site. Left unset it works for any of the
    # customer's farms, which is what a single-site customer wants.
    farm_id: uuid.UUID | None = None
    send_email: bool = True


class LicenceIssueOut(BaseModel):
    """The whole handover, returned exactly once.

    Both the key and the URL are credentials: neither is stored in a form
    that can be read back, so a pack that is lost is reissued rather than
    looked up.
    """

    tenant_id: uuid.UUID
    company_code: str
    display_name: str
    plan_code: str | None
    plan_name: str | None
    # The licences this customer holds — what the key will actually open.
    licences: list[str]

    licence_key: str
    licence_key_expires_at: datetime

    owner_email: str
    owner_name: str
    activation_url: str
    activation_expires_at: datetime

    delivery: str
    delivery_detail: str


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
    # Without this a subscription was stuck on the model default forever:
    # nothing in the codebase assigned it, so every customer stayed an
    # onboarding trial and MRR could never be anything but zero.
    status: SubscriptionStatus = SubscriptionStatus.ONBOARDING_TRIAL
    # On by default because the opposite default is the bug this fixes: a
    # customer recorded as subscribed who can open nothing. Turn it off to
    # record a commercial fact without touching what they may use.
    apply_plan_modules: bool = True


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


class SubscriptionSaveResponse(BaseModel):
    """The subscription, plus what putting the tenant on that plan changed.

    modules_not_in_plan is reported, never revoked: moving a customer to a
    smaller plan must not switch off the module a farm is recording with
    today. The console shows the list so somebody decides.
    """

    subscription: SubscriptionOut
    plan_code: str
    modules_granted: list[str]
    modules_already_active: list[str]
    modules_not_in_plan: list[str]


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


class InvitationCreateRequest(BaseModel):
    ttl_hours: int = Field(default=168, ge=1, le=720)
    # Attempted only when this deployment has a mail server; the response
    # says which of the two actually happened rather than assuming.
    send_email: bool = True


class InvitationOut(BaseModel):
    """The link, returned exactly once.

    url is the plaintext token in a URL, so this response is a credential:
    it is shown to the admin who created it and never recoverable
    afterwards — a lost link is reissued, not looked up.
    """

    invitation_id: uuid.UUID
    email: str
    url: str
    expires_at: datetime
    # "email" when it was sent, "manual" when no mail server is configured,
    # "failed" when one is and the send did not work. The console shows the
    # link to copy in the latter two cases.
    delivery: str
    delivery_detail: str


class InvitationStatusOut(BaseModel):
    """Where this person is in getting started, without exposing the token."""

    membership_id: uuid.UUID
    email: str
    display_name: str
    has_password: bool
    invitation_sent_at: datetime | None
    invitation_expires_at: datetime | None
    invitation_accepted_at: datetime | None
    # What to show: "no_invitation", "pending", "expired", "accepted".
    state: str


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
