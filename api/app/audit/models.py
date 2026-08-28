from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import ControlBase
from app.common.enums import ActorType
from app.common.mixins import UUIDPrimaryKeyMixin
from app.common.types import str_enum


class AuditEvent(UUIDPrimaryKeyMixin, ControlBase):
    """Append-only platform audit trail. Application code must never expose
    an UPDATE/DELETE path for this table — see app/audit/service.py, the
    only writer.
    """

    __tablename__ = "audit_event"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType))
    actor_role: Mapped[str | None] = mapped_column(nullable=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(nullable=True)
    before_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(nullable=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(nullable=True)
    session_id: Mapped[str | None] = mapped_column(nullable=True)
    # --- FarmOS tablet contract fields (GET /audit, docs/FARMOS_API.md) --
    # Populated only by FarmOS mutation routes; platform-side callers leave
    # these null and keep using before_summary/after_summary/reason above.
    module_code: Mapped[str | None] = mapped_column(nullable=True)
    summary: Mapped[str | None] = mapped_column(nullable=True)
    changes_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    device: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
