from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import ControlBase
from app.common.enums import BillingCycle, EntitlementSource, EntitlementStatus, SubscriptionStatus
from app.common.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.common.types import str_enum


class Plan(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    __tablename__ = "plan"

    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    limits: Mapped[dict] = mapped_column(JSONB, default=dict)


class ModuleCatalog(ControlBase):
    __tablename__ = "module_catalog"

    module_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name_en: Mapped[str] = mapped_column()
    name_ar: Mapped[str] = mapped_column()
    description: Mapped[str] = mapped_column(default="")
    version: Mapped[str] = mapped_column(default="1.0.0")
    minimum_app_version: Mapped[str] = mapped_column(default="0.0.0")
    dependencies: Mapped[list[str]] = mapped_column(JSONB, default=list)
    default_features: Mapped[dict] = mapped_column(JSONB, default=dict)
    commercial_status: Mapped[str] = mapped_column(String(16), default="AVAILABLE")
    trial_allowed: Mapped[bool] = mapped_column(default=True)
    active: Mapped[bool] = mapped_column(default=True)
    # FarmOS tablet contract (GET /modules/catalog): a display grouping
    # ("operations", "livestock", ...) and, for the small set of modules
    # that are paid add-ons rather than included in every plan, the
    # license_code that GET /modules rows are checked against — see
    # app/farmos/modules.py.
    group: Mapped[str] = mapped_column(default="")
    license_code: Mapped[str | None] = mapped_column(nullable=True)


class PlanModule(UUIDPrimaryKeyMixin, ControlBase):
    __tablename__ = "plan_module"
    __table_args__ = (UniqueConstraint("plan_id", "module_code", name="uq_plan_module"),)

    plan_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("plan.id", ondelete="CASCADE"), index=True
    )
    module_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("module_catalog.module_code", ondelete="CASCADE")
    )
    included: Mapped[bool] = mapped_column(default=True)


class Subscription(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    """One current commercial subscription per tenant. State transitions are
    validated in app/entitlements/state_machine.py and always audited.
    """

    __tablename__ = "subscription"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), unique=True, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("plan.id"))
    status: Mapped[SubscriptionStatus] = mapped_column(
        str_enum(SubscriptionStatus), default=SubscriptionStatus.ONBOARDING_TRIAL
    )
    billing_cycle: Mapped[BillingCycle] = mapped_column(
        str_enum(BillingCycle), default=BillingCycle.MONTHLY
    )
    starts_at: Mapped[datetime] = mapped_column()
    renews_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(nullable=True)
    grace_until: Mapped[datetime | None] = mapped_column(nullable=True)


class SubscriptionItem(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    """Capacity add-ons: extra farm sites, devices, seats, storage, etc."""

    __tablename__ = "subscription_item"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("subscription.id", ondelete="CASCADE"), index=True
    )
    item_code: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    effective_from: Mapped[datetime] = mapped_column()
    effective_until: Mapped[datetime | None] = mapped_column(nullable=True)


class TenantEntitlement(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    """The server-enforced right for a tenant to use one module. This is the
    row EntitlementService reads — never trust a client's cached copy.
    """

    __tablename__ = "tenant_entitlement"
    __table_args__ = (
        UniqueConstraint("tenant_id", "module_code", name="uq_tenant_entitlement"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    module_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("module_catalog.module_code")
    )
    status: Mapped[EntitlementStatus] = mapped_column(
        str_enum(EntitlementStatus), default=EntitlementStatus.INACTIVE
    )
    source: Mapped[EntitlementSource] = mapped_column(
        str_enum(EntitlementSource), default=EntitlementSource.PLAN
    )
    effective_from: Mapped[datetime] = mapped_column()
    effective_until: Mapped[datetime | None] = mapped_column(nullable=True)
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user_identity.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(nullable=True)
    # FarmOS tablet contract (GET /modules -> ModuleLicenseOut): the
    # commercial plan name and optional capacity caps for licensed
    # add-ons (Mouneh, Farm Visits). Unset for the ordinary included
    # modules, which never appear in that endpoint at all.
    plan: Mapped[str | None] = mapped_column(nullable=True)
    max_users: Mapped[int | None] = mapped_column(nullable=True)
    max_products: Mapped[int | None] = mapped_column(nullable=True)
