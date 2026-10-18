"""farmos stage 6: animal identifiers, animal groups, care record (conditions, weights)

Revision ID: b4e7d2a9c1f3
Revises: cbd6ac9cefaf
Create Date: 2026-10-18 09:12:41.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b4e7d2a9c1f3'
down_revision: Union[str, None] = 'cbd6ac9cefaf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RLS_TABLES = [
    "animal_group",
    "animal_health_condition",
    "animal_identifier",
    "animal_weight_record",
]


def _enable_rls(table: str) -> None:
    # Mandatory tenant isolation for every new farm-data-plane table — see
    # TENANCY.md and the initial tenant migration, which created
    # app_current_tenant_id().
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


def _synced_columns() -> list[sa.Column]:
    return [
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('farm_id', sa.UUID(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('origin_device_id', sa.UUID(), nullable=True),
        sa.Column('last_modified_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def upgrade() -> None:
    # --- animal: the generic model's inputs. `tag` becomes nullable because
    # it now mirrors the primary identifier, and an animal identified only
    # by a microchip has none.
    op.alter_column('animal', 'tag', existing_type=sa.String(length=64), nullable=True)
    op.add_column('animal', sa.Column('breed_id', sa.String(length=64), nullable=True))
    op.add_column(
        'animal',
        sa.Column('birth_date_estimated', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column('animal', sa.Column('life_stage', sa.String(length=30), nullable=True))
    op.add_column('animal', sa.Column('management_profile', sa.String(length=30), nullable=True))

    op.create_table(
        'animal_identifier',
        sa.Column('animal_id', sa.UUID(), nullable=False),
        sa.Column('identifier_type', sa.String(length=30), nullable=False),
        sa.Column('identifier_value', sa.String(length=120), nullable=False),
        sa.Column('issuing_authority', sa.String(length=120), nullable=True),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=True),
        sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_primary', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('notes', sa.String(), nullable=True),
        *_synced_columns(),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_animal_identifier_animal_id'), 'animal_identifier', ['animal_id'], unique=False)
    op.create_index(op.f('ix_animal_identifier_farm_id'), 'animal_identifier', ['farm_id'], unique=False)
    op.create_index(op.f('ix_animal_identifier_tenant_id'), 'animal_identifier', ['tenant_id'], unique=False)
    op.create_index(
        'ix_animal_identifier_lookup', 'animal_identifier', ['identifier_type', 'identifier_value'], unique=False
    )

    op.create_table(
        'animal_group',
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('species', sa.String(length=30), nullable=False),
        sa.Column('group_type', sa.String(length=20), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('sex_composition', sa.String(length=20), nullable=True),
        sa.Column('life_stage', sa.String(length=30), nullable=True),
        sa.Column('management_profile', sa.String(length=30), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('location_label', sa.String(length=200), nullable=True),
        *_synced_columns(),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_animal_group_farm_id'), 'animal_group', ['farm_id'], unique=False)
    op.create_index(op.f('ix_animal_group_tenant_id'), 'animal_group', ['tenant_id'], unique=False)

    op.create_table(
        'animal_health_condition',
        sa.Column('animal_id', sa.UUID(), nullable=False),
        sa.Column('condition_type', sa.String(length=40), nullable=False),
        sa.Column('code', sa.String(length=120), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('severity', sa.String(length=30), nullable=True),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('recorded_by', sa.UUID(), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('notes', sa.String(), nullable=True),
        *_synced_columns(),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_animal_health_condition_animal_id'), 'animal_health_condition', ['animal_id'], unique=False
    )
    op.create_index(op.f('ix_animal_health_condition_farm_id'), 'animal_health_condition', ['farm_id'], unique=False)
    op.create_index(
        op.f('ix_animal_health_condition_tenant_id'), 'animal_health_condition', ['tenant_id'], unique=False
    )
    op.create_index(
        'ix_animal_health_condition_animal_active', 'animal_health_condition', ['animal_id', 'active'], unique=False
    )

    op.create_table(
        'animal_weight_record',
        sa.Column('animal_id', sa.UUID(), nullable=False),
        sa.Column('weight_kg', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('measured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('method', sa.String(length=30), nullable=False),
        sa.Column('recorded_by', sa.UUID(), nullable=True),
        sa.Column('source_type', sa.String(length=40), nullable=True),
        sa.Column('source_id', sa.String(length=64), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        *_synced_columns(),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_animal_weight_record_animal_id'), 'animal_weight_record', ['animal_id'], unique=False)
    op.create_index(op.f('ix_animal_weight_record_farm_id'), 'animal_weight_record', ['farm_id'], unique=False)
    op.create_index(op.f('ix_animal_weight_record_tenant_id'), 'animal_weight_record', ['tenant_id'], unique=False)
    op.create_index(
        'ix_animal_weight_record_animal_measured',
        'animal_weight_record',
        ['animal_id', 'measured_at'],
        unique=False,
    )

    for _table in _RLS_TABLES:
        _enable_rls(_table)

    # No data backfill here on purpose: under FORCE ROW LEVEL SECURITY the
    # migration role sees no tenant's rows, so an animal's legacy `tag` is
    # materialised as its primary identifier lazily, inside a tenant-scoped
    # request — see app/farmos/identifier_service.py:ensure_legacy_identifier.


def downgrade() -> None:
    op.drop_index('ix_animal_weight_record_animal_measured', table_name='animal_weight_record')
    op.drop_index(op.f('ix_animal_weight_record_tenant_id'), table_name='animal_weight_record')
    op.drop_index(op.f('ix_animal_weight_record_farm_id'), table_name='animal_weight_record')
    op.drop_index(op.f('ix_animal_weight_record_animal_id'), table_name='animal_weight_record')
    op.drop_table('animal_weight_record')
    op.drop_index('ix_animal_health_condition_animal_active', table_name='animal_health_condition')
    op.drop_index(op.f('ix_animal_health_condition_tenant_id'), table_name='animal_health_condition')
    op.drop_index(op.f('ix_animal_health_condition_farm_id'), table_name='animal_health_condition')
    op.drop_index(op.f('ix_animal_health_condition_animal_id'), table_name='animal_health_condition')
    op.drop_table('animal_health_condition')
    op.drop_index(op.f('ix_animal_group_tenant_id'), table_name='animal_group')
    op.drop_index(op.f('ix_animal_group_farm_id'), table_name='animal_group')
    op.drop_table('animal_group')
    op.drop_index('ix_animal_identifier_lookup', table_name='animal_identifier')
    op.drop_index(op.f('ix_animal_identifier_tenant_id'), table_name='animal_identifier')
    op.drop_index(op.f('ix_animal_identifier_farm_id'), table_name='animal_identifier')
    op.drop_index(op.f('ix_animal_identifier_animal_id'), table_name='animal_identifier')
    op.drop_table('animal_identifier')
    op.drop_column('animal', 'management_profile')
    op.drop_column('animal', 'life_stage')
    op.drop_column('animal', 'birth_date_estimated')
    op.drop_column('animal', 'breed_id')
    # Restoring NOT NULL needs every tag filled in first; under FORCE RLS
    # this UPDATE only reaches rows of the tenant the session is set to, so
    # run the downgrade with app.tenant_id set per tenant (or as a role
    # that bypasses RLS) if any animal was registered without a tag.
    op.execute("UPDATE animal SET tag = '' WHERE tag IS NULL")
    op.alter_column('animal', 'tag', existing_type=sa.String(length=64), nullable=False)
