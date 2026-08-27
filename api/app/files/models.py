from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import ControlBase
from app.common.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class FileObject(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    """Metadata only — bytes live in object storage under
    tenants/{tenant_id}/... (see docs/ARCHITECTURE.md, Object Storage
    Structure). Never a public bucket; access is always a short-lived
    presigned URL issued after authorization.
    """

    __tablename__ = "file_object"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    farm_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(unique=True)
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user_identity.id"), nullable=True
    )
