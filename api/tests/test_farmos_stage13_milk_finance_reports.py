"""Stage 13 of the FarmOS tablet contract: milk sold from the tank, the
farm's financials and the period reports. Ports the reference backend's
test_milk_sales_and_reports.py (v0.7.9, PR 24) and test_finance.py
(v0.8.0, PR 25) to the server's fixtures, plus the cross-tenant isolation
of the three new tables.

* The tank is worked out from the records: stored milk − sold + adjustments.
* A milk sale writes the farm's revenue (Sales & Finance sees it); paid or
  pending; cancelled with a reason, reversed — never deleted.
* Selling more than the tank holds needs the manager; a count puts the
  tank right. An employee without Sales / Finance does not sell.
* Any sale and any expense or bill, in USD or in LBP at the rate the
  manager set (or the rate given); the LBP amount and the rate stay on the
  record, every total is in USD. Who owes what; profit and loss per month
  and activity.
* Reports for a period — milk, feed, herd, finance, sales, stock — from the
  canonical records, money only for those who may see it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.audit.models import AuditEvent
from app.common.tenant_router import TenantDataRouter
from app.farmos.duplicates import PREFIX
from app.farmos.finance_models import Sale
from app.farmos.mouneh_models import FinishedGoodsStock, MounehProduct
from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant

API = "/api/v1"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _login(client, email: str) -> dict:
    return farmos_headers(farmos_login(client, email, FARMOS_DEMO_PASSWORD))


def _audit(control_db, tenant_id, action: str, entity_id: str | None = None) -> list[AuditEvent]:
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id, AuditEvent.action == action)
    if entity_id is not None:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    return list(control_db.scalars(stmt.order_by(AuditEvent.created_at)))


class Farm:
    """A farm built through the API: an owner, a worker who records milk
    (view/create on Milk Production, nothing on money), a dairy cow with
    four stored milkings in the tank, a feed and a feeding."""

    def __init__(
        self, client, control_db, prefix: str, *, worker_grid: dict | None = None, milk: bool = True
    ):
        self.client = client
        self.control_db = control_db
        self.tenant = create_tenant(control_db, company_code=unique_code(prefix))
        key = prefix.lower()
        self.owner, _ = add_farmos_user(
            control_db, self.tenant, f"{key}-owner@origami-demo.com", role="owner", display_name="Rami"
        )
        self.worker, _ = add_farmos_user(
            control_db,
            self.tenant,
            f"{key}-worker@origami-demo.com",
            role="worker",
            display_name="Karim",
            grid=worker_grid or {"milk_production": ["view", "create"], "animals": ["view"]},
        )
        control_db.commit()
        self.h = _login(client, self.owner.email)
        self.worker_h = _login(client, self.worker.email)
        self.farm_id = str(self.tenant.id)
        self.bella = self.post(
            "/animals",
            {
                "name": "Bella",
                "species": "cow",
                "sex": "F",
                "life_stage": "adult",
                "management_profile": "dairy",
                "lactating": True,
                "lactation_started_at": _iso(120),
                "identifiers": [
                    {"identifier_type": "EAR_TAG", "identifier_value": "744", "is_primary": True}
                ],
            },
        )
        if milk:
            # 4 × 30 L stored on four different days: 120 L in the tank.
            for days_ago in (3, 2, 1, 0):
                self.milk(30, session="morning", days_ago=days_ago)

    # -- http
    def post(self, path, body=None, expect=201, h=None):
        r = self.client.post(f"{API}{path}", json=body if body is not None else {}, headers=h or self.h)
        assert r.status_code == expect, f"POST {path}: {r.status_code} {r.text}"
        return r.json()

    def raw_post(self, path, body=None, h=None):
        return self.client.post(f"{API}{path}", json=body if body is not None else {}, headers=h or self.h)

    def get(self, path, params=None, h=None, expect=200):
        r = self.client.get(f"{API}{path}", params=params, headers=h or self.h)
        assert r.status_code == expect, f"GET {path}: {r.status_code} {r.text}"
        return r.json()

    def raw_get(self, path, params=None, h=None):
        return self.client.get(f"{API}{path}", params=params, headers=h or self.h)

    # -- the records
    def milk(self, liters: float, *, session: str = "midday", days_ago: float = 0, h=None) -> dict:
        return self.post(
            "/production/milk",
            {
                "animal_id": self.bella["id"],
                "session": session,
                "liters": liters,
                "destination": "stored",
                "recorded_at": _iso(days_ago),
            },
            h=h,
        )

    def tank(self, h=None) -> float:
        return self.get("/production/milk-tank", h=h)["balance_l"]

    def sell_milk(self, h=None, **body):
        return self.raw_post(
            "/production/milk-sales",
            {"liters": 100, "price_per_liter": 0.6, "buyer_name": "Bekaa Dairy", **body},
            h=h,
        )

    def rate(self, rate=89500.0, h=None, **body):
        return self.raw_post(
            "/finance/exchange-rates", {"currency": "LBP", "rate_per_usd": rate, **body}, h=h
        )

    def sell(self, h=None, **body):
        payload = {
            "product_type": "eggs",
            "product_label": "Eggs (tray of 30)",
            "quantity": 10,
            "unit": "tray",
            "unit_price": 6,
            "buyer_name": "Abu Karim shop",
            "payment_status": "paid",
        }
        payload.update(body)
        return self.raw_post("/finance/sales", payload, h=h)

    def spend(self, h=None, **body):
        payload = {
            "category": "fuel",
            "description": "Diesel for the tractor",
            "amount": 120,
            "supplier_name": "Bekaa Fuel",
            "payment_status": "paid",
        }
        payload.update(body)
        return self.raw_post("/finance/expenses", payload, h=h)

    def summary(self) -> dict:
        return self.get("/reports/daily-summary", params={"farm_id": self.farm_id})

    def revenue_rows(self) -> list[dict]:
        return self.get("/sales", params={"farm_id": self.farm_id})

    def feed(self) -> None:
        """A feed with stock and one feeding of Bella, for the feed report."""
        barley = self.post(
            "/feed-products",
            {"name": "Barley", "unit_cost": 0.36, "opening_quantity": 1000, "is_feedable": True},
        )
        self.post(
            "/feeding-events",
            {
                "subject_type": "animal",
                "subject_id": self.bella["id"],
                "event_type": "offered",
                "components": [{"feed_product_id": barley["id"], "quantity_offered": 8}],
            },
        )


# ------------------------------------------------------------------ the tank
def test_the_tank_is_stored_milk_less_what_was_sold(client, control_db):
    f = Farm(client, control_db, "S13-TANK")
    tank = f.get("/production/milk-tank")
    assert tank["balance_l"] == round(tank["stored_l"] - tank["sold_l"] + tank["adjusted_l"], 3) == 120
    assert tank["currency"] == "USD" and tank["last_price_per_liter"] is None
    f.milk(12)
    assert f.tank() == 132, "stored milk goes into the tank"
    sold = f.sell_milk(liters=40)
    assert sold.status_code == 201, sold.text
    assert f.tank() == 92
    tank = f.get("/production/milk-tank")
    assert (tank["last_price_per_liter"], tank["last_buyer_name"]) == (0.6, "Bekaa Dairy")
    # Withheld milk never reaches the tank.
    f.post(
        "/production/milk",
        {"animal_id": f.bella["id"], "session": "evening", "liters": 5, "destination": "withheld"},
    )
    assert f.tank() == 92


# -------------------------------------------------------------------- a sale
def test_a_milk_sale_is_revenue_in_sales_and_finance(client, control_db):
    f = Farm(client, control_db, "S13-REV")
    sale = f.sell_milk(liters=50, price_per_liter=1.1, payment_status="paid").json()
    assert (sale["amount"], sale["buyer_name"], sale["payment_status"], sale["status"]) == (
        55.0,
        "Bekaa Dairy",
        "paid",
        "recorded",
    )
    revenue = [s for s in f.revenue_rows() if s["product_type"] == "milk"]
    assert any(
        s["amount"] == 55 and s["quantity"] == 50 and s["unit"] == "L" and s["unit_price"] == 1.1
        for s in revenue
    )
    assert revenue[0]["buyer_name"] == "Bekaa Dairy" and revenue[0]["activity"] == "dairy"
    summary = f.summary()
    assert summary["revenue_today"] >= 55
    assert any(b["product_type"] == "milk" for b in summary["sales_breakdown"])
    # The buyer is found again by name, whatever the case: the same litres
    # to "bekaa dairy" within half an hour is the same collection.
    again = f.sell_milk(liters=50, buyer_name="bekaa dairy")
    assert again.status_code == 409 and again.json()["detail"].startswith(PREFIX), again.text
    assert f.sell_milk(liters=10, buyer_name="bekaa dairy").status_code == 201
    audit = _audit(control_db, f.tenant.id, "milk.sold", sale["id"])
    assert len(audit) == 1 and audit[0].module_code == "milk_production", "every sale is in the audit history"
    assert audit[0].metadata_json["over_tank"] is False


def test_to_be_paid_then_paid(client, control_db):
    f = Farm(client, control_db, "S13-PAID")
    sale = f.sell_milk(payment_status="pending").json()
    assert sale["paid_at"] is None
    assert f.summary()["pending_payments"] >= 60
    paid = f.post(f"/production/milk-sales/{sale['id']}/paid", expect=200)
    assert paid["payment_status"] == "paid" and paid["paid_at"]
    with TenantDataRouter.session_for(f.tenant.id) as db:
        rows = db.scalars(select(Sale).where(Sale.product_type == "milk")).all()
        assert rows and all(s.payment_status == "paid" and s.paid_at is not None for s in rows)
    assert f.summary()["pending_payments"] == 0
    assert _audit(control_db, f.tenant.id, "milk_sale.paid", sale["id"])


def test_a_cancelled_sale_stays_on_file_reversed(client, control_db):
    f = Farm(client, control_db, "S13-CANCEL")
    before = f.tank()
    sale = f.sell_milk(liters=30).json()
    assert f.raw_post(f"/production/milk-sales/{sale['id']}/cancel", {"reason": ""}).status_code == 422
    cancelled = f.post(
        f"/production/milk-sales/{sale['id']}/cancel", {"reason": "Typed 30 for 3"}, expect=200
    )
    assert (cancelled["status"], cancelled["cancel_reason"]) == ("cancelled", "Typed 30 for 3")
    assert f.tank() == before, "the milk is back in the tank"
    milk = [s for s in f.revenue_rows() if s["product_type"] == "milk"]
    assert sorted(s["amount"] for s in milk if abs(s["amount"]) == 18) == [-18, 18], "reversed, both kept"
    assert {s["status"] for s in milk} == {"cancelled", "reversal"}
    report = f.get("/reports/milk")
    assert all(s["liters"] != 30 for s in report["sales"]), "a cancelled sale is not sold milk"
    assert f.raw_post(f"/production/milk-sales/{sale['id']}/cancel", {"reason": "again"}).status_code == 409
    assert f.raw_post(f"/production/milk-sales/{sale['id']}/paid").status_code == 409
    assert f.summary()["revenue_today"] == 0, "the two rows cancel out"
    event = _audit(control_db, f.tenant.id, "milk_sale.cancelled", sale["id"])
    assert len(event) == 1 and event[0].metadata_json["reason"] == "Typed 30 for 3"


def test_more_than_the_tank_holds_needs_the_manager(client, control_db):
    f = Farm(client, control_db, "S13-OVER")
    # A seller without the milk approval: Sales create only.
    seller, _ = add_farmos_user(
        control_db,
        f.tenant,
        "s13-over-seller@origami-demo.com",
        role="worker",
        grid={"sales": ["view", "create"]},
    )
    control_db.commit()
    seller_h = _login(client, seller.email)
    tank = f.tank()
    over = f.sell_milk(liters=tank + 500)
    assert over.status_code == 422 and "tank" in over.json()["detail"], over.text
    refused = f.sell_milk(liters=tank + 500, allow_negative=True, h=seller_h)
    assert refused.status_code == 403 and "Milk production approval" in refused.json()["detail"]
    anyway = f.sell_milk(liters=tank + 500, allow_negative=True)
    assert anyway.status_code == 201, anyway.text
    assert f.tank() < 0, "the records are behind: a count puts them right"
    assert (
        _audit(control_db, f.tenant.id, "milk.sold", anyway.json()["id"])[0].metadata_json["over_tank"]
        is True
    )
    counted = f.post("/production/milk-tank/adjustments", {"reason": "count", "counted_level": 120})
    assert counted["reason"] == "count" and counted["counted_level"] == 120
    assert f.tank() == 120
    spoiled = f.post(
        "/production/milk-tank/adjustments", {"reason": "spoiled", "liters": 20, "notes": "Power cut"}
    )
    assert spoiled["liters"] == -20 and spoiled["notes"] == "Power cut"
    assert f.tank() == 100
    listed = f.get("/production/milk-tank/adjustments")
    assert [a["reason"] for a in listed] == ["spoiled", "count"]
    assert f.raw_post("/production/milk-tank/adjustments", {"reason": "spoiled"}).status_code == 422
    assert f.raw_post("/production/milk-tank/adjustments", {"reason": "count"}).status_code == 422
    assert (
        f.raw_post("/production/milk-tank/adjustments", {"reason": "evaporated", "liters": 1}).status_code
        == 422
    )
    assert len(_audit(control_db, f.tenant.id, "milk_tank.adjusted")) == 2


def test_the_same_collection_twice_is_asked(client, control_db):
    f = Farm(client, control_db, "S13-DUP")
    assert f.sell_milk(liters=17).status_code == 201
    again = f.sell_milk(liters=17)
    assert again.status_code == 409 and again.json()["detail"].startswith(PREFIX), again.text
    assert "17 L of milk to Bekaa Dairy" in again.json()["detail"]
    confirmed = f.sell_milk(liters=17, confirm_duplicate=True)
    assert confirmed.status_code == 201
    logged = _audit(control_db, f.tenant.id, "duplicate.confirmed")
    assert len(logged) == 1 and logged[0].metadata_json["record"] == f"milk_sale:{confirmed.json()['id']}"
    # A different buyer the same minute is another collection.
    assert f.sell_milk(liters=17, buyer_name="Hotel Massabki").status_code == 201


def test_an_employee_without_sales_does_not_sell_or_adjust(client, control_db):
    f = Farm(client, control_db, "S13-WORKER")
    assert f.raw_get("/production/milk-tank", h=f.worker_h).status_code == 200, "they see the tank"
    assert f.raw_get("/production/milk-sales", h=f.worker_h).status_code == 200
    refused = f.sell_milk(h=f.worker_h)
    assert refused.status_code == 403 and "Selling milk" in refused.json()["detail"], refused.text
    r = f.raw_post("/production/milk-tank/adjustments", {"reason": "spoiled", "liters": 5}, h=f.worker_h)
    assert r.status_code == 403
    sale = f.sell_milk(payment_status="pending").json()
    assert f.raw_post(f"/production/milk-sales/{sale['id']}/paid", h=f.worker_h).status_code == 403
    assert (
        f.raw_post(f"/production/milk-sales/{sale['id']}/cancel", {"reason": "x"}, h=f.worker_h).status_code
        == 403
    )
    # Someone with Sales sells and marks paid, but cancelling needs an approval.
    seller, _ = add_farmos_user(
        control_db,
        f.tenant,
        "s13-worker-seller@origami-demo.com",
        role="worker",
        grid={"sales": ["view", "create"]},
    )
    control_db.commit()
    seller_h = _login(client, seller.email)
    assert f.sell_milk(liters=5, h=seller_h).status_code == 201
    assert f.raw_post(f"/production/milk-sales/{sale['id']}/paid", h=seller_h).status_code == 200
    assert (
        f.raw_post(f"/production/milk-sales/{sale['id']}/cancel", {"reason": "x"}, h=seller_h).status_code
        == 403
    )


def test_a_sale_cannot_be_dated_in_the_future_or_be_empty(client, control_db):
    f = Farm(client, control_db, "S13-FUTURE")
    tomorrow = (date.today() + timedelta(days=2)).isoformat() + "T08:00:00Z"
    future = f.sell_milk(sold_at=tomorrow)
    assert (
        future.status_code == 422 and future.json()["detail"] == "A milk sale cannot be dated in the future."
    )
    assert f.sell_milk(liters=0).status_code == 422
    assert f.sell_milk(price_per_liter=-1).status_code == 422
    assert f.sell_milk(payment_status="partial").status_code == 422
    assert f.raw_post("/production/milk-sales/not-a-sale/paid").status_code == 404


# ----------------------------------------------------------------- reports
def test_the_milk_report_reads_the_records(client, control_db):
    f = Farm(client, control_db, "S13-MILKREP")
    f.sell_milk(liters=50, price_per_liter=0.5, payment_status="pending")
    report = f.get("/reports/milk")
    assert report["kind"] == "milk" and report["days"] == 30 and len(report["by_day"]) == 30
    assert report["farm_name"] == f.tenant.display_name and report["currency"] == "USD"
    assert report["kpis"]["total_l"] == round(sum(d["total"] for d in report["by_day"]), 1) == 120
    assert any(a["animal_id"] == f.bella["id"] and a["tag"] == "744" for a in report["by_animal"])
    assert report["kpis"]["tank_now_l"] == 70 and report["kpis"]["tank_at_end_l"] == 70
    assert report["kpis"]["sold_l"] == 50 and report["kpis"]["revenue"] == 25
    assert report["kpis"]["pending_amount"] == 25 and report["kpis"]["avg_price_per_liter"] == 0.5
    assert report["sales"] and report["by_buyer"][0]["buyer"] == "Bekaa Dairy"
    assert [d["destination"] for d in report["destinations"]] == ["stored"]


def test_a_report_for_a_chosen_period(client, control_db):
    f = Farm(client, control_db, "S13-PERIOD", milk=False)
    end = date.today()
    start = end - timedelta(days=6)
    report = f.get("/reports/milk", params={"start": start.isoformat(), "end": end.isoformat()})
    assert (report["start"], report["end"], report["days"]) == (start.isoformat(), end.isoformat(), 7)
    assert (
        f.raw_get("/reports/milk", params={"start": end.isoformat(), "end": start.isoformat()}).status_code
        == 422
    )
    assert (
        f.raw_get("/reports/milk", params={"start": (end - timedelta(days=400)).isoformat()}).status_code
        == 422
    )
    assert f.raw_get("/reports/weather").status_code == 404


def test_feed_herd_and_finance_reports(client, control_db):
    f = Farm(client, control_db, "S13-REPORTS")
    f.feed()
    feed = f.get("/reports/feed")
    assert feed["kpis"]["feedings"] == 1 and feed["kpis"]["subjects_fed"] == 1
    assert feed["used"][0]["name"] == "Barley" and feed["used"][0]["quantity"] == 8
    assert feed["kpis"]["feed_used_kg"] == round(
        sum(u["quantity"] for u in feed["used"] if u["unit"] == "kg"), 1
    )
    assert feed["kpis"]["feed_cost"] == round(8 * 0.36, 2)
    assert feed["stock"][0]["name"] == "Barley" and feed["stock"][0]["on_hand"] == 992
    herd = f.get("/reports/herd")
    assert herd["kpis"]["on_farm"] == sum(s["on_farm"] for s in herd["by_species"]) == 1
    assert herd["by_species"][0] == {
        "species": "cow",
        "on_farm": 1,
        "females": 1,
        "males": 0,
        "in_milk": 1,
        "pregnant": 0,
    }
    assert herd["kpis"]["added"] == 1 and herd["kpis"]["exits"] == 0
    f.sell_milk(liters=10, price_per_liter=1.0)
    f.spend(amount=4)
    finance = f.get("/reports/finance")
    assert any(r["product_type"] == "milk" and r["amount"] == 10 for r in finance["revenue_by_type"])
    assert finance["kpis"]["expenses"] == 4
    assert (
        finance["kpis"]["margin"] == round(finance["kpis"]["revenue"] - finance["kpis"]["expenses"], 2) == 6
    )
    assert finance["expenses_by_category"] == [{"category": "fuel", "amount": 4}]


def test_who_reads_which_report(client, control_db):
    f = Farm(client, control_db, "S13-WHO")
    f.sell_milk(liters=10)
    milk = f.raw_get("/reports/milk", h=f.worker_h)
    assert milk.status_code == 200
    body = milk.json()
    assert "sales" not in body and "revenue" not in body["kpis"], "no money without Sales or Finance"
    assert body["kpis"]["total_l"] == 120
    feed = f.raw_get("/reports/feed", h=f.worker_h)
    assert feed.status_code == 403 and "Feed & Nutrition" in feed.json()["detail"]
    finance = f.raw_get("/reports/finance", h=f.worker_h)
    assert finance.status_code == 403 and finance.json()["detail"].startswith(
        "This report needs Reports or Finance"
    )
    # The Reports permission alone reads them all, money included.
    reader, _ = add_farmos_user(
        control_db, f.tenant, "s13-who-reader@origami-demo.com", role="worker", grid={"reports": ["view"]}
    )
    control_db.commit()
    reader_h = _login(client, reader.email)
    for kind in ("milk", "feed", "herd", "finance", "sales", "stock"):
        assert f.raw_get(f"/reports/{kind}", h=reader_h).status_code == 200, kind
    assert f.get("/reports/milk", h=reader_h)["kpis"]["revenue"] == 6


# ------------------------------------------------------------------ the rate
def test_the_manager_sets_the_lbp_rate_and_it_has_a_history(client, control_db):
    f = Farm(client, control_db, "S13-RATE", milk=False)
    rates = f.get("/finance/exchange-rates")
    assert rates["current"]["LBP"] is None and rates["currencies"] == ["USD", "LBP"]
    first = f.rate(89000, effective_date=(date.today() - timedelta(days=10)).isoformat())
    assert first.status_code == 201, first.text
    assert f.rate(89500).status_code == 201
    rates = f.get("/finance/exchange-rates")
    assert rates["base"] == "USD"
    assert rates["current"]["LBP"]["rate_per_usd"] == 89500
    assert rates["current"]["LBP"]["set_by"] == str(f.owner.id)
    assert [r["rate_per_usd"] for r in rates["history"]][:2] == [
        89500,
        89000,
    ], "the earlier rate stays for its days"
    assert f.rate(0).status_code == 422
    assert f.rate(90000, effective_date=(date.today() + timedelta(days=2)).isoformat()).status_code == 422
    assert f.rate(90000, currency="EUR").status_code == 422
    refused = f.rate(90000, h=f.worker_h)
    assert refused.status_code == 403 and "Finance approval" in refused.json()["detail"]
    assert f.raw_get("/finance/exchange-rates", h=f.worker_h).status_code == 403
    events = _audit(control_db, f.tenant.id, "exchange_rate.set")
    assert len(events) == 2 and events[1].changes_json == {"rate_per_usd": {"from": 89000, "to": 89500}}


# -------------------------------------------------------------------- a sale
def test_any_sale_in_usd_is_revenue_with_its_buyer(client, control_db):
    f = Farm(client, control_db, "S13-SALE", milk=False)
    sale = f.sell().json()
    assert (sale["amount"], sale["currency"], sale["unit_price"], sale["buyer_name"]) == (
        60,
        "USD",
        6,
        "Abu Karim shop",
    )
    assert (sale["status"], sale["payment_status"], sale["activity"]) == ("recorded", "paid", "poultry")
    assert sale["paid_at"] and sale["original_amount"] is None and sale["exchange_rate"] is None
    listed = f.get("/finance/sales")
    assert listed[0]["id"] == sale["id"]
    legacy = f.revenue_rows()
    assert legacy[0]["id"] == sale["id"] and legacy[0]["buyer_name"] == "Abu Karim shop"
    assert legacy[0]["farm_id"] == f.farm_id and legacy[0]["status"] == "recorded"
    summary = f.summary()
    assert any(b["product_type"] == "eggs" for b in summary["sales_breakdown"])
    audit = _audit(control_db, f.tenant.id, "sale.recorded", sale["id"])
    assert len(audit) == 1 and audit[0].module_code == "sales"
    assert audit[0].summary == "Sold Eggs (tray of 30) × 10 tray for 60 USD to Abu Karim shop (paid)"
    assert f.raw_get("/finance/sales", params={"days": 0}).status_code == 422


def test_a_sale_in_lbp_uses_the_days_rate_or_the_rate_given(client, control_db):
    f = Farm(client, control_db, "S13-LBP", milk=False)
    no_rate = f.sell(currency="LBP", unit_price=537000, quantity=1, product_label="Tray")
    assert no_rate.status_code == 422 and "exchange rate" in no_rate.json()["detail"]
    f.rate(89500)
    sale = f.sell(currency="LBP", unit_price=537000, quantity=1, product_label="Tray").json()
    assert (sale["original_amount"], sale["original_currency"], sale["exchange_rate"]) == (
        537000,
        "LBP",
        89500,
    )
    assert sale["amount"] == 6.0, "537,000 LBP at 89,500 = 6 USD"
    own = f.sell(
        currency="LBP",
        total=900000,
        quantity=None,
        unit_price=None,
        product_label="Old hen",
        exchange_rate=90000,
        product_type="animals",
    ).json()
    assert (own["amount"], own["exchange_rate"], own["activity"]) == (10.0, 90000, "livestock")
    assert f.sell(currency="EUR").status_code == 422
    assert f.sell(currency="LBP", exchange_rate=0).status_code == 422
    audit = _audit(control_db, f.tenant.id, "sale.recorded", sale["id"])[0]
    assert "537,000 LBP (6 USD)" in audit.summary


def test_milk_and_mouneh_are_sold_where_their_stock_is(client, control_db):
    f = Farm(client, control_db, "S13-WHERE", milk=False)
    milk = f.sell(product_type="milk")
    assert milk.status_code == 422 and milk.json()["detail"].startswith("Milk is sold from the tank")
    assert f.sell(product_type="mouneh").status_code == 422
    assert f.sell(product_type="cars").status_code == 422
    assert f.sell(quantity=None, unit_price=None).status_code == 422, "no price, no total"
    assert f.sell(unit_price=0).status_code == 422, "a sale of nothing"
    assert f.sell(quantity=-1).status_code == 422
    assert f.sell(activity="space").status_code == 422
    assert f.sell(sold_at=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat()).status_code == 422
    assert f.sell(customer_id="not-a-customer").status_code == 404


def test_to_be_paid_then_paid_then_owed_no_more(client, control_db):
    f = Farm(client, control_db, "S13-OWED", milk=False)
    sale = f.sell(payment_status="pending", buyer_name="Hotel Massabki").json()
    owed = f.get("/finance/owed")
    assert owed["currency"] == "USD" and "payables" in owed
    receivables = owed["receivables"]
    assert receivables["total"] == 60
    assert any(i["id"] == sale["id"] and i["days_outstanding"] == 0 for i in receivables["items"])
    assert any(b["buyer_name"] == "Hotel Massabki" and b["amount"] >= 60 for b in receivables["by_buyer"])
    paid = f.post(f"/finance/sales/{sale['id']}/paid", {}, expect=200)
    assert paid["payment_status"] == "paid" and paid["paid_at"]
    receivables = f.get("/finance/owed")["receivables"]
    assert all(i["id"] != sale["id"] for i in receivables["items"])
    assert _audit(control_db, f.tenant.id, "sale.paid", sale["id"])
    # Paid twice is not an error, and a payment is never dated in the future.
    assert f.raw_post(f"/finance/sales/{sale['id']}/paid", {}).status_code == 200
    other = f.sell(payment_status="pending", buyer_name="Chtaura market").json()
    future = f.raw_post(
        f"/finance/sales/{other['id']}/paid",
        {"paid_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    )
    assert future.status_code == 422 and future.json()["detail"] == "A payment cannot be dated in the future."


def test_a_cancelled_sale_stays_on_file_and_is_reversed(client, control_db):
    f = Farm(client, control_db, "S13-REVERSE", milk=False)
    sale = f.sell(payment_status="pending").json()
    assert f.raw_post(f"/finance/sales/{sale['id']}/cancel", {"reason": ""}).status_code == 422
    cancelled = f.post(f"/finance/sales/{sale['id']}/cancel", {"reason": "typed twice"}, expect=200)
    assert (cancelled["status"], cancelled["cancel_reason"]) == ("cancelled", "typed twice")
    assert cancelled["cancelled_at"]
    rows = f.get("/finance/sales")
    reversal = next(r for r in rows if r["reversal_of_id"] == sale["id"])
    assert (reversal["status"], reversal["amount"], reversal["quantity"]) == ("reversal", -60, -10)
    assert reversal["product_label"] == "Eggs (tray of 30) (cancelled sale)"
    assert f.raw_post(f"/finance/sales/{sale['id']}/cancel", {"reason": "again"}).status_code == 409
    assert f.raw_post(f"/finance/sales/{reversal['id']}/cancel", {"reason": "x"}).status_code == 409
    assert f.raw_post(f"/finance/sales/{sale['id']}/paid", {}).status_code == 409
    owed = f.get("/finance/owed")["receivables"]
    assert all(i["id"] != sale["id"] for i in owed["items"]), "a cancelled sale is owed by nobody"
    assert f.summary()["revenue_today"] == 0
    event = _audit(control_db, f.tenant.id, "sale.cancelled", sale["id"])
    assert len(event) == 1 and event[0].metadata_json["reversal_sale_id"] == reversal["id"]
    # A farm-shop sale from a visit is cancelled in Farm visits, not here.
    with TenantDataRouter.session_for(f.tenant.id) as db:
        from app.farmos.visits_models import VisitRetailSale

        shop = Sale(
            tenant_id=f.tenant.id,
            product_type="visit_retail",
            product_label="Farm shop — 1 item(s)",
            quantity=1,
            amount=12,
            currency="USD",
            payment_status="paid",
            sold_at=datetime.now(timezone.utc),
        )
        db.add(shop)
        db.flush()
        db.add(VisitRetailSale(tenant_id=f.tenant.id, sale_id=shop.id, channel="farm_shop", total_amount=12))
        shop_id = str(shop.id)
    r = f.raw_post(f"/finance/sales/{shop_id}/cancel", {"reason": "x"})
    assert r.status_code == 409 and "Farm visits" in r.json()["detail"]
    assert next(s for s in f.get("/finance/sales") if s["id"] == shop_id)["activity"] == "visits"


def test_cancelling_a_milk_sale_from_finance_puts_the_milk_back(client, control_db):
    f = Farm(client, control_db, "S13-MILKFIN")
    tank = f.tank()
    milk = f.sell_milk(liters=30, price_per_liter=0.5, buyer_name="Dairy", payment_status="pending").json()
    assert f.tank() == tank - 30
    revenue = next(r for r in f.get("/finance/sales") if r["product_type"] == "milk" and r["quantity"] == 30)
    assert revenue["buyer_name"] == "Dairy" and revenue["unit_price"] == 0.5
    # Marking the revenue row paid marks the milk sale paid too.
    assert f.post(f"/finance/sales/{revenue['id']}/paid", {}, expect=200)["payment_status"] == "paid"
    assert (
        next(s for s in f.get("/production/milk-sales") if s["id"] == milk["id"])["payment_status"] == "paid"
    )
    assert f.post(f"/finance/sales/{revenue['id']}/cancel", {"reason": "wrong day"}, expect=200)[
        "status"
    ] == ("cancelled")
    assert f.tank() == tank
    cancelled = next(s for s in f.get("/production/milk-sales") if s["id"] == milk["id"])
    assert cancelled["status"] == "cancelled" and cancelled["cancel_reason"] == "wrong day"
    assert _audit(control_db, f.tenant.id, "milk_sale.cancelled", milk["id"])


def test_the_same_sale_twice_is_asked(client, control_db):
    f = Farm(client, control_db, "S13-DUPSALE", milk=False)
    assert f.sell().status_code == 201
    again = f.sell()
    assert again.status_code == 409 and again.json()["detail"].startswith(PREFIX)
    assert f.sell(confirm_duplicate=True).status_code == 201
    assert f.sell(buyer_name="Someone else").status_code == 201, "another buyer is another sale"
    assert f.spend().status_code == 201
    again = f.spend()
    assert again.status_code == 409 and "fuel expense of 120 USD to Bekaa Fuel" in again.json()["detail"]
    assert f.spend(confirm_duplicate=True).status_code == 201
    logged = _audit(control_db, f.tenant.id, "duplicate.confirmed")
    assert {e.entity_type for e in logged} == {"sale", "expense"}


# ----------------------------------------------------------------- expenses
def test_an_expense_in_lbp_and_a_bill_to_pay(client, control_db):
    f = Farm(client, control_db, "S13-BILL", milk=False)
    f.rate(89500)
    paid = f.spend(currency="LBP", amount=8950000).json()
    assert (paid["amount"], paid["original_amount"], paid["supplier_name"], paid["payment_status"]) == (
        100,
        8950000,
        "Bekaa Fuel",
        "paid",
    )
    assert paid["paid_at"] and paid["due_date"] is None and paid["activity"] == "general"
    due = (date.today() - timedelta(days=3)).isoformat() + "T00:00:00Z"
    bill = f.spend(
        category="veterinary",
        description="Dr. Haddad visit",
        amount=150,
        supplier_name="Dr. Haddad",
        payment_status="pending",
        due_date=due,
        incurred_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
    ).json()
    assert bill["activity"] == "livestock" and bill["due_date"] and bill["paid_at"] is None
    payables = f.get("/finance/owed")["payables"]
    item = next(i for i in payables["items"] if i["id"] == bill["id"])
    assert item["overdue_days"] >= 3 and item["days_outstanding"] >= 10 and payables["overdue"] >= 150
    assert (
        payables["by_supplier"][0]["supplier_name"] == "Dr. Haddad"
        and payables["by_supplier"][0]["bills"] == 1
    )
    assert f.post(f"/finance/expenses/{bill['id']}/paid", {}, expect=200)["payment_status"] == "paid"
    assert all(i["id"] != bill["id"] for i in f.get("/finance/owed")["payables"]["items"])
    legacy = f.get("/expenses", params={"farm_id": f.farm_id})
    assert {e["supplier_label"] for e in legacy} == {"Bekaa Fuel", "Dr. Haddad"}
    assert all(e["status"] == "recorded" and e["payment_status"] == "paid" for e in legacy)
    audit = _audit(control_db, f.tenant.id, "expense.recorded")
    assert len(audit) == 2 and "8,950,000 LBP (100 USD) to Bekaa Fuel (paid)" in audit[0].summary
    assert "to pay" in audit[1].summary
    assert _audit(control_db, f.tenant.id, "expense.paid", bill["id"])


def test_a_cancelled_expense_is_out_of_the_totals(client, control_db):
    f = Farm(client, control_db, "S13-EXPCANCEL", milk=False)
    before = f.summary()["expenses_today"]
    e = f.spend(amount=77, category="repairs").json()
    assert f.summary()["expenses_today"] == before + 77
    assert f.raw_post(f"/finance/expenses/{e['id']}/cancel", {"reason": ""}).status_code == 422
    cancelled = f.post(f"/finance/expenses/{e['id']}/cancel", {"reason": "not ours"}, expect=200)
    assert (cancelled["status"], cancelled["cancel_reason"]) == ("cancelled", "not ours")
    assert f.summary()["expenses_today"] == before
    assert f.get("/reports/finance")["kpis"]["expenses"] == 0
    assert f.raw_post(f"/finance/expenses/{e['id']}/paid", {}).status_code == 409
    assert f.raw_post(f"/finance/expenses/{e['id']}/cancel", {"reason": "again"}).status_code == 409
    assert f.spend(category="gambling").status_code == 422
    assert f.spend(amount=0).status_code == 422
    assert f.spend(payment_status="partial").status_code == 422
    assert f.spend(supplier_id="nobody").status_code == 404
    assert (
        f.spend(incurred_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()).status_code == 422
    )
    assert _audit(control_db, f.tenant.id, "expense.cancelled", e["id"])[0].metadata_json == {
        "reason": "not ours"
    }
    # The cancelled expense is still on file for whoever reads the list.
    assert next(x for x in f.get("/finance/expenses") if x["id"] == e["id"])["status"] == "cancelled"


def test_an_employee_without_finance_records_no_money(client, control_db):
    f = Farm(client, control_db, "S13-NOMONEY", milk=False)
    sell = f.sell(h=f.worker_h)
    assert sell.status_code == 403 and sell.json()["detail"] == (
        "Recording a sale needs Sales or Finance — ask the farm manager."
    )
    spend = f.spend(h=f.worker_h)
    assert spend.status_code == 403 and "Expenses or Finance" in spend.json()["detail"]
    assert f.raw_get("/finance/owed", h=f.worker_h).status_code == 403
    assert f.raw_get("/finance/profit-loss", h=f.worker_h).status_code == 403
    assert f.raw_get("/finance/sales", h=f.worker_h).status_code == 403
    assert f.raw_get("/finance/expenses", h=f.worker_h).status_code == 403
    # Sales alone: sells, marks paid, sees what is owed to the farm, not the bills.
    seller, _ = add_farmos_user(
        control_db,
        f.tenant,
        "s13-nomoney-seller@origami-demo.com",
        role="worker",
        grid={"sales": ["view", "create"]},
    )
    # Expenses alone: records bills and sees them; approving cancels.
    clerk, _ = add_farmos_user(
        control_db,
        f.tenant,
        "s13-nomoney-clerk@origami-demo.com",
        role="worker",
        grid={"expenses": ["view", "create", "approve"]},
    )
    control_db.commit()
    seller_h, clerk_h = _login(client, seller.email), _login(client, clerk.email)
    sale = f.sell(h=seller_h, payment_status="pending").json()
    owed = f.get("/finance/owed", h=seller_h)
    assert "receivables" in owed and "payables" not in owed
    assert f.raw_post(f"/finance/sales/{sale['id']}/paid", {}, h=seller_h).status_code == 200
    cancel = f.raw_post(f"/finance/sales/{sale['id']}/cancel", {"reason": "x"}, h=seller_h)
    assert cancel.status_code == 403 and "Sales or Finance approval" in cancel.json()["detail"]
    assert f.raw_get("/finance/expenses", h=seller_h).status_code == 403
    bill = f.spend(h=clerk_h, payment_status="pending").json()
    owed = f.get("/finance/owed", h=clerk_h)
    assert "payables" in owed and "receivables" not in owed
    assert f.raw_post(f"/finance/expenses/{bill['id']}/cancel", {"reason": "x"}, h=clerk_h).status_code == 200
    assert f.raw_get("/finance/sales", h=clerk_h).status_code == 403
    assert f.raw_get("/finance/profit-loss", h=clerk_h).status_code == 403


# ------------------------------------------------------------ profit and loss
def test_profit_and_loss_per_month_and_activity(client, control_db):
    f = Farm(client, control_db, "S13-PL")
    f.feed()
    f.sell(quantity=20, unit_price=6)  # 120 eggs revenue
    f.spend(amount=50, category="fuel")
    f.sell_milk(liters=10, price_per_liter=1.0)
    pl = f.get("/finance/profit-loss")
    assert pl["currency"] == "USD" and len(pl["months"]) in (12, 13)
    this = pl["months"][-1]
    assert this["revenue_by_activity"]["poultry"] == 120 and this["revenue_by_activity"]["dairy"] == 10
    assert this["cost_lines"]["expense:fuel"] == 50
    assert this["cost_lines"]["feed_fed"] == round(8 * 0.36, 2), "feed is a cost when it is fed"
    assert this["profit"] == round(this["revenue"] - this["costs"], 2)
    assert pl["totals"]["profit"] == round(pl["totals"]["revenue"] - pl["totals"]["costs"], 2)
    by_activity = {a["activity"]: a for a in pl["by_activity"]}
    assert by_activity["poultry"]["revenue"] == 120 and by_activity["dairy"]["costs"] == round(8 * 0.36, 2)
    assert list(by_activity) == ["dairy", "poultry", "general"], "in the activities' own order"
    assert this["cash_in"] == 130 and this["cash_out"] == 50, "opening stock was not bought this month"
    assert pl["cost_lines"][0]["line"] == "expense:fuel"
    one = f.get(
        "/finance/profit-loss",
        params={"start": date.today().replace(day=1).isoformat(), "end": date.today().isoformat()},
    )
    assert len(one["months"]) == 1 and one["months"][0]["month"] == date.today().strftime("%Y-%m")
    assert (
        f.raw_get(
            "/finance/profit-loss", params={"start": "2020-01-01", "end": date.today().isoformat()}
        ).status_code
        == 422
    )
    assert (
        f.raw_get(
            "/finance/profit-loss", params={"start": date.today().isoformat(), "end": "2020-01-01"}
        ).status_code
        == 422
    )
    # The accountant's view: Finance view alone reads profit and loss.
    acct, _ = add_farmos_user(
        control_db, f.tenant, "s13-pl-acct@origami-demo.com", role="accountant", grid={"finance": ["view"]}
    )
    control_db.commit()
    assert f.raw_get("/finance/profit-loss", h=_login(client, acct.email)).status_code == 200


# ------------------------------------------------------------------- reports
def test_the_sales_report_shows_what_was_sold(client, control_db):
    f = Farm(client, control_db, "S13-SALESREP", milk=False)
    f.sell(payment_status="pending", buyer_name="Chtaura market")
    f.sell(buyer_name="Abu Karim shop")
    cancelled = f.sell(buyer_name="Nobody", product_label="Mistake").json()
    f.post(f"/finance/sales/{cancelled['id']}/cancel", {"reason": "oops"}, expect=200)
    report = f.get("/reports/sales")
    assert report["kind"] == "sales" and report["kpis"]["sales"] == 2 and report["kpis"]["cancelled"] == 1
    eggs = next(p for p in report["by_product"] if p["product"] == "Eggs (tray of 30)")
    assert eggs["quantity"] == 20 and eggs["average_price"] == 6 and eggs["sales"] == 2
    mistake = next(p for p in report["by_product"] if p["product"] == "Mistake")
    assert mistake["amount"] == 0 and mistake["sales"] == 0, "the reversal takes the cancelled sale out"
    assert any(b["buyer"] == "Chtaura market" and b["unpaid"] >= 60 for b in report["by_buyer"])
    assert any(u["buyer"] == "Chtaura market" and u["days"] == 0 for u in report["unpaid"])
    assert report["kpis"]["revenue"] == round(sum(s["amount"] for s in report["sales"]), 2) == 120
    assert report["kpis"]["unpaid"] == 60 and report["kpis"]["paid"] == 60 and report["kpis"]["buyers"] == 3
    assert report["by_activity"] == [{"activity": "poultry", "amount": 120}]
    assert {s["status"] for s in report["sales"]} == {"recorded", "cancelled", "reversal"}
    assert sum(d["sales"] for d in report["by_day"]) == 2


def test_the_stock_report_shows_what_is_in_stock(client, control_db):
    f = Farm(client, control_db, "S13-STOCK")
    f.feed()
    saline = f.post(
        "/pharmacy/medicines",
        {
            "name": "Sodium chloride 0.9%",
            "unit": "bag",
            "dosage_form": "intravenous_fluid",
            "administration_routes": ["IV"],
            "category_codes": ["IV_FLUID"],
            "ingredients": [{"code": "sodium_chloride"}],
            "unit_cost": 2.5,
        },
    )["inventory_item_id"]
    f.post(
        "/pharmacy/lots/receive",
        {
            "inventory_item_id": saline,
            "quantity": 10,
            "lot_code": "NACL-1",
            "expiry_date": _iso(-400),
            "unit_cost": 2.5,
        },
    )
    with TenantDataRouter.session_for(f.tenant.id) as db:
        product = MounehProduct(
            tenant_id=f.tenant.id, name="Makdous", output_unit="jar", low_stock_threshold=100
        )
        db.add(product)
        db.flush()
        db.add(
            FinishedGoodsStock(
                tenant_id=f.tenant.id,
                batch_id=uuid.uuid4(),
                product_id=product.id,
                quantity_produced=58,
                quantity_available=48,
                quantity_sold=10,
                unit_cost=1.1,
            )
        )
    f.sell_milk(liters=20, price_per_liter=0.5)
    report = f.get("/reports/stock")
    assert report["kind"] == "stock" and report["as_of"] == date.today().isoformat()
    assert report["sections"] == ["feed", "medicine", "mouneh_goods", "mouneh_materials", "milk", "other"]
    assert report["feed"] == [
        {
            "name": "Barley",
            "unit": "kg",
            "on_hand": 992,
            "available": 992,
            "reserved": 0,
            "unusable": 0,
            "days_of_cover": report["feed"][0]["days_of_cover"],
            "low": report["feed"][0]["low"],
            "value": round(992 * 0.36, 2),
        }
    ]
    medicine = report["medicine"]
    assert len(medicine) == 1 and medicine[0]["name"] == "Sodium chloride 0.9%"
    assert medicine[0]["on_hand"] == 10 and medicine[0]["usable"] == 10 and medicine[0]["value"] == 25
    assert medicine[0]["earliest_expiry"] and medicine[0]["low"] is False
    goods = report["mouneh_goods"]
    assert goods == [
        {
            "name": "Makdous",
            "unit": "jar",
            "available": 48,
            "reserved": 0,
            "sold": 10,
            "earliest_expiry": None,
            "low": True,
            "value": round(48 * 1.1, 2),
        }
    ]
    assert report["other"] == [], "feed and medicine items are not counted twice"
    assert report["milk"]["in_tank_l"] == f.tank() == 100
    assert report["milk"] == {"in_tank_l": 100, "last_price_per_liter": 0.5, "value": 50}
    assert (
        report["kpis"]["milk_in_tank_l"] == 100
        and report["kpis"]["lines"] == 3
        and report["kpis"]["low"] == 1
    )
    assert report["kpis"]["value"] == round(992 * 0.36 + 25 + 48 * 1.1 + 50, 2)


def test_who_reads_the_sales_and_stock_reports(client, control_db):
    f = Farm(client, control_db, "S13-WHOSTOCK")
    f.feed()
    f.sell_milk(liters=20, price_per_liter=0.5)
    # Karim keeps only milk recording: no money, no other stock areas.
    assert f.raw_get("/reports/sales", h=f.worker_h).status_code == 403
    stock = f.raw_get("/reports/stock", h=f.worker_h)
    assert stock.status_code == 200, "milk in the tank is his area"
    body = stock.json()
    assert body["sections"] == ["milk"]
    assert (
        body["feed"] == [] and body["medicine"] == [] and body["mouneh_goods"] == [] and body["other"] == []
    )
    assert body["kpis"]["value"] is None and body["kpis"]["milk_in_tank_l"] == 100
    assert body["milk"] == {"in_tank_l": 100, "last_price_per_liter": None, "value": None}
    # The feed room sees the feed, without its value.
    feeder, _ = add_farmos_user(
        control_db,
        f.tenant,
        "s13-whostock-feeder@origami-demo.com",
        role="worker",
        grid={"feed_nutrition": ["view", "create"]},
    )
    control_db.commit()
    feed_view = f.get("/reports/stock", h=_login(client, feeder.email))
    assert feed_view["sections"] == ["feed"] and feed_view["feed"][0]["value"] is None
    assert feed_view["milk"] is None and feed_view["kpis"]["milk_in_tank_l"] is None
    owner = f.get("/reports/stock")
    assert set(owner["sections"]) >= {"feed", "medicine", "milk"} and owner["kpis"]["value"] is not None


# -------------------------------------------------------- the stage 4 contract
def test_the_legacy_sales_and_expenses_endpoints_write_the_same_books(client, control_db):
    f = Farm(client, control_db, "S13-LEGACY", milk=False)
    sale = f.post(
        "/sales",
        {"product_type": "produce", "product_label": "Zucchini", "quantity": 90, "unit": "kg", "amount": 945},
    )
    assert (sale["status"], sale["unit_price"], sale["activity"], sale["payment_status"]) == (
        "recorded",
        10.5,
        "crops",
        "paid",
    )
    assert next(s for s in f.get("/finance/sales") if s["id"] == sale["id"])["product_label"] == "Zucchini"
    assert f.raw_post("/sales", {"product_type": "milk", "amount": 10}).status_code == 422
    expense = f.post("/expenses", {"category": "fuel", "amount": 420})
    assert (expense["status"], expense["payment_status"], expense["activity"]) == (
        "recorded",
        "paid",
        "general",
    )
    assert expense["paid_at"] and expense["currency"] == "USD"
    assert f.raw_post("/expenses", {"category": "gambling", "amount": 1}).status_code == 422
    # The same permissions as the finance paths.
    assert f.raw_post("/sales", {"product_type": "eggs", "amount": 10}, h=f.worker_h).status_code == 403
    assert f.raw_post("/expenses", {"category": "fuel", "amount": 1}, h=f.worker_h).status_code == 403
    assert f.summary()["revenue_today"] == 945 and f.summary()["expenses_today"] == 420


# ---------------------------------------------------------------- isolation
def test_another_farm_cannot_see_or_touch_this_farms_money(client, control_db):
    f = Farm(client, control_db, "S13-ISO-A")
    other = Farm(client, control_db, "S13-ISO-B", milk=False)
    f.rate(89500)
    milk = f.sell_milk(liters=10, payment_status="pending").json()
    sale = f.sell(payment_status="pending").json()
    bill = f.spend(payment_status="pending").json()
    f.post("/production/milk-tank/adjustments", {"reason": "spoiled", "liters": 5})
    # Nothing of farm A's shows at farm B.
    assert other.get("/production/milk-tank")["balance_l"] == 0
    assert other.get("/production/milk-sales") == [] and other.get("/production/milk-tank/adjustments") == []
    assert other.get("/finance/exchange-rates")["current"]["LBP"] is None
    assert other.get("/finance/sales") == [] and other.get("/finance/expenses") == []
    owed = other.get("/finance/owed")
    assert owed["receivables"]["items"] == [] and owed["payables"]["items"] == []
    assert other.get("/reports/sales")["kpis"]["sales"] == 0
    assert other.get("/reports/milk")["kpis"]["total_l"] == 0
    # Nor can farm B act on them: a guess and a missing id look the same.
    assert other.raw_post(f"/production/milk-sales/{milk['id']}/paid").status_code == 404
    assert other.raw_post(f"/production/milk-sales/{milk['id']}/cancel", {"reason": "x"}).status_code == 404
    assert other.raw_post(f"/finance/sales/{sale['id']}/paid", {}).status_code == 404
    assert other.raw_post(f"/finance/sales/{sale['id']}/cancel", {"reason": "x"}).status_code == 404
    assert other.raw_post(f"/finance/expenses/{bill['id']}/paid", {}).status_code == 404
    assert other.raw_post(f"/finance/expenses/{bill['id']}/cancel", {"reason": "x"}).status_code == 404
    # Farm A is untouched.
    assert f.tank() == 105
    assert next(s for s in f.get("/finance/sales") if s["id"] == sale["id"])["payment_status"] == "pending"
    assert next(e for e in f.get("/finance/expenses") if e["id"] == bill["id"])["status"] == "recorded"
    with TenantDataRouter.session_for(other.tenant.id) as db:
        assert db.scalars(select(Sale)).all() == []
