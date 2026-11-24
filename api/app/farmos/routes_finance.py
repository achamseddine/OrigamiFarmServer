"""Origami's financials (reference v0.8.0 `api/v1/finance.py` and
`api/v1/sales.py`, PR 25): any sale, expenses and bills, who owes what,
profit and loss, and the USD / LBP exchange rate. The rules live in
app/farmos/finance_service.py; the books are `sale` and `expense`
(finance_models.py).

Two faces of the same books:

* `GET/POST /sales`, `GET/POST /expenses` — the stage 4 contract the
  tablet's Sales & Finance *Today* tab reads (`SalesProvider`), kept at the
  same paths with the same fields and the books' new fields added.
  A `POST` here is the same record as a `POST /finance/...`: `amount` is
  the sale's total.
* `/finance/...` — the v0.8.0 tabs: the rate, every sale and expense with
  mark paid and cancel, who owes what, profit and loss.

Who may do what (owners and managers hold everything):
- record a sale, mark one paid: Sales or Finance *create*;
- cancel a sale: Sales or Finance *approve*;
- record an expense or bill, mark it paid: Expenses or Finance *create*;
- cancel an expense: Expenses or Finance *approve*;
- set the exchange rate: Finance *approve* or Settings *configure*;
- read: sales with Sales / Finance, expenses with Expenses / Finance,
  profit and loss with Finance / Reports.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.db import get_control_db
from app.farmos import finance_service as finance
from app.farmos.deps import (
    AccessContext,
    check_farm_id,
    get_access_context,
    get_farmos_tenant_db,
    require_any_permission,
)
from app.farmos.finance_models import Expense, Sale
from app.farmos.finance_schemas import (
    CancelWithReason,
    ExchangeRateCreate,
    FinanceExpenseCreate,
    FinanceSaleCreate,
    MarkPaid,
)
from app.farmos.livestock_service import ensure_utc
from app.farmos.schemas import ExpenseCreate, ExpenseOut, SaleCreate, SaleOut

router = APIRouter()

# Finance is the tablet's Sales & Finance screen, which shows all of these;
# Sales and Expenses are the narrower responsibilities a manager can grant.
_ANY_MONEY = require_any_permission(
    ("sales", "view"), ("expenses", "view"), ("finance", "view"), ("reports", "view")
)
_SALES_VIEW = require_any_permission(("sales", "view"), ("finance", "view"))
_EXPENSES_VIEW = require_any_permission(("expenses", "view"), ("finance", "view"))
_PL_VIEW = require_any_permission(("finance", "view"), ("reports", "view"))


def _need(*grants: tuple[str, str], message: str) -> Callable:
    """A dependency: the caller holds one of [grants] ((module, action)),
    else 403 [message] — the sentence names the permission, as the
    reference does, so the farmer knows whom to ask."""

    def dep(access: AccessContext = Depends(get_access_context)) -> AccessContext:
        if not any(access.has(m, a) for m, a in grants):
            raise HTTPException(status_code=403, detail=message)
        return access

    return dep


_SELL = _need(
    ("sales", "create"),
    ("finance", "create"),
    message="Recording a sale needs Sales or Finance — ask the farm manager.",
)
_CANCEL_SALE = _need(
    ("sales", "approve"),
    ("finance", "approve"),
    message="Cancelling a sale is the farm manager's (Sales or Finance approval).",
)
_SPEND = _need(
    ("expenses", "create"),
    ("finance", "create"),
    message="Recording an expense needs Expenses or Finance — ask the farm manager.",
)
_CANCEL_EXPENSE = _need(
    ("expenses", "approve"),
    ("finance", "approve"),
    message="Cancelling an expense is the farm manager's (Expenses or Finance approval).",
)
_SET_RATE = _need(
    ("finance", "approve"),
    ("settings", "configure"),
    message="The exchange rate is set by the farm manager (Finance approval).",
)


# ------------------------------------------------------ the stage 4 contract
def _to_expense_out(e: Expense, tenant_id: uuid.UUID) -> ExpenseOut:
    return ExpenseOut(
        id=str(e.id),
        farm_id=str(tenant_id),
        supplier_id=str(e.supplier_id) if e.supplier_id else None,
        category=e.category,
        amount=float(e.amount),
        currency=e.currency,
        linked_entity_type=e.linked_entity_type,
        linked_entity_id=e.linked_entity_id,
        incurred_at=e.incurred_at,
        description=e.description,
        supplier_label=e.supplier_label,
        payment_status=e.payment_status,
        due_date=ensure_utc(e.due_date),
        paid_at=ensure_utc(e.paid_at),
        status=e.status,
        reference=e.reference,
        original_amount=e.original_amount,
        original_currency=e.original_currency,
        exchange_rate=e.exchange_rate,
        activity=e.activity or finance.ACTIVITY_OF_CATEGORY.get(e.category, "general"),
    )


def to_sale_out(s: Sale, tenant_id: uuid.UUID) -> SaleOut:
    return SaleOut(
        id=str(s.id),
        farm_id=str(tenant_id),
        customer_id=str(s.customer_id) if s.customer_id else None,
        product_type=s.product_type,
        product_label=s.product_label,
        quantity=float(s.quantity) if s.quantity is not None else None,
        unit=s.unit,
        amount=float(s.amount),
        currency=s.currency,
        payment_status=s.payment_status,
        sold_at=s.sold_at,
        status=s.status,
        unit_price=s.unit_price,
        buyer_name=s.buyer_name,
        reference=s.reference,
        paid_at=ensure_utc(s.paid_at),
        original_amount=s.original_amount,
        original_currency=s.original_currency,
        exchange_rate=s.exchange_rate,
        activity=s.activity or finance.ACTIVITY_OF_PRODUCT.get(s.product_type, "general"),
    )


@router.get("/expenses", response_model=list[ExpenseOut])
def list_expenses(
    farm_id: str = Query(...),
    access: AccessContext = Depends(_EXPENSES_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[ExpenseOut]:
    check_farm_id(farm_id, access)
    rows = (
        db.execute(select(Expense).where(Expense.deleted_at.is_(None)).order_by(Expense.incurred_at.desc()))
        .scalars()
        .all()
    )
    return [_to_expense_out(row, access.tenant_id) for row in rows]


@router.post("/expenses", response_model=ExpenseOut, status_code=201)
def create_expense(
    payload: ExpenseCreate,
    access: AccessContext = Depends(_SPEND),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> ExpenseOut:
    """The stage 4 manual entry: the same record `POST /finance/expenses`
    writes (a paid expense, in the farm's currency unless a rate applies)."""
    row = finance.record_expense(
        db,
        control_db,
        access,
        finance.farm_books(control_db, access.tenant_id),
        category=payload.category,
        description=None,
        amount=payload.amount,
        currency=payload.currency,
        exchange_rate=None,
        incurred_at=payload.incurred_at,
        supplier_id=payload.supplier_id,
        supplier_name=None,
        payment_status="paid",
        due_date=None,
        reference=None,
        notes=None,
        activity=None,
        confirm_duplicate=False,
        linked_entity_type=payload.linked_entity_type,
        linked_entity_id=payload.linked_entity_id,
    )
    return _to_expense_out(row, access.tenant_id)


@router.get("/sales", response_model=list[SaleOut])
def list_sales(
    farm_id: str = Query(...),
    access: AccessContext = Depends(_SALES_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[SaleOut]:
    """Every sale row — milk, eggs, produce, the farm shop — newest first,
    cancelled ones and their reversals included (they cancel out in the
    totals the Today tab adds up)."""
    check_farm_id(farm_id, access)
    rows = (
        db.execute(select(Sale).where(Sale.deleted_at.is_(None)).order_by(Sale.sold_at.desc()))
        .scalars()
        .all()
    )
    return [to_sale_out(row, access.tenant_id) for row in rows]


@router.post("/sales", response_model=SaleOut, status_code=201)
def create_sale(
    payload: SaleCreate,
    access: AccessContext = Depends(_SELL),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> SaleOut:
    """The stage 4 manual entry: `amount` is the sale's total; otherwise
    the same record `POST /finance/sales` writes."""
    sale = finance.record_sale(
        db,
        control_db,
        access,
        finance.farm_books(control_db, access.tenant_id),
        product_type=payload.product_type,
        product_label=payload.product_label,
        quantity=payload.quantity,
        unit=payload.unit,
        unit_price=None,
        total=payload.amount,
        currency=payload.currency,
        exchange_rate=None,
        sold_at=payload.sold_at,
        customer_id=payload.customer_id,
        buyer_name=None,
        payment_status=payload.payment_status,
        reference=None,
        notes=None,
        activity=None,
        confirm_duplicate=False,
    )
    return to_sale_out(sale, access.tenant_id)


# ------------------------------------------------------------------ the rate
@router.get("/finance/exchange-rates")
def exchange_rates(
    access: AccessContext = Depends(_ANY_MONEY),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """The rate in force today (LBP per USD) and the rates set before it."""
    return finance.rates(db, finance.farm_books(control_db, access.tenant_id))


@router.post("/finance/exchange-rates", status_code=201)
def set_exchange_rate(
    payload: ExchangeRateCreate,
    access: AccessContext = Depends(_SET_RATE),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """1 USD = [rate_per_usd] LBP from [effective_date] (today when left out).
    A new rate is a new row; the earlier ones stay for the days they covered."""
    row = finance.set_rate(
        db,
        control_db,
        access,
        finance.farm_books(control_db, access.tenant_id),
        currency=payload.currency.upper(),
        rate_per_usd=payload.rate_per_usd,
        effective_date=payload.effective_date,
        notes=payload.notes,
    )
    return finance.rate_dict(row)


# -------------------------------------------------------------------- sales
@router.get("/finance/sales")
def list_finance_sales(
    days: int = Query(90, ge=1, le=731),
    access: AccessContext = Depends(_SALES_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    """Every sale of the last [days] days — milk, eggs, produce, animals,
    Mouneh, the farm shop — newest first, cancelled ones and their
    reversals included."""
    return finance.list_sales(db, days=days)


@router.post("/finance/sales", status_code=201)
def record_sale(
    payload: FinanceSaleCreate,
    access: AccessContext = Depends(_SELL),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    sale = finance.record_sale(
        db,
        control_db,
        access,
        finance.farm_books(control_db, access.tenant_id),
        product_type=payload.product_type,
        product_label=payload.product_label,
        quantity=payload.quantity,
        unit=payload.unit,
        unit_price=payload.unit_price,
        total=payload.total,
        currency=payload.currency,
        exchange_rate=payload.exchange_rate,
        sold_at=payload.sold_at,
        customer_id=payload.customer_id,
        buyer_name=payload.buyer_name,
        payment_status=payload.payment_status,
        reference=payload.reference,
        notes=payload.notes,
        activity=payload.activity,
        confirm_duplicate=payload.confirm_duplicate,
    )
    return finance.sale_dict(sale)


@router.post("/finance/sales/{sale_id}/paid")
def mark_sale_paid(
    sale_id: str,
    payload: MarkPaid | None = None,
    access: AccessContext = Depends(_SELL),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    sale = finance.mark_sale_paid(
        db, control_db, access, finance.get_sale(db, sale_id), paid_at=payload.paid_at if payload else None
    )
    return finance.sale_dict(sale)


@router.post("/finance/sales/{sale_id}/cancel")
def cancel_sale(
    sale_id: str,
    payload: CancelWithReason,
    access: AccessContext = Depends(_CANCEL_SALE),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    sale = finance.cancel_sale(db, control_db, access, finance.get_sale(db, sale_id), reason=payload.reason)
    db.flush()
    db.refresh(sale)
    return finance.sale_dict(sale)


# ----------------------------------------------------------------- expenses
@router.get("/finance/expenses")
def list_finance_expenses(
    days: int = Query(90, ge=1, le=731),
    access: AccessContext = Depends(_EXPENSES_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    return finance.list_expenses(db, days=days)


@router.post("/finance/expenses", status_code=201)
def record_expense(
    payload: FinanceExpenseCreate,
    access: AccessContext = Depends(_SPEND),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    row = finance.record_expense(
        db,
        control_db,
        access,
        finance.farm_books(control_db, access.tenant_id),
        category=payload.category,
        description=payload.description,
        amount=payload.amount,
        currency=payload.currency,
        exchange_rate=payload.exchange_rate,
        incurred_at=payload.incurred_at,
        supplier_id=payload.supplier_id,
        supplier_name=payload.supplier_name,
        payment_status=payload.payment_status,
        due_date=payload.due_date,
        reference=payload.reference,
        notes=payload.notes,
        activity=payload.activity,
        confirm_duplicate=payload.confirm_duplicate,
    )
    return finance.expense_dict(row)


@router.post("/finance/expenses/{expense_id}/paid")
def mark_expense_paid(
    expense_id: str,
    payload: MarkPaid | None = None,
    access: AccessContext = Depends(_SPEND),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    row = finance.mark_expense_paid(
        db,
        control_db,
        access,
        finance.get_expense(db, expense_id),
        paid_at=payload.paid_at if payload else None,
    )
    return finance.expense_dict(row)


@router.post("/finance/expenses/{expense_id}/cancel")
def cancel_expense(
    expense_id: str,
    payload: CancelWithReason,
    access: AccessContext = Depends(_CANCEL_EXPENSE),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    row = finance.cancel_expense(
        db, control_db, access, finance.get_expense(db, expense_id), reason=payload.reason
    )
    return finance.expense_dict(row)


# ----------------------------------------------------- who owes what, P&L
@router.get("/finance/owed")
def owed(
    access: AccessContext = Depends(_ANY_MONEY),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """What customers still owe the farm (with Sales / Finance) and what the
    farm still owes its suppliers (with Expenses / Finance)."""
    receivables = access.has("sales", "view") or access.has("finance", "view")
    payables = access.has("expenses", "view") or access.has("finance", "view")
    if not (receivables or payables):
        raise HTTPException(
            status_code=403, detail="This needs Sales, Expenses or Finance — ask the farm manager."
        )
    return finance.owed(
        db, finance.farm_books(control_db, access.tenant_id), receivables=receivables, payables=payables
    )


@router.get("/finance/profit-loss")
def profit_loss(
    start: date | None = Query(None),
    end: date | None = Query(None),
    access: AccessContext = Depends(_PL_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Month by month from [start] to [end] (default the last 12 months):
    revenue by activity, costs by line, profit, cash in and out."""
    return finance.profit_loss(db, finance.farm_books(control_db, access.tenant_id), start, end)
