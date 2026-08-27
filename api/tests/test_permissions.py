from __future__ import annotations

import uuid

from app.common.enums import TenantRole
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import add_membership, create_tenant, grant_module


def test_user_without_create_permission_cannot_create(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-PERM"))
    grant_module(control_db, tenant, "ANIMALS")
    # Entitled tenant, but this employee was only ever granted read access.
    add_membership(
        control_db,
        tenant,
        "readonly@test.com",
        role=TenantRole.EMPLOYEE,
        permissions=["ANIMALS:read"],
    )
    control_db.commit()

    token = dev_login(client, "readonly@test.com")
    resp = client.post(
        "/api/v1/animals", json={"tag_code": "P-1", "species": "cow"}, headers=auth_headers(token)
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "PERMISSION_DENIED"

    # Read still works — this proves the block is permission-specific, not
    # a blanket module lockout.
    listing = client.get("/api/v1/animals", headers=auth_headers(token))
    assert listing.status_code == 200


def test_employee_without_farm_access_cannot_reach_other_farm_scoped_data(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-SCOPE"))
    grant_module(control_db, tenant, "ANIMALS")
    add_membership(
        control_db,
        tenant,
        "scoped@test.com",
        role=TenantRole.EMPLOYEE,
        permissions=["ANIMALS:create"],
        farm_ids=[],  # explicit employee with zero farm grants
    )
    control_db.commit()

    token = dev_login(client, "scoped@test.com")
    resp = client.post(
        "/api/v1/animals",
        json={"tag_code": "S-1", "species": "cow", "farm_id": str(uuid.uuid4())},
        headers=auth_headers(token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FARM_SCOPE_DENIED"
