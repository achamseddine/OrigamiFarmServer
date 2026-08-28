"""Expenses and sales — both read-only in this contract today; see
app/farmos/finance_models.py for why (no matching POST endpoint exists
yet — rows land here via other write paths as those get built).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos.deps import AccessContext, check_farm_id, get_farmos_tenant_db, require_permission
from app.farmos.finance_models import Expense, Sale
from app.farmos.schemas import ExpenseOut, SaleOut

router = APIRouter()


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
    )


@router.get("/expenses", response_model=list[ExpenseOut])
def list_expenses(
    farm_id: str = Query(...),
    access: AccessContext = Depends(require_permission("finance", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[ExpenseOut]:
    check_farm_id(farm_id, access)
    rows = db.execute(
        select(Expense).where(Expense.deleted_at.is_(None)).order_by(Expense.incurred_at.desc())
    ).scalars().all()
    return [_to_expense_out(row, access.tenant_id) for row in rows]


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
    )


@router.get("/sales", response_model=list[SaleOut])
def list_sales(
    farm_id: str = Query(...),
    access: AccessContext = Depends(require_permission("sales", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[SaleOut]:
    check_farm_id(farm_id, access)
    rows = db.execute(
        select(Sale).where(Sale.deleted_at.is_(None)).order_by(Sale.sold_at.desc())
    ).scalars().all()
    return [to_sale_out(row, access.tenant_id) for row in rows]
