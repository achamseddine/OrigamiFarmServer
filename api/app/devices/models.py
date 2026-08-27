from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import ControlBase
from app.common.enums import DeviceActivationStatus, DevicePlatform, DeviceStatus
from app.common.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.common.types import str_enum


class Device(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    __tablename__ = "device"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    farm_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("farm.id", ondelete="SET NULL"), nullable=True
    )
    installation_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str] = mapped_column()
    platform: Mapped[DevicePlatform] = mapped_column(
        str_enum(DevicePlatform), default=DevicePlatform.ANDROID
    )
    app_version: Mapped[str] = mapped_column(default="0.0.0")
    fingerprint_hash: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[DeviceStatus] = mapped_column(
        str_enum(DeviceStatus), default=DeviceStatus.PENDING
    )
    activated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user_identity.id"), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(nullable=True)


class DeviceActivation(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    """A one-time, hashed, expiring code that binds a future device to a
    tenant/farm. Never store the plaintext code — only its hash.
    """

    __tablename__ = "device_activation"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    farm_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("farm.id", ondelete="SET NULL"), nullable=True
    )
    code_hash: Mapped[str] = mapped_column(unique=True, index=True)
    status: Mapped[DeviceActivationStatus] = mapped_column(
        str_enum(DeviceActivationStatus), default=DeviceActivationStatus.PENDING
    )
    expires_at: Mapped[datetime] = mapped_column()
    used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    used_by_device_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("device.id"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user_identity.id"), nullable=True
    )


class LicenseLease(UUIDPrimaryKeyMixin, ControlBase):
    """Metadata for an issued signed offline entitlement lease. The signed
    lease itself is handed to the device; this row lets us audit/revoke.
    """

    __tablename__ = "license_lease"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("device.id", ondelete="CASCADE"), index=True
    )
    issued_at: Mapped[datetime] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column()
    policy_version: Mapped[int] = mapped_column(default=1)
    modules: Mapped[list[str]] = mapped_column(JSONB, default=list)
    farm_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    permission_profile_hash: Mapped[str] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
