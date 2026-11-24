"""Work an employee did that waits for the farm manager (OrigamiFarmOS
backend/app/domain/approval_models.py; handbook/03-Behavioral-Model.md §5,
approval before effect).

An employee records the day's facts — milk, feedings — directly. Making
a numbered feed mix and correcting a record already on file are not
facts of the day but changes to the farm's stock and history, and the
farm decides them through its manager: the employee's version is kept
here as a *request*, and nothing in stock, mixes or records changes until
someone with approval on the module approves it. A rejected or cancelled
request changes nothing. Buying feed goes through purchase requisitions
(procurement_models.py), not through this table.

A request is never a transaction (CLAUDE.md: "Plans/recommendations are
never stored as actual transactions"): approving it runs the same service
the manager would have used directly, as the manager, recording who asked
and who approved. The people are control-plane users (`requested_by`,
`decided_by` are `user_identity.id`), so neither is a foreign key.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin

# feed_mix: a numbered mix as it was made (POST /feed-mixes body).
# milk_correction: new values for one milk record (target = the record).
# feeding_correction: undo one feeding, optionally with what was really fed
# (target = the feeding event).
APPROVAL_KINDS = ("feed_mix", "milk_correction", "feeding_correction")
APPROVAL_STATUSES = ("pending", "approved", "rejected", "cancelled")


class ApprovalRequest(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "approval_request"
    __table_args__ = (
        Index("ix_approval_request_status", "tenant_id", "status"),
        Index("ix_approval_request_target", "tenant_id", "target_id"),
    )

    kind: Mapped[str] = mapped_column(String(40))
    # The module whose approval decides it (feed_nutrition, milk_production).
    module_code: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # The animal / group the request is about, for the manager's list.
    subject_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # The record a correction changes.
    target_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    summary: Mapped[str] = mapped_column(String(300))
    # The employee's version, as the direct endpoint would take it.
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What approving it created or changed (feed_batch, milk_record, feeding_event).
    result_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
