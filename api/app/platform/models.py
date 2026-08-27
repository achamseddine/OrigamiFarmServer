from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import ControlBase
from app.common.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class FeatureFlag(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    """Release/rollout control — distinct from commercial entitlements.
    See LICENSE_ENTITLEMENTS.md for how the two are evaluated together.
    """

    __tablename__ = "feature_flag"

    flag_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(default="")
    enabled_globally: Mapped[bool] = mapped_column(default=False)
    enabled_tenant_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)


class UsageMeter(UUIDPrimaryKeyMixin, ControlBase):
    __tablename__ = "usage_meter"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "metric_code", "period_start", name="uq_usage_meter_period"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    metric_code: Mapped[str] = mapped_column(String(64))
    period_start: Mapped[datetime] = mapped_column()
    period_end: Mapped[datetime] = mapped_column()
    value: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
