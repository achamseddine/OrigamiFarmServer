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
