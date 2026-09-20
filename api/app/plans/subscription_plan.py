"""The one plan Origami sells.

The product used to be sold as tiers — Starter, Dairy Only, Growth — each
a different bundle of licences at a different price, and a great deal of
machinery existed to express that: a plan picker, per-plan module
contents, per-module entitlement checks on the tablet, and a licensing
dashboard answering "who has bought what".

Origami is now one subscription at one monthly price, covering the whole
product. Every customer gets every module. That makes all of the above a
question nobody asks any more, and the honest thing to do with a question
nobody asks is stop asking it: the tablet no longer checks a per-module
entitlement (app/farmos/routes_employees.py), and the console no longer
offers a choice of plan.

The Plan row survives because the commercial side still needs it: a price
to bill, a currency, and something for a subscription to point at so
revenue, renewals and the Business dashboard keep working. It is a
constant, not a catalogue.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.plans.models import Plan

# Stable and never generated: migrations, seeds and the console all have
# to name the same row, and a random code would make that a lookup by
# display name.
PLAN_CODE = "ORIGAMI"
PLAN_NAME = "Origami"

# Deliberately unpriced at birth. An invented figure would flow straight
# into the revenue dashboard and be indistinguishable from a real one;
# the console already says plainly that an unpriced plan cannot be
# counted, which is the truthful state until somebody types a number.
PLAN_PRICE_CENTS: int | None = None


def get_or_create_plan(db: Session, *, currency: str = "USD") -> Plan:
    """The subscription every customer is on.

    Created on first use rather than assumed to exist, so a fresh
    database, a restored backup and a test all reach the same state
    without a seeding step somebody can forget.
    """
    plan = db.execute(select(Plan).where(Plan.code == PLAN_CODE)).scalar_one_or_none()
    if plan is not None:
        return plan

    plan = Plan(
        code=PLAN_CODE,
        name=PLAN_NAME,
        status="ACTIVE",
        currency=currency,
        monthly_price_cents=PLAN_PRICE_CENTS,
        annual_price_cents=None,
        limits={},
    )
    db.add(plan)
    db.flush()
    return plan
