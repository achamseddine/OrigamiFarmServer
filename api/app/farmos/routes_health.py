"""Animal health: treatments. Diagnosing and prescribing is gated to a
veterinarian or the farm owner/manager (see require_diagnostic_role) —
recording an observation (app/farmos/routes_observations.py) is not.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos.deps import (
    AccessContext,
    check_farm_id,
    get_farmos_tenant_db,
    require_diagnostic_role,
    require_permission,
)
from app.farmos.production_models import Treatment
from app.farmos.schemas import TreatmentCreate, TreatmentOut
from app.tenant_api.models import Animal

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
    )


@router.get("/health/treatments", response_model=list[TreatmentOut])
def list_treatments(
    farm_id: str = Query(...),
    access: AccessContext = Depends(require_permission("animal_health", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[TreatmentOut]:
    check_farm_id(farm_id, access)
    rows = db.execute(
        select(Treatment)
        .where(Treatment.deleted_at.is_(None))
        .order_by(Treatment.start_at.desc())
    ).scalars().all()
    return [_to_treatment_out(row) for row in rows]


@router.post("/health/treatments", response_model=TreatmentOut, status_code=201)
def record_treatment(
    payload: TreatmentCreate,
    access: AccessContext = Depends(require_permission("animal_health", "create")),
    _diagnostic: AccessContext = Depends(require_diagnostic_role),
    db: Session = Depends(get_farmos_tenant_db),
) -> TreatmentOut:
    treatment = Treatment(
        tenant_id=access.tenant_id,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        diagnosis=payload.diagnosis,
        medication=payload.medication,
        dose=payload.dose,
        route=payload.route,
        start_at=payload.start_at or datetime.now(timezone.utc),
        end_at=payload.end_at,
        withdrawal_until=payload.withdrawal_until,
        vet_id=uuid.UUID(payload.vet_id) if payload.vet_id else None,
        responsible_user_id=uuid.UUID(payload.responsible_user_id),
        cost=payload.cost,
        notes=payload.notes,
        last_modified_by=access.membership_id,
    )
    db.add(treatment)

    # RULE-WITHDRAWAL: an animal under treatment with a withdrawal period
    # carries that onto its own record, so POST /production/milk can hard-
    # block selling milk from it without re-deriving the rule from history.
    if payload.entity_type == "animal" and payload.withdrawal_until is not None:
        try:
            animal_id = uuid.UUID(payload.entity_id)
        except ValueError:
            animal_id = None
        if animal_id is not None:
            animal = db.get(Animal, animal_id)
            if animal is not None:
                animal.withdrawal_until = payload.withdrawal_until
                animal.withdrawal_reason = payload.diagnosis or payload.medication
                animal.version += 1
                animal.last_modified_by = access.membership_id

    db.flush()
    return _to_treatment_out(treatment)
