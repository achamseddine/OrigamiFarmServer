"""Mandatory permission-enforcement tests, exercised against the FarmOS
tablet contract's own permission grid (app/farmos/permissions.py) and
/api/v1/animals — the old TenantRole/permission-string scheme these
originally targeted was superseded by it in Stage 2.
"""

from __future__ import annotations

import uuid

from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant


def test_user_without_create_permission_cannot_create(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-PERM"))
    # Entitled tenant, but this employee was only ever granted view access.
    add_farmos_user(
        control_db,
        tenant,
        "readonly@origami-demo.com",
        role="worker",
        grid={"animals": ["view"]},
    )
    control_db.commit()

    token = farmos_login(client, "readonly@origami-demo.com", FARMOS_DEMO_PASSWORD)
    resp = client.post(
        "/api/v1/animals",
        json={"tag": "P-1", "name": "Bessie", "species": "cow"},
        headers=farmos_headers(token),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]

    # Read still works — this proves the block is permission-specific, not
    # a blanket module lockout.
    listing = client.get(
        "/api/v1/animals", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert listing.status_code == 200


def test_employee_with_no_module_permission_cannot_reach_farm_scoped_data(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-SCOPE"))
    # This employee has no "animals" permission rows at all — the module is
    # simply absent from their grid, not present-and-false.
    add_farmos_user(
        control_db,
        tenant,
        "scoped@origami-demo.com",
        role="worker",
        grid={"tasks": ["view"]},
    )
    control_db.commit()

    token = farmos_login(client, "scoped@origami-demo.com", FARMOS_DEMO_PASSWORD)
    resp = client.post(
        "/api/v1/animals",
        json={"tag": "S-1", "name": "Bessie", "species": "cow"},
        headers=farmos_headers(token),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]

    listing = client.get(
        "/api/v1/animals", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert listing.status_code == 403


def test_farm_id_from_another_tenant_is_rejected_not_enumerable(client, control_db):
    """farm_id is never trusted as authorization — see check_farm_id in
    app/farmos/deps.py. A client-supplied farm_id belonging to a real,
    different tenant must fail exactly like a made-up one: 404, not 403,
    so it can't be used to probe which farm ids exist.
    """
    tenant = create_tenant(control_db, company_code=unique_code("FARM-OWN"))
    other_tenant = create_tenant(control_db, company_code=unique_code("FARM-OTHER"))
    add_farmos_user(control_db, tenant, "owner@origami-demo.com", role="owner")
    control_db.commit()

    token = farmos_login(client, "owner@origami-demo.com", FARMOS_DEMO_PASSWORD)

    other_farm = client.get(
        "/api/v1/animals", params={"farm_id": str(other_tenant.id)}, headers=farmos_headers(token)
    )
    assert other_farm.status_code == 404

    made_up_farm = client.get(
        "/api/v1/animals", params={"farm_id": str(uuid.uuid4())}, headers=farmos_headers(token)
    )
    assert made_up_farm.status_code == 404
    assert made_up_farm.json()["detail"] == other_farm.json()["detail"]
