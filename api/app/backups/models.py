from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import ControlBase
from app.common.enums import BackupJobType, JobStatus
from app.common.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.common.types import str_enum


class BackupJob(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    """Infrastructure DB backups (tenant_id is null) and tenant-scoped
    logical snapshots (tenant_id set) both live here so the admin web
    'Data & Backups' tab has one place to read status from.
    """

    __tablename__ = "backup_job"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_type: Mapped[BackupJobType] = mapped_column(str_enum(BackupJobType))
    status: Mapped[JobStatus] = mapped_column(str_enum(JobStatus), default=JobStatus.PENDING)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(nullable=True)
    storage_key: Mapped[str | None] = mapped_column(nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class TenantExport(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    __tablename__ = "tenant_export"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user_identity.id")
    )
    status: Mapped[JobStatus] = mapped_column(str_enum(JobStatus), default=JobStatus.PENDING)
    storage_key: Mapped[str | None] = mapped_column(nullable=True)
    download_url_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    reason: Mapped[str | None] = mapped_column(nullable=True)
