"""Work responsibilities: what an employee looks after and which duties that
means (Settings > Employees > Responsibilities; OrigamiFarmOS
backend/app/services/responsibility_service.py).

A responsibility is a scope — one animal, a group, every animal of a
species, or a field — and its duties: milking (and the milk record),
feeding (and the feeding record), observations, health care, egg
collection, crop care, harvest. It does two things:

- routes the work: the employee's day lists their animals and what is still
  to record today (`of_user` with the animals in scope);
- grants the module access the duties need, as ordinary
  `membership_module_permission` rows (view + create + edit) when the
  employee does not hold that module yet — never a hidden second permission
  system, and never removed silently when the responsibility ends (the
  manager revokes access on the same screen).

Ended, never deleted, so "who looked after the dairy herd in March" stays
answerable. The responsibility rows are tenant data (RLS); the employee and
the access they are granted live in the control database, so the service
takes both sessions.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.auth.models import UserIdentity
from app.common.enums import ActorType, MembershipStatus
from app.farmos import group_service, livestock_catalog
from app.farmos.deps import AccessContext, is_full_access_role
from app.farmos.feed_catalog import FeedError
from app.farmos.livestock_models import AnimalGroup
from app.farmos.livestock_service import now_utc
from app.farmos.permissions import DEFAULT_RESPONSIBILITY_GRANT
from app.farmos.responsibility_models import WorkResponsibility
from app.tenant_api.models import Animal, Field
from app.tenants.models import MembershipModulePermission, TenantMembership

# duty -> the module whose access it needs
DUTIES: dict[str, str] = {
    "milking": "milk_production",
    "feeding": "feed_nutrition",
    "observation": "animals",
    "health_care": "animal_health",
    "egg_collection": "egg_production",
    "crop_care": "agriculture",
    "harvest": "produce_harvest",
}
ANIMAL_DUTIES = ("milking", "feeding", "observation", "health_care", "egg_collection")
FIELD_DUTIES = ("crop_care", "harvest", "observation")
SCOPE_TYPES = ("animal", "group", "species", "field")


class ResponsibilityError(FeedError):
    """A refusal said as a sentence (→ 422 / 404 / 409 like the feed errors)."""


def _parse(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _scope(db: Session, scope_type: str, scope_id: str) -> Any:
    """The row (or species code) a responsibility is about, on this farm —
    RLS hides another farm's rows, so a guessed id answers like a missing one."""
    if scope_type not in SCOPE_TYPES:
        raise ResponsibilityError(f"scope_type must be one of {list(SCOPE_TYPES)}")
    if scope_type == "species":
        if scope_id not in livestock_catalog.SPECIES_BY_CODE:
            raise ResponsibilityError(f"'{scope_id}' is not a species this farm keeps.")
        return scope_id
    pk = _parse(scope_id)
    row: Animal | AnimalGroup | Field | None
    if scope_type == "animal":
        row, label = (db.get(Animal, pk) if pk else None), "Animal"
    elif scope_type == "group":
        row, label = (db.get(AnimalGroup, pk) if pk else None), "Group"
    else:
        row, label = (db.get(Field, pk) if pk else None), "Field"
    if row is None or row.deleted_at is not None:
        raise ResponsibilityError(f"{label} not found", 404)
    return row


def scope_name(db: Session, r: WorkResponsibility) -> str:
    try:
        target = _scope(db, r.scope_type, r.scope_id)
    except ResponsibilityError:
        return r.scope_id
    if r.scope_type == "species":
        return str(livestock_catalog.SPECIES_BY_CODE[target]["name_en"])
    return str(getattr(target, "name", r.scope_id))


def animals_in_scope(db: Session, r: WorkResponsibility) -> list[Animal]:
    """The animals a responsibility covers today — the group's animals,
    every animal of the species, the one animal — on the farm.

    A group's animals are its current members (stage 10 memberships,
    LIVESTOCK-SCHEMA §7), on the farm.
    """
    if r.scope_type == "animal":
        pk = _parse(r.scope_id)
        a = db.get(Animal, pk) if pk else None
        return [a] if a is not None and a.active and a.deleted_at is None else []
    if r.scope_type == "group":
        pk = _parse(r.scope_id)
        group = db.get(AnimalGroup, pk) if pk else None
        if group is None or group.deleted_at is not None:
            return []
        members = [
            a for _m, a in group_service.current_members(db, group) if a.active and a.deleted_at is None
        ]
        return sorted(members, key=lambda a: a.name or "")
    if r.scope_type == "species":
        return list(
            db.scalars(
                select(Animal)
                .where(Animal.species == r.scope_id, Animal.active.is_(True), Animal.deleted_at.is_(None))
                .order_by(Animal.name)
            )
        )
    return []


def _ensure_module_access(
    control_db: Session,
    access: AccessContext,
    membership: TenantMembership,
    employee: UserIdentity,
    duties: list[str],
    scope_type: str,
) -> list[str]:
    """The access the duties need, added where the employee lacks it: a
    permission row per (module, action) of the default grant, on the
    employee's membership — the same rows `PUT /employees/{id}/permissions`
    writes, and shown on the same screen."""
    if is_full_access_role(membership.role):
        return []
    needed = {DUTIES[d] for d in duties}
    if scope_type in ("animal", "group", "species"):
        needed.add("animals")
    held = set(
        control_db.scalars(
            select(MembershipModulePermission.module_code).where(
                MembershipModulePermission.membership_id == membership.id
            )
        )
    )
    added = []
    for code in sorted(needed - held):
        for action in DEFAULT_RESPONSIBILITY_GRANT:
            control_db.add(
                MembershipModulePermission(
                    membership_id=membership.id, module_code=code, permission_code=action
                )
            )
        added.append(code)
    if added:
        control_db.flush()
        record_audit_event(
            control_db,
            actor_id=access.user_id,
            actor_type=ActorType.TENANT_USER,
            actor_role=access.role,
            tenant_id=access.tenant_id,
            action="employee.permissions_granted_by_responsibility",
            entity_type="employee",
            entity_id=str(employee.id),
            module_code="employees",
            summary=(
                f"{access.display_name} gave {employee.display_name} access to {', '.join(added)} "
                "for their new duties"
            ),
            metadata={"modules": added, "actions": list(DEFAULT_RESPONSIBILITY_GRANT)},
        )
    return added


def assign(
    db: Session,
    control_db: Session,
    access: AccessContext,
    *,
    membership: TenantMembership,
    employee: UserIdentity,
    scope_type: str,
    scope_id: str,
    duties: list[str],
    notes: str | None,
) -> tuple[WorkResponsibility, list[str]]:
    if membership.status != MembershipStatus.ACTIVE:
        raise ResponsibilityError(f"{employee.display_name} is not active.")
    duties = sorted({d.strip().lower() for d in duties if d and d.strip()})
    if not duties:
        raise ResponsibilityError("Choose at least one duty (milking, feeding, observations…).")
    unknown = [d for d in duties if d not in DUTIES]
    if unknown:
        raise ResponsibilityError(f"Unknown duty: {', '.join(unknown)}. Duties are {', '.join(DUTIES)}.")
    allowed = FIELD_DUTIES if scope_type == "field" else ANIMAL_DUTIES
    wrong = [d for d in duties if d not in allowed]
    if wrong:
        raise ResponsibilityError(
            f"{', '.join(w.replace('_', ' ') for w in wrong)}: not a duty for "
            f"{'a field' if scope_type == 'field' else 'animals'}."
        )
    _scope(db, scope_type, scope_id)
    existing = db.scalar(
        select(WorkResponsibility).where(
            WorkResponsibility.user_id == employee.id,
            WorkResponsibility.scope_type == scope_type,
            WorkResponsibility.scope_id == scope_id,
            WorkResponsibility.status == "active",
            WorkResponsibility.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise ResponsibilityError(
            f"{employee.display_name} already looks after this; "
            "end that responsibility to change its duties.",
            409,
        )
    row = WorkResponsibility(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        user_id=employee.id,
        scope_type=scope_type,
        scope_id=scope_id,
        duties=duties,
        status="active",
        valid_from=now_utc(),
        notes=(notes or "").strip() or None,
        created_by=access.user_id,
    )
    db.add(row)
    db.flush()
    added = _ensure_module_access(control_db, access, membership, employee, duties, scope_type)
    name = scope_name(db, row)
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action="responsibility.assigned",
        entity_type="employee",
        entity_id=str(employee.id),
        module_code="employees",
        summary=(
            f"{access.display_name} made {employee.display_name} responsible for {name}: {', '.join(duties)}"
        ),
        metadata={
            "responsibility_id": str(row.id),
            "scope_type": scope_type,
            "scope_id": scope_id,
            "duties": duties,
            "modules_granted": added,
        },
    )
    return row, added


def end(
    db: Session,
    control_db: Session,
    access: AccessContext,
    row: WorkResponsibility,
    *,
    reason: str | None = None,
) -> WorkResponsibility:
    if row.status != "active":
        return row
    row.status = "ended"
    row.valid_to = now_utc()
    row.version += 1
    row.last_modified_by = access.membership_id
    db.flush()
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action="responsibility.ended",
        entity_type="employee",
        entity_id=str(row.user_id),
        module_code="employees",
        summary=f"{access.display_name} ended a responsibility for {scope_name(db, row)}"
        + (f": {reason}" if reason else ""),
        metadata={"responsibility_id": str(row.id), "reason": reason},
    )
    return row


def to_dict(db: Session, r: WorkResponsibility, *, with_animals: bool = False) -> dict:
    out: dict[str, Any] = {
        "id": str(r.id),
        "user_id": str(r.user_id),
        "scope_type": r.scope_type,
        "scope_id": r.scope_id,
        "scope_name": scope_name(db, r),
        "duties": list(r.duties or []),
        "status": r.status,
        "valid_from": r.valid_from,
        "valid_to": r.valid_to,
        "notes": r.notes,
    }
    if with_animals:
        out["animals"] = [
            {
                "id": str(a.id),
                "name": a.name,
                "tag": a.tag,
                "species": a.species,
                "lactating": bool(a.lactating),
            }
            for a in animals_in_scope(db, r)
        ]
    return out


def of_user(db: Session, user_id: uuid.UUID, *, active_only: bool = True) -> list[WorkResponsibility]:
    stmt = select(WorkResponsibility).where(
        WorkResponsibility.user_id == user_id, WorkResponsibility.deleted_at.is_(None)
    )
    if active_only:
        stmt = stmt.where(WorkResponsibility.status == "active")
    return list(db.scalars(stmt.order_by(WorkResponsibility.created_at)))


def get_or_404(db: Session, responsibility_id: str, user_id: uuid.UUID) -> WorkResponsibility:
    pk = _parse(responsibility_id)
    row = db.get(WorkResponsibility, pk) if pk else None
    if row is None or row.deleted_at is not None or row.user_id != user_id:
        raise ResponsibilityError("Responsibility not found", 404)
    return row
