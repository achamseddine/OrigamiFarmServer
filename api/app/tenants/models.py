from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db import ControlBase
from app.common.enums import (
    MembershipStatus,
    OnboardingStatus,
    TenantDataMode,
    TenantRole,
    TenantStatus,
)
from app.common.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.common.types import str_enum


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    """The security boundary. See TENANCY.md: tenant_id is immutable and is
    never derived from a client-supplied company_code.
    """

    __tablename__ = "tenant"

    company_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    legal_name: Mapped[str] = mapped_column()
    display_name: Mapped[str] = mapped_column()
    # Free text, not a constrained ISO-2 code: the FarmOS tablet contract's
    # GET /farms/me returns a display value like "Lebanon", not "LB".
    country: Mapped[str] = mapped_column()
    region: Mapped[str | None] = mapped_column(nullable=True)
    timezone: Mapped[str] = mapped_column(default="UTC")
    default_currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[TenantStatus] = mapped_column(
        str_enum(TenantStatus), default=TenantStatus.ONBOARDING
    )
    onboarding_status: Mapped[OnboardingStatus] = mapped_column(
        str_enum(OnboardingStatus), default=OnboardingStatus.NOT_STARTED
    )

    farms: Mapped[list["Farm"]] = relationship(back_populates="tenant")
    memberships: Mapped[list["TenantMembership"]] = relationship(back_populates="tenant")


class Farm(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    __tablename__ = "farm"
    __table_args__ = (UniqueConstraint("tenant_id", "farm_code", name="uq_farm_tenant_code"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    farm_code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column()
    location_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    timezone_override: Mapped[str | None] = mapped_column(nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    tenant: Mapped[Tenant] = relationship(back_populates="farms")


class TenantMembership(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    """A user's relationship to exactly one tenant, including their farm
    scope and module permissions inside that tenant's entitlements.
    """

    __tablename__ = "tenant_membership"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user_identity.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[MembershipStatus] = mapped_column(
        str_enum(MembershipStatus), default=MembershipStatus.ACTIVE
    )
    tenant_role: Mapped[TenantRole] = mapped_column(str_enum(TenantRole))
    default_farm_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("farm.id", ondelete="SET NULL"), nullable=True
    )

    # --- FarmOS tablet app profile fields (app/farmos/) -----------------
    # `role` is free text (job title-ish: "owner", "manager", "veterinarian",
    # "guide", ...) per the tablet API contract — distinct from tenant_role
    # above, which stays a fixed enum for the platform's own admin-web
    # semantics. full_access (owner/manager) is computed from `role`, never
    # stored, so it can't drift from the value actually returned to the app.
    role: Mapped[str] = mapped_column(default="worker")
    phone: Mapped[str | None] = mapped_column(nullable=True)
    department: Mapped[str | None] = mapped_column(nullable=True)
    language: Mapped[str] = mapped_column(default="en")
    job_title: Mapped[str | None] = mapped_column(nullable=True)
    employment_status: Mapped[str] = mapped_column(default="active")
    start_date: Mapped[datetime | None] = mapped_column(nullable=True)
    photo_path: Mapped[str | None] = mapped_column(nullable=True)
    working_days: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    working_hours: Mapped[str | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="memberships")


class MembershipFarmAccess(UUIDPrimaryKeyMixin, ControlBase):
    """Which farms a membership may operate on (many-to-many)."""

    __tablename__ = "membership_farm_access"
    __table_args__ = (
        UniqueConstraint("membership_id", "farm_id", name="uq_membership_farm"),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant_membership.id", ondelete="CASCADE"), index=True
    )
    farm_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("farm.id", ondelete="CASCADE"), index=True
    )


class MembershipModulePermission(UUIDPrimaryKeyMixin, ControlBase):
    """Flexible many-to-many: a membership may hold permissions in any
    combination of entitled modules (Animals-only, Mouneh+Sales, etc). The
    Farm Manager can only grant permissions for modules the tenant is
    entitled to — enforced in app/tenants/service.py, not just here.
    """

    __tablename__ = "membership_module_permission"
    __table_args__ = (
        UniqueConstraint(
            "membership_id", "module_code", "permission_code", name="uq_membership_permission"
        ),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant_membership.id", ondelete="CASCADE"), index=True
    )
    module_code: Mapped[str] = mapped_column(String(64), index=True)
    permission_code: Mapped[str] = mapped_column(String(64))


class PlatformRoleAssignment(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    """Origami staff authorization — entirely separate from tenant
    memberships. A platform role never implies tenant data access; that
    still requires an explicit, audited support session.
    """

    __tablename__ = "platform_role_assignment"
    __table_args__ = (
        UniqueConstraint("user_id", "platform_role", name="uq_platform_role_user"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user_identity.id", ondelete="CASCADE"), index=True
    )
    platform_role: Mapped[str] = mapped_column(String(64))
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user_identity.id"), nullable=True
    )


class TenantDataLocator(TimestampMixin, ControlBase):
    """Resolves where a tenant's farm operational data actually lives.

    Application repositories never hard-code a database; they call
    TenantDataRouter, which reads this row. connection_secret_ref names an
    environment variable holding the dedicated connection string — the
    string itself is never stored in this table.
    """

    __tablename__ = "tenant_data_locator"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[TenantDataMode] = mapped_column(
        str_enum(TenantDataMode), default=TenantDataMode.SHARED_RLS
    )
    connection_secret_ref: Mapped[str | None] = mapped_column(nullable=True)
    schema_version: Mapped[str | None] = mapped_column(nullable=True)


Index("ix_farm_tenant_active", Farm.tenant_id, Farm.active)
