"""Selling milk from the farm's tank (milk_models.py; reference
`services/milk_sales_service.py`, PR 24).

    tank = milk recorded as stored − milk sold + tank adjustments

A sale is refused when it is more than the tank holds, unless someone with
the approval says the records are behind (a stock override, as for feed);
a count of the tank puts the records right. Every sale writes the farm's
revenue record (`sale`, product_type "milk"), so Sales & Finance and the
reports see the money; marking it paid updates that record; cancelling it
adds a negative one and keeps both.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.enums import ActorType
from app.farmos import duplicates
from app.farmos import milk_models as mm
from app.farmos.deps import AccessContext
from app.farmos.feed_catalog import FeedError
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.finance_models import Sale
from app.farmos.livestock_service import ensure_utc, now_utc
from app.farmos.production_models import MilkRecord
from app.farmos.record_time import not_in_future

PRODUCT_LABEL = "Raw milk"
# A tablet's clock may run a few minutes ahead of the server's.
CLOCK_TOLERANCE = timedelta(minutes=5)


class MilkSaleError(FeedError):
    pass


def _audit(control_db: Session, access: AccessContext, **fields: Any) -> None:
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        module_code="milk_production",
        **fields,
    )


# ------------------------------------------------------------------- the tank
def _stored(db: Session, *, before: datetime | None = None) -> float:
    stmt = select(func.coalesce(func.sum(MilkRecord.liters), 0)).where(
        MilkRecord.deleted_at.is_(None), MilkRecord.destination == "stored"
    )
    if before is not None:
        stmt = stmt.where(MilkRecord.recorded_at < before)
    return float(db.scalar(stmt) or 0.0)


def _sold(db: Session, *, before: datetime | None = None) -> float:
    stmt = select(func.coalesce(func.sum(mm.MilkSale.liters), 0.0)).where(
        mm.MilkSale.deleted_at.is_(None), mm.MilkSale.status == "recorded"
    )
    if before is not None:
        stmt = stmt.where(mm.MilkSale.sold_at < before)
    return float(db.scalar(stmt) or 0.0)


def _adjusted(db: Session, *, before: datetime | None = None) -> float:
    stmt = select(func.coalesce(func.sum(mm.MilkTankAdjustment.liters), 0.0)).where(
        mm.MilkTankAdjustment.deleted_at.is_(None)
    )
    if before is not None:
        stmt = stmt.where(mm.MilkTankAdjustment.occurred_at < before)
    return float(db.scalar(stmt) or 0.0)


def tank_balance(db: Session, *, before: datetime | None = None) -> float:
    return round(_stored(db, before=before) - _sold(db, before=before) + _adjusted(db, before=before), 3)


def tank(db: Session, currency: str) -> dict:
    """Milk in the tank: stored − sold + adjusted, worked out from the
    records every time, with the last price and buyer for the form."""
    last = db.scalar(
        select(mm.MilkSale)
        .where(mm.MilkSale.deleted_at.is_(None), mm.MilkSale.status == "recorded")
        .order_by(mm.MilkSale.sold_at.desc())
        .limit(1)
    )
    stored, sold, adjusted = _stored(db), _sold(db), _adjusted(db)
    return {
        "balance_l": round(stored - sold + adjusted, 3),
        "stored_l": round(stored, 3),
        "sold_l": round(sold, 3),
        "adjusted_l": round(adjusted, 3),
        "currency": currency,
        "last_price_per_liter": last.price_per_liter if last else None,
        "last_buyer_name": last.buyer_name if last else None,
    }


def get_sale(db: Session, sale_id: str | uuid.UUID) -> mm.MilkSale:
    # RLS already hides other farms' sales: a guess and a missing id look the same.
    pk = parse_uuid(sale_id)
    sale = db.get(mm.MilkSale, pk) if pk else None
    if sale is None or sale.deleted_at is not None:
        raise MilkSaleError("Milk sale not found", 404)
    return sale


def customer(customer_id: str | None, buyer_name: str | None) -> tuple[uuid.UUID | None, str | None]:
    """The buyer: an opaque customer id the farm chooses, and the name as
    given. The server has no customers master ("Known, deliberate gaps"),
    so the name is kept on the record and a buyer is found again by it."""
    pk = None
    if customer_id:
        pk = parse_uuid(customer_id)
        if pk is None:
            raise MilkSaleError("Buyer not found", 404)
    name = (buyer_name or "").strip()[:200] or None
    return pk, name


def _same_buyer(model: Any, cust_id: uuid.UUID | None, name: str | None):
    """The SQL test for "the same buyer": by id when one is named, else by
    name whatever the case, else no buyer at all."""
    if cust_id is not None:
        return model.customer_id == cust_id
    if name:
        return func.lower(model.buyer_name) == name.lower()
    return model.buyer_name.is_(None)


def sell(
    db: Session,
    control_db: Session,
    access: AccessContext,
    *,
    liters: float,
    price_per_liter: float,
    sold_at: datetime | None,
    customer_id: str | None,
    buyer_name: str | None,
    payment_status: str,
    reference: str | None,
    notes: str | None,
    allow_negative: bool,
    can_override: bool,
    confirm_duplicate: bool,
    currency: str,
) -> mm.MilkSale:
    if liters <= 0:
        raise MilkSaleError("Say how many litres were sold.")
    if price_per_liter < 0:
        raise MilkSaleError("The price cannot be negative.")
    if payment_status not in mm.MILK_PAYMENT_STATUSES:
        raise MilkSaleError(f"payment_status must be one of {list(mm.MILK_PAYMENT_STATUSES)}")
    at = not_in_future(sold_at, "A milk sale") or now_utc()
    in_tank = tank_balance(db, before=at + timedelta(seconds=1))
    if liters > in_tank + 1e-6:
        if not allow_negative:
            raise MilkSaleError(
                f"The tank holds {max(in_tank, 0):g} L on record, less than the {liters:g} L sold. "
                "Record the milk first, or count the tank; a manager can record the sale anyway.",
                422,
            )
        if not can_override:
            raise MilkSaleError(
                "Selling more milk than the tank holds on record needs Milk production approval — "
                "ask the farm manager.",
                403,
            )
    cust_id, name = customer(customer_id, buyer_name)

    # The same quantity to the same buyer within half an hour: two people
    # (or one person twice) recording one collection.
    lo, hi = duplicates.near(at)
    earlier = db.scalar(
        select(mm.MilkSale).where(
            mm.MilkSale.deleted_at.is_(None),
            mm.MilkSale.status == "recorded",
            mm.MilkSale.liters == liters,
            _same_buyer(mm.MilkSale, cust_id, name),
            mm.MilkSale.sold_at >= lo,
            mm.MilkSale.sold_at <= hi,
        )
    )
    match = duplicates.guard(
        earlier,
        f"{liters:g} L of milk{(' to ' + name) if name else ''} was already recorded as sold "
        "within half an hour."
        if earlier
        else "",
        confirm=confirm_duplicate,
    )

    amount = round(liters * price_per_liter, 2)
    revenue = Sale(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        customer_id=cust_id,
        product_type="milk",
        product_label=PRODUCT_LABEL,
        quantity=liters,
        unit="L",
        amount=amount,
        currency=currency,
        payment_status=payment_status,
        sold_at=at,
        unit_price=price_per_liter,
        buyer_name=name,
        reference=(reference or "").strip()[:120] or None,
        paid_at=at if payment_status == "paid" else None,
        status="recorded",
        activity="dairy",
        recorded_by=access.user_id,
    )
    db.add(revenue)
    db.flush()
    sale = mm.MilkSale(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        sold_at=at,
        liters=liters,
        price_per_liter=price_per_liter,
        amount=amount,
        currency=currency,
        customer_id=cust_id,
        buyer_name=name,
        payment_status=payment_status,
        paid_at=at if payment_status == "paid" else None,
        reference=(reference or "").strip()[:120] or None,
        notes=(notes or "").strip() or None,
        status="recorded",
        sale_id=revenue.id,
        recorded_by=access.user_id,
    )
    db.add(sale)
    db.flush()
    duplicates.log_confirmed(
        control_db,
        access,
        entity_type="milk_sale",
        entity_id=str(sale.id),
        match_id=match.id if match else None,
        what=f"milk_sale:{sale.id}",
        module_code="milk_production",
    )
    _audit(
        control_db,
        access,
        action="milk.sold",
        entity_type="milk_sale",
        entity_id=str(sale.id),
        summary=(
            f"Milk sold: {liters:g} L at {price_per_liter:g} {currency}/L to {name or 'no buyer named'} "
            f"({payment_status})"
        )[:500],
        changes={"status": {"from": None, "to": "recorded"}},
        metadata={"sale_id": str(revenue.id), "over_tank": liters > in_tank + 1e-6},
    )
    return sale


def mark_paid(db: Session, control_db: Session, access: AccessContext, sale: mm.MilkSale) -> mm.MilkSale:
    if sale.status != "recorded":
        raise MilkSaleError("This sale was cancelled.", 409)
    if sale.payment_status == "paid":
        return sale
    sale.payment_status, sale.paid_at = "paid", now_utc()
    sale.version += 1
    sale.last_modified_by = access.membership_id
    revenue = db.get(Sale, sale.sale_id) if sale.sale_id else None
    if revenue is not None:
        revenue.payment_status, revenue.paid_at = "paid", sale.paid_at
        revenue.version += 1
        revenue.last_modified_by = access.membership_id
    _audit(
        control_db,
        access,
        action="milk_sale.paid",
        entity_type="milk_sale",
        entity_id=str(sale.id),
        summary=f"Milk sale of {sale.liters:g} L marked paid"[:500],
        changes={"payment_status": {"from": "pending", "to": "paid"}},
    )
    return sale


def cancel(
    db: Session, control_db: Session, access: AccessContext, sale: mm.MilkSale, *, reason: str
) -> mm.MilkSale:
    """A sale recorded by mistake. It stays on file, marked cancelled with
    the reason; the revenue is reversed by a negative record and the milk
    counts as in the tank again."""
    if sale.status == "cancelled":
        raise MilkSaleError("This sale is already cancelled.", 409)
    if not reason or not reason.strip():
        raise MilkSaleError("Say why the sale is cancelled.")
    at = now_utc()
    reversal = Sale(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        customer_id=sale.customer_id,
        product_type="milk",
        product_label=f"{PRODUCT_LABEL} (cancelled sale)",
        quantity=-sale.liters,
        unit="L",
        amount=-sale.amount,
        currency=sale.currency,
        payment_status=sale.payment_status,
        sold_at=at,
        status="reversal",
        reversal_of_id=sale.sale_id,
        buyer_name=sale.buyer_name,
        unit_price=sale.price_per_liter,
        activity="dairy",
        paid_at=at if sale.payment_status == "paid" else None,
        recorded_by=access.user_id,
    )
    db.add(reversal)
    db.flush()
    original = db.get(Sale, sale.sale_id) if sale.sale_id else None
    if original is not None:
        original.status, original.cancel_reason, original.cancelled_at = "cancelled", reason.strip(), at
        original.version += 1
        original.last_modified_by = access.membership_id
    sale.status, sale.cancel_reason, sale.cancelled_at = "cancelled", reason.strip(), at
    sale.reversal_sale_id = reversal.id
    sale.version += 1
    sale.last_modified_by = access.membership_id
    db.flush()
    _audit(
        control_db,
        access,
        action="milk_sale.cancelled",
        entity_type="milk_sale",
        entity_id=str(sale.id),
        summary=f"Milk sale of {sale.liters:g} L cancelled: {reason.strip()}"[:500],
        changes={"status": {"from": "recorded", "to": "cancelled"}},
        metadata={"reason": reason.strip(), "reversal_sale_id": str(reversal.id)},
    )
    return sale


def adjust_tank(
    db: Session,
    control_db: Session,
    access: AccessContext,
    *,
    reason: str,
    liters: float | None,
    counted_level: float | None,
    occurred_at: datetime | None,
    notes: str | None,
) -> mm.MilkTankAdjustment:
    """Milk out of the tank outside a sale (spoiled, fed to calves, home use:
    [liters] taken out) or a count of the tank ([counted_level]; the
    difference with the records is what is adjusted)."""
    if reason not in mm.TANK_ADJUSTMENT_REASONS:
        raise MilkSaleError(f"reason must be one of {list(mm.TANK_ADJUSTMENT_REASONS)}")
    at = not_in_future(occurred_at, "A tank adjustment") or now_utc()
    if reason == "count":
        if counted_level is None or counted_level < 0:
            raise MilkSaleError("Give the litres counted in the tank.")
        delta = round(counted_level - tank_balance(db, before=at + timedelta(seconds=1)), 3)
    else:
        if liters is None or liters <= 0:
            raise MilkSaleError("Say how many litres left the tank.")
        delta = -liters
    row = mm.MilkTankAdjustment(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        occurred_at=at,
        liters=delta,
        reason=reason,
        counted_level=counted_level if reason == "count" else None,
        notes=(notes or "").strip() or None,
        recorded_by=access.user_id,
    )
    db.add(row)
    db.flush()
    _audit(
        control_db,
        access,
        action="milk_tank.adjusted",
        entity_type="milk_tank_adjustment",
        entity_id=str(row.id),
        summary=(
            f"Milk tank counted at {counted_level:g} L ({delta:+g} L)"
            if reason == "count" and counted_level is not None
            else f"{-delta:g} L out of the milk tank: {reason}"
        )[:500],
        changes={"liters": {"from": None, "to": delta}},
        metadata={"reason": reason, "counted_level": row.counted_level},
    )
    return row


def _iso(value: datetime | None) -> str | None:
    at = ensure_utc(value)
    return at.isoformat() if at else None


def sale_dict(s: mm.MilkSale) -> dict:
    return {
        "id": str(s.id),
        "sold_at": _iso(s.sold_at),
        "liters": s.liters,
        "price_per_liter": s.price_per_liter,
        "amount": s.amount,
        "currency": s.currency,
        "customer_id": str(s.customer_id) if s.customer_id else None,
        "buyer_name": s.buyer_name,
        "payment_status": s.payment_status,
        "paid_at": _iso(s.paid_at),
        "reference": s.reference,
        "notes": s.notes,
        "status": s.status,
        "cancel_reason": s.cancel_reason,
        "recorded_by": str(s.recorded_by) if s.recorded_by else None,
    }


def adjustment_dict(a: mm.MilkTankAdjustment) -> dict:
    return {
        "id": str(a.id),
        "occurred_at": _iso(a.occurred_at),
        "liters": a.liters,
        "reason": a.reason,
        "counted_level": a.counted_level,
        "notes": a.notes,
        "recorded_by": str(a.recorded_by) if a.recorded_by else None,
    }
