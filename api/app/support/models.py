from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import ControlBase
from app.common.enums import SupportCaseStatus
from app.common.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.common.types import str_enum


class SupportCase(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    __tablename__ = "support_case"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    opened_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user_identity.id")
    )
    subject: Mapped[str] = mapped_column()
    status: Mapped[SupportCaseStatus] = mapped_column(
        str_enum(SupportCaseStatus), default=SupportCaseStatus.OPEN
    )


class SupportSession(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    """A time-boxed, audited grant of elevated tenant visibility to a
    support user. Never a standing "god mode" — expires automatically.
    """

    __tablename__ = "support_session"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    support_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user_identity.id")
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("support_case.id"), nullable=True
    )
    reason: Mapped[str] = mapped_column()
    scope: Mapped[list[str]] = mapped_column(JSONB, default=list)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user_identity.id"), nullable=True
    )
    starts_at: Mapped[datetime] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column()
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)

    def is_active(self, *, now: datetime) -> bool:
        if self.ended_at is not None:
            return False
        return self.starts_at <= now < self.expires_at
