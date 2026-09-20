"""user_identity.password_changed_at

Records when an account last set its own password, so the console can tell
the difference between "this person chose their password" and "this person
is still using the one that whoever created the account typed for them".

Nullable with no backfill on purpose: an existing account genuinely has no
evidence that its holder ever changed it, and guessing a date here would
turn an unknown into a false reassurance on the setup checklist.

Revision ID: b7d21f508c44
Revises: a1c4e7f92b30
Create Date: 2026-09-11 12:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d21f508c44"
down_revision: Union[str, None] = "a1c4e7f92b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_identity",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_identity", "password_changed_at")
