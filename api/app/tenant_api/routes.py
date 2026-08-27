from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.auth.dependencies import get_identity, get_tenant_context, require_permission
from app.auth.models import UserIdentity
from app.auth.schemas import Identity, TenantContext
from app.common.db import get_control_db
from app.common.enums import ActorType, MembershipStatus, TenantRole
from app.common.errors import AppError, ErrorCode
from app.common.tenant_db import get_tenant_db
from app.devices.models import LicenseLease
from app.entitlements.service import EntitlementService
from app.tenant_api.models import Animal
from app.tenant_api.schemas import (
    AnimalCreateRequest,
    AnimalOut,
    AnimalUpdateRequest,
    EntitlementsOut,
    FarmOut,
    MeContextOut,
    MembershipCreateRequest,
    MembershipOut,
    MeOut,
)
from app.tenants.models import Farm, MembershipFarmAccess, MembershipModulePermission, TenantMembership

router = APIRouter()


@router.get("/me", response_model=MeOut)
def me(identity: Identity = Depends(get_identity)) -> MeOut:
    return MeOut(user_id=identity.user_id, email=identity.email, display_name=identity.display_name)


@router.get("/me/context", response_model=MeContextOut)
def me_context(tenant_context: TenantContext = Depends(get_tenant_context)) -> MeContextOut:
    return MeContextOut(
        tenant_id=tenant_context.tenant_id,
        tenant_status=tenant_context.tenant_status,
        membership_id=tenant_context.membership_id,
        tenant_role=tenant_context.tenant_role,
        farm_ids=tenant_context.farm_ids,
        permissions=sorted(tenant_context.module_permissions),
        device_id=tenant_context.device_id,
    )


@router.get("/me/entitlements", response_model=EntitlementsOut)
def me_entitlements(
    tenant_context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_control_db),
) -> EntitlementsOut:
    modules = EntitlementService(db).effective_entitlements(tenant_context.tenant_id)

    lease_expires_at = None
    if tenant_context.device_id:
        lease = db.execute(
            select(LicenseLease)
            .where(
                LicenseLease.device_id == tenant_context.device_id,
                LicenseLease.revoked_at.is_(None),
            )
            .order_by(LicenseLease.expires_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if lease:
            lease_expires_at = lease.expires_at

    return EntitlementsOut(
        tenant_id=tenant_context.tenant_id,
        status=tenant_context.tenant_status,
        modules=modules,
        lease_expires_at=lease_expires_at,
    )


@router.get("/farms", response_model=list[FarmOut])
def list_my_farms(
    tenant_context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_control_db),
) -> list[Farm]:
    farms = db.execute(select(Farm).where(Farm.tenant_id == tenant_context.tenant_id)).scalars().all()
    return [f for f in farms if tenant_context.has_farm_access(f.id)]


# --- Animals (representative FarmOS domain CRUD) ----------------------------


def _load_animal_or_404(db: Session, animal_id: uuid.UUID) -> Animal:
    # RLS already scopes this query to the caller's tenant; a guessed UUID
    # belonging to another tenant simply matches zero rows here, so a
    # cross-tenant probe and a truly-missing ID are indistinguishable.
    animal = db.get(Animal, animal_id)
    if animal is None or animal.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND)
    return animal


@router.post("/animals", response_model=AnimalOut, status_code=201)
def create_animal(
    payload: AnimalCreateRequest,
    tenant_context: TenantContext = Depends(require_permission("ANIMALS", "create")),
    db: Session = Depends(get_tenant_db),
) -> Animal:
    if payload.farm_id and not tenant_context.has_farm_access(payload.farm_id):
        raise AppError(ErrorCode.FARM_SCOPE_DENIED)
    animal = Animal(
        tenant_id=tenant_context.tenant_id,
        farm_id=payload.farm_id,
        tag_code=payload.tag_code,
        species=payload.species,
        name=payload.name,
        attributes=payload.attributes,
        last_modified_by=tenant_context.membership_id,
    )
    db.add(animal)
    db.flush()
    return animal


@router.get("/animals", response_model=list[AnimalOut])
def list_animals(
    tenant_context: TenantContext = Depends(require_permission("ANIMALS", "read")),
    db: Session = Depends(get_tenant_db),
) -> list[Animal]:
    rows = db.execute(select(Animal).where(Animal.deleted_at.is_(None))).scalars().all()
    return [a for a in rows if a.farm_id is None or tenant_context.has_farm_access(a.farm_id)]


@router.get("/animals/{animal_id}", response_model=AnimalOut)
def get_animal(
    animal_id: uuid.UUID,
    tenant_context: TenantContext = Depends(require_permission("ANIMALS", "read")),
    db: Session = Depends(get_tenant_db),
) -> Animal:
    animal = _load_animal_or_404(db, animal_id)
    if animal.farm_id and not tenant_context.has_farm_access(animal.farm_id):
        raise AppError(ErrorCode.NOT_FOUND)
    return animal


@router.patch("/animals/{animal_id}", response_model=AnimalOut)
def update_animal(
    animal_id: uuid.UUID,
    payload: AnimalUpdateRequest,
    tenant_context: TenantContext = Depends(require_permission("ANIMALS", "update")),
    db: Session = Depends(get_tenant_db),
) -> Animal:
    animal = _load_animal_or_404(db, animal_id)
    if animal.farm_id and not tenant_context.has_farm_access(animal.farm_id):
        raise AppError(ErrorCode.NOT_FOUND)
    if animal.version != payload.expected_version:
        raise AppError(ErrorCode.SYNC_CONFLICT, "Animal was modified by someone else")

    if payload.name is not None:
        animal.name = payload.name
    if payload.attributes is not None:
        animal.attributes = payload.attributes
    animal.version += 1
    animal.last_modified_by = tenant_context.membership_id
    db.flush()
    return animal


# --- Memberships (Farm Manager self-service, bounded by entitlements) ------


@router.post("/memberships", response_model=MembershipOut, status_code=201)
def create_membership(
    payload: MembershipCreateRequest,
    tenant_context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_control_db),
) -> MembershipOut:
    if tenant_context.tenant_role not in (TenantRole.TENANT_OWNER, TenantRole.FARM_MANAGER):
        raise AppError(ErrorCode.PERMISSION_DENIED, "Only owners/managers can create employees")

    entitlements = EntitlementService(db)
    requested_modules = {perm.split(":", 1)[0] for perm in payload.permissions}
    for module_code in requested_modules:
        if not entitlements.is_module_active(tenant_context.tenant_id, module_code):
            raise AppError(
                ErrorCode.MODULE_NOT_ENTITLED,
                f"Cannot grant access to {module_code}: tenant is not entitled to this module",
            )

    user = db.execute(select(UserIdentity).where(UserIdentity.email == payload.email)).scalar_one_or_none()
    if user is None:
        user = UserIdentity(idp_subject=payload.email, email=payload.email, display_name=payload.display_name)
        db.add(user)
        db.flush()

    membership = TenantMembership(
        tenant_id=tenant_context.tenant_id,
        user_id=user.id,
        status=MembershipStatus.ACTIVE,
        tenant_role=payload.tenant_role,
        default_farm_id=payload.default_farm_id,
    )
    db.add(membership)
    try:
        db.flush()
    except IntegrityError as exc:
        raise AppError(ErrorCode.CONFLICT, "This user is already a member of this tenant") from exc

    for farm_id in payload.farm_ids:
        db.add(MembershipFarmAccess(membership_id=membership.id, farm_id=farm_id))
    for perm in payload.permissions:
        module_code, _, action = perm.partition(":")
        db.add(
            MembershipModulePermission(
                membership_id=membership.id, module_code=module_code, permission_code=action
            )
        )
    db.flush()

    record_audit_event(
        db,
        actor_id=None,
        actor_type=ActorType.TENANT_USER,
        actor_role=tenant_context.tenant_role.value,
        tenant_id=tenant_context.tenant_id,
        action="membership.created",
        entity_type="tenant_membership",
        entity_id=str(membership.id),
        after={"email": payload.email, "permissions": payload.permissions},
    )

    return MembershipOut(
        id=membership.id,
        user_id=user.id,
        tenant_role=membership.tenant_role,
        farm_ids=payload.farm_ids,
        permissions=payload.permissions,
    )
