"""The farm's reports for a period (reference v0.7.9
`services/farm_reports_service.py`, PR 24): milk, feed, herd, finance;
sales and stock (v0.8.0, PR 25).

Every figure is read from the canonical records — milk records, milk
sales and the tank, feeding events and their components, delivered lots,
numbered mixes, animals, births, exits, health cases, sales and expenses
— never from a figure kept on the side. A period is whole farm days in
the farm's own time zone, from [start] to [end] inclusive.

The answers are data (codes, numbers, dates), not sentences: the tablet
and the web app label them in English or Arabic, draw them, print them and
export them.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos import feed_forecast_service as forecast
from app.farmos import feed_models as fm
from app.farmos import milk_models as mm
from app.farmos import milk_sales_service as milk_sales
from app.farmos import reproduction_models as rm
from app.farmos.feed_catalog import FeedError
from app.farmos.finance_models import Expense, Sale
from app.farmos.finance_service import ACTIVITY_OF_PRODUCT, FarmBooks
from app.farmos.health_case_models import HealthCase
from app.farmos.livestock_service import ensure_utc
from app.farmos.production_models import MilkRecord
from app.tenant_api.models import Animal, InventoryItem

KINDS = ("milk", "feed", "herd", "finance", "sales", "stock")
MAX_DAYS = 366


class ReportError(FeedError):
    pass


def period(
    books: FarmBooks, start: date | None, end: date | None
) -> tuple[date, date, datetime, datetime, ZoneInfo]:
    """The period's first and last farm day and its UTC bounds [lo, hi).
    Default: the last 30 days including today."""
    tz = books.tz
    today = books.today()
    end = end or today
    start = start or end - timedelta(days=29)
    if start > end:
        raise ReportError("The period starts after it ends.")
    if (end - start).days + 1 > MAX_DAYS:
        raise ReportError(f"A report covers at most {MAX_DAYS} days.")
    lo = datetime(start.year, start.month, start.day, tzinfo=tz).astimezone(timezone.utc)
    nxt = end + timedelta(days=1)
    hi = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).astimezone(timezone.utc)
    return start, end, lo, hi, tz


def _utc(at: datetime) -> datetime:
    utc = ensure_utc(at)
    assert utc is not None
    return utc


def _day(at: datetime, tz: ZoneInfo) -> str:
    return _utc(at).astimezone(tz).date().isoformat()


def _days(start: date, end: date) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def _r(v: Any, n: int = 2) -> float:
    return round(float(v or 0), n)


def _base(books: FarmBooks, kind: str, start: date, end: date) -> dict[str, Any]:
    return {
        "kind": kind,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": (end - start).days + 1,
        "farm_name": books.farm_name,
        "currency": books.currency,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------- milk
def milk_report(
    db: Session, books: FarmBooks, start: date | None, end: date | None, *, with_sales: bool = True
) -> dict:
    start, end, lo, hi, tz = period(books, start, end)
    out = _base(books, "milk", start, end)
    rows = db.execute(
        select(MilkRecord, Animal)
        .join(Animal, Animal.id == MilkRecord.animal_id)
        .where(MilkRecord.deleted_at.is_(None), MilkRecord.recorded_at >= lo, MilkRecord.recorded_at < hi)
    ).all()
    days: dict[str, dict[str, Any]] = {
        d: {
            "date": d,
            "morning": 0.0,
            "midday": 0.0,
            "evening": 0.0,
            "total": 0.0,
            "withheld": 0.0,
            "sold": 0.0,
        }
        for d in _days(start, end)
    }
    by_dest: dict[str, float] = defaultdict(float)
    animals: dict[str, dict[str, Any]] = {}
    for rec, animal in rows:
        d = days.get(_day(rec.recorded_at, tz))
        if d is None:
            continue
        liters = float(rec.liters)
        d[rec.session] = d.get(rec.session, 0.0) + liters
        d["total"] += liters
        if rec.destination == "withheld":
            d["withheld"] += liters
        by_dest[rec.destination] += liters
        a = animals.setdefault(
            str(animal.id),
            {
                "animal_id": str(animal.id),
                "tag": animal.tag,
                "name": animal.name,
                "species": animal.species,
                "milkings": 0,
                "total_l": 0.0,
                "days": set(),
                "best_day_l": 0.0,
                "_by_day": defaultdict(float),
            },
        )
        a["milkings"] += 1
        a["total_l"] += liters
        a["days"].add(d["date"])
        a["_by_day"][d["date"]] += liters
    total = sum(d["total"] for d in days.values())
    animal_rows = []
    for a in animals.values():
        animal_rows.append(
            {
                "animal_id": a["animal_id"],
                "tag": a["tag"],
                "name": a["name"],
                "species": a["species"],
                "milkings": a["milkings"],
                "total_l": _r(a["total_l"], 1),
                "days_milked": len(a["days"]),
                "avg_per_day_l": _r(a["total_l"] / len(a["days"]), 1) if a["days"] else 0.0,
                "best_day_l": _r(max(a["_by_day"].values()) if a["_by_day"] else 0, 1),
            }
        )
    animal_rows.sort(key=lambda r: -r["total_l"])

    sales = db.scalars(
        select(mm.MilkSale)
        .where(
            mm.MilkSale.deleted_at.is_(None),
            mm.MilkSale.status == "recorded",
            mm.MilkSale.sold_at >= lo,
            mm.MilkSale.sold_at < hi,
        )
        .order_by(mm.MilkSale.sold_at)
    ).all()
    for s in sales:
        d = days.get(_day(s.sold_at, tz))
        if d is not None:
            d["sold"] += s.liters
    sold_l = sum(s.liters for s in sales)
    revenue = sum(s.amount for s in sales)
    out.update(
        {
            "kpis": {
                "total_l": _r(total, 1),
                "avg_per_day_l": _r(total / out["days"], 1),
                "animals_milked": len(animals),
                "avg_per_animal_day_l": _r(sum(r["avg_per_day_l"] for r in animal_rows) / len(animal_rows), 1)
                if animal_rows
                else 0.0,
                "withheld_l": _r(by_dest.get("withheld", 0), 1),
                "tank_now_l": _r(milk_sales.tank_balance(db), 1),
                "tank_at_end_l": _r(milk_sales.tank_balance(db, before=hi), 1),
            },
            "destinations": [
                {"destination": k, "liters": _r(v, 1)}
                for k, v in sorted(by_dest.items(), key=lambda kv: -kv[1])
            ],
            "by_day": [
                {k: (_r(v, 1) if isinstance(v, float) else v) for k, v in d.items()} for d in days.values()
            ],
            "by_animal": animal_rows,
        }
    )
    if with_sales:
        buyers: dict[str, dict[str, Any]] = {}
        for s in sales:
            b = buyers.setdefault(
                s.buyer_name or "",
                {"buyer": s.buyer_name, "sales": 0, "liters": 0.0, "amount": 0.0, "pending": 0.0},
            )
            b["sales"] += 1
            b["liters"] += s.liters
            b["amount"] += s.amount
            if s.payment_status == "pending":
                b["pending"] += s.amount
        out["kpis"].update(
            {
                "sold_l": _r(sold_l, 1),
                "revenue": _r(revenue),
                "avg_price_per_liter": _r(revenue / sold_l, 3) if sold_l else None,
                "pending_amount": _r(sum(s.amount for s in sales if s.payment_status == "pending")),
            }
        )
        out["sales"] = [
            {
                "date": _day(s.sold_at, tz),
                "buyer": s.buyer_name,
                "liters": _r(s.liters, 1),
                "price_per_liter": s.price_per_liter,
                "amount": _r(s.amount),
                "payment_status": s.payment_status,
            }
            for s in sales
        ]
        out["by_buyer"] = [
            {**b, "liters": _r(b["liters"], 1), "amount": _r(b["amount"]), "pending": _r(b["pending"])}
            for b in sorted(buyers.values(), key=lambda b: -b["amount"])
        ]
    return out


# --------------------------------------------------------------------- feed
def feed_report(db: Session, books: FarmBooks, start: date | None, end: date | None) -> dict:
    start, end, lo, hi, tz = period(books, start, end)
    out = _base(books, "feed", start, end)
    products = {
        p.id: p for p in db.scalars(select(fm.FeedProduct).where(fm.FeedProduct.deleted_at.is_(None)))
    }

    def name_of(product_id: Any) -> str:
        p = products.get(product_id)
        return p.name if p else str(product_id)

    events = db.scalars(
        select(fm.FeedingEvent).where(
            fm.FeedingEvent.deleted_at.is_(None),
            fm.FeedingEvent.status == "recorded",
            fm.FeedingEvent.occurred_at >= lo,
            fm.FeedingEvent.occurred_at < hi,
        )
    ).all()
    used: dict[tuple[str, str], dict[str, Any]] = {}
    subjects = set()
    for e in events:
        if e.event_type not in ("offered", "delivered"):
            continue
        subjects.add((e.subject_type, e.subject_id))
        for c in e.components:
            u = used.setdefault(
                (str(c.feed_product_id), c.unit),
                {
                    "feed_product_id": str(c.feed_product_id),
                    "name": name_of(c.feed_product_id),
                    "unit": c.unit,
                    "quantity": 0.0,
                    "cost": 0.0,
                    "feedings": 0,
                },
            )
            u["quantity"] += c.quantity_offered
            u["cost"] += c.cost or 0.0
            u["feedings"] += 1
    used_rows = sorted(
        ({**u, "quantity": _r(u["quantity"], 1), "cost": _r(u["cost"])} for u in used.values()),
        key=lambda u: -u["quantity"],
    )

    lots = db.scalars(
        select(fm.FeedLot)
        .where(
            fm.FeedLot.deleted_at.is_(None),
            fm.FeedLot.source_type == "purchased",
            fm.FeedLot.received_at >= lo,
            fm.FeedLot.received_at < hi,
        )
        .order_by(fm.FeedLot.received_at)
    ).all()
    deliveries = [
        {
            "date": _day(lot.received_at, tz),
            "feed_product_id": str(lot.feed_product_id),
            "name": name_of(lot.feed_product_id),
            "quantity": _r(lot.received_quantity, 1),
            "unit": lot.unit,
            "supplier": lot.supplier_label,
            "unit_cost": lot.unit_cost,
            "cost": _r((lot.unit_cost or 0) * lot.received_quantity) if lot.unit_cost is not None else None,
            "lot_code": lot.lot_code,
        }
        for lot in lots
    ]
    batches = db.scalars(
        select(fm.FeedBatch)
        .where(
            fm.FeedBatch.deleted_at.is_(None),
            fm.FeedBatch.status == "completed",
            fm.FeedBatch.produced_at >= lo,
            fm.FeedBatch.produced_at < hi,
        )
        .order_by(fm.FeedBatch.mix_number)
    ).all()
    mixes = [
        {
            "mix_code": b.mix_code,
            "date": _day(b.produced_at, tz) if b.produced_at else None,
            "feed_product_id": str(b.feed_product_id),
            "name": name_of(b.feed_product_id),
            "quantity": _r(b.actual_quantity or 0, 1),
            "unit": b.unit,
            "cost": _r(b.actual_cost) if b.actual_cost is not None else None,
        }
        for b in batches
    ]
    stock = []
    for r in forecast.cover_for_products(db):
        stock.append(
            {
                "feed_product_id": r["feed_product_id"],
                "name": r["name"],
                "unit": r["unit"],
                "on_hand": _r(r["on_hand"], 1),
                "available": _r(r["available"], 1),
                "daily_demand": _r(r["daily_demand"], 1) if r["daily_demand"] is not None else None,
                "days_of_cover": r["days_of_cover"],
            }
        )

    kg_used = sum(u["quantity"] for u in used.values() if u["unit"] == "kg")
    out.update(
        {
            "kpis": {
                "feedings": sum(1 for e in events if e.event_type in ("offered", "delivered")),
                "subjects_fed": len(subjects),
                "feed_used_kg": _r(kg_used, 1),
                "feed_cost": _r(sum(u["cost"] for u in used.values())),
                "deliveries": len(deliveries),
                "delivered_kg": _r(sum(d["quantity"] for d in deliveries if d["unit"] == "kg"), 1),
                "delivered_cost": _r(sum(d["cost"] or 0 for d in deliveries)),
                "mixes": len(mixes),
                "mixed_kg": _r(sum(m["quantity"] for m in mixes if m["unit"] == "kg"), 1),
            },
            "used": used_rows,
            "deliveries": deliveries,
            "mixes": mixes,
            "stock": sorted(stock, key=lambda s: s["name"].lower()),
        }
    )
    return out


# --------------------------------------------------------------------- herd
def herd_report(db: Session, books: FarmBooks, start: date | None, end: date | None) -> dict:
    start, end, lo, hi, tz = period(books, start, end)
    out = _base(books, "herd", start, end)
    animals = db.scalars(select(Animal).where(Animal.deleted_at.is_(None))).all()
    species: dict[str, dict[str, Any]] = {}
    for a in animals:
        if not a.active:
            continue
        s = species.setdefault(
            a.species,
            {"species": a.species, "on_farm": 0, "females": 0, "males": 0, "in_milk": 0, "pregnant": 0},
        )
        s["on_farm"] += 1
        s["females"] += 1 if (a.sex or "").upper() == "F" else 0
        s["males"] += 1 if (a.sex or "").upper() == "M" else 0
        s["in_milk"] += 1 if a.lactating else 0
        s["pregnant"] += 1 if a.pregnant else 0
    by_id = {a.id: a for a in animals}
    births = db.scalars(
        select(rm.BirthEvent)
        .where(
            rm.BirthEvent.deleted_at.is_(None),
            rm.BirthEvent.status == "recorded",
            rm.BirthEvent.occurred_at >= lo,
            rm.BirthEvent.occurred_at < hi,
        )
        .order_by(rm.BirthEvent.occurred_at)
    ).all()
    exits: list[tuple[datetime, Animal]] = sorted(
        ((_utc(a.exited_at), a) for a in animals if a.exited_at is not None),
        key=lambda pair: pair[0],
    )
    exits = [(at, a) for at, a in exits if lo <= at < hi]
    added = [a for a in animals if a.created_at is not None and lo <= _utc(a.created_at) < hi]
    cases = db.scalars(
        select(HealthCase).where(
            HealthCase.deleted_at.is_(None), HealthCase.opened_at >= lo, HealthCase.opened_at < hi
        )
    ).all()
    exit_counts: dict[str, int] = defaultdict(int)
    for _at, a in exits:
        exit_counts[a.lifecycle_status] += 1

    def dam(b: rm.BirthEvent) -> tuple[str | None, str | None]:
        d = by_id.get(b.dam_animal_id)
        return (d.name, d.tag) if d else (str(b.dam_animal_id), None)

    out.update(
        {
            "kpis": {
                "on_farm": sum(s["on_farm"] for s in species.values()),
                "in_milk": sum(s["in_milk"] for s in species.values()),
                "pregnant": sum(s["pregnant"] for s in species.values()),
                "births": len(births),
                "born_alive": sum(b.live_count for b in births),
                "added": len(added),
                "exits": len(exits),
                "health_cases": len(cases),
                "health_cases_open": sum(1 for c in cases if c.status not in ("resolved", "closed")),
            },
            "by_species": sorted(species.values(), key=lambda s: -s["on_farm"]),
            "exit_counts": [{"status": k, "count": v} for k, v in sorted(exit_counts.items())],
            "births": [
                {
                    "date": _day(b.occurred_at, tz),
                    "dam": dam(b)[0],
                    "dam_tag": dam(b)[1],
                    "born": b.offspring_count,
                    "alive": b.live_count,
                    "stillborn": b.stillborn_count,
                }
                for b in births
            ],
            "exits": [
                {
                    "date": _day(at, tz),
                    "name": a.name,
                    "tag": a.tag,
                    "species": a.species,
                    "status": a.lifecycle_status,
                }
                for at, a in exits
            ],
        }
    )
    return out


# ------------------------------------------------------------------ finance
def finance_report(db: Session, books: FarmBooks, start: date | None, end: date | None) -> dict:
    start, end, lo, hi, tz = period(books, start, end)
    out = _base(books, "finance", start, end)
    sales = db.scalars(
        select(Sale)
        .where(Sale.deleted_at.is_(None), Sale.sold_at >= lo, Sale.sold_at < hi)
        .order_by(Sale.sold_at)
    ).all()
    expenses = db.scalars(
        select(Expense)
        .where(
            Expense.deleted_at.is_(None),
            Expense.incurred_at >= lo,
            Expense.incurred_at < hi,
            Expense.status == "recorded",
        )
        .order_by(Expense.incurred_at)
    ).all()
    by_type: dict[str, float] = defaultdict(float)
    by_label: dict[str, dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {
        d: {"date": d, "revenue": 0.0, "expenses": 0.0} for d in _days(start, end)
    }
    for s in sales:
        amount = float(s.amount)
        by_type[s.product_type] += amount
        lbl = by_label.setdefault(
            s.product_label or s.product_type,
            {
                "product": s.product_label or s.product_type,
                "product_type": s.product_type,
                "quantity": 0.0,
                "unit": s.unit,
                "amount": 0.0,
            },
        )
        lbl["quantity"] += float(s.quantity or 0.0)
        lbl["amount"] += amount
        d = by_day.get(_day(s.sold_at, tz))
        if d is not None:
            d["revenue"] += amount
    by_cat: dict[str, float] = defaultdict(float)
    for e in expenses:
        by_cat[e.category] += float(e.amount)
        d = by_day.get(_day(e.incurred_at, tz))
        if d is not None:
            d["expenses"] += float(e.amount)
    revenue = sum(float(s.amount) for s in sales)
    expense_total = sum(float(e.amount) for e in expenses)
    paid = sum(float(s.amount) for s in sales if s.payment_status == "paid")
    out.update(
        {
            "kpis": {
                "revenue": _r(revenue),
                "expenses": _r(expense_total),
                "margin": _r(revenue - expense_total),
                "cash_collected": _r(paid),
                "pending": _r(revenue - paid),
                "sales": len(sales),
            },
            "revenue_by_type": [
                {"product_type": k, "amount": _r(v)}
                for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])
            ],
            "revenue_by_product": [
                {**v, "quantity": _r(v["quantity"], 1), "amount": _r(v["amount"])}
                for v in sorted(by_label.values(), key=lambda v: -v["amount"])
            ],
            "expenses_by_category": [
                {"category": k, "amount": _r(v)} for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])
            ],
            "by_day": [
                {
                    **d,
                    "revenue": _r(d["revenue"]),
                    "expenses": _r(d["expenses"]),
                    "margin": _r(d["revenue"] - d["expenses"]),
                }
                for d in by_day.values()
            ],
            "sales": [
                {
                    "date": _day(s.sold_at, tz),
                    "product": s.product_label or s.product_type,
                    "product_type": s.product_type,
                    "quantity": None if s.quantity is None else float(s.quantity),
                    "unit": s.unit,
                    "amount": _r(s.amount),
                    "payment_status": s.payment_status,
                }
                for s in sales
            ],
        }
    )
    return out


# -------------------------------------------------------------------- sales
_CANCELLED_SUFFIX = " (cancelled sale)"


def sales_report(db: Session, books: FarmBooks, start: date | None, end: date | None) -> dict:
    """Everything sold in the period — every product, every module: what,
    how much, for how much, to whom, paid or still owed. A sale cancelled
    in the period shows as cancelled and its reversal takes it out of the
    totals."""
    start, end, lo, hi, tz = period(books, start, end)
    out = _base(books, "sales", start, end)
    rows = db.scalars(
        select(Sale)
        .where(Sale.deleted_at.is_(None), Sale.sold_at >= lo, Sale.sold_at < hi)
        .order_by(Sale.sold_at)
    ).all()
    today = books.today()

    def label(s: Sale) -> str:
        lbl = s.product_label or s.product_type
        return lbl[: -len(_CANCELLED_SUFFIX)] if lbl.endswith(_CANCELLED_SUFFIX) else lbl

    by_product: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    by_buyer: dict[str, dict[str, Any]] = {}
    by_activity: dict[str, float] = defaultdict(float)
    by_day: dict[str, dict[str, Any]] = {d: {"date": d, "sales": 0, "amount": 0.0} for d in _days(start, end)}
    unpaid: list[dict[str, Any]] = []
    for s in rows:
        amount = float(s.amount)
        key = (s.product_type, label(s), s.unit)
        p = by_product.setdefault(
            key,
            {
                "product_type": s.product_type,
                "product": key[1],
                "unit": s.unit,
                "quantity": 0.0,
                "amount": 0.0,
                "sales": 0,
            },
        )
        p["quantity"] += float(s.quantity or 0.0)
        p["amount"] += amount
        if s.status == "recorded":
            p["sales"] += 1
        name = s.buyer_name or ""
        b = by_buyer.setdefault(
            name.strip().lower(), {"buyer": s.buyer_name, "sales": 0, "amount": 0.0, "unpaid": 0.0}
        )
        b["amount"] += amount
        if s.status == "recorded":
            b["sales"] += 1
        by_activity[s.activity or ACTIVITY_OF_PRODUCT.get(s.product_type, "general")] += amount
        d = by_day.get(_day(s.sold_at, tz))
        if d is not None:
            d["amount"] += amount
            if s.status == "recorded":
                d["sales"] += 1
        if s.status == "recorded" and s.payment_status in ("pending", "partial") and amount > 0:
            b["unpaid"] += amount
            unpaid.append(
                {
                    "date": _day(s.sold_at, tz),
                    "product": label(s),
                    "buyer": s.buyer_name,
                    "amount": _r(amount),
                    "days": (today - _utc(s.sold_at).astimezone(tz).date()).days,
                }
            )
    revenue = sum(float(s.amount) for s in rows)
    paid = sum(float(s.amount) for s in rows if s.payment_status == "paid")
    count = sum(1 for s in rows if s.status == "recorded")
    out.update(
        {
            "kpis": {
                "sales": count,
                "revenue": _r(revenue),
                "paid": _r(paid),
                "unpaid": _r(sum(u["amount"] for u in unpaid)),
                "average_sale": _r(revenue / count) if count else 0.0,
                "cancelled": sum(1 for s in rows if s.status == "cancelled"),
                "buyers": len([k for k in by_buyer if k]),
            },
            "by_product": [
                {
                    **p,
                    "quantity": _r(p["quantity"], 2),
                    "amount": _r(p["amount"]),
                    "average_price": _r(p["amount"] / p["quantity"], 3) if p["quantity"] else None,
                }
                for p in sorted(by_product.values(), key=lambda p: -p["amount"])
            ],
            "by_activity": [
                {"activity": a, "amount": _r(v)}
                for a, v in sorted(by_activity.items(), key=lambda kv: -kv[1])
                if _r(v)
            ],
            "by_buyer": [
                {**b, "amount": _r(b["amount"]), "unpaid": _r(b["unpaid"])}
                for b in sorted(by_buyer.values(), key=lambda b: -b["amount"])
            ],
            "by_day": [{**d, "amount": _r(d["amount"])} for d in by_day.values()],
            "unpaid": unpaid,
            "sales": [
                {
                    "date": _day(s.sold_at, tz),
                    "product_type": s.product_type,
                    "product": label(s),
                    "quantity": None if s.quantity is None else float(s.quantity),
                    "unit": s.unit,
                    "unit_price": s.unit_price,
                    "amount": _r(s.amount),
                    "original_amount": s.original_amount,
                    "original_currency": s.original_currency,
                    "buyer": s.buyer_name,
                    "payment_status": s.payment_status,
                    "status": s.status,
                }
                for s in rows
            ],
        }
    )
    return out


# -------------------------------------------------------------------- stock
STOCK_SECTIONS = ("feed", "medicine", "mouneh_goods", "mouneh_materials", "milk", "other")


def stock_report(
    db: Session,
    books: FarmBooks,
    start: date | None,
    end: date | None,
    *,
    with_values: bool = True,
    sections: set[str] | None = None,
) -> dict:
    """What the farm has in stock now: feed, medicine, Mouneh goods and
    materials, milk in the tank and every other stock item — on hand, what
    can be used, what runs low or expires, and (for those who see money)
    what it is worth at its cost. Read from the lots and the ledger."""
    start, end, lo, hi, tz = period(books, start, end)
    out = _base(books, "stock", start, end)
    today = books.today()
    horizon = (datetime.now(timezone.utc) + timedelta(days=30)).astimezone(tz).date().isoformat()
    from app.farmos import mouneh_models as mo
    from app.farmos import pharmacy_models as pm
    from app.farmos import pharmacy_service as pharmacy

    used_items: set[Any] = set()
    # Feed: the stock ledger by lot.
    feed: list[dict[str, Any]] = []
    lots_by_product: dict[str, list[fm.FeedLot]] = defaultdict(list)
    for lot in db.scalars(
        select(fm.FeedLot).where(fm.FeedLot.deleted_at.is_(None), fm.FeedLot.quantity_on_hand > 0.0005)
    ):
        lots_by_product[str(lot.feed_product_id)].append(lot)
    for p in db.scalars(select(fm.FeedProduct).where(fm.FeedProduct.deleted_at.is_(None))):
        used_items.add(p.inventory_item_id)
    for r in forecast.cover_for_products(db):
        value = sum(
            (lot.quantity_on_hand or 0) * (lot.unit_cost or 0)
            for lot in lots_by_product.get(r["feed_product_id"], [])
        )
        low = r["status"] in ("below_minimum", "reorder", "stockout_risk") or (
            r["days_of_cover"] is not None and r["days_of_cover"] < 7
        )
        feed.append(
            {
                "name": r["name"],
                "unit": r["unit"],
                "on_hand": _r(r["on_hand"], 1),
                "available": _r(r["available"], 1),
                "reserved": _r(r["reserved"], 1),
                "unusable": _r(r["unusable"], 1),
                "days_of_cover": r["days_of_cover"],
                "low": low,
                "value": _r(value) if with_values else None,
            }
        )
    # Medicine: lots on hand, usable, expiring.
    medicine: list[dict[str, Any]] = []
    for prod in db.scalars(select(pm.MedicineProduct).where(pm.MedicineProduct.deleted_at.is_(None))):
        used_items.add(prod.inventory_item_id)
        item = db.get(InventoryItem, prod.inventory_item_id)
        b = pharmacy.stock_breakdown(db, prod)
        if b["on_hand"] <= 0.0005 and item is None:
            continue
        value = sum(
            (lot.quantity_on_hand or 0) * (lot.unit_cost or 0)
            for lot in prod.lots
            if lot.deleted_at is None and (lot.quantity_on_hand or 0) > 0
        )
        exp = b["earliest_expiry"]
        medicine.append(
            {
                "name": item.name if item else (prod.brand_name or prod.generic_name or "?"),
                "unit": item.unit if item else None,
                "on_hand": _r(b["on_hand"], 2),
                "usable": _r(b["eligible"], 2),
                "expired": _r(b["expired"], 2),
                "expiring_soon": _r(b["expiring_quantity"], 2),
                "earliest_expiry": _utc(exp).astimezone(tz).date().isoformat() if exp else None,
                "low": b["eligible"] <= 0.0005,
                "value": _r(value) if with_values else None,
            }
        )
    # Mouneh finished goods, by product.
    goods: dict[str, dict[str, Any]] = {}
    names = {
        p.id: p for p in db.scalars(select(mo.MounehProduct).where(mo.MounehProduct.deleted_at.is_(None)))
    }
    for g in db.scalars(select(mo.FinishedGoodsStock).where(mo.FinishedGoodsStock.deleted_at.is_(None))):
        prod_m = names.get(g.product_id)
        row = goods.setdefault(
            str(g.product_id),
            {
                "name": prod_m.name if prod_m else str(g.product_id),
                "unit": (prod_m.custom_output_unit_label or prod_m.output_unit) if prod_m else None,
                "available": 0.0,
                "reserved": 0.0,
                "sold": 0.0,
                "value": 0.0,
                "earliest_expiry": None,
                "threshold": float(prod_m.low_stock_threshold)
                if prod_m and prod_m.low_stock_threshold is not None
                else None,
            },
        )
        row["available"] += float(g.quantity_available or 0)
        row["reserved"] += float(g.quantity_reserved or 0)
        row["sold"] += float(g.quantity_sold or 0)
        row["value"] += float(g.quantity_available or 0) * float(g.unit_cost or 0)
        if g.expiry_date and float(g.quantity_available or 0) > 0:
            e = _utc(g.expiry_date).astimezone(tz).date().isoformat()
            row["earliest_expiry"] = (
                e if row["earliest_expiry"] is None or e < row["earliest_expiry"] else row["earliest_expiry"]
            )
    mouneh_goods: list[dict[str, Any]] = [
        {
            "name": g["name"],
            "unit": g["unit"],
            "available": _r(g["available"], 1),
            "reserved": _r(g["reserved"], 1),
            "sold": _r(g["sold"], 1),
            "earliest_expiry": g["earliest_expiry"],
            "low": g["threshold"] is not None and g["available"] <= g["threshold"],
            "value": _r(g["value"]) if with_values else None,
        }
        for g in sorted(goods.values(), key=lambda g: g["name"].lower())
    ]
    # Mouneh materials and packaging.
    materials: list[dict[str, Any]] = []
    for m in db.scalars(
        select(mo.RawMaterial).where(mo.RawMaterial.deleted_at.is_(None), mo.RawMaterial.active.is_(True))
    ):
        if m.inventory_item_id:
            used_items.add(m.inventory_item_id)
        if not m.stock_tracking_enabled:
            continue
        materials.append(
            {
                "name": m.name,
                "unit": m.unit,
                "category": m.category,
                "on_hand": _r(m.current_stock, 2),
                "low": float(m.current_stock or 0) <= 0,
                "value": _r(float(m.current_stock or 0) * float(m.default_unit_cost or 0))
                if with_values
                else None,
            }
        )
    # Every other stock item (tools, spare parts, supplies…).
    other: list[dict[str, Any]] = []
    for it in db.scalars(
        select(InventoryItem).where(InventoryItem.deleted_at.is_(None)).order_by(InventoryItem.name)
    ):
        if it.id in used_items:
            continue
        reorder = float(it.reorder_level or 0)
        on_hand = float(it.current_qty or 0)
        other.append(
            {
                "name": it.name,
                "category": it.category,
                "unit": it.unit,
                "on_hand": _r(on_hand, 2),
                "reorder_level": reorder,
                "low": reorder > 0 and on_hand <= reorder,
                "value": _r(on_hand * float(it.unit_cost or 0)) if with_values else None,
            }
        )
    tank = milk_sales.tank(db, books.currency)
    milk_value = (
        tank["balance_l"] * tank["last_price_per_liter"] if tank.get("last_price_per_liter") else None
    )
    # Each section only for whoever works in that area (all with Reports).
    show = set(STOCK_SECTIONS) if sections is None else sections
    feed = feed if "feed" in show else []
    medicine = medicine if "medicine" in show else []
    mouneh_goods = mouneh_goods if "mouneh_goods" in show else []
    materials = materials if "mouneh_materials" in show else []
    other = other if "other" in show else []
    if "milk" not in show:
        milk_value = None
    sections_ = (feed, medicine, mouneh_goods, materials, other)
    lines = sum(1 for sec in sections_ for r in sec if (r.get("on_hand") or r.get("available") or 0) > 0)
    expiring = sum(1 for r in medicine if float(r["expiring_soon"]) > 0) + sum(
        1 for r in mouneh_goods if r["earliest_expiry"] and str(r["earliest_expiry"]) <= horizon
    )
    value = sum((r.get("value") or 0) for sec in sections_ for r in sec) + (milk_value or 0)
    out.update(
        {
            "as_of": today.isoformat(),
            "sections": [x for x in STOCK_SECTIONS if x in show],
            "kpis": {
                "lines": lines,
                "low": sum(1 for sec in sections_ for r in sec if r.get("low")),
                "expiring": expiring,
                "milk_in_tank_l": _r(tank["balance_l"], 1) if "milk" in show else None,
                "value": _r(value) if with_values else None,
            },
            "feed": feed,
            "medicine": medicine,
            "mouneh_goods": mouneh_goods,
            "mouneh_materials": materials,
            "other": other,
            "milk": None
            if "milk" not in show
            else {
                "in_tank_l": _r(tank["balance_l"], 1),
                "last_price_per_liter": tank.get("last_price_per_liter") if with_values else None,
                "value": _r(milk_value) if with_values and milk_value is not None else None,
            },
        }
    )
    return out


def build(
    db: Session,
    books: FarmBooks,
    kind: str,
    start: date | None,
    end: date | None,
    *,
    with_sales: bool = True,
    stock_sections: set[str] | None = None,
) -> dict:
    if kind == "milk":
        return milk_report(db, books, start, end, with_sales=with_sales)
    if kind == "feed":
        return feed_report(db, books, start, end)
    if kind == "herd":
        return herd_report(db, books, start, end)
    if kind == "finance":
        return finance_report(db, books, start, end)
    if kind == "sales":
        return sales_report(db, books, start, end)
    if kind == "stock":
        return stock_report(db, books, start, end, with_values=with_sales, sections=stock_sections)
    raise ReportError(f"kind must be one of {list(KINDS)}", 404)
