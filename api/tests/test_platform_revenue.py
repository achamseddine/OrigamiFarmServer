"""Recurring revenue reporting.

The distinction these tests exist to protect: a subscription whose plan has
no price must never be counted as zero revenue, because that turns "nobody
priced this plan" into "this customer pays nothing" — and the second one
nobody goes and fixes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.common.enums import BillingCycle, PlatformRole, SubscriptionStatus
from app.plans.models import Plan, Subscription
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import create_tenant, grant_platform_role


def admin_token(client, control_db, email: str) -> str:
    grant_platform_role(control_db, email, PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()
    return dev_login(client, email)


def make_plan(db, *, monthly: int | None = None, annual: int | None = None, currency="USD") -> Plan:
    plan = Plan(
        code=unique_code("PLAN"),
        name="Test Plan",
        currency=currency,
        monthly_price_cents=monthly,
        annual_price_cents=annual,
    )
    db.add(plan)
    db.flush()
    return plan


def subscribe(db, tenant, plan, *, status=SubscriptionStatus.ACTIVE, cycle=BillingCycle.MONTHLY,
              renews_in_days: int | None = None) -> Subscription:
    renews_at = (
        datetime.now(timezone.utc) + timedelta(days=renews_in_days) if renews_in_days is not None else None
    )
    subscription = Subscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        status=status,
        billing_cycle=cycle,
        starts_at=datetime.now(timezone.utc),
        renews_at=renews_at,
    )
    db.add(subscription)
    db.flush()
    return subscription


def revenue(client, token) -> dict:
    resp = client.get("/platform/v1/metrics/revenue", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_mrr_sums_priced_active_subscriptions(client, control_db):
    token = admin_token(client, control_db, "rev-mrr@test.com")
    plan = make_plan(control_db, monthly=25_000)
    for _ in range(3):
        subscribe(control_db, create_tenant(control_db, company_code=unique_code("FARM-REV")), plan)
    control_db.commit()

    body = revenue(client, token)
    assert body["mrr_cents"] >= 75_000
    assert body["arr_cents"] == body["mrr_cents"] * 12
    assert body["paying_tenants"] >= 3


def test_annual_subscriptions_are_normalised_into_mrr(client, control_db):
    token = admin_token(client, control_db, "rev-annual@test.com")
    before = revenue(client, token)["mrr_cents"]

    plan = make_plan(control_db, annual=120_000)
    subscribe(
        control_db,
        create_tenant(control_db, company_code=unique_code("FARM-ANN")),
        plan,
        cycle=BillingCycle.ANNUAL,
    )
    control_db.commit()

    after = revenue(client, token)["mrr_cents"]
    assert after - before == 10_000  # 120_000 / 12


def test_an_unpriced_plan_is_reported_as_unpriced_not_as_zero(client, control_db):
    token = admin_token(client, control_db, "rev-unpriced@test.com")
    before = revenue(client, token)

    plan = make_plan(control_db)  # no price at all
    subscribe(control_db, create_tenant(control_db, company_code=unique_code("FARM-NOPRICE")), plan)
    control_db.commit()

    after = revenue(client, token)
    assert after["mrr_cents"] == before["mrr_cents"], "an unpriced plan must not move MRR"
    assert after["unpriced_subscriptions"] == before["unpriced_subscriptions"] + 1
    assert after["paying_tenants"] == before["paying_tenants"], "unpriced is not a paying tenant"


def test_a_monthly_subscription_on_an_annual_only_price_is_unpriced(client, control_db):
    """Priced for one cycle is not priced for the other."""
    token = admin_token(client, control_db, "rev-cycle@test.com")
    before = revenue(client, token)

    plan = make_plan(control_db, annual=120_000)  # annual only
    subscribe(
        control_db,
        create_tenant(control_db, company_code=unique_code("FARM-CYC")),
        plan,
        cycle=BillingCycle.MONTHLY,
    )
    control_db.commit()

    after = revenue(client, token)
    assert after["mrr_cents"] == before["mrr_cents"]
    assert after["unpriced_subscriptions"] == before["unpriced_subscriptions"] + 1


def test_trials_are_pipeline_not_revenue(client, control_db):
    token = admin_token(client, control_db, "rev-trial@test.com")
    before = revenue(client, token)

    plan = make_plan(control_db, monthly=30_000)
    subscribe(
        control_db,
        create_tenant(control_db, company_code=unique_code("FARM-TRIAL")),
        plan,
        status=SubscriptionStatus.ONBOARDING_TRIAL,
    )
    control_db.commit()

    after = revenue(client, token)
    assert after["mrr_cents"] == before["mrr_cents"]
    assert after["trial_tenants"] == before["trial_tenants"] + 1


def test_grace_counts_as_revenue_but_is_flagged_at_risk(client, control_db):
    """Past due is a collections problem, not a churn event — leaving it out
    of MRR would make the book look smaller than it is contracted to be.
    """
    token = admin_token(client, control_db, "rev-grace@test.com")
    before = revenue(client, token)

    plan = make_plan(control_db, monthly=40_000)
    subscribe(
        control_db,
        create_tenant(control_db, company_code=unique_code("FARM-GRACE")),
        plan,
        status=SubscriptionStatus.GRACE,
    )
    control_db.commit()

    after = revenue(client, token)
    assert after["mrr_cents"] == before["mrr_cents"] + 40_000
    assert after["at_risk_mrr_cents"] == before["at_risk_mrr_cents"] + 40_000
    assert after["at_risk_tenants"] == before["at_risk_tenants"] + 1


def test_terminated_subscriptions_leave_revenue(client, control_db):
    token = admin_token(client, control_db, "rev-lost@test.com")
    before = revenue(client, token)

    plan = make_plan(control_db, monthly=50_000)
    subscribe(
        control_db,
        create_tenant(control_db, company_code=unique_code("FARM-LOST")),
        plan,
        status=SubscriptionStatus.TERMINATED,
    )
    control_db.commit()

    after = revenue(client, token)
    assert after["mrr_cents"] == before["mrr_cents"]
    assert after["lost_tenants"] == before["lost_tenants"] + 1


def test_renewals_due_carry_the_revenue_they_put_at_stake(client, control_db):
    token = admin_token(client, control_db, "rev-renew@test.com")
    before = revenue(client, token)

    plan = make_plan(control_db, monthly=60_000)
    subscribe(
        control_db,
        create_tenant(control_db, company_code=unique_code("FARM-RENEW")),
        plan,
        renews_in_days=10,
    )
    control_db.commit()

    after = revenue(client, token)
    assert after["renewals_due_30d"] == before["renewals_due_30d"] + 1
    assert after["renewals_due_30d_mrr_cents"] == before["renewals_due_30d_mrr_cents"] + 60_000


def test_tenants_with_no_subscription_are_counted(client, control_db):
    token = admin_token(client, control_db, "rev-nosub@test.com")
    before = revenue(client, token)
    create_tenant(control_db, company_code=unique_code("FARM-NOSUB"))
    control_db.commit()

    after = revenue(client, token)
    assert after["tenants_without_subscription"] == before["tenants_without_subscription"] + 1


def test_invoicing_reports_itself_as_not_configured_when_empty(client, control_db):
    token = admin_token(client, control_db, "rev-inv@test.com")
    invoicing = revenue(client, token)["invoicing"]
    assert invoicing["invoices_recorded"] == 0
    # The flag is what stops a reader mistaking "nothing wired up" for
    # "nothing owed".
    assert invoicing["billing_configured"] is False
    assert invoicing["collected_cents"] == 0


def test_support_admins_cannot_read_revenue(client, control_db):
    grant_platform_role(control_db, "support-rev@test.com", PlatformRole.PLATFORM_SUPPORT_ADMIN)
    control_db.commit()
    token = dev_login(client, "support-rev@test.com")
    assert client.get("/platform/v1/metrics/revenue", headers=auth_headers(token)).status_code == 403


def test_plan_pricing_round_trips_through_the_api(client, control_db):
    token = admin_token(client, control_db, "rev-planapi@test.com")

    created = client.post(
        "/platform/v1/plans",
        json={
            "code": unique_code("PRICED"),
            "name": "Priced Plan",
            "monthly_price_cents": 19_900,
            "annual_price_cents": 199_000,
        },
        headers=auth_headers(token),
    )
    assert created.status_code == 201, created.text
    assert created.json()["monthly_price_cents"] == 19_900

    plan_id = created.json()["id"]
    updated = client.patch(
        f"/platform/v1/plans/{plan_id}",
        json={"monthly_price_cents": 24_900},
        headers=auth_headers(token),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["monthly_price_cents"] == 24_900
    assert updated.json()["annual_price_cents"] == 199_000


def test_the_whole_commercial_loop_works_through_the_api(client, control_db):
    """Price a plan, sign a customer, put them on it — and see MRR move.

    Every other test here builds Subscription rows directly, which is how a
    real gap survived: the upsert endpoint had no status field, so a
    subscription created through the product stayed an onboarding trial
    forever and MRR could never leave zero. This test only touches HTTP.
    """
    token = admin_token(client, control_db, "rev-loop@test.com")
    headers = auth_headers(token)
    before = revenue(client, token)["mrr_cents"]

    plan = client.post(
        "/platform/v1/plans",
        json={"code": unique_code("LOOP"), "name": "Loop Plan", "monthly_price_cents": 30_000},
        headers=headers,
    ).json()

    tenant = client.post(
        "/platform/v1/tenants",
        json={
            "company_code": unique_code("FARM-LOOP"),
            "legal_name": "Loop Farm Ltd",
            "display_name": "Loop Farm",
            "country": "Lebanon",
        },
        headers=headers,
    ).json()

    # As the wizard leaves it: on a plan, but not yet paying.
    trial = client.patch(
        f"/platform/v1/tenants/{tenant['id']}/subscription",
        json={
            "plan_id": plan["id"],
            "billing_cycle": "MONTHLY",
            "starts_at": datetime.now(timezone.utc).isoformat(),
        },
        headers=headers,
    )
    assert trial.status_code == 200, trial.text
    assert trial.json()["status"] == "ONBOARDING_TRIAL"
    assert revenue(client, token)["mrr_cents"] == before, "a trial is pipeline, not revenue"

    # Trial converts.
    converted = client.patch(
        f"/platform/v1/tenants/{tenant['id']}/subscription",
        json={
            "plan_id": plan["id"],
            "billing_cycle": "MONTHLY",
            "starts_at": datetime.now(timezone.utc).isoformat(),
            "renews_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "status": "ACTIVE",
        },
        headers=headers,
    )
    assert converted.status_code == 200, converted.text
    assert converted.json()["status"] == "ACTIVE"

    after = revenue(client, token)
    assert after["mrr_cents"] == before + 30_000
    assert after["renewals_due_30d"] >= 1
