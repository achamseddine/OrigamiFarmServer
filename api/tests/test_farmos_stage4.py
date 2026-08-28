"""Stage 4 of the FarmOS tablet contract: management — employees &
permissions, expenses/sales listing, audit history, recommendations, and
the daily-summary report. See docs/FARMOS_API.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.common.tenant_router import TenantDataRouter
from app.farmos.finance_models import Expense, Sale
from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant


def _owner(client, control_db, prefix: str, email: str):
    tenant = create_tenant(control_db, company_code=unique_code(prefix))
    add_farmos_user(control_db, tenant, email, role="owner")
    control_db.commit()
    token = farmos_login(client, email, FARMOS_DEMO_PASSWORD)
    return tenant, token


# --- Employees -------------------------------------------------------------


def test_employee_crud_and_permission_lifecycle(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-EMP", "empowner@origami-demo.com")

    created = client.post(
        "/api/v1/employees",
        json={
            "name": "Layla Haddad",
            "email": "layla@origami-demo.com",
            "role": "veterinarian",
            "job_title": "Veterinarian",
            "password": "vet-password-1",
            "permissions": [
                {"module_code": "animal_health", "can_view": True, "can_create": True, "can_edit": True}
            ],
        },
        headers=farmos_headers(token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["farm_id"] == str(tenant.id)
    assert body["full_access"] is False
    perm = next(p for p in body["permissions"] if p["module_code"] == "animal_health")
    assert perm["can_view"] is True
    assert perm["can_delete"] is False
    employee_id = body["id"]

    # The new employee can actually log in with the password they were given.
    vet_token = farmos_login(client, "layla@origami-demo.com", "vet-password-1")
    assert vet_token

    listing = client.get("/api/v1/employees", headers=farmos_headers(token))
    assert len(listing.json()) == 2  # the owner plus the new hire

    updated = client.patch(
        f"/api/v1/employees/{employee_id}",
        json={"department": "animals", "job_title": "Senior Veterinarian"},
        headers=farmos_headers(token),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["department"] == "animals"
    assert updated.json()["job_title"] == "Senior Veterinarian"

    replaced = client.put(
        f"/api/v1/employees/{employee_id}/permissions",
        json={"permissions": [{"module_code": "tasks", "can_view": True}]},
        headers=farmos_headers(token),
    )
    assert replaced.status_code == 200, replaced.text
    codes = {p["module_code"] for p in replaced.json()["permissions"]}
    assert codes == {"tasks"}

    deactivated = client.delete(f"/api/v1/employees/{employee_id}", headers=farmos_headers(token))
    assert deactivated.status_code == 204

    active_listing = client.get("/api/v1/employees", headers=farmos_headers(token))
    assert len(active_listing.json()) == 1
    with_inactive = client.get(
        "/api/v1/employees", params={"include_inactive": True}, headers=farmos_headers(token)
    )
    assert len(with_inactive.json()) == 2

    # A deactivated employee's own token stops working.
    denied = client.get("/api/v1/auth/me", headers=farmos_headers(vet_token))
    assert denied.status_code == 403


def test_worker_cannot_manage_employees(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-EMP2"))
    add_farmos_user(control_db, tenant, "worker@origami-demo.com", role="worker", grid={"tasks": ["view"]})
    control_db.commit()
    token = farmos_login(client, "worker@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.post(
        "/api/v1/employees",
        json={"name": "New Hire", "password": "whatever-1"},
        headers=farmos_headers(token),
    )
    assert resp.status_code == 403


# --- Expenses & sales (read-only listings) --------------------------------


def test_expenses_and_sales_are_listed_read_only(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-FIN", "finowner@origami-demo.com")
    now = datetime.now(timezone.utc)
    with TenantDataRouter.session_for(tenant.id) as db:
        db.add(
            Expense(
                tenant_id=tenant.id, category="feed", amount=1680, currency="USD", incurred_at=now
            )
        )
        db.add(
            Sale(
                tenant_id=tenant.id,
                product_type="milk",
                product_label="Milk",
                quantity=340,
                unit="L",
                amount=4250,
                currency="USD",
                payment_status="paid",
                sold_at=now,
            )
        )

    expenses = client.get(
        "/api/v1/expenses", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert expenses.status_code == 200
    assert expenses.json()[0]["category"] == "feed"

    sales = client.get(
        "/api/v1/sales", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert sales.status_code == 200
    assert sales.json()[0]["product_type"] == "milk"


# --- Audit -----------------------------------------------------------------


def test_audit_trail_records_animal_move_and_treatment(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-AUD", "audowner@origami-demo.com")
    animal = client.post(
        "/api/v1/animals",
        json={"tag": "COW-AUD", "name": "Bessie", "species": "cow"},
        headers=farmos_headers(token),
    ).json()

    client.patch(
        f"/api/v1/animals/{animal['id']}",
        json={"location_label": "Isolation Pen"},
        headers=farmos_headers(token),
    )
    client.post(
        "/api/v1/health/treatments",
        json={
            "entity_type": "animal",
            "entity_id": animal["id"],
            "medication": "Amoxicillin",
            "dose": "20 ml",
            "route": "IM",
            "responsible_user_id": animal["id"],
        },
        headers=farmos_headers(token),
    )

    events = client.get(
        "/api/v1/audit", params={"entity_type": "animal"}, headers=farmos_headers(token)
    )
    assert events.status_code == 200
    body = events.json()
    assert len(body) == 1
    assert body[0]["action"] == "animal.updated"
    assert body[0]["changes_json"]["location_label"]["to"] == "Isolation Pen"

    all_events = client.get("/api/v1/audit", headers=farmos_headers(token))
    assert {e["action"] for e in all_events.json()} == {"animal.updated", "treatment.created"}


def test_worker_without_reports_view_cannot_see_audit(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-AUD2"))
    add_farmos_user(control_db, tenant, "worker2@origami-demo.com", role="worker", grid={"tasks": ["view"]})
    control_db.commit()
    token = farmos_login(client, "worker2@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.get("/api/v1/audit", headers=farmos_headers(token))
    assert resp.status_code == 403


# --- Recommendations -------------------------------------------------------


def test_feed_cost_recommendation_fires_above_threshold_and_can_be_decided(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-REC", "recowner@origami-demo.com")
    now = datetime.now(timezone.utc)
    with TenantDataRouter.session_for(tenant.id) as db:
        db.add(Expense(tenant_id=tenant.id, category="feed", amount=800, currency="USD", incurred_at=now))
        db.add(Expense(tenant_id=tenant.id, category="medicine", amount=200, currency="USD", incurred_at=now))

    resp = client.get(
        "/api/v1/recommendations", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert resp.status_code == 200
    recs = resp.json()
    feed_rec = next(r for r in recs if r["rule_id"] == "RULE-FEED-COST-INSIGHT")
    assert feed_rec["category"] == "finance"
    assert feed_rec["status"] == "generated"
    assert "80.0%" in feed_rec["rationale"]

    # A second refresh must not duplicate the still-undecided recommendation.
    again = client.get(
        "/api/v1/recommendations", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert len([r for r in again.json() if r["rule_id"] == "RULE-FEED-COST-INSIGHT"]) == 1

    decided = client.patch(
        f"/api/v1/recommendations/{feed_rec['id']}/decision",
        json={"decision": "accept", "decided_by": str(uuid.uuid4()), "note": "Switching suppliers"},
        headers=farmos_headers(token),
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "accepted"


def test_harvest_due_recommendation_fires_for_soon_ready_planting(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-REC2", "recowner2@origami-demo.com")
    field = client.post(
        "/api/v1/fields", json={"name": "Herb Garden"}, headers=farmos_headers(token)
    ).json()
    crop = client.post(
        "/api/v1/crops", json={"name": "Basil"}, headers=farmos_headers(token)
    ).json()
    client.post(
        "/api/v1/crop-plantings",
        json={
            "field_id": field["id"],
            "crop_id": crop["id"],
            "expected_harvest_date": (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat(),
            "expected_yield_kg": 65,
        },
        headers=farmos_headers(token),
    )

    resp = client.get(
        "/api/v1/recommendations", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    harvest_rec = next(r for r in resp.json() if r["rule_id"] == "RULE-HARVEST-DUE")
    assert harvest_rec["entity_label"] == "Herb Garden — Basil"
    assert "65 kg" in harvest_rec["rationale"]


# --- Daily summary ---------------------------------------------------------


def test_daily_summary_aggregates_todays_sales_and_expenses(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-SUM", "sumowner@origami-demo.com")
    now = datetime.now(timezone.utc)
    with TenantDataRouter.session_for(tenant.id) as db:
        db.add(
            Sale(
                tenant_id=tenant.id, product_type="milk", product_label="Milk", amount=4250,
                currency="USD", payment_status="paid", sold_at=now,
            )
        )
        db.add(
            Sale(
                tenant_id=tenant.id, product_type="eggs", product_label="Eggs", amount=2380,
                currency="USD", payment_status="pending", sold_at=now,
            )
        )
        db.add(
            Expense(tenant_id=tenant.id, category="feed", amount=1680, currency="USD", incurred_at=now)
        )

    resp = client.get(
        "/api/v1/reports/daily-summary", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["revenue_today"] == 6630.0
    assert body["expenses_today"] == 1680.0
    assert body["gross_margin"] == 4950.0
    assert body["cash_collected"] == 4250.0
    assert body["pending_payments"] == 2380.0
    assert {b["product_type"] for b in body["sales_breakdown"]} == {"milk", "eggs"}
