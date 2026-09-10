"""Workers record observations; workers do not diagnose (Constitution).
ObservationCreate structurally has no diagnosis field, so this endpoint
cannot be used to smuggle one in regardless of caller role — see
app/farmos/routes_health.py for the (role-gated) place a diagnosis
actually gets recorded.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos.deps import AccessContext, check_farm_id, get_farmos_tenant_db, require_permission
from app.farmos.production_models import (
    DEFAULT_OBSERVATION_CONFIDENCE,
    OBSERVATION_QUALITY_CONFIDENCE,
    Observation,
)
from app.farmos.schemas import ObservationCreate, ObservationOut

router = APIRouter()


def _to_observation_out(o: Observation, tenant_id: uuid.UUID) -> ObservationOut:
    return ObservationOut(
        id=str(o.id),
        farm_id=str(tenant_id),
        entity_type=o.entity_type,
        entity_id=o.entity_id,
        observation_type=o.observation_type,
        quality=o.quality,
        confidence=float(o.confidence),
        value_numeric=float(o.value_numeric) if o.value_numeric is not None else None,
        value_text=o.value_text,
        unit=o.unit,
        severity=o.severity,
        observed_at=o.observed_at,
        observer_id=str(o.observer_id),
        notes=o.notes,
    )


@router.get("/observations", response_model=list[ObservationOut])
def list_observations(
    farm_id: str = Query(...),
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    days: int = Query(default=30),
    access: AccessContext = Depends(require_permission("animal_health", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[ObservationOut]:
    check_farm_id(farm_id, access)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = select(Observation).where(
        Observation.deleted_at.is_(None), Observation.observed_at >= since
    )
    if entity_type:
        stmt = stmt.where(Observation.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(Observation.entity_id == entity_id)
    rows = db.execute(stmt.order_by(Observation.observed_at.desc())).scalars().all()
    return [_to_observation_out(row, access.tenant_id) for row in rows]


@router.post("/observations", response_model=ObservationOut, status_code=201)
def create_observation(
    payload: ObservationCreate,
    access: AccessContext = Depends(require_permission("animal_health", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> ObservationOut:
    check_farm_id(payload.farm_id, access)
    confidence = OBSERVATION_QUALITY_CONFIDENCE.get(payload.quality, DEFAULT_OBSERVATION_CONFIDENCE)
    observation = Observation(
        tenant_id=access.tenant_id,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        observation_type=payload.observation_type,
        quality=payload.quality,
        confidence=confidence,
        value_numeric=payload.value_numeric,
        value_text=payload.value_text,
        unit=payload.unit,
        severity=payload.severity,
        observed_at=payload.observed_at or datetime.now(timezone.utc),
        observer_id=uuid.UUID(payload.observer_id),
        notes=payload.notes,
        last_modified_by=access.membership_id,
    )
    db.add(observation)
    db.flush()
    return _to_observation_out(observation, access.tenant_id)
