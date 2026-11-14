"""FarmOS stage 11: work responsibilities and feed-lot quality at receipt

Revision ID: b2e1d3c5f7a9
Revises: 8de412824ec8
Create Date: 2026-11-14 18:10:00.000000

New table
- work_responsibility (RLS): what an employee looks after (an animal, a
  group, a species, a field) and the duties it means. The employee is the
  control-plane user id; the scope is a tenant row id or a species code.

Column additions
- feed_lot.quality_grade / moisture_pct / quality_notes: the quality seen
  at receipt (FEED-SCHEMA §26). Nullable: a lot received before this
  revision simply has no grade on record.

The reference migration (a8d0c2e4f6b1) also created the three standard
feeds for every existing farm. Here a farm gets them on request —
`POST /feed-products/standard`, idempotent, matching an existing feed by
code, name keyword or (concentrate) category — so no stock master is
invented behind a farm's back by a schema change.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2e1d3c5f7a9'
down_revision: Union[str, None] = "a1f0c2d4e6b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RLS_TABLES = ["work_responsibility"]


def _enable_rls(table: str) -> None:
    # Mandatory tenant isolation for every new farm-data-plane table — see
    # TENANCY.md and the initial tenant migration, which created
    # app_current_tenant_id(). Who looks after which animals is a farm's own.
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


def upgrade() -> None:
    op.create_table('work_responsibility',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('scope_type', sa.String(length=20), nullable=False),
    sa.Column('scope_id', sa.String(length=64), nullable=False),
    sa.Column('duties', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('farm_id', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('origin_device_id', sa.UUID(), nullable=True),
    sa.Column('last_modified_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_work_responsibility_user', 'work_responsibility', ['tenant_id', 'user_id'], unique=False)
    op.create_index(op.f('ix_work_responsibility_farm_id'), 'work_responsibility', ['farm_id'], unique=False)
    op.create_index(op.f('ix_work_responsibility_tenant_id'), 'work_responsibility', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_work_responsibility_user_id'), 'work_responsibility', ['user_id'], unique=False)
    for table in _RLS_TABLES:
        _enable_rls(table)

    op.add_column('feed_lot', sa.Column('quality_grade', sa.String(length=20), nullable=True))
    op.add_column('feed_lot', sa.Column('moisture_pct', sa.Float(), nullable=True))
    op.add_column('feed_lot', sa.Column('quality_notes', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('feed_lot', 'quality_notes')
    op.drop_column('feed_lot', 'moisture_pct')
    op.drop_column('feed_lot', 'quality_grade')
    op.drop_index(op.f('ix_work_responsibility_user_id'), table_name='work_responsibility')
    op.drop_index(op.f('ix_work_responsibility_tenant_id'), table_name='work_responsibility')
    op.drop_index(op.f('ix_work_responsibility_farm_id'), table_name='work_responsibility')
    op.drop_index('ix_work_responsibility_user', table_name='work_responsibility')
    op.drop_table('work_responsibility')
