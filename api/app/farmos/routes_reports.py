"""GET /morning-briefing — the tablet app's home-screen summary: today's
date, a handful of top priorities, today's tasks, and a few headline KPIs —
and the farm's reports for a period (stage 13, reference v0.7.9/v0.8.0
`api/v1/reports.py`): `GET /reports/{milk,feed,herd,finance,sales,stock}`,
read from the canonical records by app/farmos/farm_reports_service.py.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.common.db import get_control_db
from app.common.enums import MembershipStatus
from app.farmos import farm_reports_service as farm_reports
from app.farmos import finance_service as finance
from app.farmos.deps import (
    AccessContext,
    check_farm_id,
    get_access_context,
    get_farmos_tenant_db,
    require_any_permission,
    require_permission,
)
from app.farmos.finance_models import Expense, Sale
from app.farmos.permissions import MODULE_LABELS
from app.farmos.production_models import EggRecord, MilkRecord
from app.farmos.recommendations import FEED_COST_SHARE_THRESHOLD
from app.farmos.routes_priorities import build_priorities
from app.farmos.routes_tasks import to_task_out
from app.farmos.schemas import (
    AmountByCategory,
    AmountByProductLabel,
    AmountByProductType,
    DailySummaryOut,
    MorningBriefingOut,
)
from app.tenant_api.models import Animal, Field, Task
from app.tenants.models import Tenant, TenantMembership

router = APIRouter()

_OPEN_TASK_STATUSES = ("open", "in_progress")
_MANAGER_ROLE_RANK = {"owner": 0, "manager": 1}

_MORNING_VIEW = require_permission("morning_operations", "view")
# The daily summary is sales and expense totals: the tablet's Sales &
# Finance screen loads it for whoever holds Finance.
_SUMMARY_VIEW = require_any_permission(("reports", "view"), ("finance", "view"))


@router.get("/morning-briefing", response_model=MorningBriefingOut)
def morning_briefing(
    farm_id: str = Query(...),
    access: AccessContext = Depends(_MORNING_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> MorningBriefingOut:
    check_farm_id(farm_id, access)

    tenant = control_db.get(Tenant, access.tenant_id)
    assert tenant is not None  # AccessContext already resolved this same tenant

    managers = (
        control_db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == access.tenant_id,
                TenantMembership.status == MembershipStatus.ACTIVE,
                TenantMembership.role.in_(("owner", "manager")),
            )
        )
        .scalars()
        .all()
    )
    manager_name = None
    if managers:
        best = min(managers, key=lambda m: _MANAGER_ROLE_RANK.get(m.role, 99))
        user = control_db.get(UserIdentity, best.user_id)
        manager_name = user.display_name if user else None

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    animal_count = db.execute(
        select(func.count()).select_from(Animal).where(Animal.deleted_at.is_(None), Animal.active.is_(True))
    ).scalar_one()
    active_fields = db.execute(
        select(func.count()).select_from(Field).where(Field.deleted_at.is_(None), Field.status == "active")
    ).scalar_one()
    milk_today_l = db.execute(
        select(func.coalesce(func.sum(MilkRecord.liters), 0)).where(
            MilkRecord.deleted_at.is_(None), MilkRecord.recorded_at >= today_start
        )
    ).scalar_one()
    eggs_today = db.execute(
        select(func.coalesce(func.sum(EggRecord.sellable_eggs), 0)).where(
            EggRecord.deleted_at.is_(None), EggRecord.recorded_at >= today_start
        )
    ).scalar_one()

    todays_tasks = list(
        db.execute(
            select(Task)
            .where(
                Task.deleted_at.is_(None),
                Task.status.in_(_OPEN_TASK_STATUSES),
                Task.due_at.is_not(None),
                Task.due_at <= now,
            )
            .order_by(Task.due_at.asc())
        )
        .scalars()
        .all()
    )
    tasks_due = len(todays_tasks)

    priorities = build_priorities(db, control_db)
    open_alerts = sum(1 for p in priorities if p.priority in ("critical", "high"))

    return MorningBriefingOut(
        date=now.date().isoformat(),
        farm_name=tenant.display_name,
        manager_name=manager_name,
        kpis={
            "animals": animal_count,
            "milk_today_l": float(milk_today_l),
            "eggs_today": eggs_today,
            "active_fields": active_fields,
            "tasks_due": tasks_due,
            "open_alerts": open_alerts,
        },
        priorities=priorities[:5],
        tasks=[to_task_out(t) for t in todays_tasks[:10]],
    )


@router.get("/reports/daily-summary", response_model=DailySummaryOut)
def daily_summary(
    farm_id: str = Query(...),
    access: AccessContext = Depends(_SUMMARY_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> DailySummaryOut:
    check_farm_id(farm_id, access)
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    sales = (
        db.execute(select(Sale).where(Sale.deleted_at.is_(None), Sale.sold_at >= today_start)).scalars().all()
    )
    # A cancelled expense stays on file but is not spent (v0.8.0); a
    # cancelled sale and its negative reversal cancel each other out.
    expenses = (
        db.execute(
            select(Expense).where(
                Expense.deleted_at.is_(None), Expense.incurred_at >= today_start, Expense.status == "recorded"
            )
        )
        .scalars()
        .all()
    )

    revenue_today = sum(float(s.amount) for s in sales)
    expenses_today = sum(float(e.amount) for e in expenses)
    cash_collected = sum(float(s.amount) for s in sales if s.payment_status == "paid")
    pending_payments = sum(float(s.amount) for s in sales if s.payment_status != "paid")

    by_product_type: dict[str, float] = {}
    by_product_label: dict[str, float] = {}
    for s in sales:
        by_product_type[s.product_type] = by_product_type.get(s.product_type, 0) + float(s.amount)
        label = s.product_label or s.product_type
        by_product_label[label] = by_product_label.get(label, 0) + float(s.amount)

    by_category: dict[str, float] = {}
    for e in expenses:
        by_category[e.category] = by_category.get(e.category, 0) + float(e.amount)

    top_products = sorted(by_product_label.items(), key=lambda kv: kv[1], reverse=True)[:5]

    business_insights: list[str] = []
    if expenses_today > 0:
        feed_share = by_category.get("feed", 0) / expenses_today
        if feed_share > FEED_COST_SHARE_THRESHOLD:
            business_insights.append(
                f"Feed remains the highest-share cost category at {feed_share:.1%} of total "
                "expenses. Consider reviewing feed usage or suppliers."
            )

    return DailySummaryOut(
        date=now.date().isoformat(),
        revenue_today=revenue_today,
        expenses_today=expenses_today,
        gross_margin=revenue_today - expenses_today,
        cash_collected=cash_collected,
        pending_payments=pending_payments,
        sales_breakdown=[AmountByProductType(product_type=k, amount=v) for k, v in by_product_type.items()],
        expense_breakdown=[AmountByCategory(category=k, amount=v) for k, v in by_category.items()],
        top_selling_products=[AmountByProductLabel(product_label=k, amount=v) for k, v in top_products],
        business_insights=business_insights,
    )


# ------------------------------------------------------------- the reports
# Who reads each report: Reports holders read them all; otherwise the
# report's own area. Money (milk sales in the milk report, the finance
# report, stock values) is shown only to someone who may see sales or finance.
_REPORT_MODULES = {
    "milk": ("milk_production",),
    "feed": ("feed_nutrition", "inventory"),
    "herd": ("animals", "animal_health"),
    "finance": ("finance", "sales"),
    "sales": ("sales", "finance"),
    "stock": ("inventory", "feed_nutrition", "mouneh_inventory", "animal_health", "milk_production"),
}
_MONEY_MODULES = ("reports", "finance", "sales")
# The stock report shows each part to whoever works in that area.
_STOCK_SECTION_MODULES = {
    "feed": ("feed_nutrition", "inventory"),
    "medicine": ("animal_health", "inventory"),
    "mouneh_goods": ("mouneh_inventory", "mouneh_production", "inventory"),
    "mouneh_materials": ("mouneh_inventory", "mouneh_production", "inventory"),
    "milk": ("milk_production",),
    "other": ("inventory",),
}


def _farm_report(
    kind: str, start: date | None, end: date | None, db: Session, control_db: Session, access: AccessContext
) -> dict:
    """The farm's report for a period — milk, feed, herd, finance, sales or
    stock — from [start] to [end] (farm days, inclusive; default the last
    30 days). Read from the canonical records every time."""
    modules = ("reports", *_REPORT_MODULES[kind])
    if not any(access.has(m, "view") for m in modules):
        names = " or ".join(MODULE_LABELS[m] for m in modules)
        raise HTTPException(
            status_code=403, detail=f"This report needs {names}. Ask a farm manager to grant it."
        )
    money = any(access.has(m, "view") for m in _MONEY_MODULES)
    sections = None
    if kind == "stock" and not access.has("reports", "view"):
        sections = {
            section
            for section, areas in _STOCK_SECTION_MODULES.items()
            if any(access.has(m, "view") for m in areas)
        }
    books = finance.farm_books(control_db, access.tenant_id)
    return farm_reports.build(db, books, kind, start, end, with_sales=money, stock_sections=sections)


# One route per report, not `/reports/{kind}`: a path parameter there would
# also answer `/reports/daily-summary` and the other modules' reports.
@router.get("/reports/milk")
def milk_report(
    start: date | None = Query(None),
    end: date | None = Query(None),
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Milk for a period: produced, per day and animal, where it went, the tank; with money, the sales."""
    return _farm_report("milk", start, end, db, control_db, access)


@router.get("/reports/feed")
def feed_report(
    start: date | None = Query(None),
    end: date | None = Query(None),
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Feed for a period: feedings, feed used and its cost, deliveries, mixes, stock now."""
    return _farm_report("feed", start, end, db, control_db, access)


@router.get("/reports/herd")
def herd_report(
    start: date | None = Query(None),
    end: date | None = Query(None),
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """The herd for a period: on the farm by species, births, animals added and that left, health cases."""
    return _farm_report("herd", start, end, db, control_db, access)


@router.get("/reports/finance")
def finance_report(
    start: date | None = Query(None),
    end: date | None = Query(None),
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Money for a period: revenue, expenses, margin, cash collected, unpaid; by product, category and day."""
    return _farm_report("finance", start, end, db, control_db, access)


@router.get("/reports/sales")
def sales_report(
    start: date | None = Query(None),
    end: date | None = Query(None),
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Everything sold in a period: by product, buyer, activity and day; what is still unpaid; every sale."""
    return _farm_report("sales", start, end, db, control_db, access)


@router.get("/reports/stock")
def stock_report(
    start: date | None = Query(None),
    end: date | None = Query(None),
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """What is in stock now: feed, medicine, Mouneh goods and materials, milk in the tank, other items;
    low and expiring stock; its value at cost (for those who see money)."""
    return _farm_report("stock", start, end, db, control_db, access)
