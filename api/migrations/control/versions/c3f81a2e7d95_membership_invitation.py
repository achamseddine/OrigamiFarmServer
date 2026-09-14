"""membership_invitation: let an invited tenant user actually get in

Inviting a tenant owner created a user_identity with no password and sent
nothing — no email, no code, no link — so the person a farm was handed to
could not sign in anywhere, and nothing in the product could tell you that.
This table is the missing half: a hashed, single-use, expiring token the
invited person redeems to set their own password.

Hashed rather than stored, like device_activation: the plaintext is shown
to the inviter once and never kept, so the row is useless to anyone who
reads the database.

Revision ID: c3f81a2e7d95
Revises: b7d21f508c44
Create Date: 2026-09-14 10:05:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f81a2e7d95"
down_revision: Union[str, None] = "b7d21f508c44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "membership_invitation",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by", sa.UUID(), nullable=True),
        sa.Column("delivery", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["membership_id"], ["tenant_membership.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by"], ["user_identity.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_membership_invitation_tenant_id"),
        "membership_invitation",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_membership_invitation_membership_id"),
        "membership_invitation",
        ["membership_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_membership_invitation_token_hash"),
        "membership_invitation",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_membership_invitation_token_hash"), table_name="membership_invitation")
    op.drop_index(
        op.f("ix_membership_invitation_membership_id"), table_name="membership_invitation"
    )
    op.drop_index(op.f("ix_membership_invitation_tenant_id"), table_name="membership_invitation")
    op.drop_table("membership_invitation")
