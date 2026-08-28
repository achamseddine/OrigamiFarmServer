"""Stage 5 (part 2) of the FarmOS tablet contract: the Farm Visits /
agritourism module — activities, packages, visitors, sessions, bookings +
confirm, staff roster, incidents, feedback, and retail sales.
See docs/FARMOS_API.md.
"""

from __future__ import annotations

import uuid

from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant


def _owner(client, control_db, prefix: str, email: str):
    tenant = create_tenant(control_db, company_code=unique_code(prefix))
    add_farmos_user(control_db, tenant, email, role="owner")
    control_db.commit()
    token = farmos_login(client, email, FARMOS_DEMO_PASSWORD)
    return tenant, token


def _activate_visits(client, token):
    resp = client.post(
        "/api/v1/modules/visits_agritourism/activate",
        json={"status": "active", "plan": "farmos_experience"},
        headers=farmos_headers(token),
    )
    assert resp.status_code == 200, resp.text


def _make_session(client, token, *, capacity=4):
    resp = client.post(
        "/api/v1/visit-sessions",
        json={
            "date": "2026-09-05",
            "start_time": "09:00:00",
            "end_time": "17:00:00",
            "capacity": capacity,
        },
        headers=farmos_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_package(client, token, *, base_price=15):
    resp = client.post(
        "/api/v1/visit-packages",
        json={"name": "Family Farm Day", "base_price": base_price},
        headers=farmos_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_module_status_reflects_activation(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-VIS", "visowner@origami-demo.com")

    before = client.get("/api/v1/modules/visits/status", headers=farmos_headers(token))
    assert before.json()["active"] is False

    _activate_visits(client, token)

    after = client.get("/api/v1/modules/visits/status", headers=farmos_headers(token))
    body = after.json()
    assert body["active"] is True
    assert body["module_code"] == "visits_agritourism"
    assert body["features"]["staff_costing"] is True


def test_booking_creation_totals_package_and_activities(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-VIS2", "visowner2@origami-demo.com")
    _activate_visits(client, token)
    session_row = _make_session(client, token)
    package = _make_package(client, token, base_price=15)
    activity = client.post(
        "/api/v1/visit-activities",
        json={"name": "Cheese Workshop", "activity_type": "workshop", "price": 12, "capacity_per_slot": 8},
        headers=farmos_headers(token),
    ).json()

    booking = client.post(
        "/api/v1/visit-bookings",
        json={
            "visitor": {"full_name": "Nour Khalil", "phone": "+961 71 555 010"},
            "session_id": session_row["id"],
            "package_id": package["id"],
            "adults": 2,
            "children": 1,
            "activities": [{"activity_id": activity["id"], "quantity": 2}],
            "deposit_amount": 10,
        },
        headers=farmos_headers(token),
    )
    assert booking.status_code == 201, booking.text
    body = booking.json()
    assert body["farm_id"] == str(tenant.id)
    assert body["status"] == "pending"
    assert body["total_amount"] == 15 + 12 * 2
    assert body["balance_due"] == body["total_amount"] - 10
    assert len(body["activities"]) == 1
    assert body["activities"][0]["total_price"] == 24.0

    visitors = client.get("/api/v1/visitors", headers=farmos_headers(token))
    assert any(v["full_name"] == "Nour Khalil" for v in visitors.json())


def test_confirm_booking_blocks_when_session_capacity_would_be_exceeded(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-VIS3", "visowner3@origami-demo.com")
    _activate_visits(client, token)
    session_row = _make_session(client, token, capacity=3)
    package = _make_package(client, token)

    def _book(adults):
        return client.post(
            "/api/v1/visit-bookings",
            json={
                "visitor": {"full_name": f"Visitor {adults}"},
                "session_id": session_row["id"],
                "package_id": package["id"],
                "adults": adults,
                "children": 0,
            },
            headers=farmos_headers(token),
        ).json()

    first = _book(2)
    second = _book(2)

    confirm_first = client.post(
        f"/api/v1/visit-bookings/{first['id']}/confirm", headers=farmos_headers(token)
    )
    assert confirm_first.status_code == 200
    assert confirm_first.json()["status"] == "confirmed"

    confirm_second = client.post(
        f"/api/v1/visit-bookings/{second['id']}/confirm", headers=farmos_headers(token)
    )
    assert confirm_second.status_code == 422
    assert "spot" in confirm_second.json()["detail"].lower()

    # Re-confirming an already-confirmed booking is a harmless no-op.
    reconfirm = client.post(
        f"/api/v1/visit-bookings/{first['id']}/confirm", headers=farmos_headers(token)
    )
    assert reconfirm.status_code == 200


def test_booking_idempotency_key_prevents_duplicate_creation(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-VIS4", "visowner4@origami-demo.com")
    _activate_visits(client, token)
    session_row = _make_session(client, token)
    package = _make_package(client, token)
    key = str(uuid.uuid4())

    payload = {
        "visitor": {"full_name": "Repeat Visitor"},
        "session_id": session_row["id"],
        "package_id": package["id"],
        "idempotency_key": key,
    }
    first = client.post("/api/v1/visit-bookings", json=payload, headers=farmos_headers(token))
    second = client.post("/api/v1/visit-bookings", json=payload, headers=farmos_headers(token))
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    listing = client.get(
        "/api/v1/visit-bookings", params={"session_id": session_row["id"]}, headers=farmos_headers(token)
    )
    assert len(listing.json()) == 1


def test_staff_roster_computes_total_cost_from_hours(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-VIS5", "visowner5@origami-demo.com")
    _activate_visits(client, token)
    session_row = _make_session(client, token)
    worker = client.post(
        "/api/v1/employees",
        json={"name": "Rami Farah", "password": "worker-pw-1", "role": "worker"},
        headers=farmos_headers(token),
    ).json()

    entry = client.post(
        "/api/v1/visit-staff-roster",
        json={
            "session_id": session_row["id"],
            "worker_id": worker["id"],
            "role": "guide",
            "start_time": "08:30:00",
            "end_time": "17:30:00",
            "hourly_rate": 6,
        },
        headers=farmos_headers(token),
    )
    assert entry.status_code == 201, entry.text
    assert entry.json()["total_cost"] == 54.0


def test_visitors_endpoint_denied_to_a_plain_worker(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-VIS6"))
    add_farmos_user(
        control_db, tenant, "visworker@origami-demo.com", role="worker", grid={"farm_visits": ["view"]}
    )
    control_db.commit()
    token = farmos_login(client, "visworker@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.get("/api/v1/visitors", headers=farmos_headers(token))
    assert resp.status_code == 403


def test_visitor_coordinator_role_can_read_visitors(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-VIS7"))
    add_farmos_user(
        control_db,
        tenant,
        "coordinator@origami-demo.com",
        role="visitor_coordinator",
        grid={"farm_visits": ["view", "create"]},
    )
    control_db.commit()
    token = farmos_login(client, "coordinator@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.get("/api/v1/visitors", headers=farmos_headers(token))
    assert resp.status_code == 200


def test_retail_sale_deducts_finished_goods_and_creates_core_sale(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-VIS8", "visowner8@origami-demo.com")
    _activate_visits(client, token)

    material = client.post(
        "/api/v1/mouneh/raw-materials",
        json={"name": "Eggplant", "unit": "kg", "default_unit_cost": 1.2, "current_stock": 100},
        headers=farmos_headers(token),
    ).json()
    product = client.post(
        "/api/v1/mouneh/products",
        json={"name": "Makdous Tasting Jar", "output_unit": "jar"},
        headers=farmos_headers(token),
    ).json()
    client.post(
        f"/api/v1/mouneh/products/{product['id']}/recipes",
        json={
            "basis_quantity": 10,
            "basis_unit": "jar",
            "items": [{"material_id": material["id"], "quantity": 5, "unit": "kg"}],
        },
        headers=farmos_headers(token),
    )
    batch = client.post(
        "/api/v1/mouneh/batches",
        json={"product_id": product["id"], "planned_qty": 10},
        headers=farmos_headers(token),
    ).json()
    client.post(
        f"/api/v1/mouneh/batches/{batch['id']}/complete",
        json={"actual_output_qty": 10},
        headers=farmos_headers(token),
    )
    stock = client.get(
        "/api/v1/mouneh/finished-goods", params={"product_id": product["id"]}, headers=farmos_headers(token)
    ).json()[0]

    sale = client.post(
        "/api/v1/visit-retail-sales",
        json={
            "channel": "farm_shop",
            "lines": [
                {
                    "item_type": "finished_goods",
                    "item_id": stock["id"],
                    "quantity": 3,
                    "unit_price": 6.5,
                }
            ],
        },
        headers=farmos_headers(token),
    )
    assert sale.status_code == 201, sale.text
    body = sale.json()
    assert body["total_amount"] == 19.5
    assert body["sale_id"]

    stock_after = client.get(
        "/api/v1/mouneh/finished-goods", params={"product_id": product["id"]}, headers=farmos_headers(token)
    ).json()[0]
    assert stock_after["quantity_available"] == stock["quantity_available"] - 3

    sales_ledger = client.get(
        "/api/v1/sales", params={"farm_id": str(_tenant.id)}, headers=farmos_headers(token)
    )
    assert any(s["product_type"] == "visit_retail" for s in sales_ledger.json())

    retail_listing = client.get("/api/v1/visit-retail-sales", headers=farmos_headers(token))
    assert len(retail_listing.json()) == 1
