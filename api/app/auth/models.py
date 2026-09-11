"""Local reference to identities.

Platform/admin-web identities are authenticated by the external IdP and
store no credential here — that stays with OIDC (Keycloak or whatever
replaces it). password_hash exists for a second, unrelated identity path:
the FarmOS tablet app's own username/password login (see app/farmos/) —
field workers who never touch the admin console and have no OIDC account.
The two paths share this table only because both need a stable internal
user id to hang memberships/audit events off of; an OIDC identity simply
never has a password_hash.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import ControlBase
from app.common.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class UserIdentity(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    __tablename__ = "user_identity"

    idp_subject: Mapped[str] = mapped_column(unique=True, index=True)
    email: Mapped[str] = mapped_column(unique=True, index=True)
    display_name: Mapped[str] = mapped_column()
    password_hash: Mapped[str | None] = mapped_column(nullable=True)
    # Set only when the account holder changes their own password. Null
    # means the password in force was typed by somebody else — the admin who
    # created the account, or one who reset it — which is a different
    # security position, and the one the setup checklist asks about.
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
