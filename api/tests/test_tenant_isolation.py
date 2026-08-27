"""Mandatory cross-tenant isolation tests (build prompt section 28, items
2-3, and technical spec section 36/37 scenarios 2-3).
"""

from __future__ import annotations

import uuid

from app.common.enums import TenantRole
from app.common.tenant_router import TenantDataRouter
from app.tenant_api.models import Animal
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import add_membership, create_tenant, grant_module


def _setup_tenant_with_owner(control_db, code_prefix: str, email: str):
    tenant = create_tenant(control_db, company_code=unique_code(code_prefix), display_name=code_prefix)
    grant_module(control_db, tenant, "ANIMALS")
    add_membership(
        control_db,
        tenant,
        email,
        role=TenantRole.TENANT_OWNER,
        permissions=["ANIMALS:create", "ANIMALS:read", "ANIMALS:update"],
    )
    control_db.commit()
    return tenant


def test_tenant_cannot_read_other_tenants_animal(client, control_db):
    tenant_a = _setup_tenant_with_owner(control_db, "FARM-A", "owner-a@test.com")
    tenant_b = _setup_tenant_with_owner(control_db, "FARM-B", "owner-b@test.com")

    token_a = dev_login(client, "owner-a@test.com")
    token_b = dev_login(client, "owner-b@test.com")

    created = client.post(
        "/api/v1/animals",
        json={"tag_code": "A-001", "species": "cow"},
        headers=auth_headers(token_a),
    )
    assert created.status_code == 201, created.text
    animal_id = created.json()["id"]

    # Owner A can read their own animal.
    own_read = client.get(f"/api/v1/animals/{animal_id}", headers=auth_headers(token_a))
    assert own_read.status_code == 200

    # Owner B, authenticated for tenant B, must not be able to read it —
    # and the response must not distinguish "exists but forbidden" from
    # "does not exist" (no enumeration).
    cross_read = client.get(f"/api/v1/animals/{animal_id}", headers=auth_headers(token_b))
    assert cross_read.status_code == 404
    assert cross_read.json()["error"]["code"] == "NOT_FOUND"

    # Tenant B's own animal listing must never include tenant A's rows.
    listing = client.get("/api/v1/animals", headers=auth_headers(token_b))
    assert listing.status_code == 200
    assert listing.json() == []

    assert tenant_a.id != tenant_b.id  # sanity: genuinely different tenants


def test_tenant_cannot_write_other_tenants_animal(client, control_db):
    tenant_a = _setup_tenant_with_owner(control_db, "FARM-A", "owner-a2@test.com")
    _setup_tenant_with_owner(control_db, "FARM-B", "owner-b2@test.com")

    token_a = dev_login(client, "owner-a2@test.com")
    token_b = dev_login(client, "owner-b2@test.com")

    created = client.post(
        "/api/v1/animals",
        json={"tag_code": "A-002", "species": "goat"},
        headers=auth_headers(token_a),
    )
    animal_id = created.json()["id"]

    patch = client.patch(
        f"/api/v1/animals/{animal_id}",
        json={"name": "Hacked", "expected_version": 1},
        headers=auth_headers(token_b),
    )
    assert patch.status_code == 404

    # Prove the row was never touched, reading directly through the router
    # with tenant A's own context (bypassing the API layer entirely).
    with TenantDataRouter.session_for(tenant_a.id) as db:
        animal = db.get(Animal, uuid.UUID(animal_id))
        assert animal is not None
        assert animal.name is None
        assert animal.version == 1


def test_rls_denies_cross_tenant_access_at_the_database_layer(control_db):
    """Direct proof independent of the API/authorization layer: RLS itself
    is what prevents the leak, not just application-level filtering.
    """
    tenant_a = create_tenant(control_db, company_code=unique_code("FARM-RLS-A"))
    tenant_b = create_tenant(control_db, company_code=unique_code("FARM-RLS-B"))
    control_db.commit()

    with TenantDataRouter.session_for(tenant_a.id) as db:
        animal = Animal(tenant_id=tenant_a.id, tag_code="RLS-A", species="cow")
        db.add(animal)
        db.flush()
        animal_id = animal.id

    with TenantDataRouter.session_for(tenant_b.id) as db:
        assert db.get(Animal, animal_id) is None

    with TenantDataRouter.session_for(tenant_a.id) as db:
        assert db.get(Animal, animal_id) is not None
