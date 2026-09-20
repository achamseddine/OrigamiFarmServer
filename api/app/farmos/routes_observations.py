"""Workers record observations; workers do not diagnose (Constitution).
ObservationCreate structurally has no diagnosis field, so this endpoint
cannot be used to smuggle one in regardless of caller role — see
app/farmos/routes_health.py for the (role-gated) place a diagnosis
actually gets recorded.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.farmos.deps import AccessContext, check_farm_id, get_farmos_tenant_db, require_permission
from app.farmos.production_models import (
    DEFAULT_OBSERVATION_CONFIDENCE,
    OBSERVATION_QUALITY_CONFIDENCE,
    Observation,
)
from app.farmos.schemas import ObservationCreate, ObservationOut

router = APIRouter()


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
    return ObservationOut(
        id=str(observation.id),
        farm_id=str(access.tenant_id),
        entity_type=observation.entity_type,
        entity_id=observation.entity_id,
        observation_type=observation.observation_type,
        quality=observation.quality,
        confidence=float(observation.confidence),
        value_numeric=float(observation.value_numeric) if observation.value_numeric is not None else None,
        value_text=observation.value_text,
        unit=observation.unit,
        severity=observation.severity,
        observed_at=observation.observed_at,
        observer_id=str(observation.observer_id),
        notes=observation.notes,
    )
