"""FarmOS stage 12: approval requests (employees enter, the manager approves)

Revision ID: c3d5e7f9a1b2
Revises: b2e1d3c5f7a9
Create Date: 2026-11-24 12:30:00.000000

New table
- approval_request (RLS): a numbered feed mix or a correction to a milk
  record or a feeding, made by an employee without approval on the module
  (OrigamiFarmOS reference migration d5f7b9c1e3a6). It is kept as a
  request — kind, the employee's version (payload), reason, who asked —
  and nothing in stock, mixes or records changes until a manager approves
  it; approving runs the same service a manager would use directly and
  records the result. Rejected and cancelled requests change nothing.
  The people are control-plane users, so neither column is a foreign key.

The record-consistency rules of the same reference range (nothing dated in
the future, milk only from an animal in milk, no records on an animal that
left, a mix drawing no lot received after its date) are rules in the
services, not schema. No data change. Downgrade drops the table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3d5e7f9a1b2'
down_revision: Union[str, None] = "b2e1d3c5f7a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RLS_TABLES = ["approval_request"]


def _enable_rls(table: str) -> None:
    # Mandatory tenant isolation for every new farm-data-plane table — see
    # TENANCY.md and the initial tenant migration, which created
    # app_current_tenant_id(). What an employee asked the manager to
    # approve is a farm's own.
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
    op.create_table('approval_request',
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('module_code', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
    sa.Column('subject_type', sa.String(length=30), nullable=True),
    sa.Column('subject_id', sa.UUID(), nullable=True),
    sa.Column('target_id', sa.UUID(), nullable=True),
    sa.Column('summary', sa.String(length=300), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('requested_by', sa.UUID(), nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_by', sa.UUID(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('result_type', sa.String(length=40), nullable=True),
    sa.Column('result_id', sa.UUID(), nullable=True),
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
    op.create_index('ix_approval_request_status', 'approval_request', ['tenant_id', 'status'], unique=False)
    op.create_index('ix_approval_request_target', 'approval_request', ['tenant_id', 'target_id'], unique=False)
    op.create_index(op.f('ix_approval_request_farm_id'), 'approval_request', ['farm_id'], unique=False)
    op.create_index(op.f('ix_approval_request_tenant_id'), 'approval_request', ['tenant_id'], unique=False)
    for table in _RLS_TABLES:
        _enable_rls(table)


def downgrade() -> None:
    op.drop_index(op.f('ix_approval_request_tenant_id'), table_name='approval_request')
    op.drop_index(op.f('ix_approval_request_farm_id'), table_name='approval_request')
    op.drop_index('ix_approval_request_target', table_name='approval_request')
    op.drop_index('ix_approval_request_status', table_name='approval_request')
    op.drop_table('approval_request')
