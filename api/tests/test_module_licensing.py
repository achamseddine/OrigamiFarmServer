"""Does selling a plan actually change what the farm's tablet can open?

Until the licence mapping existed the answer was no, and nothing said so.
Seventeen of the twenty modules the tablet app checks carried no
license_code, so GET /modules/catalog reported them licensed_active for
every farm regardless of plan, and the platform's own module codes
(ANIMALS, MILK, …) gated nothing anyone could see. A plan built out of
them reported success and changed nothing.

These tests run the whole way through — plan, subscription, tablet
catalog — because both halves existed before and it was only the join
between them that was missing.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.common.enums import PlatformRole
from app.farmos.permissions import MODULE_CODES
from app.plans.licensing_map import LICENCE_MODULES, MODULE_LICENCES
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import (
    FARMOS_DEMO_PASSWORD,
    add_farmos_user,
    create_tenant,
    ensure_farmos_catalog,
    grant_module,
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
    email = f"owner-{unique_code('x').lower()}@farm-lic.com"
    add_farmos_user(control_db, tenant, email, role="owner")
    ensure_farmos_catalog(control_db)
    control_db.commit()
    token = client.post(
        "/api/v1/auth/login", json={"email": email, "password": FARMOS_DEMO_PASSWORD}
    ).json()["access_token"]
    return tenant, token


def test_every_tablet_module_names_a_licence(client, control_db):
    """A module with no licence is free for every farm forever, which is
    how seventeen of them ended up ungated without anyone noticing.
    """
    missing = [code for code in MODULE_CODES if code not in MODULE_LICENCES]
    assert missing == [], f"these modules can never be sold: {missing}"


def test_a_module_is_dark_until_its_licence_is_held(client, control_db):
    tenant, token = farm_with_owner(client, control_db, "FARM-DARK")

    before = tablet_catalog(client, token)
    assert before["milk_production"] is False
    assert before["animals"] is False

    grant_module(control_db, tenant, "MILK")
    control_db.commit()

    after = tablet_catalog(client, token)
    assert after["milk_production"] is True, "holding MILK must open milk_production"
    assert after["animals"] is False, "and must not open anything else"


def test_one_licence_can_open_several_screens(client, control_db):
    """SALES covers sales, expenses and finance — the licence is the unit
    of sale, not the screen.
    """
    tenant, token = farm_with_owner(client, control_db, "FARM-SALES")
    grant_module(control_db, tenant, "SALES")
    control_db.commit()

    catalog = tablet_catalog(client, token)
    for module in LICENCE_MODULES["SALES"]:
        assert catalog[module] is True, f"{module} should be open under SALES"


def test_subscribing_to_a_plan_opens_the_tablet(client, control_db):
    """The join that did not exist: price list -> subscription -> app.

    This is the test the previous version of this feature would have
    failed while reporting three modules granted.
    """
    headers = admin_headers(client, control_db, "lic-plan@test.com")
    tenant, token = farm_with_owner(client, control_db, "FARM-PLAN")

    assert tablet_catalog(client, token)["milk_production"] is False

    plan = client.post(
        "/platform/v1/plans",
        json={
            "code": unique_code("DAIRY"),
            "name": "Dairy",
            "monthly_price_cents": 24_900,
            "module_codes": ["CORE", "ANIMALS", "MILK"],
        },
        headers=headers,
    )
    assert plan.status_code == 201, plan.text

    saved = client.patch(
        f"/platform/v1/tenants/{tenant.id}/subscription",
        json={
            "plan_id": plan.json()["id"],
            "billing_cycle": "MONTHLY",
            "starts_at": datetime.now(timezone.utc).isoformat(),
            "status": "ACTIVE",
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert sorted(saved.json()["modules_granted"]) == ["ANIMALS", "CORE", "MILK"]

    after = tablet_catalog(client, token)
    assert after["milk_production"] is True
    assert after["animals"] is True
    assert after["tasks"] is True, "CORE covers the everyday screens"
    # And the plan's boundary is real: nothing outside it opened.
    assert after["agriculture"] is False
    assert after["mouneh_production"] is False


def test_the_add_ons_keep_the_codes_the_mobile_app_calls(client, control_db):
    """docs/FARMOS_API.md pins POST /api/v1/modules/mouneh/activate as a
    literal path, so the two add-on licences stay lowercase however untidy
    that looks beside the rest. Renaming them would break a shipped app.
    """
    tenant, token = farm_with_owner(client, control_db, "FARM-ADDON")
    assert MODULE_LICENCES["mouneh_production"] == "mouneh"
    assert MODULE_LICENCES["farm_visits"] == "visits_agritourism"

    grant_module(control_db, tenant, "mouneh")
    control_db.commit()

    catalog = tablet_catalog(client, token)
    assert catalog["mouneh_production"] is True
    assert catalog["mouneh_inventory"] is True
    assert catalog["farm_visits"] is False


def test_the_licence_list_only_offers_codes_that_gate_something(client, control_db):
    """The console builds its plan picker from this, so a code nothing
    points at must not appear — that is what made a plan sell nothing.
    """
    headers = admin_headers(client, control_db, "lic-list@test.com")
    ensure_farmos_catalog(control_db)
    control_db.commit()

    licences = client.get("/platform/v1/licences", headers=headers)
    assert licences.status_code == 200, licences.text
    by_code = {row["license_code"]: row for row in licences.json()}

    assert set(by_code) == set(LICENCE_MODULES), "every licence, and only licences"
    assert by_code["SALES"]["unlocks"] == LICENCE_MODULES["SALES"]
    assert by_code["mouneh"]["is_addon"] is True
    assert by_code["MILK"]["is_addon"] is False
    # MOUNEH and FARM_VISITS are platform codes nothing points at, so they
    # cannot be sold by mistake.
    assert "MOUNEH" not in by_code
    assert "FARM_VISITS" not in by_code


def test_the_licence_list_counts_who_holds_each_one(client, control_db):
    headers = admin_headers(client, control_db, "lic-count@test.com")
    tenant, _ = farm_with_owner(client, control_db, "FARM-COUNT")

    def count_for(code: str) -> int:
        rows = client.get("/platform/v1/licences", headers=headers).json()
        return next(row["tenants_licensed"] for row in rows if row["license_code"] == code)

    before = count_for("EGGS")
    grant_module(control_db, tenant, "EGGS")
    control_db.commit()
    assert count_for("EGGS") == before + 1
