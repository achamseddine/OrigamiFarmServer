from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.common.enums import TenantRole, TenantStatus


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str


class MeContextOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_status: TenantStatus
    membership_id: uuid.UUID
    tenant_role: TenantRole
    farm_ids: list[uuid.UUID]
    permissions: list[str]
    device_id: uuid.UUID | None


class EntitlementsOut(BaseModel):
    tenant_id: uuid.UUID
    status: TenantStatus
    modules: dict[str, dict]
    lease_expires_at: datetime | None


class FarmOut(BaseModel):
    id: uuid.UUID
    farm_code: str
    name: str
    active: bool

    model_config = {"from_attributes": True}


class AnimalCreateRequest(BaseModel):
    farm_id: uuid.UUID | None = None
    tag_code: str
    species: str
    name: str | None = None
    attributes: dict = {}


class AnimalUpdateRequest(BaseModel):
    name: str | None = None
    attributes: dict | None = None
    expected_version: int


class AnimalOut(BaseModel):
    id: uuid.UUID
    farm_id: uuid.UUID | None
    tag_code: str
    species: str
    name: str | None
    version: int
    attributes: dict

    model_config = {"from_attributes": True}


class MembershipCreateRequest(BaseModel):
    email: str
    display_name: str
    tenant_role: TenantRole
    default_farm_id: uuid.UUID | None = None
    farm_ids: list[uuid.UUID] = []
    permissions: list[str] = []  # "module_code:action" pairs


class MembershipOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    tenant_role: TenantRole
    farm_ids: list[uuid.UUID]
    permissions: list[str]
