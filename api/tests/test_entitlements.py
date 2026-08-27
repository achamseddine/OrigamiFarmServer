from __future__ import annotations

from app.common.enums import PlatformRole, TenantRole
from app.common.tenant_router import TenantDataRouter
from app.tenant_api.models import Animal
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import add_membership, create_tenant, ensure_module, grant_module, grant_platform_role


def test_disabled_module_endpoint_returns_module_not_entitled(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-NOMOD"))
    # Note: ANIMALS is deliberately NOT granted to this tenant.
    add_membership(
        control_db,
        tenant,
        "noent@test.com",
        role=TenantRole.TENANT_OWNER,
        permissions=["ANIMALS:create"],
    )
    control_db.commit()

    token = dev_login(client, "noent@test.com")
    resp = client.post(
        "/api/v1/animals", json={"tag_code": "X-1", "species": "cow"}, headers=auth_headers(token)
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "MODULE_NOT_ENTITLED"


def test_module_activation_appears_in_entitlement_response(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-ACT"))
    ensure_module(control_db, "ANIMALS")
    add_membership(control_db, tenant, "act@test.com", role=TenantRole.TENANT_OWNER)
    grant_platform_role(control_db, "super@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()

    admin_token = dev_login(client, "super@test.com")
    before = client.get(
        f"/platform/v1/tenants/{tenant.id}/entitlements", headers=auth_headers(admin_token)
    )
    assert before.json() == []

    activate = client.post(
        f"/platform/v1/tenants/{tenant.id}/entitlements/ANIMALS/activate",
        json={"reason": "customer purchased Animals module"},
        headers=auth_headers(admin_token),
    )
    assert activate.status_code == 200, activate.text

    user_token = dev_login(client, "act@test.com")
    entitlements = client.get("/api/v1/me/entitlements", headers=auth_headers(user_token))
    assert entitlements.status_code == 200
    body = entitlements.json()
    assert body["modules"]["ANIMALS"]["enabled"] is True


def test_module_deactivation_preserves_data_but_blocks_access(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-DEACT"))
    grant_module(control_db, tenant, "ANIMALS")
    add_membership(
        control_db,
        tenant,
        "deact@test.com",
        role=TenantRole.TENANT_OWNER,
        permissions=["ANIMALS:create", "ANIMALS:read"],
    )
    grant_platform_role(control_db, "super2@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()

    user_token = dev_login(client, "deact@test.com")
    created = client.post(
        "/api/v1/animals", json={"tag_code": "D-1", "species": "cow"}, headers=auth_headers(user_token)
    )
    assert created.status_code == 201
    animal_id = created.json()["id"]

    admin_token = dev_login(client, "super2@test.com")
    deactivate = client.post(
        f"/platform/v1/tenants/{tenant.id}/entitlements/ANIMALS/deactivate",
        json={"reason": "subscription downgraded"},
        headers=auth_headers(admin_token),
    )
    assert deactivate.status_code == 200, deactivate.text

    blocked = client.get("/api/v1/animals", headers=auth_headers(user_token))
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "MODULE_NOT_ENTITLED"

    # The historical record itself must still exist, untouched.
    import uuid as _uuid

    with TenantDataRouter.session_for(tenant.id) as db:
        animal = db.get(Animal, _uuid.UUID(animal_id))
        assert animal is not None
        assert animal.deleted_at is None
        assert animal.tag_code == "D-1"
