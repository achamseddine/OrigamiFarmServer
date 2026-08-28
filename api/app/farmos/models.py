"""Control-plane models specific to the FarmOS tablet contract."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import ControlBase


class IdempotencyRecord(ControlBase):
    """The first successful (2xx) response for a given (Idempotency-Key,
    user) pair. A replay with the same key returns this row's stored
    status/body verbatim without touching any other table — see
    app/farmos/idempotency.py, the only reader/writer of this table.
    """

    __tablename__ = "farmos_idempotency_record"
    __table_args__ = (UniqueConstraint("key", "user_id", name="uq_idempotency_key_user"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user_identity.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[str] = mapped_column()
    path: Mapped[str] = mapped_column()
    status_code: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
