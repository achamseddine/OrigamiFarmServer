from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.common.enums import SyncOperation


class SyncChange(BaseModel):
    event_id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    operation: SyncOperation
    base_version: int | None = None
    payload: dict = {}


class SyncPushRequest(BaseModel):
    base_cursor: str | None = None
    changes: list[SyncChange]


class SyncChangeResult(BaseModel):
    event_id: uuid.UUID
    entity_id: uuid.UUID
    status: str  # APPLIED | CONFLICT | REPLAYED | REJECTED
    current_version: int | None = None
    reason: str | None = None


class SyncPushResponse(BaseModel):
    results: list[SyncChangeResult]
    cursor: str


class SyncPullResponseItem(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    version: int
    deleted: bool
    updated_at: datetime
    payload: dict


class SyncPullResponse(BaseModel):
    changes: list[SyncPullResponseItem]
    cursor: str
