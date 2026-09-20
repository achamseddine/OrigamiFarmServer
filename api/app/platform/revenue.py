"""Recurring revenue, computed from contracted subscriptions.

What this is and is not: MRR here is *contracted* revenue — what tenants
have agreed to pay at their plan's list price — not cash collected. Nothing
in this system issues invoices or talks to a payment provider yet, so
billed and collected figures come from the invoice table and are reported
separately, and will read as zero until something writes to it.

The one rule the whole module is built around: a subscription on a plan
with no price is never counted as zero revenue. It is counted as
*unpriced*, and reported as its own number, because "we charge this
customer nothing" and "nobody has told the system what this customer pays"
are different facts and only one of them is a pricing problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.billing.models import Invoice
from app.common.enums import BillingCycle, InvoiceStatus, SubscriptionStatus
from app.plans.models import Plan, Subscription
from app.tenants.models import Tenant

# Subscriptions that represent money the business has contracted for.
# GRACE is included and flagged: the customer is past due, not gone, and
# leaving it out would make a collections problem look like a churn event.
REVENUE_STATUSES = (SubscriptionStatus.ACTIVE, SubscriptionStatus.GRACE)

# Contracted, but not yet paying anything.
PIPELINE_STATUSES = (SubscriptionStatus.ONBOARDING_TRIAL,)

# Contracted revenue that has stopped.
LOST_STATUSES = (SubscriptionStatus.SUSPENDED, SubscriptionStatus.TERMINATED)


def monthly_cents(plan: Plan, cycle: BillingCycle) -> int | None:
    """Plan price normalised to one month, or None when it isn't priced.

    An annual plan is divided by twelve so the two cycles can be summed
    into one MRR; the annual figure is the contracted one, so this is a
    presentation of the same money rather than an estimate of it.
    """
    if cycle == BillingCycle.ANNUAL:
        return None if plan.annual_price_cents is None else round(plan.annual_price_cents / 12)
    return plan.monthly_price_cents


@dataclass
class PlanRevenue:
    plan_code: str
    plan_name: str
    currency: str
    monthly_price_cents: int | None
    annual_price_cents: int | None
    subscriptions: int = 0
    mrr_cents: int = 0
    unpriced_subscriptions: int = 0


@dataclass
class RevenueSnapshot:
    currency: str
    mrr_cents: int = 0
    arr_cents: int = 0
    paying_tenants: int = 0
    unpriced_subscriptions: int = 0
    at_risk_mrr_cents: int = 0
    at_risk_tenants: int = 0
    trial_tenants: int = 0
    lost_tenants: int = 0
    renewals_due_30d: int = 0
    renewals_due_30d_mrr_cents: int = 0
    tenants_without_subscription: int = 0
    by_plan: list[PlanRevenue] = field(default_factory=list)
    currencies_present: list[str] = field(default_factory=list)


def revenue_snapshot(db: Session) -> RevenueSnapshot:
    rows = db.execute(select(Subscription, Plan).join(Plan, Plan.id == Subscription.plan_id)).all()

    by_plan: dict[str, PlanRevenue] = {}
    snapshot = RevenueSnapshot(currency="USD")
    currencies: set[str] = set()
    now = datetime.now(timezone.utc)
    renewal_cutoff = now + timedelta(days=30)

    for subscription, plan in rows:
        entry = by_plan.setdefault(
            plan.code,
            PlanRevenue(
                plan_code=plan.code,
                plan_name=plan.name,
                currency=plan.currency,
                monthly_price_cents=plan.monthly_price_cents,
                annual_price_cents=plan.annual_price_cents,
            ),
        )
        entry.subscriptions += 1
        currencies.add(plan.currency)

        if subscription.status in PIPELINE_STATUSES:
            snapshot.trial_tenants += 1
            continue
        if subscription.status in LOST_STATUSES:
            snapshot.lost_tenants += 1
            continue
        if subscription.status not in REVENUE_STATUSES:
            continue

        amount = monthly_cents(plan, subscription.billing_cycle)
        if amount is None:
            snapshot.unpriced_subscriptions += 1
            entry.unpriced_subscriptions += 1
            continue

        snapshot.mrr_cents += amount
        snapshot.paying_tenants += 1
        entry.mrr_cents += amount

        if subscription.status == SubscriptionStatus.GRACE:
            snapshot.at_risk_mrr_cents += amount
            snapshot.at_risk_tenants += 1

        if subscription.renews_at is not None and subscription.renews_at <= renewal_cutoff:
            snapshot.renewals_due_30d += 1
            snapshot.renewals_due_30d_mrr_cents += amount

    snapshot.arr_cents = snapshot.mrr_cents * 12
    snapshot.by_plan = sorted(by_plan.values(), key=lambda entry: -entry.mrr_cents)
    snapshot.currencies_present = sorted(currencies)
    # Mixed currencies cannot be summed into one MRR. Report the dominant
    # one and let the console warn rather than silently adding them up.
    if currencies:
        snapshot.currency = snapshot.by_plan[0].currency if snapshot.by_plan else sorted(currencies)[0]

    tenants_total = db.execute(select(func.count()).select_from(Tenant)).scalar_one()
    subscribed = db.execute(select(func.count(func.distinct(Subscription.tenant_id)))).scalar_one()
    snapshot.tenants_without_subscription = tenants_total - subscribed

    return snapshot


def invoice_totals(db: Session, months: int = 12) -> dict:
    """Billed and collected, straight from the invoice ledger.

    Everything here reads zero until invoice generation exists — which is
    the honest answer, and the reason the console labels this section as
    not yet wired rather than as a result.
    """
    since = datetime.now(timezone.utc) - timedelta(days=30 * months)

    def total(*statuses: InvoiceStatus) -> int:
        return (
            db.execute(
                select(func.coalesce(func.sum(Invoice.amount_cents), 0)).where(
                    Invoice.status.in_(statuses), Invoice.period_start >= since
                )
            ).scalar_one()
            or 0
        )

    issued = total(InvoiceStatus.ISSUED, InvoiceStatus.PAID, InvoiceStatus.OVERDUE)
    collected = total(InvoiceStatus.PAID)
    outstanding = total(InvoiceStatus.ISSUED, InvoiceStatus.OVERDUE)
    overdue = total(InvoiceStatus.OVERDUE)
    count = db.execute(select(func.count()).select_from(Invoice)).scalar_one()

    return {
        "invoices_recorded": count,
        "billed_cents": issued,
        "collected_cents": collected,
        "outstanding_cents": outstanding,
        "overdue_cents": overdue,
        # Explicit rather than inferred from the zeroes, so a reader is never
        # left deciding whether this means "nothing owed" or "not wired up".
        "billing_configured": count > 0,
    }
