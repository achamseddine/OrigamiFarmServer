"""Farm-data-plane (RLS-protected, tenant-scoped) table for work
responsibilities (OrigamiFarmOS backend/app/domain/models.py
`WorkResponsibility`; Settings > Employees > Responsibilities).

The employee is a control-plane user (`user_identity.id`, the same id the
employee endpoints address them by); the scope is a tenant row (an animal,
a group, a field) or a species code. Neither side is a foreign key: the two
live in different databases, and a species is catalog, not a row.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin


class WorkResponsibility(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """What an employee looks after, and which duties that means: the
    animals of a group, one animal, a species, or a field, with duties such
    as milking, feeding and observations. It routes the work — their day
    shows these animals and what is still to record — and grants the module
    access the duties need; it does not hide the rest of the farm from
    someone whose grants already show it."""

    __tablename__ = "work_responsibility"
    __table_args__ = (Index("ix_work_responsibility_user", "tenant_id", "user_id"),)

    # The employee: a control-plane user id (TenantMembership.user_id).
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    # animal | group | species | field
    scope_type: Mapped[str] = mapped_column(String(20))
    # The animal / group / field id, or the species code.
    scope_id: Mapped[str] = mapped_column(String(64))
    # milking | feeding | observation | health_care | egg_collection | crop_care | harvest
    duties: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # active | ended — ended, never deleted, so "who looked after Bella in
    # March" stays answerable.
    status: Mapped[str] = mapped_column(String(20), default="active")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
