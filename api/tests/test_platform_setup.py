"""The first-run checklist behind the console's Getting started screen.

What these protect is the difference between a checklist and a decoration:
every step has to be decided from the database on each call, so a step that
was ticked once goes back to outstanding the moment the thing it checked
stops being true. The subscription step is the one that matters most — a
customer signed up and never put on a plan is exactly the gap that makes
revenue read as zero.
"""

from __future__ import annotations

from app.common.enums import PlatformRole
from app.plans.models import Plan, Subscription
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import create_tenant, grant_platform_role
from tests.test_platform_auth import PASSWORD, login, make_staff

SETUP = "/platform/v1/setup"

# The console renders these in order and links each one to a screen, so the
# set and its order are part of the contract, not an implementation detail.
EXPECTED_KEYS = ["password", "staff", "plans", "pricing", "tenant", "subscription", "device"]


def setup_state(client, token) -> dict:
    resp = client.get(SETUP, headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


def step(body: dict, key: str) -> dict:
    return next(item for item in body["steps"] if item["key"] == key)


def unsubscribed_count(detail: str) -> int:
    """The leading number of "N of M customers with no plan recorded"."""
    return 0 if not detail[0].isdigit() else int(detail.split(" ", 1)[0])


def admin_token(client, control_db, email: str) -> str:
    grant_platform_role(control_db, email, PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()
    return dev_login(client, email)


def test_every_step_is_reported_in_the_order_the_console_renders(client, control_db):
    token = admin_token(client, control_db, "setup-keys@test.com")
    body = setup_state(client, token)

    assert [item["key"] for item in body["steps"]] == EXPECTED_KEYS
    assert body["steps_total"] == len(EXPECTED_KEYS)
    assert body["steps_done"] == sum(1 for item in body["steps"] if item["done"])
    assert body["complete"] is (body["steps_done"] == body["steps_total"])
    # Never a bare boolean: the count that decided it is what makes the step
    # actionable.
    assert all(item["detail"] for item in body["steps"])


def test_a_password_someone_else_typed_is_an_outstanding_step(client, control_db):
    """The bootstrap admin's password is known to whoever created the
    account, which is a different security position from one they chose.
    """
    make_staff(control_db, "setup-pw@test.com")
    grant_platform_role(control_db, "setup-pw@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()

    token = login(client, "setup-pw@test.com", PASSWORD).json()["access_token"]
    assert step(setup_state(client, token), "password")["done"] is False

    changed = client.post(
        "/platform/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "a-much-longer-new-secret"},
        headers=auth_headers(token),
    )
    assert changed.status_code == 204, changed.text

    after = step(setup_state(client, token), "password")
    assert after["done"] is True
    assert "Set by you" in after["detail"]


def test_an_admin_reset_puts_the_password_step_back(client, control_db):
    """Whoever reset it knows it, so the account is back where it started."""
    subject = make_staff(control_db, "setup-reset@test.com")
    make_staff(control_db, "setup-resetter@test.com")
    grant_platform_role(control_db, "setup-reset@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    grant_platform_role(control_db, "setup-resetter@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()

    token = login(client, "setup-reset@test.com", PASSWORD).json()["access_token"]
    client.post(
        "/platform/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "a-much-longer-new-secret"},
        headers=auth_headers(token),
    )
    assert step(setup_state(client, token), "password")["done"] is True

    resetter = login(client, "setup-resetter@test.com", PASSWORD).json()["access_token"]
    reset = client.post(
        f"/platform/v1/staff/{subject.id}/password",
        json={"new_password": "another-long-secret-here"},
        headers=auth_headers(resetter),
    )
    assert reset.status_code == 204, reset.text

    # Their session outlives the reset, so the same token now reports the
    # step as outstanding again.
    assert step(setup_state(client, token), "password")["done"] is False


def test_an_identity_with_no_password_has_nothing_to_change(client, control_db):
    """An OIDC-only deployment must not be left with a step nobody can
    ever complete.
    """
    token = admin_token(client, control_db, "setup-oidc@test.com")
    password = step(setup_state(client, token), "password")
    assert password["done"] is True
    assert "identity provider" in password["detail"]


def test_me_reports_the_same_password_state_to_the_console(client, control_db):
    make_staff(control_db, "setup-me@test.com")
    control_db.commit()
    token = login(client, "setup-me@test.com", PASSWORD).json()["access_token"]

    me = client.get("/platform/v1/me", headers=auth_headers(token))
    assert me.json()["password_set_by_someone_else"] is True

    client.post(
        "/platform/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "a-much-longer-new-secret"},
        headers=auth_headers(token),
    )
    me = client.get("/platform/v1/me", headers=auth_headers(token))
    assert me.json()["password_set_by_someone_else"] is False


def test_a_customer_with_no_plan_recorded_reopens_the_subscription_step(client, control_db):
    """The step this whole screen exists for. It is not "one subscription
    exists" — every customer needs one, so a new tenant puts it back.
    """
    token = admin_token(client, control_db, "setup-sub@test.com")

    plan = Plan(code=unique_code("SETUP"), name="Setup Plan", monthly_price_cents=10_000)
    control_db.add(plan)
    tenant = create_tenant(control_db, company_code=unique_code("FARM-SETUP"))
    control_db.commit()

    unpaid = step(setup_state(client, token), "subscription")
    assert unpaid["done"] is False
    assert "no plan recorded" in unpaid["detail"]
    before = unsubscribed_count(unpaid["detail"])

    # And the tenant step it depends on is satisfied by that same tenant.
    assert step(setup_state(client, token), "tenant")["done"] is True

    control_db.add(
        Subscription(tenant_id=tenant.id, plan_id=plan.id, starts_at=tenant.created_at)
    )
    control_db.commit()

    # Other tests leave unsubscribed tenants of their own behind, so what
    # holds is that this one left the gap — not that the gap closed.
    after = step(setup_state(client, token), "subscription")
    assert unsubscribed_count(after["detail"]) == before - 1


def test_pricing_counts_only_plans_that_carry_a_price(client, control_db):
    token = admin_token(client, control_db, "setup-price@test.com")

    control_db.add(Plan(code=unique_code("UNPRICED"), name="Unpriced Plan"))
    control_db.commit()
    detail = step(setup_state(client, token), "pricing")["detail"]

    priced, _, rest = detail.partition(" of ")
    total = rest.split(" ")[0]
    assert int(priced) < int(total), f"an unpriced plan must not count as priced: {detail}"


def test_any_platform_role_may_read_the_checklist(client, control_db):
    """It says what the platform is missing, not what it charges — a
    support admin needs it as much as a super admin does.
    """
    grant_platform_role(control_db, "setup-support@test.com", PlatformRole.PLATFORM_SUPPORT_ADMIN)
    control_db.commit()
    token = dev_login(client, "setup-support@test.com")
    assert client.get(SETUP, headers=auth_headers(token)).status_code == 200


def test_an_account_with_no_platform_role_cannot_read_it(client, control_db):
    make_staff(control_db, "setup-norole@test.com")
    control_db.commit()
    token = login(client, "setup-norole@test.com", PASSWORD).json()["access_token"]
    assert client.get(SETUP, headers=auth_headers(token)).status_code == 403
