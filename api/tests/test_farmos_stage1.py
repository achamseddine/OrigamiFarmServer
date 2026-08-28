"""Stage 1 of the FarmOS tablet contract: the five endpoints (plus login)
that make the app render at all. See docs/FARMOS_API.md.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.common.enums import EntitlementSource, EntitlementStatus, MembershipStatus, TenantStatus
from app.plans.models import TenantEntitlement
from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant


def test_root_health_is_unauthenticated_and_cheap(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_login_and_restore_session_via_auth_me(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-S1"), display_name="Origami Farms")
    user, _ = add_farmos_user(control_db, tenant, "ali@origami-demo.com", role="owner", display_name="Ali")
    control_db.commit()

    token = farmos_login(client, "ali@origami-demo.com", FARMOS_DEMO_PASSWORD)

    me = client.get("/api/v1/auth/me", headers=farmos_headers(token))
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["id"] == str(user.id)
    assert body["farm_id"] == str(tenant.id)
    assert body["name"] == "Ali"
    assert body["role"] == "owner"
    assert body["active"] is True


def test_login_rejects_wrong_password_with_a_farmer_facing_message(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-S1"))
    add_farmos_user(control_db, tenant, "wrongpw@origami-demo.com", role="owner")
    control_db.commit()

    resp = client.post(
        "/api/v1/auth/login", json={"email": "wrongpw@origami-demo.com", "password": "not-the-password"}
    )
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Incorrect email or password."}


def test_owner_gets_full_access_grid_with_all_twenty_modules(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-S1"))
    add_farmos_user(control_db, tenant, "owner@origami-demo.com", role="owner")
    control_db.commit()
    token = farmos_login(client, "owner@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.get("/api/v1/me/access", headers=farmos_headers(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["full_access"] is True
    assert body["role"] == "owner"
    assert len(body["modules"]) == 20
    for grid in body["modules"].values():
        assert all(grid.values())


def test_worker_sees_only_their_own_granted_modules(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-S1"))
    add_farmos_user(
        control_db,
        tenant,
        "vet@origami-demo.com",
        role="veterinarian",
        grid={"animal_health": ["view", "create", "edit"], "animals": ["view"]},
    )
    control_db.commit()
    token = farmos_login(client, "vet@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.get("/api/v1/me/access", headers=farmos_headers(token))
    body = resp.json()
    assert body["full_access"] is False
    # Only the two granted modules appear — a module with zero rows is
    # absent, not present-and-false.
    assert set(body["modules"].keys()) == {"animal_health", "animals"}
    assert body["modules"]["animal_health"] == {
        "view": True,
        "create": True,
        "edit": True,
        "delete": False,
        "approve": False,
        "export": False,
        "assign": False,
        "configure": False,
    }
    assert body["modules"]["animals"]["view"] is True
    assert body["modules"]["animals"]["edit"] is False


def test_modules_catalog_reflects_this_farms_own_licence(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-S1"))
    add_farmos_user(control_db, tenant, "catalog@origami-demo.com", role="owner")
    control_db.add(
        TenantEntitlement(
            tenant_id=tenant.id,
            module_code="mouneh",
            status=EntitlementStatus.ACTIVE,
            source=EntitlementSource.OVERRIDE,
            effective_from=datetime.now(timezone.utc),
            plan="mouneh_addon",
        )
    )
    control_db.commit()
    token = farmos_login(client, "catalog@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.get("/api/v1/modules/catalog", headers=farmos_headers(token))
    assert resp.status_code == 200
    by_code = {entry["code"]: entry for entry in resp.json()}
    assert len(by_code) == 20

    # Mouneh production/inventory both key off the "mouneh" licence, which
    # this farm has — active.
    assert by_code["mouneh_production"]["licensed_active"] is True
    assert by_code["mouneh_production"]["license_code"] == "mouneh"
    # Farm Visits keys off "visits_agritourism", which this farm never
    # purchased — inactive, and the app hides the module entirely.
    assert by_code["farm_visits"]["licensed_active"] is False
    # An ordinary included module always reports active with no licence.
    assert by_code["animals"]["license_code"] is None
    assert by_code["animals"]["licensed_active"] is True


def test_farms_me_returns_this_users_own_farm(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-S1"), display_name="Origami Farms")
    add_farmos_user(control_db, tenant, "settings@origami-demo.com", role="owner")
    control_db.commit()
    token = farmos_login(client, "settings@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.get("/api/v1/farms/me", headers=farmos_headers(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(tenant.id)
    assert body["name"] == "Origami Farms"


def test_suspended_farm_blocks_access_with_a_farmer_facing_message(client, control_db):
    tenant = create_tenant(
        control_db, company_code=unique_code("FARM-S1"), status=TenantStatus.SUSPENDED
    )
    add_farmos_user(control_db, tenant, "suspended@origami-demo.com", role="owner")
    control_db.commit()
    token = farmos_login(client, "suspended@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.get("/api/v1/auth/me", headers=farmos_headers(token))
    assert resp.status_code == 403
    assert "subscription is paused" in resp.json()["detail"]


def test_deactivated_employee_cannot_use_their_token(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-S1"))
    _, membership = add_farmos_user(control_db, tenant, "gone@origami-demo.com", role="worker")
    control_db.commit()
    token = farmos_login(client, "gone@origami-demo.com", FARMOS_DEMO_PASSWORD)

    membership.status = MembershipStatus.INACTIVE
    control_db.commit()

    resp = client.get("/api/v1/auth/me", headers=farmos_headers(token))
    assert resp.status_code == 403
    assert resp.json()["detail"]
