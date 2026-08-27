"""Local reference to identities authenticated by the external IdP.

We do not store passwords or credentials here — that stays with the OIDC
provider (Keycloak or whatever replaces it). This table exists so control
plane rows (memberships, audit events, role assignments) can hold a stable
foreign key instead of a bare IdP subject string, keeping identity-provider
changes an internal migration rather than a schema-wide rewrite.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import ControlBase
from app.common.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class UserIdentity(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    __tablename__ = "user_identity"

    idp_subject: Mapped[str] = mapped_column(unique=True, index=True)
    email: Mapped[str] = mapped_column(unique=True, index=True)
    display_name: Mapped[str] = mapped_column()
