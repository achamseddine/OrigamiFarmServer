"""A plan is a bundle of modules at a price, and subscribing grants them.

The gap these protect against is the one that shipped: `plan_module` had
existed since the first migration with nothing reading or writing it, so a
plan was a name and a price with no contents, and putting a customer on
one granted them nothing at all. A subscription that entitles the customer
to nothing is worse than no subscription, because every screen reports it
as done.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.common.enums import EntitlementStatus, PlatformRole
from app.plans.models import TenantEntitlement
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import create_tenant, ensure_module, grant_module, grant_platform_role


def admin_token(client, control_db, email: str) -> str:
    grant_platform_role(control_db, email, PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()
    return dev_login(client, email)


def make_plan(client, headers, *, modules: list[str], monthly: int | None = 10_000) -> dict:
    resp = client.post(
        "/platform/v1/plans",
        json={
            "code": unique_code("PLN"),
            "name": "Test Plan",
            "monthly_price_cents": monthly,
            "module_codes": modules,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def subscribe(client, headers, tenant_id: str, plan_id: str, **extra) -> dict:
    body = {
        "plan_id": plan_id,
        "billing_cycle": "MONTHLY",
        "starts_at": datetime.now(timezone.utc).isoformat(),
        "status": "ACTIVE",
        **extra,
    }
    resp = client.patch(
        f"/platform/v1/tenants/{tenant_id}/subscription", json=body, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def entitlement_status(db, tenant_id, module_code) -> EntitlementStatus | None:
    row = (
        db.query(TenantEntitlement)
        .filter(
            TenantEntitlement.tenant_id == tenant_id,
            TenantEntitlement.module_code == module_code,
        )
        .one_or_none()
    )
    return row.status if row else None


def test_a_plan_carries_its_modules_through_the_api(client, control_db):
    token = admin_token(client, control_db, "plan-modules@test.com")
    headers = auth_headers(token)
    for code in ("mod_animals", "mod_milk"):
        ensure_module(control_db, code)
    control_db.commit()

    plan = make_plan(client, headers, modules=["mod_animals", "mod_milk"])
    assert plan["module_codes"] == ["mod_animals", "mod_milk"]

    listed = client.get("/platform/v1/plans", headers=headers).json()
    mine = next(item for item in listed if item["id"] == plan["id"])
    assert mine["module_codes"] == ["mod_animals", "mod_milk"], (
        "a price without its contents cannot be read as an offer"
    )


def test_setting_a_plans_modules_replaces_the_set(client, control_db):
    """Unticking a box has to remove the module, which a merge would not do."""
    token = admin_token(client, control_db, "plan-replace@test.com")
    headers = auth_headers(token)
    for code in ("mod_a", "mod_b", "mod_c"):
        ensure_module(control_db, code)
    control_db.commit()

    plan = make_plan(client, headers, modules=["mod_a", "mod_b"])
    updated = client.put(
        f"/platform/v1/plans/{plan['id']}/modules",
        json={"module_codes": ["mod_b", "mod_c"]},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["module_codes"] == ["mod_b", "mod_c"]


def test_a_plan_cannot_include_a_module_that_does_not_exist(client, control_db):
    token = admin_token(client, control_db, "plan-unknown@test.com")
    headers = auth_headers(token)
    plan = make_plan(client, headers, modules=[])

    resp = client.put(
        f"/platform/v1/plans/{plan['id']}/modules",
        json={"module_codes": ["not_a_real_module"]},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "not_a_real_module" in resp.text


def test_subscribing_grants_every_module_the_plan_includes(client, control_db):
    """The whole point: on a plan means able to use what the plan sells."""
    token = admin_token(client, control_db, "plan-grant@test.com")
    headers = auth_headers(token)
    for code in ("mod_grant_1", "mod_grant_2"):
        ensure_module(control_db, code)
    tenant = create_tenant(control_db, company_code=unique_code("FARM-GRANT"))
    control_db.commit()

    plan = make_plan(client, headers, modules=["mod_grant_1", "mod_grant_2"])
    result = subscribe(client, headers, str(tenant.id), plan["id"])

    assert sorted(result["modules_granted"]) == ["mod_grant_1", "mod_grant_2"]
    assert result["plan_code"] == plan["code"]
    for code in ("mod_grant_1", "mod_grant_2"):
        assert entitlement_status(control_db, tenant.id, code) == EntitlementStatus.ACTIVE


def test_resubscribing_reports_modules_that_were_already_on(client, control_db):
    token = admin_token(client, control_db, "plan-again@test.com")
    headers = auth_headers(token)
    ensure_module(control_db, "mod_again")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-AGAIN"))
    control_db.commit()

    plan = make_plan(client, headers, modules=["mod_again"])
    subscribe(client, headers, str(tenant.id), plan["id"])
    second = subscribe(client, headers, str(tenant.id), plan["id"])

    assert second["modules_granted"] == []
    assert second["modules_already_active"] == ["mod_again"]


def test_moving_to_a_smaller_plan_reports_the_extras_but_keeps_them_on(client, control_db):
    """A price-list edit must not switch off what a farm is recording with.

    The extras are named so somebody can decide to withdraw them; nothing
    here decides that on their behalf.
    """
    token = admin_token(client, control_db, "plan-downgrade@test.com")
    headers = auth_headers(token)
    for code in ("mod_keep", "mod_extra"):
        ensure_module(control_db, code)
    tenant = create_tenant(control_db, company_code=unique_code("FARM-DOWN"))
    control_db.commit()

    big = make_plan(client, headers, modules=["mod_keep", "mod_extra"])
    subscribe(client, headers, str(tenant.id), big["id"])

    small = make_plan(client, headers, modules=["mod_keep"])
    result = subscribe(client, headers, str(tenant.id), small["id"])

    assert result["modules_not_in_plan"] == ["mod_extra"]
    assert entitlement_status(control_db, tenant.id, "mod_extra") == EntitlementStatus.ACTIVE, (
        "a downgrade must never silently cut off a module in use"
    )


def test_a_hand_granted_module_survives_being_outside_the_plan(client, control_db):
    """An override is somebody's explicit decision about one customer."""
    token = admin_token(client, control_db, "plan-override@test.com")
    headers = auth_headers(token)
    ensure_module(control_db, "mod_planned")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-OVR"))
    grant_module(control_db, tenant, "mod_special")
    control_db.commit()

    plan = make_plan(client, headers, modules=["mod_planned"])
    result = subscribe(client, headers, str(tenant.id), plan["id"])

    assert "mod_special" in result["modules_not_in_plan"]
    assert entitlement_status(control_db, tenant.id, "mod_special") == EntitlementStatus.ACTIVE


def test_the_grant_can_be_turned_off_to_record_a_commercial_fact_only(client, control_db):
    token = admin_token(client, control_db, "plan-noapply@test.com")
    headers = auth_headers(token)
    ensure_module(control_db, "mod_noapply")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-NOAP"))
    control_db.commit()

    plan = make_plan(client, headers, modules=["mod_noapply"])
    result = subscribe(client, headers, str(tenant.id), plan["id"], apply_plan_modules=False)

    assert result["modules_granted"] == []
    assert entitlement_status(control_db, tenant.id, "mod_noapply") is None


def test_changing_what_a_plan_sells_does_not_reach_into_existing_customers(client, control_db):
    """Editing the price list is not a way to revoke a live farm's modules."""
    token = admin_token(client, control_db, "plan-edit@test.com")
    headers = auth_headers(token)
    for code in ("mod_was_in", "mod_now_in"):
        ensure_module(control_db, code)
    tenant = create_tenant(control_db, company_code=unique_code("FARM-EDIT"))
    control_db.commit()

    plan = make_plan(client, headers, modules=["mod_was_in"])
    subscribe(client, headers, str(tenant.id), plan["id"])

    client.put(
        f"/platform/v1/plans/{plan['id']}/modules",
        json={"module_codes": ["mod_now_in"]},
        headers=headers,
    )
    assert entitlement_status(control_db, tenant.id, "mod_was_in") == EntitlementStatus.ACTIVE


def test_only_commercial_staff_can_change_what_a_plan_sells(client, control_db):
    grant_platform_role(control_db, "plan-support@test.com", PlatformRole.PLATFORM_SUPPORT_ADMIN)
    control_db.commit()
    support = auth_headers(dev_login(client, "plan-support@test.com"))

    admin = auth_headers(admin_token(client, control_db, "plan-owner@test.com"))
    plan = make_plan(client, admin, modules=[])

    resp = client.put(
        f"/platform/v1/plans/{plan['id']}/modules",
        json={"module_codes": []},
        headers=support,
    )
    assert resp.status_code == 403
