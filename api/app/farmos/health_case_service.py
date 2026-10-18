"""Health cases, diagnoses, treatment plans and treatments
(OrigamiFarmOS database/HEALTH-VETERINARY-SCHEMA.md).

The boundary the schema insists on, enforced here: a worker's observation
never becomes a diagnosis; a diagnosis is recorded by a diagnostic role
(manager, owner, veterinarian) with a certainty; a treatment plan is the
prescription frame; an administration is the pharmacy's fact. A case is
the episode they all hang on — opened by a person or by the emergency
engine, closed with an outcome, never deleted.

Every query runs in the tenant-scoped (RLS) session, so "this farm" needs
no extra filter. People's names come from the control database through a
NameCache the router hands in.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.farmos.deps import AccessContext
from app.farmos.feed_catalog import FeedError
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.health_case_models import CASE_SEVERITIES, CERTAINTIES, Diagnosis, HealthCase, TreatmentPlan
from app.farmos.livestock_models import AnimalGroup
from app.farmos.livestock_service import ensure_utc, now_utc
from app.farmos.production_models import Observation, Treatment
from app.farmos.routing_service import NameCache
from app.tenant_api.models import Animal

OPEN_STATUSES = ("open", "monitoring")
GROUP_ENTITY_TYPES = ("flock", "group")


class HealthError(FeedError):
    """A clinical-boundary rule refused the operation."""


# ---------------------------------------------------------------- helpers
def subject_or_404(db: Session, subject_type: str, subject_id: str | uuid.UUID | None) -> Any:
    if subject_type not in ("animal", "group"):
        raise HealthError("subject_type must be animal or group")
    pk = parse_uuid(subject_id)
    row: Any = None
    if pk is not None:
        row = db.get(Animal, pk) if subject_type == "animal" else db.get(AnimalGroup, pk)
    if row is None or row.deleted_at is not None:
        raise HealthError("Subject not found", 404)
    return row


def subject_type_of(entity_type: str) -> str:
    return "animal" if entity_type == "animal" else "group"


def require_diagnostic(access: AccessContext, what: str) -> None:
    if not access.full_access and access.role != "veterinarian":
        raise HealthError(
            f"Only a veterinarian or farm manager can {what}. Workers record observations.", 403
        )


def get_case(db: Session, case_id: str | uuid.UUID | None) -> HealthCase:
    pk = parse_uuid(case_id)
    c = db.get(HealthCase, pk) if pk else None
    if c is None or c.deleted_at is not None:
        raise HealthError("Health case not found", 404)
    return c


def _subject_filter(subject_type: str, subject_id: uuid.UUID):
    if subject_type == "animal":
        return HealthCase.animal_id == subject_id
    return HealthCase.animal_group_id == subject_id


def current_case(db: Session, subject_type: str, subject_id: uuid.UUID) -> HealthCase | None:
    return db.scalar(
        select(HealthCase)
        .where(_subject_filter(subject_type, subject_id), HealthCase.status.in_(OPEN_STATUSES))
        .order_by(HealthCase.opened_at.desc())
        .limit(1)
    )


def next_case_code(db: Session) -> str:
    n = (db.scalar(select(func.count(HealthCase.id))) or 0) + 1
    while db.scalar(select(HealthCase.id).where(HealthCase.case_code == f"HC-{n:06d}")):
        n += 1
    return f"HC-{n:06d}"


def severity_rank(code: str | None) -> int:
    return CASE_SEVERITIES.index(code) if code in CASE_SEVERITIES else -1


# ------------------------------------------------------------------ cases
def open_case(
    db: Session,
    access: AccessContext,
    *,
    subject_type: str,
    subject_id: str | uuid.UUID,
    severity_code: str | None = None,
    primary_problem_code: str | None = None,
    responsible_user_id: str | None = None,
    source_type: str = "manual",
    source_id: str | None = None,
    notes: str | None = None,
    opened_at: datetime | None = None,
) -> HealthCase:
    subject = subject_or_404(db, subject_type, subject_id)
    if severity_code is not None and severity_code not in CASE_SEVERITIES:
        raise HealthError(f"severity_code must be one of {list(CASE_SEVERITIES)}")
    responsible = parse_uuid(responsible_user_id) if responsible_user_id else None
    if responsible_user_id and responsible is None:
        raise HealthError("responsible_user_id must be a user id.")
    case = HealthCase(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        animal_id=subject.id if subject_type == "animal" else None,
        animal_group_id=subject.id if subject_type == "group" else None,
        case_code=next_case_code(db),
        opened_at=ensure_utc(opened_at) or now_utc(),
        status="open",
        severity_code=severity_code,
        primary_problem_code=primary_problem_code,
        responsible_user_id=responsible,
        source_type=source_type,
        source_id=source_id,
        notes=notes,
        created_by=access.user_id,
    )
    db.add(case)
    db.flush()
    # Observations of the last day on this subject join the case.
    since = case.opened_at - timedelta(hours=24)
    entity_types = ("animal",) if subject_type == "animal" else GROUP_ENTITY_TYPES
    for o in db.scalars(
        select(Observation).where(
            Observation.entity_type.in_(entity_types),
            Observation.entity_id == str(subject.id),
            Observation.observed_at >= since,
            Observation.health_case_id.is_(None),
            Observation.deleted_at.is_(None),
        )
    ):
        o.health_case_id = case.id
    if getattr(subject, "status", None) == "healthy":
        subject.status = "under_observation"
    return case


def open_or_current_case(
    db: Session, access: AccessContext, *, subject_type: str, subject_id: uuid.UUID, **fields: Any
) -> HealthCase:
    """The open case for the subject, or a new one — the emergency engine
    never opens a twin for the same animal."""
    existing = current_case(db, subject_type, subject_id)
    if existing is not None:
        wanted = fields.get("severity_code")
        if wanted and severity_rank(wanted) > severity_rank(existing.severity_code):
            existing.severity_code = wanted
        return existing
    return open_case(db, access, subject_type=subject_type, subject_id=subject_id, **fields)


def update_case(db: Session, access: AccessContext, case: HealthCase, changes: dict) -> HealthCase:
    if case.status in ("resolved", "closed"):
        raise HealthError("This case is closed; reopen it with a new case.")
    for k in ("severity_code", "primary_problem_code", "notes"):
        if k in changes:
            setattr(case, k, changes[k])
    if "responsible_user_id" in changes:
        value = changes["responsible_user_id"]
        case.responsible_user_id = parse_uuid(value) if value else None
    if changes.get("status") in OPEN_STATUSES:
        case.status = changes["status"]
    if case.severity_code is not None and case.severity_code not in CASE_SEVERITIES:
        raise HealthError(f"severity_code must be one of {list(CASE_SEVERITIES)}")
    case.last_modified_by = access.membership_id
    case.version += 1
    return case


def close_case(
    db: Session, access: AccessContext, case: HealthCase, *, outcome: str, status: str = "resolved"
) -> HealthCase:
    if case.status in ("resolved", "closed"):
        return case
    if status not in ("resolved", "closed"):
        raise HealthError("A case closes as resolved or closed.")
    if not outcome or not outcome.strip():
        raise HealthError("Say how the case ended.")
    at = now_utc()
    case.status = status
    case.closed_at = at
    case.outcome = outcome.strip()
    case.last_modified_by = access.membership_id
    case.version += 1
    for d in case.diagnoses:
        if d.status == "active":
            d.status = "resolved"
    for p in case.plans:
        if p.status in ("prescribed", "active"):
            p.status = "completed"
            p.completed_at = at
    return case


# ------------------------------------------------------------- diagnoses
def record_diagnosis(
    db: Session,
    access: AccessContext,
    case: HealthCase,
    *,
    diagnosis_code: str,
    diagnosis_text: str | None = None,
    certainty_code: str = "suspected",
    supersedes_diagnosis_id: str | None = None,
    notes: str | None = None,
    diagnosed_at: datetime | None = None,
) -> Diagnosis:
    """Diagnosis is clinical (CONSTITUTION: veterinarians diagnose and
    prescribe). The AI never calls this; a person with a diagnostic role
    does, with a certainty."""
    require_diagnostic(access, "record a diagnosis")
    if case.status in ("resolved", "closed"):
        raise HealthError("This case is closed.")
    if certainty_code not in CERTAINTIES:
        raise HealthError(f"certainty_code must be one of {list(CERTAINTIES)}")
    if not diagnosis_code or not diagnosis_code.strip():
        raise HealthError("A diagnosis code is required.")
    previous_id = None
    if supersedes_diagnosis_id:
        prev_pk = parse_uuid(supersedes_diagnosis_id)
        previous = db.get(Diagnosis, prev_pk) if prev_pk else None
        if previous is None or previous.health_case_id != case.id:
            raise HealthError("The superseded diagnosis is not on this case.", 404)
        previous.status = "superseded"
        previous_id = previous.id
    d = Diagnosis(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        health_case_id=case.id,
        diagnosis_code=diagnosis_code.strip(),
        diagnosis_text=diagnosis_text,
        certainty_code=certainty_code,
        diagnosed_at=ensure_utc(diagnosed_at) or now_utc(),
        diagnosed_by=access.user_id,
        status="active" if certainty_code != "ruled_out" else "resolved",
        supersedes_diagnosis_id=previous_id,
        notes=notes,
    )
    db.add(d)
    db.flush()
    db.expire(case, ["diagnoses"])
    if case.primary_problem_code is None and certainty_code != "ruled_out":
        case.primary_problem_code = d.diagnosis_code
    return d


def active_diagnosis_codes(db: Session, subject_type: str, subject_id: uuid.UUID) -> list[str]:
    rows = db.scalars(
        select(Diagnosis)
        .join(HealthCase, HealthCase.id == Diagnosis.health_case_id)
        .where(
            _subject_filter(subject_type, subject_id),
            HealthCase.status.in_(OPEN_STATUSES),
            Diagnosis.status == "active",
        )
    ).all()
    return sorted({r.diagnosis_code.lower() for r in rows})


# ----------------------------------------------------------------- plans
def prescribe_plan(
    db: Session,
    access: AccessContext,
    case: HealthCase,
    *,
    instructions: str | None,
    source_type: str | None = None,
    source_id: str | None = None,
    prescribed_by: uuid.UUID | None = None,
    status: str = "prescribed",
) -> TreatmentPlan:
    if case.status in ("resolved", "closed"):
        raise HealthError("This case is closed.")
    p = TreatmentPlan(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        health_case_id=case.id,
        status=status,
        prescribed_at=now_utc(),
        prescribed_by=prescribed_by or access.user_id,
        instructions=instructions,
        source_type=source_type,
        source_id=source_id,
    )
    db.add(p)
    db.flush()
    db.expire(case, ["plans"])
    return p


def record_treatment(
    db: Session,
    access: AccessContext,
    *,
    entity_type: str,
    entity_id: str,
    medication: str,
    dose: str,
    route: str,
    responsible_user_id: str | uuid.UUID,
    diagnosis: str | None = None,
    medicine_product_id: str | None = None,
    health_case_id: str | None = None,
    treatment_plan_id: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    withdrawal_until: datetime | None = None,
    vet_id: str | uuid.UUID | None = None,
    cost: float | None = None,
    notes: str | None = None,
    status: str = "active",
) -> Treatment:
    """A prescription / treatment action (the schema's treatment_action).
    When the farm pharmacy stocks the medicine, the treatment names the
    product, and an administration under it must be of that product."""
    from app.farmos.pharmacy_models import (
        MedicineProduct,
    )  # the pharmacy depends on treatments, not the reverse

    subject_type = subject_type_of(entity_type)
    subject = subject_or_404(db, subject_type, entity_id)
    product_pk = None
    if medicine_product_id:
        product_pk = parse_uuid(medicine_product_id)
        product = db.get(MedicineProduct, product_pk) if product_pk else None
        if product is None or product.deleted_at is not None:
            raise HealthError("Medicine product not found in this farm's pharmacy", 404)
        if product.species_codes and subject.species not in product.species_codes:
            raise HealthError(
                f"{medication} is not authorised for {subject.species}; it is configured for "
                f"{', '.join(product.species_codes)}."
            )
    case: HealthCase | None
    if health_case_id:
        case = get_case(db, health_case_id)
        if (case.animal_id or case.animal_group_id) != subject.id:
            raise HealthError("The health case belongs to another subject.")
    else:
        case = current_case(db, subject_type, subject.id)
    plan = None
    if treatment_plan_id:
        plan_pk = parse_uuid(treatment_plan_id)
        plan = db.get(TreatmentPlan, plan_pk) if plan_pk else None
        if plan is None or (case is not None and plan.health_case_id != case.id):
            raise HealthError("The treatment plan is not on this case.", 404)
        if case is None:
            case = get_case(db, plan.health_case_id)
        if plan.status == "prescribed":
            plan.status = "active"
    responsible = parse_uuid(responsible_user_id)
    if responsible is None:
        raise HealthError("responsible_user_id must be a user id.")
    t = Treatment(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        entity_type=entity_type,
        entity_id=str(subject.id),
        diagnosis=diagnosis,
        medication=medication,
        dose=dose,
        route=route,
        start_at=start_at or now_utc(),
        end_at=end_at,
        withdrawal_until=withdrawal_until,
        vet_id=parse_uuid(vet_id) if vet_id else None,
        responsible_user_id=responsible,
        status=status,
        cost=cost,
        notes=notes,
        medicine_product_id=product_pk,
        health_case_id=case.id if case else None,
        treatment_plan_id=plan.id if plan else None,
    )
    db.add(t)
    if subject_type == "animal":
        # RULE-WITHDRAWAL: an animal under treatment with a withdrawal period
        # carries that onto its own record, so POST /production/milk can
        # hard-block selling its milk without re-deriving the rule.
        if withdrawal_until is not None:
            subject.withdrawal_until = withdrawal_until
            subject.withdrawal_reason = diagnosis or medication
        subject.status = "under_treatment"
        subject.version += 1
        subject.last_modified_by = access.membership_id
    db.flush()
    return t


def attach_observation(db: Session, observation: Observation) -> HealthCase | None:
    """A new observation lands on the subject's open case by itself —
    evidence on the episode, never a diagnosis."""
    subject_type = subject_type_of(observation.entity_type)
    pk = parse_uuid(observation.entity_id)
    if pk is None:
        return None
    case = current_case(db, subject_type, pk)
    if case is not None:
        observation.health_case_id = case.id
    return case


# ------------------------------------------------------------------ views
def _s(value: uuid.UUID | None) -> str | None:
    return str(value) if value else None


def diagnosis_dict(d: Diagnosis, names: NameCache) -> dict:
    return {
        "id": str(d.id),
        "health_case_id": str(d.health_case_id),
        "diagnosis_code": d.diagnosis_code,
        "diagnosis_text": d.diagnosis_text,
        "certainty_code": d.certainty_code,
        "diagnosed_at": d.diagnosed_at,
        "diagnosed_by": _s(d.diagnosed_by),
        "diagnosed_by_name": names.get(d.diagnosed_by),
        "status": d.status,
        "supersedes_diagnosis_id": _s(d.supersedes_diagnosis_id),
        "notes": d.notes,
    }


def treatment_dict(t: Treatment) -> dict:
    return {
        "id": str(t.id),
        "entity_type": t.entity_type,
        "entity_id": t.entity_id,
        "diagnosis": t.diagnosis,
        "medication": t.medication,
        "medicine_product_id": _s(t.medicine_product_id),
        "health_case_id": _s(t.health_case_id),
        "treatment_plan_id": _s(t.treatment_plan_id),
        "dose": t.dose,
        "route": t.route,
        "start_at": t.start_at,
        "end_at": t.end_at,
        "withdrawal_until": t.withdrawal_until,
        "vet_id": _s(t.vet_id),
        "responsible_user_id": str(t.responsible_user_id),
        "status": t.status,
        "cost": float(t.cost) if t.cost is not None else None,
        "notes": t.notes,
    }


def plan_dict(db: Session, p: TreatmentPlan, names: NameCache) -> dict:
    treatments = db.scalars(
        select(Treatment)
        .where(Treatment.treatment_plan_id == p.id, Treatment.deleted_at.is_(None))
        .order_by(Treatment.start_at)
    ).all()
    return {
        "id": str(p.id),
        "health_case_id": str(p.health_case_id),
        "status": p.status,
        "prescribed_at": p.prescribed_at,
        "prescribed_by": _s(p.prescribed_by),
        "prescribed_by_name": names.get(p.prescribed_by),
        "instructions": p.instructions,
        "source_type": p.source_type,
        "source_id": p.source_id,
        "completed_at": p.completed_at,
        "treatments": [treatment_dict(t) for t in treatments],
    }


def case_dict(db: Session, c: HealthCase, names: NameCache, *, with_details: bool = True) -> dict:
    subject_type = "animal" if c.animal_id else "group"
    subject: Any = db.get(Animal, c.animal_id) if c.animal_id else db.get(AnimalGroup, c.animal_group_id)
    out: dict[str, Any] = {
        "id": str(c.id),
        "farm_id": str(c.tenant_id),
        "case_code": c.case_code,
        "subject_type": subject_type,
        "subject_id": str(c.animal_id or c.animal_group_id),
        "subject_name": getattr(subject, "name", None),
        "species": getattr(subject, "species", None),
        "opened_at": c.opened_at,
        "closed_at": c.closed_at,
        "status": c.status,
        "severity_code": c.severity_code,
        "primary_problem_code": c.primary_problem_code,
        "responsible_user_id": _s(c.responsible_user_id),
        "responsible_user_name": names.get(c.responsible_user_id),
        "source_type": c.source_type,
        "source_id": c.source_id,
        "outcome": c.outcome,
        "notes": c.notes,
        "created_by": _s(c.created_by),
        "created_at": c.created_at,
        "active_diagnoses": [d.diagnosis_code for d in c.diagnoses if d.status == "active"],
    }
    if with_details:
        entity_types = ("animal",) if subject_type == "animal" else GROUP_ENTITY_TYPES
        observations = db.scalars(
            select(Observation)
            .where(Observation.health_case_id == c.id, Observation.deleted_at.is_(None))
            .order_by(Observation.observed_at.desc())
            .limit(30)
        ).all()
        treatments = db.scalars(
            select(Treatment)
            .where(Treatment.health_case_id == c.id, Treatment.deleted_at.is_(None))
            .order_by(Treatment.start_at.desc())
        ).all()
        out.update(
            {
                "diagnoses": [diagnosis_dict(d, names) for d in c.diagnoses],
                "plans": [plan_dict(db, p, names) for p in c.plans],
                "treatments": [treatment_dict(t) for t in treatments],
                "observations": [
                    {
                        "id": str(o.id),
                        "observation_type": o.observation_type,
                        "value_numeric": float(o.value_numeric) if o.value_numeric is not None else None,
                        "value_text": o.value_text,
                        "unit": o.unit,
                        "severity": o.severity,
                        "observed_at": o.observed_at,
                        "entity_type": o.entity_type,
                    }
                    for o in observations
                ],
                "entity_types": list(entity_types),
            }
        )
    return out
