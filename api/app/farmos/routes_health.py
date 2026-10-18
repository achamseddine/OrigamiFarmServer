"""Animal health: treatments. Diagnosing and prescribing is gated to a
veterinarian or the farm owner/manager (see require_diagnostic_role) —
recording an observation (app/farmos/routes_observations.py) is not.

A treatment may name the stocked medicine it prescribes
(`medicine_product_id`) and the health case / treatment plan it belongs
to; an administration under it must then be of that product
(app/farmos/pharmacy_service.py, MEDICINE-PHARMACY-SCHEMA §11).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import health_case_service as cases
from app.farmos.deps import (
    AccessContext,
    check_farm_id,
    get_farmos_tenant_db,
    require_diagnostic_role,
    require_permission,
)
from app.farmos.production_models import Treatment
from app.farmos.schemas import TreatmentCreate, TreatmentOut

router = APIRouter()


def _to_treatment_out(t: Treatment) -> TreatmentOut:
    return TreatmentOut(
        id=str(t.id),
        entity_type=t.entity_type,
        entity_id=t.entity_id,
        diagnosis=t.diagnosis,
        medication=t.medication,
        dose=t.dose,
        route=t.route,
        start_at=t.start_at,
        end_at=t.end_at,
        withdrawal_until=t.withdrawal_until,
        vet_id=str(t.vet_id) if t.vet_id else None,
        responsible_user_id=str(t.responsible_user_id),
        status=t.status,
        cost=float(t.cost) if t.cost is not None else None,
        notes=t.notes,
        medicine_product_id=str(t.medicine_product_id) if t.medicine_product_id else None,
        health_case_id=str(t.health_case_id) if t.health_case_id else None,
        treatment_plan_id=str(t.treatment_plan_id) if t.treatment_plan_id else None,
    )


@router.get("/health/treatments", response_model=list[TreatmentOut])
def list_treatments(
    farm_id: str = Query(...),
    access: AccessContext = Depends(require_permission("animal_health", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[TreatmentOut]:
    check_farm_id(farm_id, access)
    rows = (
        db.execute(
            select(Treatment).where(Treatment.deleted_at.is_(None)).order_by(Treatment.start_at.desc())
        )
        .scalars()
        .all()
    )
    return [_to_treatment_out(row) for row in rows]


@router.post("/health/treatments", response_model=TreatmentOut, status_code=201)
def record_treatment(
    payload: TreatmentCreate,
    access: AccessContext = Depends(require_permission("animal_health", "create")),
    _diagnostic: AccessContext = Depends(require_diagnostic_role),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> TreatmentOut:
    treatment = cases.record_treatment(db, access, **payload.model_dump())

    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action="treatment.created",
        entity_type="treatment",
        entity_id=str(treatment.id),
        module_code="animal_health",
        summary=f"Recorded a {payload.medication} treatment for {payload.entity_type} {payload.entity_id}",
        changes={
            "medicine_product_id": payload.medicine_product_id,
            "health_case_id": str(treatment.health_case_id) if treatment.health_case_id else None,
            "withdrawal_until": payload.withdrawal_until.isoformat() if payload.withdrawal_until else None,
        },
    )

    return _to_treatment_out(treatment)
