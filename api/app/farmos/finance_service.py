"""Origami's financials (reference v0.8.0, `services/finance_service.py`,
PR 25): any sale, expenses and bills, who owes what, profit and loss, in
USD with LBP where money changed hands in LBP.

The books are the canonical records, never a figure kept on the side:

* revenue — `sale`: every product (milk from the tank, eggs, produce,
  animals, the farm shop…). A cancelled sale stays on file and is undone
  by a negative `reversal` row.
* costs — `expense` (bills and expenses recorded here), plus what other
  modules already record with a cost: feed fed to the animals (feeding
  events), feed delivered (feed lots), medicine received (pharmacy lots)
  and farm-visit session costs. Those are read from their own records and
  never typed in twice.
* an amount in LBP is converted at the rate the farm manager set for its
  day (finance_models.ExchangeRate) unless the person entering it gives
  the rate they actually used; the LBP amount and the rate stay on the
  record.

Profit and loss counts feed when it is fed (what it cost), and medicine
when it is received; cash out counts feed when it is delivered. A sale is
revenue when it is made and cash when it is paid.

The farm's own time zone and currency come from the control plane
(`farm_books`): a day is a farm day, not a UTC day.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.enums import ActorType
from app.farmos import duplicates
from app.farmos import feed_models as fm
from app.farmos import finance_models as fin
from app.farmos import milk_models as mm
from app.farmos import milk_sales_service as milk_sales
from app.farmos.deps import AccessContext
from app.farmos.feed_catalog import FeedError
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.finance_models import Expense, Sale
from app.farmos.livestock_models import AnimalGroup
from app.farmos.livestock_service import ensure_utc, now_utc
from app.farmos.milk_sales_service import not_in_future
from app.tenant_api.models import Animal
from app.tenants.models import Tenant

# What can be sold here. Milk is sold from the tank (Sell milk) and Mouneh
# from its own shop, so their stock moves with the sale.
SALE_PRODUCT_TYPES = ("eggs", "produce", "animals", "farm_products", "other")
PAYMENT_STATUSES = ("paid", "pending")
EXPENSE_CATEGORIES = (
    "feed",
    "medicine",
    "veterinary",
    "labor",
    "fuel",
    "utilities",
    "equipment",
    "repairs",
    "transport",
    "rent",
    "taxes",
    "packaging",
    "seeds",
    "fertilizer",
    "other",
)
ACTIVITIES = ("dairy", "poultry", "livestock", "crops", "mouneh", "visits", "general")

# The activity a sale belongs to, from what was sold. `visit_retail` is the
# server's farm-shop product type (routes_visit_bookings.py).
ACTIVITY_OF_PRODUCT = {
    "milk": "dairy",
    "eggs": "poultry",
    "produce": "crops",
    "animals": "livestock",
    "mouneh": "mouneh",
    "visitor_retail": "visits",
    "visit_retail": "visits",
    "visits": "visits",
    "farm_products": "general",
    "other": "general",
}
# The activity an expense belongs to when none is given.
ACTIVITY_OF_CATEGORY = {
    "feed": "livestock",
    "medicine": "livestock",
    "veterinary": "livestock",
    "seeds": "crops",
    "fertilizer": "crops",
    "packaging": "mouneh",
}
# Feed fed to an animal or group counts against what the species is kept for.
ACTIVITY_OF_SPECIES = {
    "cow": "dairy",
    "goat": "dairy",
    "sheep": "dairy",
    "layer_hen": "poultry",
    "duck": "poultry",
    "turkey": "poultry",
}
MAX_PL_DAYS = 731


class FinanceError(FeedError):
    pass


# ----------------------------------------------------------------- the farm
@dataclass(frozen=True)
class FarmBooks:
    """What the books need to know about the farm: its time zone (a day is
    a farm day), the currency they are kept in and the farm's name."""

    tz: ZoneInfo
    currency: str
    farm_name: str

    def day(self, at: datetime | None) -> date:
        utc = ensure_utc(at) or now_utc()
        return utc.astimezone(self.tz).date()

    def today(self) -> date:
        return datetime.now(timezone.utc).astimezone(self.tz).date()


def farm_books(control_db: Session, tenant_id: uuid.UUID) -> FarmBooks:
    tenant = control_db.get(Tenant, tenant_id)
    try:
        tz = ZoneInfo(tenant.timezone if tenant and tenant.timezone else "UTC")
    except Exception:  # noqa: BLE001 - an unknown zone falls back to UTC
        tz = ZoneInfo("UTC")
    return FarmBooks(
        tz=tz,
        currency=(tenant.default_currency if tenant and tenant.default_currency else fin.BASE_CURRENCY),
        farm_name=tenant.display_name if tenant else "",
    )


def _audit(control_db: Session, access: AccessContext, *, module_code: str, **fields: Any) -> None:
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        module_code=module_code,
        **fields,
    )


def _f(value: Any) -> float:
    return float(value or 0)


def _fn(value: Any) -> float | None:
    return None if value is None else float(value)


# ----------------------------------------------------------------- the rate
def rate_on(db: Session, currency: str, on: date) -> fin.ExchangeRate | None:
    """The rate in force on [on]: the latest set from that day or before
    (the latest entry wins when two were set from the same day)."""
    return db.scalar(
        select(fin.ExchangeRate)
        .where(
            fin.ExchangeRate.deleted_at.is_(None),
            fin.ExchangeRate.currency == currency,
            fin.ExchangeRate.effective_date <= on,
        )
        .order_by(fin.ExchangeRate.effective_date.desc(), fin.ExchangeRate.created_at.desc())
        .limit(1)
    )


def rate_dict(r: fin.ExchangeRate) -> dict:
    return {
        "id": str(r.id),
        "currency": r.currency,
        "rate_per_usd": r.rate_per_usd,
        "effective_date": r.effective_date.isoformat(),
        "notes": r.notes,
        "set_by": str(r.set_by) if r.set_by else None,
        "created_at": ensure_utc(r.created_at),
    }


def rates(db: Session, books: FarmBooks) -> dict:
    today = books.today()
    history = db.scalars(
        select(fin.ExchangeRate)
        .where(fin.ExchangeRate.deleted_at.is_(None))
        .order_by(fin.ExchangeRate.effective_date.desc(), fin.ExchangeRate.created_at.desc())
        .limit(60)
    ).all()
    current: dict[str, dict | None] = {}
    for c in fin.CURRENCIES:
        if c == fin.BASE_CURRENCY:
            continue
        r = rate_on(db, c, today)
        current[c] = rate_dict(r) if r else None
    return {
        "base": fin.BASE_CURRENCY,
        "currencies": list(fin.CURRENCIES),
        "current": current,
        "history": [rate_dict(r) for r in history],
    }


def set_rate(
    db: Session,
    control_db: Session,
    access: AccessContext,
    books: FarmBooks,
    *,
    currency: str,
    rate_per_usd: float,
    effective_date: date | None,
    notes: str | None,
) -> fin.ExchangeRate:
    if currency not in fin.CURRENCIES or currency == fin.BASE_CURRENCY:
        raise FinanceError(f"The rate is set for {[c for c in fin.CURRENCIES if c != fin.BASE_CURRENCY]}.")
    if rate_per_usd is None or rate_per_usd <= 0:
        raise FinanceError("The rate must be more than zero.")
    today = books.today()
    day = effective_date or today
    if day > today:
        raise FinanceError("A rate cannot start in the future.", 422)
    previous = rate_on(db, currency, day)
    row = fin.ExchangeRate(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        currency=currency,
        rate_per_usd=rate_per_usd,
        effective_date=day,
        notes=(notes or "").strip() or None,
        set_by=access.user_id,
    )
    db.add(row)
    db.flush()
    _audit(
        control_db,
        access,
        module_code="finance",
        action="exchange_rate.set",
        entity_type="exchange_rate",
        entity_id=str(row.id),
        summary=f"1 USD = {rate_per_usd:,.0f} {currency} from {day.isoformat()}"[:500],
        changes={"rate_per_usd": {"from": previous.rate_per_usd if previous else None, "to": rate_per_usd}},
    )
    return row


def to_base(
    db: Session, books: FarmBooks, amount: float, currency: str | None, rate: float | None, at: datetime
) -> tuple[float, float | None, str | None, float | None]:
    """(amount in USD, original amount, original currency, rate used). An
    amount in USD is kept as it is; one in LBP is converted at [rate] when
    given, else at the farm's rate for its day — none set is refused."""
    cur = (currency or fin.BASE_CURRENCY).upper()
    if cur not in fin.CURRENCIES:
        raise FinanceError(f"currency must be one of {list(fin.CURRENCIES)}")
    if cur == fin.BASE_CURRENCY:
        return round(amount, 2), None, None, None
    if rate is None:
        r = rate_on(db, cur, books.day(at))
        if r is None:
            raise FinanceError(
                f"No {cur} exchange rate is set for that day. Set the rate in Sales & Finance, "
                "or give the rate used.",
                422,
            )
        rate = r.rate_per_usd
    if rate <= 0:
        raise FinanceError("The exchange rate must be more than zero.")
    return round(amount / rate, 2), round(amount, 2), cur, rate


# ------------------------------------------------------------------ parties
def _supplier(supplier_id: str | None, name: str | None) -> tuple[uuid.UUID | None, str | None]:
    """The supplier: an opaque id the farm chooses plus the name as given
    (`supplier_label`), as the feed and medicine lots keep it — there is no
    suppliers master on the server ("Known, deliberate gaps")."""
    pk = None
    if supplier_id:
        pk = parse_uuid(supplier_id)
        if pk is None:
            raise FinanceError("Supplier not found", 404)
    label = (name or "").strip()[:200] or None
    return pk, label


def _same_party(id_column: Any, name_column: Any, pk: uuid.UUID | None, name: str | None) -> Any:
    if pk is not None:
        return id_column == pk
    if name:
        return func.lower(name_column) == name.lower()
    return name_column.is_(None)


# -------------------------------------------------------------------- sales
def get_sale(db: Session, sale_id: str | uuid.UUID) -> Sale:
    pk = parse_uuid(sale_id)
    s = db.get(Sale, pk) if pk else None
    if s is None or s.deleted_at is not None:
        raise FinanceError("Sale not found", 404)
    return s


def _linked(db: Session, sale: Sale) -> tuple[str, mm.MilkSale | None]:
    """Which module recorded this sale: milk (a tank sale), mouneh, visits,
    or a sale recorded here."""
    milk = db.scalar(
        select(mm.MilkSale).where(mm.MilkSale.sale_id == sale.id, mm.MilkSale.deleted_at.is_(None))
    )
    if milk is not None:
        return "milk", milk
    from app.farmos import visits_models as vm

    if sale.product_type == "mouneh":
        return "mouneh", None
    if db.scalar(select(vm.VisitRetailSale.id).where(vm.VisitRetailSale.sale_id == sale.id).limit(1)):
        return "visits", None
    return "finance", None


def record_sale(
    db: Session,
    control_db: Session,
    access: AccessContext,
    books: FarmBooks,
    *,
    product_type: str,
    product_label: str | None,
    quantity: float | None,
    unit: str | None,
    unit_price: float | None,
    total: float | None,
    currency: str | None,
    exchange_rate: float | None,
    sold_at: datetime | None,
    customer_id: str | None,
    buyer_name: str | None,
    payment_status: str,
    reference: str | None,
    notes: str | None,
    activity: str | None,
    confirm_duplicate: bool,
) -> Sale:
    if product_type == "milk":
        raise FinanceError(
            "Milk is sold from the tank: use Sell milk, so the tank and the sale stay together.", 422
        )
    if product_type == "mouneh":
        raise FinanceError("Mouneh is sold from its own stock in the Mouneh shop.", 422)
    if product_type not in SALE_PRODUCT_TYPES:
        raise FinanceError(f"product_type must be one of {list(SALE_PRODUCT_TYPES)}")
    if payment_status not in PAYMENT_STATUSES:
        raise FinanceError(f"payment_status must be one of {list(PAYMENT_STATUSES)}")
    if activity is not None and activity not in ACTIVITIES:
        raise FinanceError(f"activity must be one of {list(ACTIVITIES)}")
    if quantity is not None and quantity <= 0:
        raise FinanceError("The quantity must be more than zero.")
    if unit_price is not None and unit_price < 0:
        raise FinanceError("The price cannot be negative.")
    if total is None:
        if quantity is None or unit_price is None:
            raise FinanceError("Give the quantity and the price, or the total.")
        total = quantity * unit_price
    if total < 0:
        raise FinanceError("The total cannot be negative.")
    if total == 0:
        raise FinanceError("A sale of nothing: give the price.")
    label = (product_label or "").strip() or product_type.replace("_", " ").capitalize()
    at = not_in_future(sold_at, "A sale") or now_utc()
    amount, original, original_cur, rate = to_base(db, books, total, currency, exchange_rate, at)
    cust_id, name = milk_sales.customer(customer_id, buyer_name)

    lo, hi = duplicates.near(at)
    earlier = db.scalar(
        select(Sale).where(
            Sale.deleted_at.is_(None),
            Sale.status == "recorded",
            Sale.product_type == product_type,
            func.lower(Sale.product_label) == label.lower(),
            Sale.amount == amount,
            _same_party(Sale.customer_id, Sale.buyer_name, cust_id, name),
            Sale.sold_at >= lo,
            Sale.sold_at <= hi,
        )
    )
    match = duplicates.guard(
        earlier,
        f"A sale of {label} for {amount:g} {fin.BASE_CURRENCY}{(' to ' + name) if name else ''} was already "
        "recorded within half an hour."
        if earlier
        else "",
        confirm=confirm_duplicate,
    )
    unit_price_base = round(amount / quantity, 4) if quantity else None
    sale = Sale(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        customer_id=cust_id,
        product_type=product_type,
        product_label=label[:100],
        quantity=quantity,
        unit=(unit or "").strip()[:16] or None,
        amount=amount,
        currency=fin.BASE_CURRENCY,
        payment_status=payment_status,
        sold_at=at,
        unit_price=unit_price_base,
        buyer_name=name,
        reference=(reference or "").strip()[:120] or None,
        notes=(notes or "").strip() or None,
        status="recorded",
        paid_at=at if payment_status == "paid" else None,
        original_amount=original,
        original_currency=original_cur,
        exchange_rate=rate,
        activity=activity or ACTIVITY_OF_PRODUCT.get(product_type, "general"),
        recorded_by=access.user_id,
    )
    db.add(sale)
    db.flush()
    duplicates.log_confirmed(
        control_db,
        access,
        entity_type="sale",
        entity_id=str(sale.id),
        match_id=match.id if match else None,
        what=f"sale:{sale.id}",
        module_code="sales",
    )
    money = (
        f"{original:,.0f} {original_cur} ({amount:g} {fin.BASE_CURRENCY})"
        if original_cur
        else (f"{amount:g} {fin.BASE_CURRENCY}")
    )
    _audit(
        control_db,
        access,
        module_code="sales",
        action="sale.recorded",
        entity_type="sale",
        entity_id=str(sale.id),
        summary=(
            f"Sold {label}"
            + (f" × {quantity:g} {unit or ''}".rstrip() if quantity else "")
            + f" for {money}"
            + (f" to {name}" if name else "")
            + f" ({payment_status})"
        )[:500],
        changes={"status": {"from": None, "to": "recorded"}},
    )
    return sale


def mark_sale_paid(
    db: Session, control_db: Session, access: AccessContext, sale: Sale, *, paid_at: datetime | None = None
) -> Sale:
    if sale.status != "recorded":
        raise FinanceError("Only a sale on record (not cancelled) can be marked paid.", 409)
    if sale.payment_status == "paid":
        return sale
    module, milk = _linked(db, sale)
    if module == "milk" and milk is not None:
        milk_sales.mark_paid(db, control_db, access, milk)
        return sale
    when = not_in_future(paid_at, "A payment") or now_utc()
    before = sale.payment_status
    sale.payment_status, sale.paid_at = "paid", when
    sale.version += 1
    sale.last_modified_by = access.membership_id
    _audit(
        control_db,
        access,
        module_code="sales",
        action="sale.paid",
        entity_type="sale",
        entity_id=str(sale.id),
        summary=(
            f"{sale.product_label or sale.product_type} ({_f(sale.amount):g} {sale.currency}) marked paid"
        )[:500],
        changes={"payment_status": {"from": before, "to": "paid"}},
    )
    return sale


def cancel_sale(db: Session, control_db: Session, access: AccessContext, sale: Sale, *, reason: str) -> Sale:
    """A sale recorded by mistake: kept on file, marked cancelled with the
    reason, and undone by a negative row. A milk sale goes back to the tank;
    Mouneh and farm-shop sales are cancelled where their stock is."""
    if sale.status == "reversal":
        raise FinanceError("This row undoes a cancelled sale; it is not cancelled itself.", 409)
    if sale.status == "cancelled":
        raise FinanceError("This sale is already cancelled.", 409)
    if not reason or not reason.strip():
        raise FinanceError("Say why the sale is cancelled.")
    module, milk = _linked(db, sale)
    if module == "milk" and milk is not None:
        milk_sales.cancel(db, control_db, access, milk, reason=reason)
        return sale
    if module == "mouneh":
        raise FinanceError(
            "This is a Mouneh sale: cancel it in Mouneh, so its jars go back on the shelf.", 409
        )
    if module == "visits":
        raise FinanceError("This is a farm-shop sale from a visit: cancel it in Farm visits.", 409)
    at = now_utc()
    reversal = Sale(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        customer_id=sale.customer_id,
        product_type=sale.product_type,
        product_label=f"{sale.product_label or sale.product_type} (cancelled sale)"[:100],
        quantity=-_f(sale.quantity) if sale.quantity is not None else None,
        unit=sale.unit,
        amount=-_f(sale.amount),
        currency=sale.currency,
        payment_status=sale.payment_status,
        sold_at=at,
        unit_price=sale.unit_price,
        buyer_name=sale.buyer_name,
        status="reversal",
        reversal_of_id=sale.id,
        paid_at=at if sale.payment_status == "paid" else None,
        original_amount=-sale.original_amount if sale.original_amount is not None else None,
        original_currency=sale.original_currency,
        exchange_rate=sale.exchange_rate,
        activity=sale.activity,
        recorded_by=access.user_id,
    )
    db.add(reversal)
    sale.status, sale.cancel_reason, sale.cancelled_at = "cancelled", reason.strip(), at
    sale.version += 1
    sale.last_modified_by = access.membership_id
    db.flush()
    _audit(
        control_db,
        access,
        module_code="sales",
        action="sale.cancelled",
        entity_type="sale",
        entity_id=str(sale.id),
        summary=(
            f"Sale of {sale.product_label or sale.product_type} ({_f(sale.amount):g} {sale.currency}) "
            f"cancelled: {reason.strip()}"
        )[:500],
        changes={"status": {"from": "recorded", "to": "cancelled"}},
        metadata={"reason": reason.strip(), "reversal_sale_id": str(reversal.id)},
    )
    return sale


def sale_dict(s: Sale) -> dict:
    return {
        "id": str(s.id),
        "product_type": s.product_type,
        "product_label": s.product_label,
        "quantity": _fn(s.quantity),
        "unit": s.unit,
        "unit_price": s.unit_price,
        "amount": _f(s.amount),
        "currency": s.currency,
        "original_amount": s.original_amount,
        "original_currency": s.original_currency,
        "exchange_rate": s.exchange_rate,
        "payment_status": s.payment_status,
        "paid_at": ensure_utc(s.paid_at),
        "sold_at": ensure_utc(s.sold_at),
        "customer_id": str(s.customer_id) if s.customer_id else None,
        "buyer_name": s.buyer_name,
        "reference": s.reference,
        "notes": s.notes,
        "status": s.status,
        "cancel_reason": s.cancel_reason,
        "cancelled_at": ensure_utc(s.cancelled_at),
        "reversal_of_id": str(s.reversal_of_id) if s.reversal_of_id else None,
        "activity": s.activity or ACTIVITY_OF_PRODUCT.get(s.product_type, "general"),
        "recorded_by": str(s.recorded_by) if s.recorded_by else None,
    }


def list_sales(db: Session, *, days: int = 90) -> list[dict]:
    since = now_utc() - timedelta(days=max(1, min(days, 731)))
    rows = db.scalars(
        select(Sale)
        .where(Sale.deleted_at.is_(None), Sale.sold_at >= since)
        .order_by(Sale.sold_at.desc())
        .limit(1000)
    ).all()
    return [sale_dict(s) for s in rows]


# ----------------------------------------------------------------- expenses
def get_expense(db: Session, expense_id: str | uuid.UUID) -> Expense:
    pk = parse_uuid(expense_id)
    e = db.get(Expense, pk) if pk else None
    if e is None or e.deleted_at is not None:
        raise FinanceError("Expense not found", 404)
    return e


def record_expense(
    db: Session,
    control_db: Session,
    access: AccessContext,
    books: FarmBooks,
    *,
    category: str,
    description: str | None,
    amount: float,
    currency: str | None,
    exchange_rate: float | None,
    incurred_at: datetime | None,
    supplier_id: str | None,
    supplier_name: str | None,
    payment_status: str,
    due_date: datetime | None,
    reference: str | None,
    notes: str | None,
    activity: str | None,
    confirm_duplicate: bool,
    linked_entity_type: str | None = None,
    linked_entity_id: str | None = None,
) -> Expense:
    if category not in EXPENSE_CATEGORIES:
        raise FinanceError(f"category must be one of {list(EXPENSE_CATEGORIES)}")
    if payment_status not in PAYMENT_STATUSES:
        raise FinanceError(f"payment_status must be one of {list(PAYMENT_STATUSES)}")
    if activity is not None and activity not in ACTIVITIES:
        raise FinanceError(f"activity must be one of {list(ACTIVITIES)}")
    if amount is None or amount <= 0:
        raise FinanceError("The amount must be more than zero.")
    at = not_in_future(incurred_at, "An expense") or now_utc()
    usd, original, original_cur, rate = to_base(db, books, amount, currency, exchange_rate, at)
    sup_id, sup_name = _supplier(supplier_id, supplier_name)
    desc = (description or "").strip()[:300] or None

    lo, hi = duplicates.near(at)
    earlier = db.scalar(
        select(Expense).where(
            Expense.deleted_at.is_(None),
            Expense.status == "recorded",
            Expense.category == category,
            Expense.amount == usd,
            _same_party(Expense.supplier_id, Expense.supplier_label, sup_id, sup_name),
            Expense.incurred_at >= lo,
            Expense.incurred_at <= hi,
        )
    )
    match = duplicates.guard(
        earlier,
        f"A {category} expense of {usd:g} {fin.BASE_CURRENCY}{(' to ' + sup_name) if sup_name else ''} was "
        "already recorded within half an hour."
        if earlier
        else "",
        confirm=confirm_duplicate,
    )
    due = ensure_utc(due_date) if due_date else None
    row = Expense(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        supplier_id=sup_id,
        supplier_label=sup_name,
        category=category,
        amount=usd,
        currency=fin.BASE_CURRENCY,
        linked_entity_type=linked_entity_type,
        linked_entity_id=linked_entity_id,
        incurred_at=at,
        description=desc,
        payment_status=payment_status,
        due_date=due if payment_status == "pending" else None,
        paid_at=at if payment_status == "paid" else None,
        reference=(reference or "").strip()[:120] or None,
        notes=(notes or "").strip() or None,
        status="recorded",
        original_amount=original,
        original_currency=original_cur,
        exchange_rate=rate,
        activity=activity or ACTIVITY_OF_CATEGORY.get(category, "general"),
        recorded_by=access.user_id,
    )
    db.add(row)
    db.flush()
    duplicates.log_confirmed(
        control_db,
        access,
        entity_type="expense",
        entity_id=str(row.id),
        match_id=match.id if match else None,
        what=f"expense:{row.id}",
        module_code="expenses",
    )
    money = (
        f"{original:,.0f} {original_cur} ({usd:g} {fin.BASE_CURRENCY})"
        if original_cur
        else (f"{usd:g} {fin.BASE_CURRENCY}")
    )
    _audit(
        control_db,
        access,
        module_code="expenses",
        action="expense.recorded",
        entity_type="expense",
        entity_id=str(row.id),
        summary=(
            f"{category.capitalize()}: {desc or ''} {money}{(' to ' + sup_name) if sup_name else ''}"
            f" ({'paid' if payment_status == 'paid' else 'to pay'})"
        )[:500],
        changes={"status": {"from": None, "to": "recorded"}},
    )
    return row


def mark_expense_paid(
    db: Session, control_db: Session, access: AccessContext, e: Expense, *, paid_at: datetime | None = None
) -> Expense:
    if e.status != "recorded":
        raise FinanceError("A cancelled expense cannot be paid.", 409)
    if e.payment_status == "paid":
        return e
    when = not_in_future(paid_at, "A payment") or now_utc()
    e.payment_status, e.paid_at = "paid", when
    e.version += 1
    e.last_modified_by = access.membership_id
    _audit(
        control_db,
        access,
        module_code="expenses",
        action="expense.paid",
        entity_type="expense",
        entity_id=str(e.id),
        summary=(
            f"{e.category.capitalize()} {_f(e.amount):g} {e.currency}"
            f"{(' to ' + e.supplier_label) if e.supplier_label else ''} paid"
        )[:500],
        changes={"payment_status": {"from": "pending", "to": "paid"}},
    )
    return e


def cancel_expense(
    db: Session, control_db: Session, access: AccessContext, e: Expense, *, reason: str
) -> Expense:
    if e.status == "cancelled":
        raise FinanceError("This expense is already cancelled.", 409)
    if not reason or not reason.strip():
        raise FinanceError("Say why the expense is cancelled.")
    e.status, e.cancel_reason, e.cancelled_at = "cancelled", reason.strip(), now_utc()
    e.version += 1
    e.last_modified_by = access.membership_id
    _audit(
        control_db,
        access,
        module_code="expenses",
        action="expense.cancelled",
        entity_type="expense",
        entity_id=str(e.id),
        summary=f"{e.category.capitalize()} {_f(e.amount):g} {e.currency} cancelled: {reason.strip()}"[:500],
        changes={"status": {"from": "recorded", "to": "cancelled"}},
        metadata={"reason": reason.strip()},
    )
    return e


def expense_dict(e: Expense) -> dict:
    return {
        "id": str(e.id),
        "category": e.category,
        "description": e.description,
        "amount": _f(e.amount),
        "currency": e.currency,
        "original_amount": e.original_amount,
        "original_currency": e.original_currency,
        "exchange_rate": e.exchange_rate,
        "incurred_at": ensure_utc(e.incurred_at),
        "supplier_id": str(e.supplier_id) if e.supplier_id else None,
        "supplier_name": e.supplier_label,
        "payment_status": e.payment_status,
        "due_date": ensure_utc(e.due_date),
        "paid_at": ensure_utc(e.paid_at),
        "reference": e.reference,
        "notes": e.notes,
        "status": e.status,
        "cancel_reason": e.cancel_reason,
        "cancelled_at": ensure_utc(e.cancelled_at),
        "activity": e.activity or ACTIVITY_OF_CATEGORY.get(e.category, "general"),
        "recorded_by": str(e.recorded_by) if e.recorded_by else None,
    }


def list_expenses(db: Session, *, days: int = 90) -> list[dict]:
    since = now_utc() - timedelta(days=max(1, min(days, 731)))
    rows = db.scalars(
        select(Expense)
        .where(Expense.deleted_at.is_(None), Expense.incurred_at >= since)
        .order_by(Expense.incurred_at.desc())
        .limit(1000)
    ).all()
    return [expense_dict(e) for e in rows]


# ------------------------------------------------------------ who owes what
def owed(db: Session, books: FarmBooks, *, receivables: bool = True, payables: bool = True) -> dict:
    """Money customers still owe the farm (sales to be paid) and money the
    farm still owes (bills to pay), oldest first, with each one's age."""
    today = books.today()
    out: dict[str, Any] = {"currency": fin.BASE_CURRENCY}
    if receivables:
        rows = db.scalars(
            select(Sale)
            .where(
                Sale.deleted_at.is_(None),
                Sale.status == "recorded",
                Sale.payment_status.in_(("pending", "partial")),
                Sale.amount > 0,
            )
            .order_by(Sale.sold_at)
        ).all()
        items = []
        by_buyer: dict[str, dict] = {}
        for s in rows:
            d = sale_dict(s)
            d["days_outstanding"] = (today - books.day(s.sold_at)).days
            items.append(d)
            key = (d["buyer_name"] or "").strip().lower()
            b = by_buyer.setdefault(
                key, {"buyer_name": d["buyer_name"], "amount": 0.0, "sales": 0, "oldest_days": 0}
            )
            b["amount"] += _f(s.amount)
            b["sales"] += 1
            b["oldest_days"] = max(b["oldest_days"], d["days_outstanding"])
        out["receivables"] = {
            "total": round(sum(_f(s.amount) for s in rows), 2),
            "items": items,
            "by_buyer": sorted(
                ({**b, "amount": round(b["amount"], 2)} for b in by_buyer.values()),
                key=lambda b: -b["amount"],
            ),
        }
    if payables:
        rows_e = db.scalars(
            select(Expense)
            .where(
                Expense.deleted_at.is_(None),
                Expense.status == "recorded",
                Expense.payment_status == "pending",
            )
            .order_by(Expense.incurred_at)
        ).all()
        items = []
        by_supplier: dict[str, dict] = {}
        overdue = 0.0
        for e in rows_e:
            d = expense_dict(e)
            due = books.day(e.due_date) if e.due_date else None
            d["days_outstanding"] = (today - books.day(e.incurred_at)).days
            d["overdue_days"] = (today - due).days if due and due < today else 0
            if d["overdue_days"]:
                overdue += _f(e.amount)
            items.append(d)
            key = (d["supplier_name"] or "").strip().lower()
            b = by_supplier.setdefault(
                key, {"supplier_name": d["supplier_name"], "amount": 0.0, "bills": 0, "overdue": 0.0}
            )
            b["amount"] += _f(e.amount)
            b["bills"] += 1
            if d["overdue_days"]:
                b["overdue"] += _f(e.amount)
        out["payables"] = {
            "total": round(sum(_f(e.amount) for e in rows_e), 2),
            "overdue": round(overdue, 2),
            "items": items,
            "by_supplier": sorted(
                (
                    {**b, "amount": round(b["amount"], 2), "overdue": round(b["overdue"], 2)}
                    for b in by_supplier.values()
                ),
                key=lambda b: -b["amount"],
            ),
        }
    return out


# ------------------------------------------------------------ profit & loss
def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def profit_loss(db: Session, books: FarmBooks, start: date | None, end: date | None) -> dict:
    """Month by month: revenue by activity, costs by line (feed fed,
    medicine received, farm-visit costs, every expense category), profit;
    and the cash that came in and went out. Default: the last 12 months."""
    tz = books.tz
    today = books.today()
    end = end or today
    if start is None:
        y, m = end.year, end.month - 11
        while m < 1:
            y, m = y - 1, m + 12
        start = date(y, m, 1)
    if start > end:
        raise FinanceError("The period starts after it ends.")
    if (end - start).days + 1 > MAX_PL_DAYS:
        raise FinanceError("Profit and loss covers at most two years.")
    lo = datetime(start.year, start.month, start.day, tzinfo=tz).astimezone(timezone.utc)
    nxt = end + timedelta(days=1)
    hi = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).astimezone(timezone.utc)

    def month_of(at: datetime) -> str:
        return _month(books.day(at))

    months: dict[str, dict[str, Any]] = {
        k: {
            "month": k,
            "revenue": 0.0,
            "revenue_by_activity": defaultdict(float),
            "costs": 0.0,
            "cost_lines": defaultdict(float),
            "costs_by_activity": defaultdict(float),
            "cash_in": 0.0,
            "cash_out": 0.0,
        }
        for k in _months(start, end)
    }

    def add_cost(at: datetime, line: str, activity: str, amount: float) -> None:
        mo = months.get(month_of(at))
        if mo is None or not amount:
            return
        mo["costs"] += amount
        mo["cost_lines"][line] += amount
        mo["costs_by_activity"][activity] += amount

    def add_cash(at: datetime | None, key: str, amount: float) -> None:
        utc = ensure_utc(at)
        if utc is None or not (lo <= utc < hi):
            return
        mo = months.get(month_of(utc))
        if mo is not None:
            mo[key] += amount

    # Revenue: every sale row — a cancelled sale and its reversal cancel out.
    for s in db.scalars(select(Sale).where(Sale.deleted_at.is_(None), Sale.sold_at >= lo, Sale.sold_at < hi)):
        mo = months.get(month_of(s.sold_at))
        if mo is None:
            continue
        act = s.activity or ACTIVITY_OF_PRODUCT.get(s.product_type, "general")
        mo["revenue"] += _f(s.amount)
        mo["revenue_by_activity"][act] += _f(s.amount)
    # Cash in: sales paid in the period (a refund of a paid sale is negative).
    for s in db.scalars(select(Sale).where(Sale.deleted_at.is_(None), Sale.payment_status == "paid")):
        add_cash(s.paid_at or s.sold_at, "cash_in", _f(s.amount))

    # Expenses and bills recorded here.
    for e in db.scalars(
        select(Expense).where(
            Expense.deleted_at.is_(None),
            Expense.status == "recorded",
            Expense.incurred_at >= lo,
            Expense.incurred_at < hi,
        )
    ):
        add_cost(
            e.incurred_at,
            f"expense:{e.category}",
            e.activity or ACTIVITY_OF_CATEGORY.get(e.category, "general"),
            _f(e.amount),
        )
    for e in db.scalars(
        select(Expense).where(
            Expense.deleted_at.is_(None), Expense.status == "recorded", Expense.payment_status == "paid"
        )
    ):
        add_cash(e.paid_at or e.incurred_at, "cash_out", _f(e.amount))

    # Feed fed: what the feed given cost, against the species it was fed to.
    species_of: dict[tuple[str, uuid.UUID], str] = {}
    for animal_id, animal_species in db.execute(
        select(Animal.id, Animal.species).where(Animal.deleted_at.is_(None))
    ):
        species_of[("animal", animal_id)] = animal_species
    for group_id, group_species in db.execute(
        select(AnimalGroup.id, AnimalGroup.species).where(AnimalGroup.deleted_at.is_(None))
    ):
        species_of[("group", group_id)] = group_species
    events = db.scalars(
        select(fm.FeedingEvent).where(
            fm.FeedingEvent.deleted_at.is_(None),
            fm.FeedingEvent.status == "recorded",
            fm.FeedingEvent.occurred_at >= lo,
            fm.FeedingEvent.occurred_at < hi,
        )
    ).all()
    for ev in events:
        if ev.event_type not in ("offered", "delivered"):
            continue
        cost = sum(c.cost or 0.0 for c in ev.components)
        species = species_of.get((ev.subject_type, ev.subject_id))
        add_cost(ev.occurred_at, "feed_fed", ACTIVITY_OF_SPECIES.get(species or "", "livestock"), cost)
    # Feed delivered: cash out when it arrives (it is a cost when fed).
    for lot in db.scalars(
        select(fm.FeedLot).where(
            fm.FeedLot.deleted_at.is_(None),
            fm.FeedLot.source_type == "purchased",
            fm.FeedLot.unit_cost.is_not(None),
        )
    ):
        add_cash(lot.received_at, "cash_out", round((lot.unit_cost or 0) * (lot.received_quantity or 0), 2))
    # Medicine received: a cost, and cash out, when it arrives.
    from app.farmos import pharmacy_models as pm

    for plot in db.scalars(
        select(pm.InventoryLot).where(
            pm.InventoryLot.deleted_at.is_(None),
            pm.InventoryLot.source_type == "purchased",
            pm.InventoryLot.unit_cost.is_not(None),
        )
    ):
        cost = round((plot.unit_cost or 0) * (plot.received_quantity or 0), 2)
        received = ensure_utc(plot.received_at)
        if received is None:
            continue
        if lo <= received < hi:
            add_cost(received, "medicine_received", "livestock", cost)
        add_cash(received, "cash_out", cost)
    # Farm-visit session costs.
    from app.farmos import visits_models as vm

    for c, day in db.execute(
        select(vm.VisitCost, vm.VisitSession.date)
        .join(vm.VisitSession, vm.VisitSession.id == vm.VisitCost.session_id)
        .where(vm.VisitCost.deleted_at.is_(None))
    ):
        # The session's day (noon on the farm), not when the cost was typed in.
        at = datetime(day.year, day.month, day.day, 12, tzinfo=tz).astimezone(timezone.utc)
        if lo <= at < hi:
            add_cost(at, "visit_costs", "visits", _f(c.amount))
        add_cash(at, "cash_out", _f(c.amount))

    rows = []
    totals = {"revenue": 0.0, "costs": 0.0, "profit": 0.0, "cash_in": 0.0, "cash_out": 0.0, "net_cash": 0.0}
    by_activity: dict[str, dict[str, Any]] = {}
    lines: dict[str, float] = defaultdict(float)
    for k in sorted(months):
        mo = months[k]
        profit = mo["revenue"] - mo["costs"]
        row = {
            "month": k,
            "revenue": round(mo["revenue"], 2),
            "costs": round(mo["costs"], 2),
            "profit": round(profit, 2),
            "cash_in": round(mo["cash_in"], 2),
            "cash_out": round(mo["cash_out"], 2),
            "net_cash": round(mo["cash_in"] - mo["cash_out"], 2),
            "revenue_by_activity": {
                a: round(v, 2) for a, v in mo["revenue_by_activity"].items() if round(v, 2)
            },
            "cost_lines": {line: round(v, 2) for line, v in mo["cost_lines"].items() if round(v, 2)},
        }
        rows.append(row)
        for key in ("revenue", "costs", "cash_in", "cash_out"):
            totals[key] += mo[key]
        for a, v in mo["revenue_by_activity"].items():
            by_activity.setdefault(a, {"activity": a, "revenue": 0.0, "costs": 0.0})["revenue"] += v
        for a, v in mo["costs_by_activity"].items():
            by_activity.setdefault(a, {"activity": a, "revenue": 0.0, "costs": 0.0})["costs"] += v
        for line, v in mo["cost_lines"].items():
            lines[line] += v
    totals["profit"] = totals["revenue"] - totals["costs"]
    totals["net_cash"] = totals["cash_in"] - totals["cash_out"]
    order = {a: i for i, a in enumerate(ACTIVITIES)}
    return {
        "currency": fin.BASE_CURRENCY,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "totals": {k: round(v, 2) for k, v in totals.items()},
        "months": rows,
        "by_activity": sorted(
            (
                {
                    "activity": a["activity"],
                    "revenue": round(a["revenue"], 2),
                    "costs": round(a["costs"], 2),
                    "profit": round(a["revenue"] - a["costs"], 2),
                }
                for a in by_activity.values()
                if round(a["revenue"], 2) or round(a["costs"], 2)
            ),
            key=lambda a: order.get(a["activity"], 99),
        ),
        "cost_lines": sorted(
            ({"line": line, "amount": round(v, 2)} for line, v in lines.items() if round(v, 2)),
            key=lambda x: -x["amount"],
        ),
    }
