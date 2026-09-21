"""Collapse the plan tiers into one subscription

Origami used to be sold as tiers — Starter, Dairy Only, Growth — each a
different bundle of licences at a different price. It is now one
subscription at one monthly price covering the whole product.

This moves the data to match:

  * creates the ORIGAMI plan if it is not already there;
  * points every existing subscription at it, so no customer is left on a
    plan the console will no longer show;
  * archives the old tier rows rather than deleting them. A subscription's
    history, the audit log and any revenue figure already reported all
    refer to them by id, and deleting the row would turn that history
    into a dangling reference. ARCHIVED simply means "not on sale".

Prices are not carried across. The tiers disagreed with each other, so
any one of them would be an invented figure for the new plan, and an
invented price flows straight into the revenue dashboard where it is
indistinguishable from a real one. The console says plainly that an
unpriced plan cannot be counted as revenue, which is the truthful state
until somebody types a number.

plan_module rows are left alone. Nothing reads them any more — the
tablet no longer checks a per-module entitlement — and they are a record
of what the tiers used to contain.

Revision ID: f9a3c17e64b2
Revises: e5b2d84f1c06
"""

from __future__ import annotations

from alembic import op

revision = "f9a3c17e64b2"
down_revision = "e5b2d84f1c06"
branch_labels = None
depends_on = None

PLAN_CODE = "ORIGAMI"
PLAN_NAME = "Origami"


def upgrade() -> None:
    # Self-contained SQL rather than read-then-write from Python: in
    # Alembic's offline mode (`alembic upgrade --sql`, which
    # infrastructure/sql/generate.sh depends on) there is no connection to
    # read a result from, and op.get_bind() is None. Every decision the
    # Python version made is made by the database here instead.

    # Inherit the currency the deployment already uses rather than
    # assuming USD: a Lebanese operator pricing in USD and one pricing
    # in EUR both end up with what their existing rows say.
    op.execute(
        f"""
        INSERT INTO plan (id, code, name, status, currency,
                          monthly_price_cents, annual_price_cents, limits,
                          created_at, updated_at)
        SELECT gen_random_uuid(), '{PLAN_CODE}', '{PLAN_NAME}', 'ACTIVE',
               COALESCE((SELECT currency FROM plan ORDER BY created_at LIMIT 1), 'USD'),
               NULL, NULL, '{{}}'::jsonb, now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM plan WHERE code = '{PLAN_CODE}')
        """
    )
    op.execute(
        f"""
        UPDATE subscription
        SET plan_id = (SELECT id FROM plan WHERE code = '{PLAN_CODE}')
        WHERE plan_id <> (SELECT id FROM plan WHERE code = '{PLAN_CODE}')
        """
    )
    op.execute(
        f"UPDATE plan SET status = 'ARCHIVED' WHERE code <> '{PLAN_CODE}' AND status <> 'ARCHIVED'"
    )


def downgrade() -> None:
    # Subscriptions cannot be put back: which tier each customer was on
    # is not recoverable from the collapsed state, and inventing an
    # answer would be worse than leaving them where they are. Only the
    # "not on sale" marking is lifted.
    op.execute(
        f"UPDATE plan SET status = 'ACTIVE' WHERE status = 'ARCHIVED' AND code <> '{PLAN_CODE}'"
    )
