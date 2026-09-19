"""One subscription, one price, the whole product.

Origami used to be sold as tiers, and a great deal of machinery existed
to express that: a plan picker, per-plan module contents, a per-module
entitlement check on every tablet, and a licensing dashboard answering
"who has bought what". These tests replace the ones that described it
(test_plan_contents.py, test_module_licensing.py), because the questions
they asked no longer have answers.

What has to stay true is simpler and worth pinning hard: every customer
can open every module, there is exactly one plan and nobody can make a
second, and subscribing records what somebody pays without deciding what
they may use.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.common.enums import PlatformRole
from app.farmos.permissions import MODULE_CODES
from app.plans.licensing_map import MODULE_LICENCES
from app.plans.subscription_plan import PLAN_CODE
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import (
    FARMOS_DEMO_PASSWORD,
    add_farmos_user,
    create_tenant,
    ensure_farmos_catalog,
    grant_platform_role,
)


def admin_headers(client, control_db, email: str) -> dict:
    grant_platform_role(control_db, email, PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()
    return auth_headers(dev_login(client, email))


def tablet_catalog(client, token) -> dict[str, bool]:
    resp = client.get("/api/v1/modules/catalog", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    return {row["code"]: row["licensed_active"] for row in resp.json()}


def farm_with_owner(client, control_db, code: str) -> tuple[object, str]:
    tenant = create_tenant(control_db, company_code=unique_code(code))
    email = f"owner-{unique_code('x').lower()}@farm-sub.com"
    add_farmos_user(control_db, tenant, email, role="owner")
    ensure_farmos_catalog(control_db)
    control_db.commit()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": FARMOS_DEMO_PASSWORD}).json()[
        "access_token"
    ]
    return tenant, token


# --- What a customer gets ---------------------------------------------


def test_a_brand_new_customer_can_open_every_module(client, control_db):
    """No plan recorded, nothing granted, nothing paid yet — and the whole
    product is there.

    This is the promise the single subscription makes. A customer who has
    just been created and whose owner is signing in for the first time
    should not be looking at a farm with most of it switched off.
    """
    _tenant, token = farm_with_owner(client, control_db, "SUB-NEW")

    catalog = tablet_catalog(client, token)
    assert len(catalog) == len(MODULE_CODES) == 20
    dark = [code for code, lit in catalog.items() if not lit]
    assert dark == [], f"every module is included, but these were dark: {dark}"


def test_the_add_ons_keep_the_codes_the_mobile_app_calls(client, control_db):
    """docs/FARMOS_API.md pins POST /api/v1/modules/mouneh/activate as a
    literal path, so the two add-on licences stay lowercase however untidy
    that looks beside the rest. Renaming them would break a shipped app —
    still true now that they gate nothing, because the app still calls
    them by name.
    """
    _tenant, token = farm_with_owner(client, control_db, "SUB-ADDON")
    assert MODULE_LICENCES["mouneh_production"] == "mouneh"
    assert MODULE_LICENCES["farm_visits"] == "visits_agritourism"

    catalog = tablet_catalog(client, token)
    # Both add-ons, once sold separately, are simply part of the product.
    assert catalog["mouneh_production"] is True
    assert catalog["mouneh_inventory"] is True
    assert catalog["farm_visits"] is True


def test_the_catalog_still_says_which_part_of_the_product_a_screen_is(client, control_db):
    """license_code survives as a description, not a gate. The app shows
    it, and it is how "Milk Production" is known to belong with MILK."""
    _tenant, token = farm_with_owner(client, control_db, "SUB-DESC")
    resp = client.get("/api/v1/modules/catalog", headers=auth_headers(token))
    by_code = {row["code"]: row for row in resp.json()}
    assert by_code["milk_production"]["license_code"] == "MILK"
    assert by_code["animals"]["license_code"] == "ANIMALS"


# --- The plan ----------------------------------------------------------


def test_there_is_exactly_one_plan_and_it_makes_itself(client, control_db):
    headers = admin_headers(client, control_db, "one-plan@test.com")

    plans = client.get("/platform/v1/plans", headers=headers)
    assert plans.status_code == 200, plans.text
    body = plans.json()
    assert len(body) == 1
    assert body[0]["code"] == PLAN_CODE


def test_nobody_can_create_a_second_plan(client, control_db):
    """The endpoint is gone rather than guarded. A second plan is not a
    permission question — it is a thing the product no longer has."""
    headers = admin_headers(client, control_db, "no-second@test.com")
    resp = client.post(
        "/platform/v1/plans",
        headers=headers,
        json={"code": "PREMIUM", "name": "Premium", "currency": "USD"},
    )
    assert resp.status_code in (404, 405), resp.text


def test_the_plan_starts_unpriced_rather_than_guessing(client, control_db):
    """An invented price flows straight into the revenue dashboard and is
    indistinguishable from a real one there."""
    headers = admin_headers(client, control_db, "unpriced@test.com")
    plan = client.get("/platform/v1/plans", headers=headers).json()[0]
    assert plan["monthly_price_cents"] is None


def test_the_price_can_be_set_and_read_back(client, control_db):
    headers = admin_headers(client, control_db, "priced@test.com")
    plan = client.get("/platform/v1/plans", headers=headers).json()[0]

    resp = client.patch(
        f"/platform/v1/plans/{plan['id']}",
        headers=headers,
        json={"monthly_price_cents": 29900},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["monthly_price_cents"] == 29900

    again = client.get("/platform/v1/plans", headers=headers).json()[0]
    assert again["monthly_price_cents"] == 29900


# --- Subscribing -------------------------------------------------------


def test_subscribing_records_what_they_pay_without_choosing_anything(client, control_db):
    headers = admin_headers(client, control_db, "subscribe@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("SUB-REC"))
    control_db.commit()

    resp = client.patch(
        f"/platform/v1/tenants/{tenant.id}/subscription",
        headers=headers,
        json={
            "starts_at": datetime.now(timezone.utc).isoformat(),
            "billing_cycle": "MONTHLY",
            "status": "ACTIVE",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan_code"] == PLAN_CODE
    assert body["subscription"]["status"] == "ACTIVE"
    # Nothing to switch on: they already had everything.
    assert body["modules_granted"] == []


def test_an_older_console_sending_a_plan_id_still_works(client, control_db):
    """Deploys are not atomic. A console built before this change still
    sends plan_id and apply_plan_modules; both are ignored rather than
    rejected, so a customer is not un-subscribable for the minutes
    between the server updating and the browser reloading."""
    headers = admin_headers(client, control_db, "old-console@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("SUB-OLD"))
    control_db.commit()
    plan = client.get("/platform/v1/plans", headers=headers).json()[0]

    resp = client.patch(
        f"/platform/v1/tenants/{tenant.id}/subscription",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "apply_plan_modules": True,
            "starts_at": datetime.now(timezone.utc).isoformat(),
            "status": "ACTIVE",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan_code"] == PLAN_CODE


def test_a_customer_with_no_subscription_can_still_open_everything(client, control_db):
    """Deliberate: being unpaid is a commercial state, and cutting a farm
    off mid-season over it is a decision somebody makes, not a default the
    software applies while nobody is looking."""
    _tenant, token = farm_with_owner(client, control_db, "SUB-UNPAID")
    catalog = tablet_catalog(client, token)
    assert all(catalog.values())


# --- No gate left anywhere --------------------------------------------


def test_sync_works_for_a_customer_with_no_entitlement_rows(client, control_db):
    """The last per-module gate, and the one that would have bitten.

    /api/v1/sync/push used to sit behind require_module("ANIMALS"). Nothing
    writes entitlement rows any more, so that check would have refused every
    customer created after this change — with MODULE_NOT_ENTITLED, on the
    endpoint the tablet uses to send a day's work up, while the catalog it
    had just read said every module was open.
    """
    from app.common.enums import TenantRole
    from tests.conftest import dev_login
    from tests.helpers import add_membership

    tenant = create_tenant(control_db, company_code=unique_code("SUB-SYNC"))
    email = f"sync-{unique_code('x').lower()}@farm-sub.com"
    add_membership(
        control_db,
        tenant,
        email,
        role=TenantRole.TENANT_OWNER,
        permissions=["ANIMALS:create", "ANIMALS:read"],
    )
    control_db.commit()

    token = dev_login(client, email)
    resp = client.post(
        "/api/v1/sync/push",
        json={"device_id": str(uuid.uuid4()), "changes": []},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
