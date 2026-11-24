"""FarmOS stage 13: milk sold from the tank, the farm's financials, reports

Revision ID: d4e6f8a0b2c3
Revises: b2e1d3c5f7a9
Create Date: 2026-11-24 12:30:00.000000

Ports the reference migrations e6a8c0d2f4b7 (milk sales and the tank,
v0.7.9) and f7b9d1e3a5c8 (the finance ledger and exchange rates, v0.8.0).

New tables (all RLS)
- milk_sale: milk sold from the tank — litres, price per litre, amount,
  buyer, paid / pending, cancelled with a reason; each sale also writes the
  farm's revenue record (`sale`, product_type "milk") and a cancelled sale
  is reversed by a second, negative one.
- milk_tank_adjustment: milk that left the tank outside a sale (spoiled,
  fed to calves, home use) and tank counts, signed litres. The tank itself
  is never stored: stored milk records − sales + adjustments.
- exchange_rate: LBP (or another currency) per USD from a date, set by the
  farm manager; a history, never edited.

New columns (all nullable or with a server default — existing rows keep
their meaning)
- sale: unit_price, buyer_name, reference, notes, status (recorded |
  cancelled | reversal, default recorded), cancel_reason, cancelled_at,
  reversal_of_id, paid_at, original_amount, original_currency,
  exchange_rate, activity, recorded_by.
- expense: description, supplier_label, payment_status (paid | pending,
  default paid), due_date, paid_at, reference, notes, status (recorded |
  cancelled, default recorded), cancel_reason, cancelled_at,
  original_amount, original_currency, exchange_rate, activity, recorded_by.

Data: the reference's f7b9d1e3a5c8 marked milk sales cancelled under
v0.7.9 (`sales` rows `cancelled`, their negative twins `reversal`) and
copied the payment date onto the revenue row. On the server both milk
tables are created by this very revision, so no such rows can exist and
there is nothing to backfill; every sale and expense already on file is a
`recorded`, `paid` row, which is what it meant before. Downgrade drops the
columns and the tables.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e6f8a0b2c3"
down_revision: Union[str, None] = "b2e1d3c5f7a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RLS_TABLES = ["milk_sale", "milk_tank_adjustment", "exchange_rate"]


def _enable_rls(table: str) -> None:
    # Mandatory tenant isolation for every new farm-data-plane table — see
    # TENANCY.md and the initial tenant migration, which created
    # app_current_tenant_id(). Who the farm sold milk to, what its tank
    # holds and the rate it books at are a farm's own.
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{table}
        ON {table}
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id())
        """
    )


def _synced(*extra: sa.Column) -> list:
    """The UUIDPrimaryKeyMixin + SyncedEntityMixin + TimestampMixin columns."""
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("farm_id", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("origin_device_id", sa.UUID(), nullable=True),
        sa.Column("last_modified_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        *extra,
        sa.PrimaryKeyConstraint("id"),
    ]


def _mixin_indexes(table: str) -> None:
    op.create_index(op.f(f"ix_{table}_farm_id"), table, ["farm_id"], unique=False)
    op.create_index(op.f(f"ix_{table}_tenant_id"), table, ["tenant_id"], unique=False)


_SALE_COLUMNS = [
    ("unit_price", sa.Float()),
    ("buyer_name", sa.String(200)),
    ("reference", sa.String(120)),
    ("notes", sa.Text()),
    ("cancel_reason", sa.Text()),
    ("cancelled_at", sa.DateTime(timezone=True)),
    ("reversal_of_id", sa.UUID()),
    ("paid_at", sa.DateTime(timezone=True)),
    ("original_amount", sa.Float()),
    ("original_currency", sa.String(10)),
    ("exchange_rate", sa.Float()),
    ("activity", sa.String(20)),
    ("recorded_by", sa.UUID()),
]

_EXPENSE_COLUMNS = [
    ("description", sa.String(300)),
    ("supplier_label", sa.String(200)),
    ("due_date", sa.DateTime(timezone=True)),
    ("paid_at", sa.DateTime(timezone=True)),
    ("reference", sa.String(120)),
    ("notes", sa.Text()),
    ("cancel_reason", sa.Text()),
    ("cancelled_at", sa.DateTime(timezone=True)),
    ("original_amount", sa.Float()),
    ("original_currency", sa.String(10)),
    ("exchange_rate", sa.Float()),
    ("activity", sa.String(20)),
    ("recorded_by", sa.UUID()),
]


def upgrade() -> None:
    op.create_table(
        "milk_sale",
        *_synced(
            sa.Column("sold_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("liters", sa.Float(), nullable=False),
            sa.Column("price_per_liter", sa.Float(), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("currency", sa.String(length=10), nullable=False),
            sa.Column("customer_id", sa.UUID(), nullable=True),
            sa.Column("buyer_name", sa.String(length=200), nullable=True),
            sa.Column("payment_status", sa.String(length=20), nullable=False),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reference", sa.String(length=120), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("cancel_reason", sa.Text(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sale_id", sa.UUID(), nullable=True),
            sa.Column("reversal_sale_id", sa.UUID(), nullable=True),
            sa.Column("recorded_by", sa.UUID(), nullable=True),
        ),
    )
    _mixin_indexes("milk_sale")
    op.create_index("ix_milk_sale_sold", "milk_sale", ["tenant_id", "sold_at"], unique=False)
    op.create_index(op.f("ix_milk_sale_sale_id"), "milk_sale", ["sale_id"], unique=False)

    op.create_table(
        "milk_tank_adjustment",
        *_synced(
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("liters", sa.Float(), nullable=False),
            sa.Column("reason", sa.String(length=20), nullable=False),
            sa.Column("counted_level", sa.Float(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("recorded_by", sa.UUID(), nullable=True),
        ),
    )
    _mixin_indexes("milk_tank_adjustment")
    op.create_index(
        "ix_milk_tank_adjustment_occurred", "milk_tank_adjustment", ["tenant_id", "occurred_at"], unique=False
    )

    op.create_table(
        "exchange_rate",
        *_synced(
            sa.Column("currency", sa.String(length=10), nullable=False),
            sa.Column("rate_per_usd", sa.Float(), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("set_by", sa.UUID(), nullable=True),
        ),
    )
    _mixin_indexes("exchange_rate")
    op.create_index(
        "ix_exchange_rate_currency_date",
        "exchange_rate",
        ["tenant_id", "currency", "effective_date"],
        unique=False,
    )
    for table in _RLS_TABLES:
        _enable_rls(table)

    op.add_column(
        "sale", sa.Column("status", sa.String(length=20), nullable=False, server_default="recorded")
    )
    for name, type_ in _SALE_COLUMNS:
        op.add_column("sale", sa.Column(name, type_, nullable=True))
    op.add_column(
        "expense", sa.Column("payment_status", sa.String(length=20), nullable=False, server_default="paid")
    )
    op.add_column(
        "expense", sa.Column("status", sa.String(length=20), nullable=False, server_default="recorded")
    )
    for name, type_ in _EXPENSE_COLUMNS:
        op.add_column("expense", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_EXPENSE_COLUMNS):
        op.drop_column("expense", name)
    op.drop_column("expense", "status")
    op.drop_column("expense", "payment_status")
    for name, _ in reversed(_SALE_COLUMNS):
        op.drop_column("sale", name)
    op.drop_column("sale", "status")
    op.drop_index("ix_exchange_rate_currency_date", table_name="exchange_rate")
    op.drop_index(op.f("ix_exchange_rate_tenant_id"), table_name="exchange_rate")
    op.drop_index(op.f("ix_exchange_rate_farm_id"), table_name="exchange_rate")
    op.drop_table("exchange_rate")
    op.drop_index("ix_milk_tank_adjustment_occurred", table_name="milk_tank_adjustment")
    op.drop_index(op.f("ix_milk_tank_adjustment_tenant_id"), table_name="milk_tank_adjustment")
    op.drop_index(op.f("ix_milk_tank_adjustment_farm_id"), table_name="milk_tank_adjustment")
    op.drop_table("milk_tank_adjustment")
    op.drop_index(op.f("ix_milk_sale_sale_id"), table_name="milk_sale")
    op.drop_index("ix_milk_sale_sold", table_name="milk_sale")
    op.drop_index(op.f("ix_milk_sale_tenant_id"), table_name="milk_sale")
    op.drop_index(op.f("ix_milk_sale_farm_id"), table_name="milk_sale")
    op.drop_table("milk_sale")
