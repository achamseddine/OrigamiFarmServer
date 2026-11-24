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
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.db import get_control_db
from app.common.enums import MembershipStatus
from app.farmos import duplicates, emergency_service, livestock_state_service
from app.farmos import health_case_service as cases
from app.farmos.deps import (
    AccessContext,
    check_farm_id,
    ensure_any_permission,
    get_access_context,
    get_farmos_tenant_db,
    require_any_permission,
)
from app.farmos.livestock_models import AnimalGroup
from app.farmos.production_models import (
    DEFAULT_OBSERVATION_CONFIDENCE,
    OBSERVATION_QUALITY_CONFIDENCE,
    Observation,
)
from app.farmos.record_time import not_in_future
from app.farmos.schemas import ObservationCreate, ObservationOut
from app.tenant_api.models import Animal, Field
from app.tenants.models import TenantMembership

router = APIRouter()

# What an observation is about decides who may record it: the record
# sheet offers "observe" to Animals and Animal Health; a field belongs to
# Agriculture (and Produce & Harvest, whose screen shows the field).
_SUBJECTS: dict[str, tuple[type, str, tuple[tuple[str, str], ...]]] = {
    "animal": (Animal, "Animal", (("animals", "create"), ("animal_health", "create"))),
    "flock": (AnimalGroup, "Flock", (("animals", "create"), ("animal_health", "create"))),
    "group": (AnimalGroup, "Group", (("animals", "create"), ("animal_health", "create"))),
    "field": (Field, "Field", (("agriculture", "create"), ("produce_harvest", "create"))),
}
_OBSERVATIONS_VIEW = require_any_permission(
    ("animals", "view"), ("animal_health", "view"), ("agriculture", "view")
)


def _subject_or_404(db: Session, entity_type: str, entity_id: str) -> object:
    """The observed subject, on this farm. Another farm's row answers like a
    missing one (RLS), so an id from elsewhere reveals nothing."""
    if entity_type not in _SUBJECTS:
        raise HTTPException(status_code=422, detail=f"entity_type must be one of {sorted(_SUBJECTS)}")
    model, label, _modules = _SUBJECTS[entity_type]
    try:
        pk = uuid.UUID(entity_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"{label} not found.") from exc
    row = db.get(model, pk)
    if row is None or getattr(row, "deleted_at", None) is not None:
        raise HTTPException(status_code=404, detail=f"{label} not found.")
    return row


def _ensure_farm_user(control_db: Session, access: AccessContext, user_id: str, role: str) -> uuid.UUID:
    """A person named in a request body (the observer) must be someone on
    the caller's own farm — a record can be entered on a colleague's behalf,
    never attributed to another farm's staff."""
    try:
        pk = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"The {role} must be someone on this farm.") from exc
    if pk == access.user_id:
        return pk
    membership = control_db.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == access.tenant_id,
            TenantMembership.user_id == pk,
            TenantMembership.status == MembershipStatus.ACTIVE,
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=422, detail=f"The {role} must be someone on this farm.")
    return pk


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
    access: AccessContext = Depends(_OBSERVATIONS_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[ObservationOut]:
    check_farm_id(farm_id, access)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = select(Observation).where(Observation.deleted_at.is_(None), Observation.observed_at >= since)
    if entity_type:
        stmt = stmt.where(Observation.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(Observation.entity_id == entity_id)
    rows = db.execute(stmt.order_by(Observation.observed_at.desc())).scalars().all()
    return [_to_observation_out(row, access.tenant_id) for row in rows]


@router.post("/observations", response_model=ObservationOut, status_code=201)
def create_observation(
    payload: ObservationCreate,
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> ObservationOut:
    """The farm, the subject and the observer must all be the caller's own:
    an observation is evidence the emergency engine and the health case act
    on, so it cannot be planted on another farm's animal or in another
    farm's staff member's name. Who may record it follows the subject
    (`_SUBJECTS`), and the same observation within half an hour is asked
    about first (app/farmos/duplicates.py)."""
    check_farm_id(payload.farm_id, access)
    if payload.entity_type not in _SUBJECTS:
        raise HTTPException(status_code=422, detail=f"entity_type must be one of {sorted(_SUBJECTS)}")
    ensure_any_permission(access, *_SUBJECTS[payload.entity_type][2])
    subject = _subject_or_404(db, payload.entity_type, payload.entity_id)
    observer_id = _ensure_farm_user(control_db, access, payload.observer_id, "observer")
    not_in_future(payload.observed_at, "An observation")
    if payload.entity_type == "animal":
        # An animal that left the farm is not observed afterwards (§14).
        assert isinstance(subject, Animal)
        livestock_state_service.ensure_on_farm(subject, "an observation")
    lo, hi = duplicates.near(payload.observed_at)
    earlier = db.scalar(
        select(Observation).where(
            Observation.deleted_at.is_(None),
            Observation.entity_type == payload.entity_type,
            Observation.entity_id == payload.entity_id,
            Observation.observation_type == payload.observation_type,
            Observation.observed_at >= lo,
            Observation.observed_at <= hi,
            Observation.value_numeric.is_(None)
            if payload.value_numeric is None
            else Observation.value_numeric == payload.value_numeric,
            Observation.value_text.is_(None)
            if payload.value_text is None
            else Observation.value_text == payload.value_text,
        )
    )
    match = duplicates.guard(
        earlier,
        f"The same {payload.observation_type.replace('_', ' ')} observation was already recorded "
        "within half an hour.",
        confirm=payload.confirm_duplicate,
    )
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
        observer_id=observer_id,
        notes=payload.notes,
        last_modified_by=access.membership_id,
    )
    db.add(observation)
    db.flush()
    duplicates.log_confirmed(
        control_db,
        access,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        match_id=match.id if match else None,
        what=f"observation:{observation.id}",
        module_code="animal_health" if payload.entity_type != "field" else "agriculture",
    )
    # The observation is evidence on the subject's open health case, and a
    # severe one starts emergency triage by itself (CLINICAL-DECISION-SUPPORT
    # §12) — never a diagnosis, never a drug.
    cases.attach_observation(db, observation)
    emergency_service.on_observation(db, access, observation)
    return _to_observation_out(observation, access.tenant_id)
