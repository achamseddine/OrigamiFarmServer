"""give every tablet module a licence, without cutting anyone off

Seventeen of the twenty modules the tablet app checks carried no
license_code, so GET /modules/catalog reported them licensed_active for
every farm whatever its plan, and the platform's own module codes gated
nothing a farm worker could see. Selling a plan therefore changed nothing
in the app. This sets each module's licence (see
app/plans/licensing_map.py).

The second half matters more than the first. Gating something that was
previously free is exactly the change that takes a working screen away
from a farm mid-season, so this grants every tenant that exists today an
active entitlement for every licence that was free until now. Nobody loses
anything on deploy; the gate only begins to bite for tenants created
afterwards, whose plan decides what they get.

The two paid add-ons keep their existing lowercase codes — the mobile app
calls POST /api/v1/modules/mouneh/activate by name — so tenants that had
them keep them, and tenants that did not are unaffected here.

Revision ID: d4a9c61b83e7
Revises: c3f81a2e7d95
Create Date: 2026-09-14 14:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a9c61b83e7"
down_revision: Union[str, None] = "c3f81a2e7d95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Inlined rather than imported from app.plans.licensing_map: a migration
# has to keep doing what it did on the day it ran, and an import would let
# a later edit to that map silently change history.
MODULE_LICENCES = {
    "morning_operations": "CORE",
    "tasks": "CORE",
    "employees": "CORE",
    "reports": "CORE",
    "settings": "CORE",
    "animals": "ANIMALS",
    "animal_health": "ANIMAL_HEALTH",
    "feed_nutrition": "FEED",
    "milk_production": "MILK",
    "egg_production": "EGGS",
    "agriculture": "AGRICULTURE",
    "produce_harvest": "PRODUCE",
    "inventory": "INVENTORY",
    "sales": "SALES",
    "expenses": "SALES",
    "finance": "SALES",
    "ai_intelligence": "AI_INTELLIGENCE",
}

# Everything above was free for every farm before this migration, so every
# existing tenant is granted all of it.
NEWLY_GATED = sorted(set(MODULE_LICENCES.values()))


def upgrade() -> None:
    connection = op.get_bind()

    for module_code, licence in MODULE_LICENCES.items():
        connection.execute(
            sa.text(
                "UPDATE module_catalog SET license_code = :licence "
                "WHERE module_code = :module_code AND license_code IS NULL"
            ),
            {"licence": licence, "module_code": module_code},
        )

    # The licence codes have to exist as catalog rows in their own right —
    # a plan includes them, and plan_module has a foreign key to
    # module_catalog.
    for licence in NEWLY_GATED:
        connection.execute(
            sa.text(
                "INSERT INTO module_catalog (module_code, name_en, name_ar, description, "
                "version, minimum_app_version, dependencies, default_features, "
                "commercial_status, trial_allowed, active, \"group\") "
                "VALUES (:code, :code, :code, '', '1.0.0', '0.0.0', '[]'::jsonb, "
                "'{}'::jsonb, 'AVAILABLE', true, true, '') "
                "ON CONFLICT (module_code) DO NOTHING"
            ),
            {"code": licence},
        )

    # No existing farm loses a screen. ON CONFLICT covers a tenant that
    # already holds one of these through a hand-made entitlement.
    for licence in NEWLY_GATED:
        connection.execute(
            sa.text(
                "INSERT INTO tenant_entitlement "
                "(id, tenant_id, module_code, status, source, effective_from, "
                " configuration, created_at, updated_at) "
                "SELECT gen_random_uuid(), t.id, CAST(:code AS varchar), 'ACTIVE', 'PLAN', now(), "
                "       '{}'::jsonb, now(), now() "
                "FROM tenant t "
                "WHERE NOT EXISTS ("
                "    SELECT 1 FROM tenant_entitlement e "
                "    WHERE e.tenant_id = t.id AND e.module_code = CAST(:code AS varchar)"
                ")"
            ),
            {"code": licence},
        )


def downgrade() -> None:
    connection = op.get_bind()
    for module_code, licence in MODULE_LICENCES.items():
        connection.execute(
            sa.text(
                "UPDATE module_catalog SET license_code = NULL "
                "WHERE module_code = :module_code AND license_code = :licence"
            ),
            {"licence": licence, "module_code": module_code},
        )
    # The granted entitlements are deliberately left in place: they are
    # indistinguishable from ones an admin made, and removing somebody's
    # access is not something a downgrade should do on its own.
