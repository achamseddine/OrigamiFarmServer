"""Farm Visits booking flow: bookings + confirm, session costs, incidents,
staff roster, visitor feedback, and retail sales. See
app/farmos/routes_visits.py for the catalog entities (activities,
packages, visitors, sessions, calendar) these all reference.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos.deps import AccessContext, get_farmos_tenant_db, require_permission
from app.farmos.finance_models import Sale
from app.farmos.mouneh_models import FinishedGoodsStock
from app.farmos.routes_visits import create_visitor_row
from app.farmos.schemas import (
    BookingActivityOut,
    VisitBookingCreate,
    VisitBookingOut,
    VisitCostCreate,
    VisitCostOut,
    VisitIncidentCreate,
    VisitIncidentOut,
    VisitorFeedbackCreate,
    VisitorFeedbackOut,
    VisitorProfileCreate,
    VisitRetailSaleCreate,
    VisitRetailSaleOut,
    VisitStaffRosterCreate,
    VisitStaffRosterOut,
)
from app.farmos.visits_models import (
    BookingActivity,
    VisitActivity,
    VisitBooking,
    VisitCost,
    VisitIncident,
    VisitorFeedback,
    VisitPackage,
    VisitRetailSale,
    VisitSession,
    VisitStaffRosterEntry,
)
from app.tenant_api.models import InventoryItem

router = APIRouter()

_COUNTED_BOOKING_STATUSES = ("confirmed", "checked_in", "completed")


# --- Bookings --------------------------------------------------------------


def _to_booking_out(db: Session, b: VisitBooking) -> VisitBookingOut:
    activities = db.execute(
        select(BookingActivity).where(BookingActivity.booking_id == b.id)
    ).scalars().all()
    return VisitBookingOut(
        id=str(b.id),
        farm_id=str(b.tenant_id),
        visitor_id=str(b.visitor_id),
        session_id=str(b.session_id),
        package_id=str(b.package_id),
        status=b.status,
        adults=b.adults,
        children=b.children,
        total_amount=float(b.total_amount),
        deposit_amount=float(b.deposit_amount),
        balance_due=float(b.balance_due),
        source=b.source,
        payment_method=b.payment_method,
        notes=b.notes,
        confirmed_at=b.confirmed_at,
        checked_in_at=b.checked_in_at,
        completed_at=b.completed_at,
        cancelled_at=b.cancelled_at,
        activities=[
            BookingActivityOut(
                id=str(a.id),
                activity_id=str(a.activity_id),
                quantity=a.quantity,
                unit_price=float(a.unit_price),
                total_price=float(a.total_price),
            )
            for a in activities
        ],
    )


@router.get("/visit-bookings", response_model=list[VisitBookingOut])
def list_bookings(
    session_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    _access: AccessContext = Depends(require_permission("farm_visits", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[VisitBookingOut]:
    stmt = select(VisitBooking).where(VisitBooking.deleted_at.is_(None))
    if session_id:
        stmt = stmt.where(VisitBooking.session_id == uuid.UUID(session_id))
    if status:
        stmt = stmt.where(VisitBooking.status == status)
    rows = db.execute(stmt.order_by(VisitBooking.created_at.desc())).scalars().all()
    return [_to_booking_out(db, row) for row in rows]


@router.post("/visit-bookings", response_model=VisitBookingOut, status_code=201)
def create_booking(
    payload: VisitBookingCreate,
    access: AccessContext = Depends(require_permission("farm_visits", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> VisitBookingOut:
    if payload.idempotency_key:
        existing = db.execute(
            select(VisitBooking).where(
                VisitBooking.deleted_at.is_(None), VisitBooking.idempotency_key == payload.idempotency_key
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _to_booking_out(db, existing)

    if payload.visitor_id:
        visitor_id = uuid.UUID(payload.visitor_id)
    elif payload.visitor:
        visitor = create_visitor_row(
            db, access, VisitorProfileCreate(**payload.visitor.model_dump())
        )
        visitor_id = uuid.UUID(visitor.id)
    else:
        raise HTTPException(status_code=422, detail="A booking needs a visitor — give visitor_id or visitor.")

    session_row = db.get(VisitSession, uuid.UUID(payload.session_id))
    if session_row is None or session_row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Session not found.")

    package = db.get(VisitPackage, uuid.UUID(payload.package_id))
    if package is None or package.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Package not found.")

    total_amount = float(package.base_price)
    booking = VisitBooking(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        visitor_id=visitor_id,
        session_id=session_row.id,
        package_id=package.id,
        adults=payload.adults,
        children=payload.children,
        deposit_amount=payload.deposit_amount,
        source=payload.source,
        payment_method=payload.payment_method,
        notes=payload.notes,
        idempotency_key=payload.idempotency_key,
    )
    db.add(booking)
    db.flush()

    for selection in payload.activities:
        activity = db.get(VisitActivity, uuid.UUID(selection.activity_id))
        if activity is None or activity.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Activity not found.")
        line_total = float(activity.price) * selection.quantity
        total_amount += line_total
        db.add(
            BookingActivity(
                tenant_id=access.tenant_id,
                booking_id=booking.id,
                activity_id=activity.id,
                quantity=selection.quantity,
                unit_price=activity.price,
                total_price=line_total,
            )
        )

    booking.total_amount = total_amount
    booking.balance_due = total_amount - payload.deposit_amount
    db.flush()
    return _to_booking_out(db, booking)


@router.post("/visit-bookings/{booking_id}/confirm", response_model=VisitBookingOut)
def confirm_booking(
    booking_id: str,
    access: AccessContext = Depends(require_permission("farm_visits", "approve")),
    db: Session = Depends(get_farmos_tenant_db),
) -> VisitBookingOut:
    """RULE-VIS-002: cannot confirm if the session capacity would be
    exceeded.
    """
    try:
        pk = uuid.UUID(booking_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Booking not found.") from exc
    booking = db.get(VisitBooking, pk)
    if booking is None or booking.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Booking not found.")
    if booking.status == "confirmed":
        return _to_booking_out(db, booking)

    session_row = db.get(VisitSession, booking.session_id)
    assert session_row is not None  # a booking always points at a real session
    other_bookings = db.execute(
        select(VisitBooking).where(
            VisitBooking.session_id == booking.session_id,
            VisitBooking.id != booking.id,
            VisitBooking.status.in_(_COUNTED_BOOKING_STATUSES),
        )
    ).scalars().all()
    already_booked = sum(b.adults + b.children for b in other_bookings)
    party_size = booking.adults + booking.children
    if already_booked + party_size > session_row.capacity:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This session only has {max(session_row.capacity - already_booked, 0)} spot(s) "
                f"left — can't confirm a party of {party_size}."
            ),
        )

    booking.status = "confirmed"
    booking.confirmed_at = datetime.now(timezone.utc)
    booking.version += 1
    booking.last_modified_by = access.membership_id
    db.flush()
    return _to_booking_out(db, booking)


# --- Session costs -----------------------------------------------------


def _to_cost_out(c: VisitCost) -> VisitCostOut:
    return VisitCostOut(
        id=str(c.id),
        session_id=str(c.session_id),
        category=c.category,
        description=c.description,
        amount=float(c.amount),
        allocation_method=c.allocation_method,
    )


@router.get("/visit-costs", response_model=list[VisitCostOut])
def list_costs(
    session_id: str | None = Query(default=None),
    _access: AccessContext = Depends(require_permission("farm_visits", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[VisitCostOut]:
    stmt = select(VisitCost).where(VisitCost.deleted_at.is_(None))
    if session_id:
        stmt = stmt.where(VisitCost.session_id == uuid.UUID(session_id))
    rows = db.execute(stmt).scalars().all()
    return [_to_cost_out(row) for row in rows]


@router.post("/visit-costs", response_model=VisitCostOut, status_code=201)
def create_cost(
    payload: VisitCostCreate,
    access: AccessContext = Depends(require_permission("farm_visits", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> VisitCostOut:
    cost = VisitCost(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        session_id=uuid.UUID(payload.session_id),
        category=payload.category,
        description=payload.description,
        amount=payload.amount,
        allocation_method=payload.allocation_method,
    )
    db.add(cost)
    db.flush()
    return _to_cost_out(cost)


# --- Incidents -------------------------------------------------------------


def _to_incident_out(i: VisitIncident) -> VisitIncidentOut:
    return VisitIncidentOut(
        id=str(i.id),
        session_id=str(i.session_id),
        booking_id=str(i.booking_id) if i.booking_id else None,
        incident_type=i.incident_type,
        severity=i.severity,
        description=i.description,
        action_taken=i.action_taken,
        created_at=i.created_at,
    )


@router.get("/visit-incidents", response_model=list[VisitIncidentOut])
def list_incidents(
    session_id: str | None = Query(default=None),
    _access: AccessContext = Depends(require_permission("farm_visits", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[VisitIncidentOut]:
    stmt = select(VisitIncident).where(VisitIncident.deleted_at.is_(None))
    if session_id:
        stmt = stmt.where(VisitIncident.session_id == uuid.UUID(session_id))
    rows = db.execute(stmt.order_by(VisitIncident.created_at.desc())).scalars().all()
    return [_to_incident_out(row) for row in rows]


@router.post("/visit-incidents", response_model=VisitIncidentOut, status_code=201)
def report_incident(
    payload: VisitIncidentCreate,
    access: AccessContext = Depends(require_permission("farm_visits", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> VisitIncidentOut:
    incident = VisitIncident(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        session_id=uuid.UUID(payload.session_id),
        booking_id=uuid.UUID(payload.booking_id) if payload.booking_id else None,
        incident_type=payload.incident_type,
        severity=payload.severity,
        description=payload.description,
        action_taken=payload.action_taken,
        created_at=datetime.now(timezone.utc),
    )
    db.add(incident)
    db.flush()
    return _to_incident_out(incident)


# --- Staff roster ------------------------------------------------------


def _hours_between(start_time: str, end_time: str) -> float:
    start = datetime.strptime(start_time, "%H:%M:%S")
    end = datetime.strptime(end_time, "%H:%M:%S")
    return max((end - start).total_seconds() / 3600, 0)


def _to_roster_out(r: VisitStaffRosterEntry) -> VisitStaffRosterOut:
    return VisitStaffRosterOut(
        id=str(r.id),
        session_id=str(r.session_id),
        worker_id=str(r.worker_id),
        role=r.role,
        start_time=r.start_time,
        end_time=r.end_time,
        hourly_rate=float(r.hourly_rate),
        total_cost=float(r.total_cost) if r.total_cost is not None else None,
    )


@router.get("/visit-staff-roster", response_model=list[VisitStaffRosterOut])
def list_staff_roster(
    session_id: str | None = Query(default=None),
    _access: AccessContext = Depends(require_permission("farm_visits", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[VisitStaffRosterOut]:
    stmt = select(VisitStaffRosterEntry).where(VisitStaffRosterEntry.deleted_at.is_(None))
    if session_id:
        stmt = stmt.where(VisitStaffRosterEntry.session_id == uuid.UUID(session_id))
    rows = db.execute(stmt).scalars().all()
    return [_to_roster_out(row) for row in rows]


@router.post("/visit-staff-roster", response_model=VisitStaffRosterOut, status_code=201)
def create_staff_roster_entry(
    payload: VisitStaffRosterCreate,
    access: AccessContext = Depends(require_permission("farm_visits", "assign")),
    db: Session = Depends(get_farmos_tenant_db),
) -> VisitStaffRosterOut:
    entry = VisitStaffRosterEntry(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        session_id=uuid.UUID(payload.session_id),
        worker_id=uuid.UUID(payload.worker_id),
        role=payload.role,
        start_time=payload.start_time,
        end_time=payload.end_time,
        hourly_rate=payload.hourly_rate,
        total_cost=_hours_between(payload.start_time, payload.end_time) * payload.hourly_rate,
    )
    db.add(entry)
    db.flush()
    return _to_roster_out(entry)


# --- Visitor feedback --------------------------------------------------


def _to_feedback_out(f: VisitorFeedback) -> VisitorFeedbackOut:
    return VisitorFeedbackOut(
        id=str(f.id),
        booking_id=str(f.booking_id),
        rating=f.rating,
        comments=f.comments,
        would_return=f.would_return,
        submitted_at=f.submitted_at,
    )


@router.get("/visitor-feedback", response_model=list[VisitorFeedbackOut])
def list_feedback(
    booking_id: str | None = Query(default=None),
    _access: AccessContext = Depends(require_permission("farm_visits", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[VisitorFeedbackOut]:
    stmt = select(VisitorFeedback).where(VisitorFeedback.deleted_at.is_(None))
    if booking_id:
        stmt = stmt.where(VisitorFeedback.booking_id == uuid.UUID(booking_id))
    rows = db.execute(stmt).scalars().all()
    return [_to_feedback_out(row) for row in rows]


@router.post("/visitor-feedback", response_model=VisitorFeedbackOut, status_code=201)
def submit_feedback(
    payload: VisitorFeedbackCreate,
    access: AccessContext = Depends(require_permission("farm_visits", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> VisitorFeedbackOut:
    feedback = VisitorFeedback(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        booking_id=uuid.UUID(payload.booking_id),
        rating=payload.rating,
        comments=payload.comments,
        would_return=payload.would_return,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(feedback)
    db.flush()
    return _to_feedback_out(feedback)


# --- Retail sales --------------------------------------------------------


@router.get("/visit-retail-sales", response_model=list[VisitRetailSaleOut])
def list_retail_sales(
    booking_id: str | None = Query(default=None),
    _access: AccessContext = Depends(require_permission("farm_visits", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[VisitRetailSaleOut]:
    stmt = select(VisitRetailSale).where(VisitRetailSale.deleted_at.is_(None))
    if booking_id:
        stmt = stmt.where(VisitRetailSale.booking_id == uuid.UUID(booking_id))
    rows = db.execute(stmt).scalars().all()
    return [
        VisitRetailSaleOut(
            id=str(r.id),
            booking_id=str(r.booking_id) if r.booking_id else None,
            visitor_id=str(r.visitor_id) if r.visitor_id else None,
            sale_id=str(r.sale_id),
            channel=r.channel,
            total_amount=float(r.total_amount),
        )
        for r in rows
    ]


@router.post("/visit-retail-sales", response_model=VisitRetailSaleOut, status_code=201)
def record_retail_sale(
    payload: VisitRetailSaleCreate,
    access: AccessContext = Depends(require_permission("farm_visits", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> VisitRetailSaleOut:
    """RULE-VIS-006: deducts inventory (plain items or Mouneh finished
    goods) and creates a core Sale row so the purchase flows into Sales &
    Finance, then links it back to the booking/visitor.
    """
    total_amount = 0.0
    total_quantity = 0.0
    for line in payload.lines:
        line_total = line.quantity * line.unit_price
        total_amount += line_total
        total_quantity += line.quantity

        if line.item_type == "finished_goods":
            stock = db.get(FinishedGoodsStock, uuid.UUID(line.item_id))
            insufficient = (
                stock is None
                or stock.deleted_at is not None
                or float(stock.quantity_available) < line.quantity
            )
            if insufficient:
                raise HTTPException(
                    status_code=422, detail="Not enough finished-goods stock for this retail sale."
                )
            assert stock is not None
            stock.quantity_available = float(stock.quantity_available) - line.quantity
            stock.quantity_sold = float(stock.quantity_sold) + line.quantity
            stock.version += 1
            stock.last_modified_by = access.membership_id
        elif line.item_type == "inventory_item":
            item = db.get(InventoryItem, uuid.UUID(line.item_id))
            if item is None or item.deleted_at is not None or float(item.current_qty) < line.quantity:
                raise HTTPException(status_code=422, detail="Not enough inventory for this retail sale.")
            item.current_qty = float(item.current_qty) - line.quantity
            item.version += 1
            item.last_modified_by = access.membership_id
        else:
            raise HTTPException(status_code=422, detail=f"Unknown item_type '{line.item_type}'.")

    sale = Sale(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        product_type="visit_retail",
        product_label=f"Farm shop — {len(payload.lines)} item(s)",
        quantity=total_quantity,
        amount=total_amount,
        currency="USD",
        payment_status=payload.payment_status,
        sold_at=datetime.now(timezone.utc),
    )
    db.add(sale)
    db.flush()

    retail_sale = VisitRetailSale(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        booking_id=uuid.UUID(payload.booking_id) if payload.booking_id else None,
        visitor_id=uuid.UUID(payload.visitor_id) if payload.visitor_id else None,
        sale_id=sale.id,
        channel=payload.channel,
        total_amount=total_amount,
    )
    db.add(retail_sale)
    db.flush()

    return VisitRetailSaleOut(
        id=str(retail_sale.id),
        booking_id=str(retail_sale.booking_id) if retail_sale.booking_id else None,
        visitor_id=str(retail_sale.visitor_id) if retail_sale.visitor_id else None,
        sale_id=str(retail_sale.sale_id),
        channel=retail_sale.channel,
        total_amount=float(retail_sale.total_amount),
    )
