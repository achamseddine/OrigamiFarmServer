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

import uuid

import sqlalchemy as sa
from alembic import op

revision = "f9a3c17e64b2"
down_revision = "e5b2d84f1c06"
branch_labels = None
depends_on = None

PLAN_CODE = "ORIGAMI"
PLAN_NAME = "Origami"


def upgrade() -> None:
    conn = op.get_bind()

    plan_id = conn.execute(
        sa.text("SELECT id FROM plan WHERE code = :code"), {"code": PLAN_CODE}
    ).scalar()

    if plan_id is None:
        # Inherit the currency the deployment already uses rather than
        # assuming USD: a Lebanese operator pricing in USD and one pricing
        # in EUR both end up with what their existing rows say.
        currency = (
            conn.execute(sa.text("SELECT currency FROM plan ORDER BY created_at LIMIT 1")).scalar()
            or "USD"
        )
        plan_id = uuid.uuid4()
        conn.execute(
            sa.text(
                """
                INSERT INTO plan (id, code, name, status, currency,
                                  monthly_price_cents, annual_price_cents, limits,
                                  created_at, updated_at)
                VALUES (:id, :code, :name, 'ACTIVE', :currency,
                        NULL, NULL, '{}'::jsonb, now(), now())
                """
            ),
            {"id": plan_id, "code": PLAN_CODE, "name": PLAN_NAME, "currency": currency},
        )

    conn.execute(
        sa.text("UPDATE subscription SET plan_id = :id WHERE plan_id <> :id"), {"id": plan_id}
    )
    conn.execute(
        sa.text("UPDATE plan SET status = 'ARCHIVED' WHERE id <> :id AND status <> 'ARCHIVED'"),
        {"id": plan_id},
    )


def downgrade() -> None:
    # Subscriptions cannot be put back: which tier each customer was on
    # is not recoverable from the collapsed state, and inventing an
    # answer would be worse than leaving them where they are. Only the
    # "not on sale" marking is lifted.
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE plan SET status = 'ACTIVE' WHERE status = 'ARCHIVED' AND code <> :code"),
        {"code": PLAN_CODE},
    )
