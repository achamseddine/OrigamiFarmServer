"""Give tenant owners and farm managers their matching job role

Two vocabularies describe one person: `tenant_role` (the platform's word
— TENANT_OWNER, FARM_MANAGER, EMPLOYEE) and `role` (the tablet contract's
word — "owner", "manager", "worker"). Nothing connected them, and only
the second one decides anything: full_access is computed from `role`
(app/farmos/deps.py:is_full_access_role).

So every membership created through the console took the column default,
"worker". A customer who had just bought the product signed in to their
own farm with a worker's permissions — no modules open, and no way to add
the staff they were there to add — while the console's Access tab showed
them as "tenant owner" on the very same row.

This repairs the rows already created. Only where `role` is still the
untouched default: a role deliberately set to something else (a
veterinarian who also happens to be the account owner, say) is somebody's
decision and is left alone.

Revision ID: e5b2d84f1c06
Revises: d4a9c61b83e7
"""

from __future__ import annotations

from alembic import op

revision = "e5b2d84f1c06"
down_revision = "d4a9c61b83e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE tenant_membership
           SET role = 'owner'
         WHERE tenant_role = 'TENANT_OWNER'
           AND role = 'worker'
        """
    )
    op.execute(
        """
        UPDATE tenant_membership
           SET role = 'manager'
         WHERE tenant_role = 'FARM_MANAGER'
           AND role = 'worker'
        """
    )


def downgrade() -> None:
    # Back to the default these rows would have had. Downgrading returns
    # every owner to a worker's permissions, which is the bug this fixes —
    # it is here for completeness, not because anyone should want it.
    op.execute(
        """
        UPDATE tenant_membership
           SET role = 'worker'
         WHERE tenant_role IN ('TENANT_OWNER', 'FARM_MANAGER')
           AND role IN ('owner', 'manager')
        """
    )
