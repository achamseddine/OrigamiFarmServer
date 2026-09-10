"""Mandatory cross-tenant isolation tests (build prompt section 28, items
2-3, and technical spec section 36/37 scenarios 2-3), exercised against the
FarmOS tablet contract's /api/v1/animals — the old OIDC-based tenant_api
animals endpoint these originally targeted was superseded by it in Stage 2.
"""

from __future__ import annotations

import uuid

from app.common.tenant_router import TenantDataRouter
from app.tenant_api.models import Animal
from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant


def test_tenant_cannot_read_other_tenants_animal(client, control_db):
    tenant_a = create_tenant(control_db, company_code=unique_code("FARM-A"), display_name="FARM-A")
    tenant_b = create_tenant(control_db, company_code=unique_code("FARM-B"), display_name="FARM-B")
    add_farmos_user(control_db, tenant_a, "owner-a@origami-demo.com", role="owner")
    add_farmos_user(control_db, tenant_b, "owner-b@origami-demo.com", role="owner")
    control_db.commit()

    token_a = farmos_login(client, "owner-a@origami-demo.com", FARMOS_DEMO_PASSWORD)
    token_b = farmos_login(client, "owner-b@origami-demo.com", FARMOS_DEMO_PASSWORD)

    created = client.post(
        "/api/v1/animals",
        json={"tag": "A-001", "name": "Bessie", "species": "cow"},
        headers=farmos_headers(token_a),
    )
    assert created.status_code == 201, created.text
    animal_id = created.json()["id"]

    # Owner A can read their own animal.
    own_read = client.get(f"/api/v1/animals/{animal_id}", headers=farmos_headers(token_a))
    assert own_read.status_code == 200

    # Owner B, authenticated for tenant B, must not be able to read it —
    # and the response must not distinguish "exists but forbidden" from
    # "does not exist" (no enumeration).
    cross_read = client.get(f"/api/v1/animals/{animal_id}", headers=farmos_headers(token_b))
    assert cross_read.status_code == 404
    assert cross_read.json()["detail"]

    # Tenant B's own animal listing must never include tenant A's rows.
    listing = client.get(
        "/api/v1/animals", params={"farm_id": str(tenant_b.id)}, headers=farmos_headers(token_b)
    )
    assert listing.status_code == 200
    assert listing.json() == []

    assert tenant_a.id != tenant_b.id  # sanity: genuinely different tenants


def test_tenant_cannot_write_other_tenants_animal(client, control_db):
    tenant_a = create_tenant(control_db, company_code=unique_code("FARM-A"), display_name="FARM-A")
    tenant_b = create_tenant(control_db, company_code=unique_code("FARM-B"), display_name="FARM-B")
    add_farmos_user(control_db, tenant_a, "owner-a2@origami-demo.com", role="owner")
    add_farmos_user(control_db, tenant_b, "owner-b2@origami-demo.com", role="owner")
    control_db.commit()

    token_a = farmos_login(client, "owner-a2@origami-demo.com", FARMOS_DEMO_PASSWORD)
    token_b = farmos_login(client, "owner-b2@origami-demo.com", FARMOS_DEMO_PASSWORD)

    created = client.post(
        "/api/v1/animals",
        json={"tag": "A-002", "name": "Original", "species": "goat"},
        headers=farmos_headers(token_a),
    )
    assert created.status_code == 201, created.text
    animal_id = created.json()["id"]

    move = client.patch(
        f"/api/v1/animals/{animal_id}",
        json={"location_label": "Hacked"},
        headers=farmos_headers(token_b),
    )
    assert move.status_code == 404

    # Prove the row was never touched, reading directly through the router
    # with tenant A's own context (bypassing the API layer entirely).
    with TenantDataRouter.session_for(tenant_a.id) as db:
        animal = db.get(Animal, uuid.UUID(animal_id))
        assert animal is not None
        assert animal.name == "Original"
        assert animal.version == 1

    # Also unreadable and unlistable from tenant B's own farm_id.
    listing = client.get(
        "/api/v1/animals", params={"farm_id": str(tenant_b.id)}, headers=farmos_headers(token_b)
    )
    assert listing.json() == []


def test_endpoints_added_after_stage_5_are_farm_scoped(client, control_db):
    """The read/write endpoints added after the original 92-endpoint
    contract (GET /observations, GET/POST /feed/items, GET
    /feed/transactions, POST /expenses, POST /sales) go through the same
    check_farm_id + RLS pipeline as everything else — but they postdate the
    isolation tests above, so prove it rather than assume it.
    """
    tenant_a = create_tenant(control_db, company_code=unique_code("FARM-ISO-A"))
    tenant_b = create_tenant(control_db, company_code=unique_code("FARM-ISO-B"))
    user_a, _ = add_farmos_user(control_db, tenant_a, "owner-iso-a@origami-demo.com", role="owner")
    add_farmos_user(control_db, tenant_b, "owner-iso-b@origami-demo.com", role="owner")
    control_db.commit()

    token_a = farmos_login(client, "owner-iso-a@origami-demo.com", FARMOS_DEMO_PASSWORD)
    token_b = farmos_login(client, "owner-iso-b@origami-demo.com", FARMOS_DEMO_PASSWORD)

    # --- Tenant A creates one row through each new write endpoint --------
    animal_id = client.post(
        "/api/v1/animals",
        json={"tag": "ISO-1", "name": "Bella", "species": "cow"},
        headers=farmos_headers(token_a),
    ).json()["id"]

    observation = client.post(
        "/api/v1/observations",
        json={
            "farm_id": str(tenant_a.id),
            "entity_type": "animal",
            "entity_id": animal_id,
            "observation_type": "udder_swelling",
            "observer_id": str(user_a.id),
        },
        headers=farmos_headers(token_a),
    )
    assert observation.status_code == 201, observation.text

    item = client.post(
        "/api/v1/feed/items",
        json={"name": "Dairy Mix", "unit": "kg", "initial_qty": 100},
        headers=farmos_headers(token_a),
    )
    assert item.status_code == 201, item.text
    item_id = item.json()["id"]

    assert client.post(
        "/api/v1/expenses",
        json={"category": "feed", "amount": 1680},
        headers=farmos_headers(token_a),
    ).status_code == 201
    assert client.post(
        "/api/v1/sales",
        json={"product_type": "milk", "amount": 4250},
        headers=farmos_headers(token_a),
    ).status_code == 201

    # Tenant A genuinely sees its own rows.
    for path, params in [
        ("/api/v1/observations", {"farm_id": str(tenant_a.id)}),
        ("/api/v1/feed/items", {"farm_id": str(tenant_a.id)}),
        ("/api/v1/feed/transactions", {"farm_id": str(tenant_a.id)}),
        ("/api/v1/expenses", {"farm_id": str(tenant_a.id)}),
        ("/api/v1/sales", {"farm_id": str(tenant_a.id)}),
    ]:
        own = client.get(path, params=params, headers=farmos_headers(token_a))
        assert own.status_code == 200, f"{path}: {own.text}"
        assert own.json(), f"{path} returned nothing for the tenant that owns the row"

    # --- Tenant B must see none of it -----------------------------------
    for path in [
        "/api/v1/observations",
        "/api/v1/feed/items",
        "/api/v1/feed/transactions",
        "/api/v1/expenses",
        "/api/v1/sales",
    ]:
        cross = client.get(
            path, params={"farm_id": str(tenant_b.id)}, headers=farmos_headers(token_b)
        )
        assert cross.status_code == 200, f"{path}: {cross.text}"
        assert cross.json() == [], f"{path} leaked another tenant's rows"

    # Asking for tenant A's farm_id while authenticated as B is a 404, not
    # a 403 — no distinguishing "exists but forbidden" from "not found".
    for path in [
        "/api/v1/observations",
        "/api/v1/feed/items",
        "/api/v1/feed/transactions",
        "/api/v1/expenses",
        "/api/v1/sales",
    ]:
        spoofed = client.get(
            path, params={"farm_id": str(tenant_a.id)}, headers=farmos_headers(token_b)
        )
        assert spoofed.status_code == 404, f"{path} allowed a spoofed farm_id: {spoofed.status_code}"

    # Tenant B can't move tenant A's stock by guessing its item id either.
    hijack = client.post(
        "/api/v1/feed/transactions",
        json={"item_id": item_id, "direction": "out", "quantity": 10},
        headers=farmos_headers(token_b),
    )
    assert hijack.status_code == 404


def test_rls_denies_cross_tenant_access_at_the_database_layer(control_db):
    """Direct proof independent of the API/authorization layer: RLS itself
    is what prevents the leak, not just application-level filtering.
    """
    tenant_a = create_tenant(control_db, company_code=unique_code("FARM-RLS-A"))
    tenant_b = create_tenant(control_db, company_code=unique_code("FARM-RLS-B"))
    control_db.commit()

    with TenantDataRouter.session_for(tenant_a.id) as db:
        animal = Animal(tenant_id=tenant_a.id, tag="RLS-A", name="Bessie", species="cow")
        db.add(animal)
        db.flush()
        animal_id = animal.id

    with TenantDataRouter.session_for(tenant_b.id) as db:
        assert db.get(Animal, animal_id) is None

    with TenantDataRouter.session_for(tenant_a.id) as db:
        assert db.get(Animal, animal_id) is not None
