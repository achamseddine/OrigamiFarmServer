"""Stage 3 of the FarmOS tablet contract: recording work — animal health,
observations, feed & inventory, production, and agriculture.
See docs/FARMOS_API.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant


def _owner(client, control_db, prefix: str, email: str):
    tenant = create_tenant(control_db, company_code=unique_code(prefix))
    add_farmos_user(control_db, tenant, email, role="owner")
    control_db.commit()
    token = farmos_login(client, email, FARMOS_DEMO_PASSWORD)
    return tenant, token


def _create_animal(client, token, tenant, *, tag="COW-1"):
    resp = client.post(
        "/api/v1/animals",
        json={"tag": tag, "name": "Bessie", "species": "cow"},
        headers=farmos_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --- Animal health / observations ---------------------------------------


def test_owner_can_record_a_treatment_and_it_sets_animal_withdrawal(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-VET", "vetowner@origami-demo.com")
    animal_id = _create_animal(client, token, tenant)

    resp = client.post(
        "/api/v1/health/treatments",
        json={
            "entity_type": "animal",
            "entity_id": animal_id,
            "medication": "Amoxicillin",
            "dose": "20 ml",
            "route": "intramuscular",
            "responsible_user_id": animal_id,
            "diagnosis": "Suspected mastitis",
            "withdrawal_until": "2026-09-01T06:00:00Z",
        },
        headers=farmos_headers(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["medication"] == "Amoxicillin"
    assert body["status"] == "active"

    animal = client.get(f"/api/v1/animals/{animal_id}", headers=farmos_headers(token))
    assert animal.json()["withdrawal_reason"] == "Suspected mastitis"
    assert animal.json()["withdrawal_until"] is not None

    listing = client.get(
        "/api/v1/health/treatments", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_worker_cannot_record_a_treatment(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-VET2"))
    worker_user, _ = add_farmos_user(
        control_db,
        tenant,
        "worker@origami-demo.com",
        role="worker",
        grid={"animal_health": ["view", "create"]},
    )
    control_db.commit()
    token = farmos_login(client, "worker@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.post(
        "/api/v1/health/treatments",
        json={
            "entity_type": "animal",
            "entity_id": str(worker_user.id),
            "medication": "Amoxicillin",
            "dose": "20 ml",
            "route": "intramuscular",
            "responsible_user_id": str(worker_user.id),
        },
        headers=farmos_headers(token),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]


def test_veterinarian_role_can_record_a_treatment(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-VET3"))
    vet_user, _ = add_farmos_user(
        control_db,
        tenant,
        "vet@origami-demo.com",
        role="veterinarian",
        grid={"animal_health": ["view", "create"]},
    )
    control_db.commit()
    token = farmos_login(client, "vet@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.post(
        "/api/v1/health/treatments",
        json={
            "entity_type": "animal",
            "entity_id": str(vet_user.id),
            "medication": "Amoxicillin",
            "dose": "20 ml",
            "route": "intramuscular",
            "responsible_user_id": str(vet_user.id),
        },
        headers=farmos_headers(token),
    )
    assert resp.status_code == 201, resp.text


def test_worker_can_record_an_observation_with_no_diagnosis_field(client, control_db):
    """Unlike POST /health/treatments, this is not gated to a diagnostic
    role — a plain worker with only "create" on animal_health may log
    what they see (Constitution: workers observe, they don't diagnose;
    the schema itself has no diagnosis field to smuggle one through).
    """
    tenant, owner_token = _owner(client, control_db, "FARM-OBS", "obsowner@origami-demo.com")
    animal_id = _create_animal(client, owner_token, tenant)
    worker_user, _ = add_farmos_user(
        control_db, tenant, "obsworker@origami-demo.com", role="worker", grid={"animal_health": ["create"]}
    )
    control_db.commit()
    token = farmos_login(client, "obsworker@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.post(
        "/api/v1/observations",
        json={
            "farm_id": str(tenant.id),
            "entity_type": "animal",
            "entity_id": animal_id,
            "observation_type": "udder_swelling",
            "severity": "moderate",
            "observer_id": str(worker_user.id),
            "notes": "Swollen, warm to touch",
        },
        headers=farmos_headers(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["quality"] == "human_observed"
    assert body["confidence"] == 0.65
    assert "diagnosis" not in body

    # What got recorded is also readable back (by someone with view access,
    # e.g. the owner) — a worker's observation isn't write-only, so it can
    # show up in the animal's history on its digital twin.
    listed = client.get(
        "/api/v1/observations",
        params={"farm_id": str(tenant.id), "entity_type": "animal", "entity_id": animal_id},
        headers=farmos_headers(owner_token),
    )
    assert listed.status_code == 200, listed.text
    assert [o["id"] for o in listed.json()] == [body["id"]]
    assert listed.json()[0]["observation_type"] == "udder_swelling"

    other_animal = client.get(
        "/api/v1/observations",
        params={"farm_id": str(tenant.id), "entity_type": "animal", "entity_id": "not-this-one"},
        headers=farmos_headers(owner_token),
    )
    assert other_animal.json() == []


# --- Feed & inventory ------------------------------------------------------


def test_owner_can_register_a_new_inventory_item(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-NEWFEED", "newfeedowner@origami-demo.com")

    created = client.post(
        "/api/v1/feed/items",
        json={
            "name": "Layer Feed",
            "unit": "kg",
            "category": "Poultry",
            "reorder_level": 1500,
            "supplier_label": "Al Mashreq",
            "unit_cost": 0.39,
            "initial_qty": 1150,
        },
        headers=farmos_headers(token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Layer Feed"
    assert body["current_qty"] == 1150.0
    assert body["reorder_level"] == 1500.0
    item_id = body["id"]

    # A brand-new item can now actually be used by POST /feed/transactions,
    # which 404s on an item_id that doesn't exist.
    restock = client.post(
        "/api/v1/feed/transactions",
        json={"item_id": item_id, "direction": "in", "quantity": 200},
        headers=farmos_headers(token),
    )
    assert restock.status_code == 201, restock.text
    assert restock.json()["current_qty"] == 1350.0

    listing = client.get(
        "/api/v1/feed/items", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert any(i["id"] == item_id for i in listing.json())


def test_worker_without_feed_permission_cannot_register_inventory_item(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-NEWFEED2"))
    add_farmos_user(
        control_db, tenant, "feedworker@origami-demo.com", role="worker", grid={"tasks": ["view"]}
    )
    control_db.commit()
    token = farmos_login(client, "feedworker@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.post(
        "/api/v1/feed/items",
        json={"name": "Minerals", "unit": "kg"},
        headers=farmos_headers(token),
    )
    assert resp.status_code == 403


def _seed_feed_item(client, token, tenant):
    from app.common.tenant_router import TenantDataRouter
    from app.tenant_api.models import InventoryItem

    with TenantDataRouter.session_for(tenant.id) as db:
        item = InventoryItem(
            tenant_id=tenant.id, name="Alfalfa Hay", category="Dairy", unit="kg", current_qty=100,
            reorder_level=50,
        )
        db.add(item)
        db.flush()
        return item.id


def test_feed_transaction_updates_inventory_and_blocks_negative(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-FEED", "feedowner@origami-demo.com")
    item_id = _seed_feed_item(client, token, tenant)

    listing = client.get(
        "/api/v1/feed/items", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert listing.status_code == 200
    assert listing.json()[0]["current_qty"] == 100.0

    out = client.post(
        "/api/v1/feed/transactions",
        json={"item_id": str(item_id), "direction": "out", "quantity": 30},
        headers=farmos_headers(token),
    )
    assert out.status_code == 201, out.text
    assert out.json()["current_qty"] == 70.0

    over = client.post(
        "/api/v1/feed/transactions",
        json={"item_id": str(item_id), "direction": "out", "quantity": 1000},
        headers=farmos_headers(token),
    )
    assert over.status_code == 422
    assert over.json()["detail"]

    forced = client.post(
        "/api/v1/feed/transactions",
        json={"item_id": str(item_id), "direction": "out", "quantity": 1000, "allow_negative": True},
        headers=farmos_headers(token),
    )
    assert forced.status_code == 201
    assert forced.json()["current_qty"] < 0

    # Every transaction that moved stock is itself readable — the Feed &
    # Inventory screen's history, not just the running total.
    history = client.get(
        "/api/v1/feed/transactions",
        params={"farm_id": str(tenant.id), "item_id": str(item_id)},
        headers=farmos_headers(token),
    )
    assert history.status_code == 200, history.text
    # Only the two transactions that actually moved stock exist — the
    # blocked over-withdrawal never created a row. Newest first.
    directions = [(m["direction"], m["quantity"]) for m in history.json()]
    assert directions == [("out", 1000.0), ("out", 30.0)]


# --- Production ----------------------------------------------------------


def test_egg_record_validates_accounting_rule(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-EGG", "eggowner@origami-demo.com")

    over = client.post(
        "/api/v1/production/eggs",
        json={"flock_id": "flock-layer", "total_eggs": 10, "sellable_eggs": 8, "broken_eggs": 5},
        headers=farmos_headers(token),
    )
    assert over.status_code == 422

    ok = client.post(
        "/api/v1/production/eggs",
        json={"flock_id": "flock-layer", "total_eggs": 420, "sellable_eggs": 395, "broken_eggs": 15},
        headers=farmos_headers(token),
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["total_eggs"] == 420


def test_milk_record_hard_blocks_sale_under_withdrawal_but_allows_storage(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-MILK", "milkowner@origami-demo.com")
    animal_id = _create_animal(client, token, tenant)

    client.post(
        "/api/v1/health/treatments",
        json={
            "entity_type": "animal",
            "entity_id": animal_id,
            "medication": "Amoxicillin",
            "dose": "20 ml",
            "route": "intramuscular",
            "responsible_user_id": animal_id,
            "withdrawal_until": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        },
        headers=farmos_headers(token),
    )

    blocked = client.post(
        "/api/v1/production/milk",
        json={"animal_id": animal_id, "session": "morning", "liters": 12.5, "destination": "sale"},
        headers=farmos_headers(token),
    )
    assert blocked.status_code == 422
    assert "withdrawal" in blocked.json()["detail"].lower()

    stored = client.post(
        "/api/v1/production/milk",
        json={"animal_id": animal_id, "session": "morning", "liters": 12.5, "destination": "stored"},
        headers=farmos_headers(token),
    )
    assert stored.status_code == 201, stored.text
    assert stored.json()["under_withdrawal_warning"] is True


def test_production_harvest_simple_ledger(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-HARV", "harvowner@origami-demo.com")
    field = client.post(
        "/api/v1/fields", json={"name": "Field 1"}, headers=farmos_headers(token)
    ).json()

    created = client.post(
        "/api/v1/production/harvest",
        json={"field_id": field["id"], "product_name": "Tomatoes", "quantity": 96, "waste_qty": 6},
        headers=farmos_headers(token),
    )
    assert created.status_code == 201, created.text

    listing = client.get(
        "/api/v1/production/harvest", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert len(listing.json()) == 1

    fields_listing = client.get(
        "/api/v1/production/fields", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert [f["name"] for f in fields_listing.json()] == ["Field 1"]


# --- Agriculture -----------------------------------------------------------


def test_field_crop_and_planting_crud(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-AG", "agowner@origami-demo.com")

    field = client.post(
        "/api/v1/fields",
        json={"name": "Field 2"},
        headers=farmos_headers(token),
    )
    assert field.status_code == 201, field.text
    field_id = field.json()["id"]

    updated_field = client.patch(
        f"/api/v1/fields/{field_id}", json={"stage": "ripening"}, headers=farmos_headers(token)
    )
    assert updated_field.status_code == 200
    assert updated_field.json()["stage"] == "ripening"

    crop = client.post(
        "/api/v1/crops", json={"name": "Cucumbers", "category": "vegetable"}, headers=farmos_headers(token)
    )
    assert crop.status_code == 201, crop.text
    crop_id = crop.json()["id"]

    planting = client.post(
        "/api/v1/crop-plantings",
        json={"field_id": field_id, "crop_id": crop_id, "variety": "Beit Alpha"},
        headers=farmos_headers(token),
    )
    assert planting.status_code == 201, planting.text
    assert planting.json()["stage"] == "planted"
    assert planting.json()["status"] == "active"
    planting_id = planting.json()["id"]

    updated_planting = client.patch(
        f"/api/v1/crop-plantings/{planting_id}", json={"stage": "growing"}, headers=farmos_headers(token)
    )
    assert updated_planting.status_code == 200
    assert updated_planting.json()["stage"] == "growing"

    listing = client.get(
        "/api/v1/crop-plantings", params={"field_id": field_id}, headers=farmos_headers(token)
    )
    assert len(listing.json()) == 1

    archived = client.delete(f"/api/v1/crops/{crop_id}", headers=farmos_headers(token))
    assert archived.status_code == 204

    active_only = client.get("/api/v1/crops", headers=farmos_headers(token))
    assert crop_id not in [c["id"] for c in active_only.json()]
    with_inactive = client.get(
        "/api/v1/crops", params={"include_inactive": True}, headers=farmos_headers(token)
    )
    assert crop_id in [c["id"] for c in with_inactive.json()]


def test_record_daily_harvest_creates_inventory_stock(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-DH", "dhowner@origami-demo.com")
    field = client.post(
        "/api/v1/fields", json={"name": "Field 3"}, headers=farmos_headers(token)
    ).json()

    first = client.post(
        "/api/v1/harvest",
        json={
            "field_id": field["id"],
            "product_name": "Basil",
            "total_quantity": 20,
            "waste_quantity": 2,
        },
        headers=farmos_headers(token),
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["sellable_quantity"] == 18.0
    assert body["inventory_qty_after"] == 18.0
    item_id = body["inventory_item_id"]

    second = client.post(
        "/api/v1/harvest",
        json={"field_id": field["id"], "product_name": "Basil", "total_quantity": 10},
        headers=farmos_headers(token),
    )
    assert second.status_code == 201, second.text
    assert second.json()["inventory_item_id"] == item_id
    assert second.json()["inventory_qty_after"] == 28.0

    items = client.get(
        "/api/v1/feed/items", params={"farm_id": str(_tenant.id)}, headers=farmos_headers(token)
    )
    assert any(i["id"] == item_id and i["current_qty"] == 28.0 for i in items.json())
