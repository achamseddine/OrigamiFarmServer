from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.common.enums import TenantRole, TenantStatus


@dataclass(frozen=True)
class Identity:
    """The authenticated principal, independent of any tenant. Resolved
    once per request from the bearer token; everything tenant-specific is
    layered on top in TenantContext.
    """

    user_id: uuid.UUID
    idp_subject: str
    email: str
    display_name: str


@dataclass(frozen=True)
class TenantContext:
    """Server-resolved tenant scope for the current request. Every field
    here comes from a database lookup keyed by the authenticated identity
    (and, for device-bound requests, the device row) — never from a
    client-supplied tenant/company header. See TENANCY.md.
    """

    tenant_id: uuid.UUID
    tenant_status: TenantStatus
    membership_id: uuid.UUID
    tenant_role: TenantRole
    farm_ids: list[uuid.UUID] = field(default_factory=list)
    module_permissions: set[str] = field(default_factory=set)
    device_id: uuid.UUID | None = None

    def has_farm_access(self, farm_id: uuid.UUID) -> bool:
        if self.tenant_role in (TenantRole.TENANT_OWNER, TenantRole.FARM_MANAGER) and not self.farm_ids:
            # Owners/managers default to full farm visibility until scoped
            # down with explicit membership_farm_access rows.
            return True
        return farm_id in self.farm_ids

    def has_permission(self, module_code: str, action: str) -> bool:
        return f"{module_code}:{action}" in self.module_permissions
