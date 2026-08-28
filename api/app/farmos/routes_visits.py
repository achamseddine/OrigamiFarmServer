"""Farm Visits / agritourism: module status, activities, packages,
visitors, sessions, and the weekly opening calendar. A licensed add-on
(see app/farmos/routes_modules.py) — booking-side endpoints (bookings,
costs, incidents, staff roster, feedback, retail sales) live in
app/farmos/routes_visit_bookings.py since they all reference the entities
defined here.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.db import get_control_db
from app.farmos.deps import (
    AccessContext,
    get_access_context,
    get_farmos_tenant_db,
    require_permission,
    require_visitor_access,
)
from app.farmos.schemas import (
    OpeningCalendarDayOut,
    OpeningCalendarDayUpsert,
    VisitActivityCreate,
    VisitActivityOut,
    VisitModuleStatusOut,
    VisitorProfileCreate,
    VisitorProfileOut,
    VisitPackageCreate,
    VisitPackageOut,
    VisitSessionCreate,
    VisitSessionOut,
    VisitSessionUpdate,
)
from app.farmos.visits_models import (
    OpeningCalendarDay,
    VisitActivity,
    VisitorProfile,
    VisitPackage,
    VisitSession,
)
from app.plans.models import TenantEntitlement

router = APIRouter()


# --- Module status -----------------------------------------------------


@router.get("/modules/visits/status", response_model=VisitModuleStatusOut)
def visits_module_status(
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_control_db),
) -> VisitModuleStatusOut:
    entitlement = db.execute(
        select(TenantEntitlement).where(
            TenantEntitlement.tenant_id == access.tenant_id,
            TenantEntitlement.module_code == "visits_agritourism",
        )
    ).scalar_one_or_none()
    active = entitlement is not None and entitlement.status.value in ("ACTIVE", "TRIAL")
    return VisitModuleStatusOut(
        module_code="visits_agritourism",
        status=entitlement.status.value.lower() if entitlement else "inactive",
        active=active,
        features={"analytics": active, "pos_integration": active, "staff_costing": active},
    )


# --- Activities --------------------------------------------------------


def _to_activity_out(a: VisitActivity) -> VisitActivityOut:
    return VisitActivityOut(
        id=str(a.id),
        farm_id=str(a.tenant_id),
        name=a.name,
        activity_type=a.activity_type,
        price=float(a.price),
        capacity_per_slot=a.capacity_per_slot,
        duration_minutes=a.duration_minutes,
        requires_staff_role=a.requires_staff_role,
        requires_animal_id=a.requires_animal_id,
        welfare_limit_json=a.welfare_limit_json,
        active=a.active,
    )


@router.get("/visit-activities", response_model=list[VisitActivityOut])
def list_activities(
    _access: AccessContext = Depends(require_permission("farm_visits", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[VisitActivityOut]:
    rows = db.execute(
        select(VisitActivity).where(VisitActivity.deleted_at.is_(None)).order_by(VisitActivity.name)
    ).scalars().all()
    return [_to_activity_out(row) for row in rows]


@router.post("/visit-activities", response_model=VisitActivityOut, status_code=201)
def create_activity(
    payload: VisitActivityCreate,
    access: AccessContext = Depends(require_permission("farm_visits", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> VisitActivityOut:
    activity = VisitActivity(
        tenant_id=access.tenant_id, last_modified_by=access.membership_id, **payload.model_dump()
    )
    db.add(activity)
    db.flush()
    return _to_activity_out(activity)


# --- Packages ------------------------------------------------------------


def _to_package_out(p: VisitPackage) -> VisitPackageOut:
    return VisitPackageOut(
        id=str(p.id),
        farm_id=str(p.tenant_id),
        name=p.name,
        description=p.description,
        base_price=float(p.base_price),
        currency=p.currency,
        duration_minutes=p.duration_minutes,
        included_items_json=p.included_items_json,
        active=p.active,
    )


@router.get("/visit-packages", response_model=list[VisitPackageOut])
def list_packages(
    _access: AccessContext = Depends(require_permission("farm_visits", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[VisitPackageOut]:
    rows = db.execute(
        select(VisitPackage).where(VisitPackage.deleted_at.is_(None)).order_by(VisitPackage.name)
    ).scalars().all()
    return [_to_package_out(row) for row in rows]


@router.post("/visit-packages", response_model=VisitPackageOut, status_code=201)
def create_package(
    payload: VisitPackageCreate,
    access: AccessContext = Depends(require_permission("farm_visits", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> VisitPackageOut:
    package = VisitPackage(
        tenant_id=access.tenant_id, last_modified_by=access.membership_id, **payload.model_dump()
    )
    db.add(package)
    db.flush()
    return _to_package_out(package)


# --- Visitors (RULE-VIS-010: gated beyond the normal permission grid) ----


def to_visitor_out(v: VisitorProfile) -> VisitorProfileOut:
    return VisitorProfileOut(
        id=str(v.id),
        farm_id=str(v.tenant_id),
        full_name=v.full_name,
        phone=v.phone,
        email=v.email,
        preferred_language=v.preferred_language,
        notes=v.notes,
        consent_marketing=v.consent_marketing,
    )


@router.get("/visitors", response_model=list[VisitorProfileOut])
def list_visitors(
    _access: AccessContext = Depends(require_visitor_access),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[VisitorProfileOut]:
    rows = db.execute(
        select(VisitorProfile).where(VisitorProfile.deleted_at.is_(None)).order_by(VisitorProfile.full_name)
    ).scalars().all()
    return [to_visitor_out(row) for row in rows]


@router.post("/visitors", response_model=VisitorProfileOut, status_code=201)
def create_visitor(
    payload: VisitorProfileCreate,
    access: AccessContext = Depends(require_visitor_access),
    db: Session = Depends(get_farmos_tenant_db),
) -> VisitorProfileOut:
    return create_visitor_row(db, access, payload)


def create_visitor_row(
    db: Session, access: AccessContext, payload: VisitorProfileCreate
) -> VisitorProfileOut:
    """Shared with booking creation (POST /visit-bookings), which can
    create a visitor inline rather than requiring a separate call first.
    """
    visitor = VisitorProfile(
        tenant_id=access.tenant_id, last_modified_by=access.membership_id, **payload.model_dump()
    )
    db.add(visitor)
    db.flush()
    return to_visitor_out(visitor)


# --- Sessions ------------------------------------------------------------


def to_session_out(s: VisitSession) -> VisitSessionOut:
    return VisitSessionOut(
        id=str(s.id),
        farm_id=str(s.tenant_id),
        date=s.date.isoformat(),
        start_time=s.start_time,
        end_time=s.end_time,
        capacity=s.capacity,
        status=s.status,
        weather_note=s.weather_note,
        expected_staff_cost=float(s.expected_staff_cost) if s.expected_staff_cost is not None else None,
    )


@router.get("/visit-sessions", response_model=list[VisitSessionOut])
def list_sessions(
    date_from: str | None = None,
    date_to: str | None = None,
    _access: AccessContext = Depends(require_permission("farm_visits", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[VisitSessionOut]:
    stmt = select(VisitSession).where(VisitSession.deleted_at.is_(None))
    if date_from:
        stmt = stmt.where(VisitSession.date >= date_type.fromisoformat(date_from))
    if date_to:
        stmt = stmt.where(VisitSession.date <= date_type.fromisoformat(date_to))
    rows = db.execute(stmt.order_by(VisitSession.date, VisitSession.start_time)).scalars().all()
    return [to_session_out(row) for row in rows]


@router.post("/visit-sessions", response_model=VisitSessionOut, status_code=201)
def create_session(
    payload: VisitSessionCreate,
    access: AccessContext = Depends(require_permission("farm_visits", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> VisitSessionOut:
    session_row = VisitSession(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        date=date_type.fromisoformat(payload.date),
        start_time=payload.start_time,
        end_time=payload.end_time,
        capacity=payload.capacity,
        weather_note=payload.weather_note,
        expected_staff_cost=payload.expected_staff_cost,
    )
    db.add(session_row)
    db.flush()
    return to_session_out(session_row)


@router.patch("/visit-sessions/{session_id}", response_model=VisitSessionOut)
def update_session(
    session_id: str,
    payload: VisitSessionUpdate,
    access: AccessContext = Depends(require_permission("farm_visits", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
) -> VisitSessionOut:
    """RULE-VIS-009: the manager can close a session (weather, safety,
    staffing, private event) by setting status='closed'/'cancelled'.
    """
    try:
        pk = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Session not found.") from exc
    session_row = db.get(VisitSession, pk)
    if session_row is None or session_row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Session not found.")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(session_row, key, value)
    session_row.version += 1
    session_row.last_modified_by = access.membership_id
    db.flush()
    return to_session_out(session_row)


# --- Opening calendar ------------------------------------------------------


def _to_calendar_out(c: OpeningCalendarDay) -> OpeningCalendarDayOut:
    return OpeningCalendarDayOut(
        id=str(c.id),
        weekday=c.weekday,
        is_open=c.is_open,
        open_time=c.open_time,
        close_time=c.close_time,
        default_capacity=c.default_capacity,
        notes=c.notes,
    )


@router.get("/visit-calendar", response_model=list[OpeningCalendarDayOut])
def list_opening_calendar(
    _access: AccessContext = Depends(require_permission("farm_visits", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[OpeningCalendarDayOut]:
    rows = db.execute(
        select(OpeningCalendarDay)
        .where(OpeningCalendarDay.deleted_at.is_(None))
        .order_by(OpeningCalendarDay.weekday)
    ).scalars().all()
    return [_to_calendar_out(row) for row in rows]


@router.post("/visit-calendar", response_model=OpeningCalendarDayOut, status_code=201)
def upsert_opening_calendar_day(
    payload: OpeningCalendarDayUpsert,
    access: AccessContext = Depends(require_permission("farm_visits", "configure")),
    db: Session = Depends(get_farmos_tenant_db),
) -> OpeningCalendarDayOut:
    existing = db.execute(
        select(OpeningCalendarDay).where(
            OpeningCalendarDay.deleted_at.is_(None), OpeningCalendarDay.weekday == payload.weekday
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = OpeningCalendarDay(
            tenant_id=access.tenant_id, last_modified_by=access.membership_id, weekday=payload.weekday
        )
        db.add(existing)
    existing.is_open = payload.is_open
    existing.open_time = payload.open_time
    existing.close_time = payload.close_time
    existing.default_capacity = payload.default_capacity
    existing.notes = payload.notes
    existing.version += 1
    existing.last_modified_by = access.membership_id
    db.flush()
    return _to_calendar_out(existing)
