"""Mandatory module-entitlement lifecycle tests, exercised against the
FarmOS tablet contract's own consumer-facing surface (GET
/api/v1/modules/catalog's licensed_active flag) rather than the deleted
/api/v1/me/entitlements. "mouneh" is used as the module under test since
it is an actual licensed add-on in the FarmOS catalog (see
scripts/seed.py's FARMOS_MODULE_CATALOG) — unlike "animals", which is a
core, always-available module with no entitlement gating at all.
"""

from __future__ import annotations

from app.common.enums import PlatformRole
from app.plans.models import TenantEntitlement
from tests.conftest import auth_headers, dev_login, farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant, grant_platform_role


def test_inactive_licensed_module_reports_not_licensed(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-NOMOD"))
    add_farmos_user(control_db, tenant, "noent@origami-demo.com", role="owner")
    control_db.commit()

    token = farmos_login(client, "noent@origami-demo.com", FARMOS_DEMO_PASSWORD)
    resp = client.get("/api/v1/modules/catalog", headers=farmos_headers(token))
    assert resp.status_code == 200
    by_code = {entry["code"]: entry for entry in resp.json()}

    # Never activated for this farm — the app is expected to hide the
    # Mouneh screens entirely on this flag, not on a per-request 403.
    assert by_code["mouneh_production"]["licensed_active"] is False
    assert by_code["mouneh_inventory"]["licensed_active"] is False


def test_module_activation_appears_in_catalog_response(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-ACT"))
    add_farmos_user(control_db, tenant, "act@origami-demo.com", role="owner")
    grant_platform_role(control_db, "super@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()

    admin_token = dev_login(client, "super@test.com")
    before = client.get(
        f"/platform/v1/tenants/{tenant.id}/entitlements", headers=auth_headers(admin_token)
    )
    assert before.json() == []

    activate = client.post(
        f"/platform/v1/tenants/{tenant.id}/entitlements/mouneh/activate",
        json={"reason": "customer purchased the Mouneh add-on"},
        headers=auth_headers(admin_token),
    )
    assert activate.status_code == 200, activate.text

    user_token = farmos_login(client, "act@origami-demo.com", FARMOS_DEMO_PASSWORD)
    catalog = client.get("/api/v1/modules/catalog", headers=farmos_headers(user_token))
    assert catalog.status_code == 200
    by_code = {entry["code"]: entry for entry in catalog.json()}
    assert by_code["mouneh_production"]["licensed_active"] is True
    assert by_code["mouneh_inventory"]["licensed_active"] is True


def test_module_deactivation_preserves_the_entitlement_record(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-DEACT"))
    add_farmos_user(control_db, tenant, "deact@origami-demo.com", role="owner")
    grant_platform_role(control_db, "super2@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()

    admin_token = dev_login(client, "super2@test.com")
    activate = client.post(
        f"/platform/v1/tenants/{tenant.id}/entitlements/mouneh/activate",
        json={"reason": "customer purchased the Mouneh add-on"},
        headers=auth_headers(admin_token),
    )
    assert activate.status_code == 200, activate.text

    user_token = farmos_login(client, "deact@origami-demo.com", FARMOS_DEMO_PASSWORD)
    catalog = client.get("/api/v1/modules/catalog", headers=farmos_headers(user_token))
    assert {e["code"]: e for e in catalog.json()}["mouneh_production"]["licensed_active"] is True

    deactivate = client.post(
        f"/platform/v1/tenants/{tenant.id}/entitlements/mouneh/deactivate",
        json={"reason": "subscription downgraded"},
        headers=auth_headers(admin_token),
    )
    assert deactivate.status_code == 200, deactivate.text

    catalog_after = client.get("/api/v1/modules/catalog", headers=farmos_headers(user_token))
    by_code = {e["code"]: e for e in catalog_after.json()}
    assert by_code["mouneh_production"]["licensed_active"] is False

    # The historical entitlement record itself is never deleted — only its
    # status changes — so any Mouneh data recorded while active is never
    # lost, just no longer reachable until reactivated.
    entitlement = (
        control_db.query(TenantEntitlement)
        .filter(TenantEntitlement.tenant_id == tenant.id, TenantEntitlement.module_code == "mouneh")
        .one()
    )
    assert entitlement.status.value == "INACTIVE"
