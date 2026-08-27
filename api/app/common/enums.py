"""Shared enumerations for control-plane state machines.

Keeping these centralized makes the allowed transitions easy to audit
against LICENSE_ENTITLEMENTS.md and TENANCY.md.
"""

from __future__ import annotations

import enum


class TenantStatus(str, enum.Enum):
    ONBOARDING = "ONBOARDING"
    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    GRACE = "GRACE"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"


TENANT_STATUS_TRANSITIONS: dict[TenantStatus, set[TenantStatus]] = {
    TenantStatus.ONBOARDING: {TenantStatus.TRIAL, TenantStatus.ACTIVE, TenantStatus.TERMINATED},
    TenantStatus.TRIAL: {TenantStatus.ACTIVE, TenantStatus.SUSPENDED, TenantStatus.TERMINATED},
    TenantStatus.ACTIVE: {TenantStatus.GRACE, TenantStatus.SUSPENDED, TenantStatus.TERMINATED},
    TenantStatus.GRACE: {TenantStatus.ACTIVE, TenantStatus.SUSPENDED, TenantStatus.TERMINATED},
    TenantStatus.SUSPENDED: {TenantStatus.ACTIVE, TenantStatus.GRACE, TenantStatus.TERMINATED},
    TenantStatus.TERMINATED: set(),
}

# Tenants in these states may still read/export their own data (safe mode)
# even though write access is blocked. Never a sudden hard lockout.
TENANT_READ_ALLOWED_STATUSES = {
    TenantStatus.ONBOARDING,
    TenantStatus.TRIAL,
    TenantStatus.ACTIVE,
    TenantStatus.GRACE,
    TenantStatus.SUSPENDED,
}
TENANT_WRITE_ALLOWED_STATUSES = {
    TenantStatus.ONBOARDING,
    TenantStatus.TRIAL,
    TenantStatus.ACTIVE,
    TenantStatus.GRACE,
}


class OnboardingStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"


class TenantDataMode(str, enum.Enum):
    SHARED_RLS = "SHARED_RLS"
    DEDICATED_DB = "DEDICATED_DB"


class MembershipStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class TenantRole(str, enum.Enum):
    TENANT_OWNER = "TENANT_OWNER"
    FARM_MANAGER = "FARM_MANAGER"
    EMPLOYEE = "EMPLOYEE"


class PlatformRole(str, enum.Enum):
    PLATFORM_SUPER_ADMIN = "PLATFORM_SUPER_ADMIN"
    PLATFORM_COMMERCIAL_ADMIN = "PLATFORM_COMMERCIAL_ADMIN"
    PLATFORM_SUPPORT_ADMIN = "PLATFORM_SUPPORT_ADMIN"
    PLATFORM_AUDITOR = "PLATFORM_AUDITOR"


class SubscriptionStatus(str, enum.Enum):
    ONBOARDING_TRIAL = "ONBOARDING_TRIAL"
    ACTIVE = "ACTIVE"
    GRACE = "GRACE"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"


class BillingCycle(str, enum.Enum):
    MONTHLY = "MONTHLY"
    ANNUAL = "ANNUAL"


class EntitlementStatus(str, enum.Enum):
    INACTIVE = "INACTIVE"
    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    SCHEDULED_DISABLE = "SCHEDULED_DISABLE"
    SUSPENDED = "SUSPENDED"
    EXPIRED = "EXPIRED"


ENTITLEMENT_EFFECTIVE_STATUSES = {EntitlementStatus.TRIAL, EntitlementStatus.ACTIVE}


class EntitlementSource(str, enum.Enum):
    PLAN = "PLAN"
    OVERRIDE = "OVERRIDE"
    TRIAL = "TRIAL"


class DevicePlatform(str, enum.Enum):
    ANDROID = "ANDROID"
    IOS = "IOS"
    WEB = "WEB"
    OTHER = "OTHER"


class DeviceStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    LOST = "LOST"
    RETIRED = "RETIRED"


class DeviceActivationStatus(str, enum.Enum):
    PENDING = "PENDING"
    USED = "USED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class InvoiceStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    VOID = "VOID"


class BackupJobType(str, enum.Enum):
    DATABASE_BACKUP = "DATABASE_BACKUP"
    TENANT_SNAPSHOT = "TENANT_SNAPSHOT"


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class SupportCaseStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class ActorType(str, enum.Enum):
    PLATFORM_USER = "PLATFORM_USER"
    TENANT_USER = "TENANT_USER"
    DEVICE = "DEVICE"
    SYSTEM = "SYSTEM"


class SyncOperation(str, enum.Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
