"""Request bodies for approval requests and record corrections
(OrigamiFarmOS backend/app/schemas/approvals.py)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.farmos.feed_schemas import FeedingComponentIn


class ApprovalRequestIn(BaseModel):
    """`POST /approval-requests` — an employee's mix or correction, sent to
    the manager instead of being applied.

    kind = feed_mix: [payload] is a `POST /feed-mixes` body.
    kind = milk_correction: [target_id] is the milk record, [payload] a
    MilkCorrectionIn without its reason.
    kind = feeding_correction: [target_id] is the feeding event, [payload] a
    FeedingCorrectionIn without its reason.
    """

    kind: str
    target_id: str | None = None
    payload: dict = Field(default_factory=dict)
    reason: str | None = None


class ApprovalDecisionIn(BaseModel):
    """Approve or reject. A rejection says why. Approving a mix that is now
    more than the stock on record is the manager's stock override."""

    note: str | None = None
    allow_negative: bool = False
    confirm_duplicate: bool = False


class MilkCorrectionIn(BaseModel):
    """`POST /production/milk/{id}/correct` — the fields to change (unset
    ones keep their value) and why."""

    animal_id: str | None = None
    session: str | None = None
    liters: float | None = Field(None, ge=0)
    destination: str | None = None
    recorded_at: datetime | None = None
    reason: str = ""


class FeedingCorrectionIn(BaseModel):
    """`POST /feeding-events/{id}/correct` — undo a feeding; with
    [components], record what was really fed in its place (same animal or
    group, same time and kind of feeding). The stock of the original
    returns to its lots."""

    components: list[FeedingComponentIn] = Field(default_factory=list)
    reason: str = ""
    allow_negative: bool = False
