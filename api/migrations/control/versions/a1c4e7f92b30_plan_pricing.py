"""plan pricing: currency and per-cycle list price

Revenue could not be reported at all before this: a plan carried a name and
a set of limits but no price, and nothing in the codebase ever wrote a
billing record. Pricing the plan makes contracted recurring revenue
computable from subscriptions that already exist, without waiting on a
payment provider.

Prices are nullable rather than defaulted to zero on purpose — an unpriced
plan is a plan nobody has set a price for yet, which is a different fact
from a plan that is free, and the dashboards report the two differently.

Revision ID: a1c4e7f92b30
Revises: 9cff7b1c5dc1
Create Date: 2026-09-11 08:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c4e7f92b30"
down_revision: Union[str, None] = "9cff7b1c5dc1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("plan", sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"))
    op.add_column("plan", sa.Column("monthly_price_cents", sa.Integer(), nullable=True))
    op.add_column("plan", sa.Column("annual_price_cents", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("plan", "annual_price_cents")
    op.drop_column("plan", "monthly_price_cents")
    op.drop_column("plan", "currency")
