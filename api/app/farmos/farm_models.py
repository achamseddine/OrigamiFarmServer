"""Farm-data-plane (RLS-protected, tenant-scoped) tables specific to the
FarmOS tablet contract. Field names match docs/FARMOS_API.md exactly.
Animal and Task live in app/tenant_api/models.py instead — they predate
this package and app/sync/ already depends on them directly.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Notification(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "notification"

    module_code: Mapped[str] = mapped_column(String(64))
    notification_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column()
    description: Mapped[str | None] = mapped_column(nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    entity_type: Mapped[str | None] = mapped_column(nullable=True)
    entity_id: Mapped[str | None] = mapped_column(nullable=True)
    source_type: Mapped[str | None] = mapped_column(nullable=True)
    source_id: Mapped[str | None] = mapped_column(nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
