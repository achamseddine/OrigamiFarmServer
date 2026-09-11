from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import record_audit_event
from app.auth.dependencies import get_identity, require_platform_role
from app.auth.models import UserIdentity
from app.auth.schemas import Identity
from app.backups.models import BackupJob
from app.common.db import get_control_db
from app.common.enums import (
    ActorType,
    DeviceActivationStatus,
    DeviceStatus,
    EntitlementSource,
    EntitlementStatus,
    JobStatus,
    MembershipStatus,
    PlatformRole,
    TenantStatus,
)
from app.common.errors import AppError, ErrorCode
from app.devices.models import Device, DeviceActivation, LicenseLease
from app.devices.service import generate_activation_code, hash_activation_code
from app.entitlements.state_machine import transition_entitlement, transition_tenant_status
from app.plans.models import ModuleCatalog, Plan, Subscription, TenantEntitlement
from app.platform.schemas import (
    AuditEventOut,
    DeviceActivationCreateRequest,
    DeviceActivationCreateResponse,
    DeviceOut,
    DeviceRevokeRequest,
    EntitlementActivateRequest,
    EntitlementDeactivateRequest,
    EntitlementOut,
    FarmCreateRequest,
    FarmOut,
    LicenseLeaseOut,
    MembershipInviteRequest,
    MembershipOut,
    MembershipStatusChangeRequest,
    ModuleCreateRequest,
    ModuleOut,
    PlanCreateRequest,
    PlanOut,
    PlanUpdateRequest,
    PlatformMeOut,
    SubscriptionOut,
    SubscriptionUpsertRequest,
    TenantCreateRequest,
    TenantListOut,
    TenantOut,
    TenantStatusChangeRequest,
    TenantUpdateRequest,
)
from app.tenants.models import Farm, PlatformRoleAssignment, Tenant, TenantMembership

router = APIRouter()

_STAFF = (
    PlatformRole.PLATFORM_SUPER_ADMIN,
    PlatformRole.PLATFORM_COMMERCIAL_ADMIN,
)
_STAFF_AND_SUPPORT = (*_STAFF, PlatformRole.PLATFORM_SUPPORT_ADMIN)
_ANY_PLATFORM_ROLE = (*_STAFF_AND_SUPPORT, PlatformRole.PLATFORM_AUDITOR)


# --- Session -----------------------------------------------------------


@router.get("/me", response_model=PlatformMeOut)
def platform_me(
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_control_db),
) -> PlatformMeOut:
    """Resolves the console's stored token back to an identity.

    Deliberately requires only a valid token, not a platform role: the
    console calls this on every load to decide whether it is signed in at
    all, and a signed-in user with no staff role needs to be told that
    rather than bounced back to the login screen in a loop.
    """
    roles = db.execute(
        select(PlatformRoleAssignment.platform_role).where(
            PlatformRoleAssignment.user_id == identity.user_id
        )
    ).scalars().all()
    user = db.get(UserIdentity, identity.user_id)
    return PlatformMeOut(
        user_id=identity.user_id,
        email=identity.email,
        display_name=identity.display_name,
        platform_roles=list(roles),
        password_set_by_someone_else=bool(
            user is not None and user.password_hash and user.password_changed_at is None
        ),
    )


# --- Dashboard ---------------------------------------------------------


@router.get("/dashboard/summary")
def dashboard_summary(
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> dict:
    def count_tenants(status: TenantStatus) -> int:
        return db.execute(
            select(func.count()).select_from(Tenant).where(Tenant.status == status)
        ).scalar_one()

    renewal_cutoff = datetime.now(timezone.utc) + timedelta(days=30)
    renewals_due = db.execute(
        select(func.count()).select_from(Subscription).where(
            Subscription.renews_at.is_not(None), Subscription.renews_at <= renewal_cutoff
        )
    ).scalar_one()
    active_devices = db.execute(
        select(func.count()).select_from(Device).where(Device.status == DeviceStatus.ACTIVE)
    ).scalar_one()
    backup_failures = db.execute(
        select(func.count()).select_from(BackupJob).where(BackupJob.status == JobStatus.FAILED)
    ).scalar_one()

    return {
        "active_tenants": count_tenants(TenantStatus.ACTIVE),
        "trial_tenants": count_tenants(TenantStatus.TRIAL) + count_tenants(TenantStatus.ONBOARDING),
        "suspended_tenants": count_tenants(TenantStatus.SUSPENDED),
        "active_devices": active_devices,
        "renewals_due_30d": renewals_due,
        "backup_failures": backup_failures,
    }


# --- Tenants -------------------------------------------------------------


@router.post("/tenants", response_model=TenantOut, status_code=201)
def create_tenant(
    payload: TenantCreateRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> Tenant:
    tenant = Tenant(**payload.model_dump())
    db.add(tenant)
    try:
        db.flush()
    except IntegrityError as exc:
        raise AppError(ErrorCode.CONFLICT, "company_code already exists") from exc

    record_audit_event(
        db,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        tenant_id=tenant.id,
        action="tenant.created",
        entity_type="tenant",
        entity_id=str(tenant.id),
        after={"company_code": tenant.company_code, "display_name": tenant.display_name},
    )
    return tenant


@router.get("/tenants", response_model=TenantListOut)
def list_tenants(
    q: str | None = Query(default=None, description="Search company code/name"),
    status_filter: TenantStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> TenantListOut:
    stmt = select(Tenant)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (Tenant.company_code.ilike(like))
            | (Tenant.display_name.ilike(like))
            | (Tenant.legal_name.ilike(like))
        )
    if status_filter:
        stmt = stmt.where(Tenant.status == status_filter)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    stmt = stmt.order_by(Tenant.created_at.desc()).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return TenantListOut(
        items=[TenantOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


def _get_tenant_or_404(db: Session, tenant_id: uuid.UUID) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return tenant


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
def get_tenant(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> Tenant:
    return _get_tenant_or_404(db, tenant_id)


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
def update_tenant(
    tenant_id: uuid.UUID,
    payload: TenantUpdateRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> Tenant:
    tenant = _get_tenant_or_404(db, tenant_id)
    changes = payload.model_dump(exclude_unset=True)
    before = {k: getattr(tenant, k) for k in changes}
    for key, value in changes.items():
        setattr(tenant, key, value)
    db.flush()
    if changes:
        record_audit_event(
            db,
            actor_id=identity.user_id,
            actor_type=ActorType.PLATFORM_USER,
            tenant_id=tenant.id,
            action="tenant.updated",
            entity_type="tenant",
            entity_id=str(tenant.id),
            before=_jsonable(before),
            after=_jsonable(changes),
        )
    return tenant


def _jsonable(d: dict) -> dict:
    return {k: (v.value if hasattr(v, "value") else v) for k, v in d.items()}


@router.post("/tenants/{tenant_id}/status", response_model=TenantOut)
def change_tenant_status(
    tenant_id: uuid.UUID,
    payload: TenantStatusChangeRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> Tenant:
    tenant = _get_tenant_or_404(db, tenant_id)
    if payload.status == TenantStatus.TERMINATED:
        # Irreversible/high-blast-radius: reserved for super admins only,
        # even though suspend/reactivate/grace are commercial-admin actions.
        _require_role(db, identity, PlatformRole.PLATFORM_SUPER_ADMIN)
    transition_tenant_status(
        db,
        tenant,
        payload.status,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        reason=payload.reason,
    )
    return tenant


def _require_role(db: Session, identity: Identity, role: PlatformRole) -> None:
    from app.tenants.models import PlatformRoleAssignment

    held = db.execute(
        select(PlatformRoleAssignment.platform_role).where(
            PlatformRoleAssignment.user_id == identity.user_id
        )
    ).scalars().all()
    if role.value not in held:
        raise AppError(ErrorCode.PLATFORM_ROLE_REQUIRED, f"{role.value} required for this action")


# --- Farms ---------------------------------------------------------------


@router.post("/tenants/{tenant_id}/farms", response_model=FarmOut, status_code=201)
def create_farm(
    tenant_id: uuid.UUID,
    payload: FarmCreateRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> Farm:
    _get_tenant_or_404(db, tenant_id)
    farm = Farm(tenant_id=tenant_id, **payload.model_dump())
    db.add(farm)
    try:
        db.flush()
    except IntegrityError as exc:
        raise AppError(ErrorCode.CONFLICT, "farm_code already exists for this tenant") from exc
    record_audit_event(
        db,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        tenant_id=tenant_id,
        action="farm.created",
        entity_type="farm",
        entity_id=str(farm.id),
        after={"farm_code": farm.farm_code, "name": farm.name},
    )
    return farm


@router.get("/tenants/{tenant_id}/farms", response_model=list[FarmOut])
def list_farms(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> list[Farm]:
    return list(db.execute(select(Farm).where(Farm.tenant_id == tenant_id)).scalars().all())


# --- Plans & Modules -------------------------------------------------------


@router.post("/plans", response_model=PlanOut, status_code=201)
def create_plan(
    payload: PlanCreateRequest,
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> Plan:
    plan = Plan(**payload.model_dump())
    db.add(plan)
    db.flush()
    return plan


@router.patch("/plans/{plan_id}", response_model=PlanOut)
def update_plan(
    plan_id: uuid.UUID,
    payload: PlanUpdateRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> Plan:
    """Repricing a plan is audited: it changes what every tenant on that
    plan contributes to recurring revenue, so it should never be a silent
    edit.
    """
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise AppError(ErrorCode.NOT_FOUND, "No such plan")

    changes = payload.model_dump(exclude_unset=True)
    before = {field: getattr(plan, field) for field in changes}
    for field, value in changes.items():
        setattr(plan, field, value)
    db.flush()

    if before != changes:
        record_audit_event(
            db,
            actor_id=identity.user_id,
            actor_type=ActorType.PLATFORM_USER,
            action="plan.updated",
            entity_type="plan",
            entity_id=str(plan.id),
            before=before,
            after=changes,
            summary=f"Updated plan {plan.code}",
        )
    return plan


@router.get("/plans", response_model=list[PlanOut])
def list_plans(
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> list[Plan]:
    return list(db.execute(select(Plan)).scalars().all())


@router.post("/modules", response_model=ModuleOut, status_code=201)
def create_module(
    payload: ModuleCreateRequest,
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> ModuleCatalog:
    module = ModuleCatalog(**payload.model_dump())
    db.add(module)
    try:
        db.flush()
    except IntegrityError as exc:
        raise AppError(ErrorCode.CONFLICT, "module_code already exists") from exc
    return module


@router.get("/modules", response_model=list[ModuleOut])
def list_modules(
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> list[ModuleCatalog]:
    return list(db.execute(select(ModuleCatalog)).scalars().all())


# --- Subscriptions ---------------------------------------------------------


@router.get("/tenants/{tenant_id}/subscription", response_model=SubscriptionOut | None)
def get_subscription(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> Subscription | None:
    return db.execute(select(Subscription).where(Subscription.tenant_id == tenant_id)).scalar_one_or_none()


@router.patch("/tenants/{tenant_id}/subscription", response_model=SubscriptionOut)
def upsert_subscription(
    tenant_id: uuid.UUID,
    payload: SubscriptionUpsertRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> Subscription:
    _get_tenant_or_404(db, tenant_id)
    subscription = db.execute(
        select(Subscription).where(Subscription.tenant_id == tenant_id)
    ).scalar_one_or_none()
    is_new = subscription is None
    if subscription is None:
        subscription = Subscription(tenant_id=tenant_id, **payload.model_dump())
        db.add(subscription)
    else:
        for key, value in payload.model_dump().items():
            setattr(subscription, key, value)
    db.flush()
    record_audit_event(
        db,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        tenant_id=tenant_id,
        action="subscription.created" if is_new else "subscription.updated",
        entity_type="subscription",
        entity_id=str(subscription.id),
        after={"plan_id": str(subscription.plan_id), "status": subscription.status.value},
    )
    return subscription


# --- Entitlements -----------------------------------------------------------


@router.get("/tenants/{tenant_id}/entitlements", response_model=list[EntitlementOut])
def list_entitlements(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> list[TenantEntitlement]:
    return list(
        db.execute(select(TenantEntitlement).where(TenantEntitlement.tenant_id == tenant_id))
        .scalars()
        .all()
    )


def _get_or_create_entitlement(db: Session, tenant_id: uuid.UUID, module_code: str) -> TenantEntitlement:
    module = db.get(ModuleCatalog, module_code)
    if module is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Unknown module {module_code}")
    entitlement = db.execute(
        select(TenantEntitlement).where(
            TenantEntitlement.tenant_id == tenant_id, TenantEntitlement.module_code == module_code
        )
    ).scalar_one_or_none()
    if entitlement is None:
        entitlement = TenantEntitlement(
            tenant_id=tenant_id,
            module_code=module_code,
            status=EntitlementStatus.INACTIVE,
            source=EntitlementSource.OVERRIDE,
            effective_from=datetime.now(timezone.utc),
        )
        db.add(entitlement)
        db.flush()
    return entitlement


@router.post("/tenants/{tenant_id}/entitlements/{module_code}/activate", response_model=EntitlementOut)
def activate_module(
    tenant_id: uuid.UUID,
    module_code: str,
    payload: EntitlementActivateRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> TenantEntitlement:
    _get_tenant_or_404(db, tenant_id)
    entitlement = _get_or_create_entitlement(db, tenant_id, module_code)
    new_status = EntitlementStatus.TRIAL if payload.trial else EntitlementStatus.ACTIVE
    transition_entitlement(
        db,
        entitlement,
        new_status,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        reason=payload.reason,
        effective_from=payload.effective_from or datetime.now(timezone.utc),
        effective_until=payload.effective_until,
    )
    if payload.configuration:
        entitlement.configuration = payload.configuration
        db.flush()
    return entitlement


@router.post("/tenants/{tenant_id}/entitlements/{module_code}/deactivate", response_model=EntitlementOut)
def deactivate_module(
    tenant_id: uuid.UUID,
    module_code: str,
    payload: EntitlementDeactivateRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> TenantEntitlement:
    entitlement = _get_or_create_entitlement(db, tenant_id, module_code)
    # Deactivation never deletes farm-data-plane rows — see TENANCY.md. It
    # only stops the module being served as entitled from this point on.
    transition_entitlement(
        db,
        entitlement,
        EntitlementStatus.INACTIVE,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        reason=payload.reason,
        effective_until=payload.effective_until or datetime.now(timezone.utc),
    )
    return entitlement


# --- Devices ----------------------------------------------------------------


@router.get("/tenants/{tenant_id}/devices", response_model=list[DeviceOut])
def list_devices(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_STAFF_AND_SUPPORT)),
) -> list[Device]:
    return list(db.execute(select(Device).where(Device.tenant_id == tenant_id)).scalars().all())


@router.post(
    "/tenants/{tenant_id}/device-activations",
    response_model=DeviceActivationCreateResponse,
    status_code=201,
)
def create_device_activation(
    tenant_id: uuid.UUID,
    payload: DeviceActivationCreateRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF_AND_SUPPORT)),
) -> DeviceActivationCreateResponse:
    _get_tenant_or_404(db, tenant_id)
    code = generate_activation_code()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=payload.ttl_hours)
    activation = DeviceActivation(
        tenant_id=tenant_id,
        farm_id=payload.farm_id,
        code_hash=hash_activation_code(code),
        status=DeviceActivationStatus.PENDING,
        expires_at=expires_at,
        created_by=identity.user_id,
    )
    db.add(activation)
    db.flush()
    record_audit_event(
        db,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        tenant_id=tenant_id,
        action="device_activation.created",
        entity_type="device_activation",
        entity_id=str(activation.id),
    )
    return DeviceActivationCreateResponse(
        activation_id=activation.id, activation_code=code, expires_at=expires_at
    )


@router.post("/devices/{device_id}/revoke", response_model=DeviceOut)
def revoke_device(
    device_id: uuid.UUID,
    payload: DeviceRevokeRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF_AND_SUPPORT)),
) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise AppError(ErrorCode.DEVICE_NOT_FOUND)
    device.status = DeviceStatus.REVOKED
    device.revoked_at = datetime.now(timezone.utc)
    device.revoked_by = identity.user_id
    device.revoked_reason = payload.reason
    db.flush()
    record_audit_event(
        db,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        tenant_id=device.tenant_id,
        action="device.revoked",
        entity_type="device",
        entity_id=str(device.id),
        reason=payload.reason,
    )
    return device


# --- Memberships (Tenant Owner invitation from onboarding wizard) ----------


@router.post("/tenants/{tenant_id}/memberships", response_model=MembershipOut, status_code=201)
def invite_membership(
    tenant_id: uuid.UUID,
    payload: MembershipInviteRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> TenantMembership:
    _get_tenant_or_404(db, tenant_id)
    user = db.execute(select(UserIdentity).where(UserIdentity.email == payload.email)).scalar_one_or_none()
    if user is None:
        user = UserIdentity(idp_subject=payload.email, email=payload.email, display_name=payload.display_name)
        db.add(user)
        db.flush()

    membership = TenantMembership(
        tenant_id=tenant_id,
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

    record_audit_event(
        db,
        actor_id=identity.user_id,
        actor_type=ActorType.PLATFORM_USER,
        tenant_id=tenant_id,
        action="membership.invited",
        entity_type="tenant_membership",
        entity_id=str(membership.id),
        after={"email": payload.email, "tenant_role": payload.tenant_role.value},
    )
    return _membership_out(user, membership)


def _membership_out(user: UserIdentity, membership: TenantMembership) -> MembershipOut:
    return MembershipOut(
        id=membership.id,
        tenant_id=membership.tenant_id,
        user_id=membership.user_id,
        tenant_role=membership.tenant_role,
        status=membership.status.value,
        email=user.email,
        display_name=user.display_name,
        role=membership.role,
        default_farm_id=membership.default_farm_id,
        has_password=bool(user.password_hash),
    )


@router.get("/tenants/{tenant_id}/memberships", response_model=list[MembershipOut])
def list_memberships(
    tenant_id: uuid.UUID,
    include_inactive: bool = Query(default=True),
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> list[MembershipOut]:
    """Who can reach this tenant's data, and with what role.

    This is control-plane metadata about access — it reads no farm data,
    so it needs no support session (contrast app/support/).
    """
    _get_tenant_or_404(db, tenant_id)
    stmt = (
        select(TenantMembership, UserIdentity)
        .join(UserIdentity, UserIdentity.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == tenant_id)
        .order_by(UserIdentity.email)
    )
    if not include_inactive:
        stmt = stmt.where(TenantMembership.status == MembershipStatus.ACTIVE)
    return [_membership_out(user, membership) for membership, user in db.execute(stmt).all()]


@router.post("/tenants/{tenant_id}/memberships/{membership_id}/status", response_model=MembershipOut)
def set_membership_status(
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: MembershipStatusChangeRequest,
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(require_platform_role(*_STAFF)),
) -> MembershipOut:
    """Deactivates or restores one person's access to one tenant.

    Deactivation rather than deletion, for the same reason the tenant API
    deactivates employees: their name is attached to records they entered,
    and history is never silently rewritten.
    """
    membership = db.execute(
        select(TenantMembership).where(
            TenantMembership.id == membership_id, TenantMembership.tenant_id == tenant_id
        )
    ).scalar_one_or_none()
    if membership is None:
        raise AppError(ErrorCode.NOT_FOUND, "No such membership in this tenant")

    user = db.get(UserIdentity, membership.user_id)
    assert user is not None  # FK-guaranteed

    before = membership.status
    membership.status = MembershipStatus.ACTIVE if payload.active else MembershipStatus.INACTIVE
    db.flush()

    if before != membership.status:
        record_audit_event(
            db,
            actor_id=identity.user_id,
            actor_type=ActorType.PLATFORM_USER,
            tenant_id=tenant_id,
            action="membership.activated" if payload.active else "membership.deactivated",
            entity_type="tenant_membership",
            entity_id=str(membership.id),
            reason=payload.reason,
            before={"status": before.value},
            after={"status": membership.status.value},
            summary=f"{'Restored' if payload.active else 'Suspended'} access for {user.email}",
        )
    return _membership_out(user, membership)


@router.get("/tenants/{tenant_id}/leases", response_model=list[LicenseLeaseOut])
def list_license_leases(
    tenant_id: uuid.UUID,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> list[LicenseLeaseOut]:
    """Offline license leases issued to this tenant's devices.

    What a device can still do without a network, and until when — the
    lease is verified on the device against the public key, so a lease
    already issued keeps working until its own expires_at (there is no
    revocation for one already handed out; see LICENSE_ENTITLEMENTS.md).
    """
    _get_tenant_or_404(db, tenant_id)
    rows = db.execute(
        select(LicenseLease, Device.display_name)
        .outerjoin(Device, Device.id == LicenseLease.device_id)
        .where(LicenseLease.tenant_id == tenant_id)
        .order_by(LicenseLease.issued_at.desc())
        .limit(limit)
    ).all()
    return [
        LicenseLeaseOut(
            id=lease.id,
            tenant_id=lease.tenant_id,
            device_id=lease.device_id,
            device_name=device_name,
            issued_at=lease.issued_at,
            expires_at=lease.expires_at,
            policy_version=lease.policy_version,
            modules=list(lease.modules or []),
            revoked_at=lease.revoked_at,
        )
        for lease, device_name in rows
    ]


# --- Audit -------------------------------------------------------------


@router.get("/audit-events", response_model=list[AuditEventOut])
def list_audit_events(
    tenant_id: uuid.UUID | None = None,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> list[AuditEvent]:
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
    if tenant_id:
        stmt = stmt.where(AuditEvent.tenant_id == tenant_id)
    return list(db.execute(stmt).scalars().all())
