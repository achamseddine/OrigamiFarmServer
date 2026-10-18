"""Health cases, diagnoses and treatment plans (OrigamiFarmOS
database/HEALTH-VETERINARY-SCHEMA.md).

Anyone with animal-health view reads. Opening, updating and closing a
case needs animal-health create / edit. A diagnosis and a plan are
clinical: a veterinarian or manager (CONSTITUTION: veterinarians diagnose
and prescribe). The animal's conditions and weights are served by
app/farmos/routes_livestock.py since stage 6.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import health_case_service as cases
from app.farmos.deps import AccessContext, get_farmos_tenant_db, require_diagnostic_role, require_permission
from app.farmos.emergency_schemas import (
    DiagnosisIn,
    HealthCaseClose,
    HealthCaseIn,
    HealthCasePatch,
    TreatmentPlanIn,
)
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.health_case_models import HealthCase
from app.farmos.routing_service import NameCache

router = APIRouter()

_view = require_permission("animal_health", "view")
_create = require_permission("animal_health", "create")
_edit = require_permission("animal_health", "edit")


def _audit(
    control_db: Session,
    access: AccessContext,
    *,
    action: str,
    entity_id: str,
    summary: str,
    changes: dict | None = None,
) -> None:
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action=action,
        entity_type="health_case",
        entity_id=entity_id,
        module_code="animal_health",
        summary=summary,
        changes=changes,
    )


@router.get("/health/cases")
def list_cases(
    case_status: str | None = Query("active", alias="status"),
    subject_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    """`status=active` (default) is open and monitoring; `all` includes
    closed cases."""
    stmt = select(HealthCase).where(HealthCase.deleted_at.is_(None))
    if case_status == "active":
        stmt = stmt.where(HealthCase.status.in_(cases.OPEN_STATUSES))
    elif case_status and case_status != "all":
        stmt = stmt.where(HealthCase.status == case_status)
    if subject_id:
        pk = parse_uuid(subject_id)
        if pk is None:
            return []
        stmt = stmt.where((HealthCase.animal_id == pk) | (HealthCase.animal_group_id == pk))
    names = NameCache()
    rows = db.scalars(stmt.order_by(HealthCase.opened_at.desc()).limit(limit))
    return [cases.case_dict(db, c, names, with_details=False) for c in rows]


@router.post("/health/cases", status_code=201)
def open_case(
    payload: HealthCaseIn,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    c = cases.open_case(db, access, source_type="manual", **payload.model_dump())
    out = cases.case_dict(db, c, NameCache())
    _audit(
        control_db,
        access,
        action="health_case.opened",
        entity_id=str(c.id),
        summary=f"Opened health case {c.case_code} for {out['subject_type']} "
        f"{out['subject_name'] or out['subject_id']}" + (f" ({c.severity_code})" if c.severity_code else ""),
        changes={"severity": c.severity_code, "problem": c.primary_problem_code, "source": "manual"},
    )
    return out


@router.get("/health/cases/{case_id}")
def get_case(
    case_id: str, access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return cases.case_dict(db, cases.get_case(db, case_id), NameCache())


@router.patch("/health/cases/{case_id}")
def update_case(
    case_id: str,
    payload: HealthCasePatch,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    c = cases.update_case(db, access, cases.get_case(db, case_id), changes)
    _audit(
        control_db,
        access,
        action="health_case.updated",
        entity_id=str(c.id),
        summary=f"Updated health case {c.case_code}",
        changes={"changed": sorted(changes)},
    )
    return cases.case_dict(db, c, NameCache())


@router.post("/health/cases/{case_id}/close")
def close_case(
    case_id: str,
    payload: HealthCaseClose,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    c = cases.close_case(
        db, access, cases.get_case(db, case_id), outcome=payload.outcome, status=payload.status
    )
    _audit(
        control_db,
        access,
        action="health_case.closed",
        entity_id=str(c.id),
        summary=f"Closed health case {c.case_code} ({c.status}): {c.outcome}",
        changes={"status": c.status, "outcome": c.outcome},
    )
    return cases.case_dict(db, c, NameCache())


@router.post("/health/cases/{case_id}/diagnoses", status_code=201)
def record_diagnosis(
    case_id: str,
    payload: DiagnosisIn,
    access: AccessContext = Depends(require_diagnostic_role),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """A diagnosis with its certainty, by a diagnostic role. The AI never
    writes one."""
    c = cases.get_case(db, case_id)
    d = cases.record_diagnosis(db, access, c, **payload.model_dump())
    _audit(
        control_db,
        access,
        action="diagnosis.recorded",
        entity_id=str(c.id),
        summary=f"Diagnosis on {c.case_code}: {d.diagnosis_code} ({d.certainty_code})",
        changes={
            "diagnosis_id": str(d.id),
            "code": d.diagnosis_code,
            "certainty": d.certainty_code,
            "supersedes": payload.supersedes_diagnosis_id,
        },
    )
    return cases.diagnosis_dict(d, NameCache())


@router.post("/health/cases/{case_id}/plans", status_code=201)
def prescribe_plan(
    case_id: str,
    payload: TreatmentPlanIn,
    access: AccessContext = Depends(require_diagnostic_role),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    c = cases.get_case(db, case_id)
    cases.require_diagnostic(access, "prescribe a treatment plan")
    p = cases.prescribe_plan(db, access, c, instructions=payload.instructions)
    _audit(
        control_db,
        access,
        action="treatment_plan.prescribed",
        entity_id=str(c.id),
        summary=f"Treatment plan prescribed on {c.case_code}",
        changes={"plan_id": str(p.id)},
    )
    return cases.plan_dict(db, p, NameCache())
