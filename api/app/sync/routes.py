"""FarmOS offline synchronization: push (tablet -> cloud) and pull (cloud ->
tablet). See SYNC_PROTOCOL.md for the full contract, conflict rules, and
known limitations of the v0.1 timestamp-based cursor.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_module
from app.auth.schemas import TenantContext
from app.common.enums import SyncOperation
from app.common.tenant_db import get_tenant_db
from app.sync.schemas import (
    SyncChangeResult,
    SyncPullResponse,
    SyncPullResponseItem,
    SyncPushRequest,
    SyncPushResponse,
)
from app.tenant_api.models import Animal, SyncEvent

router = APIRouter()

# entity_type -> ORM model, for this representative slice of the domain.
_ENTITY_MODELS = {"animal": Animal}

_MAX_PULL_BATCH = 500


def _serialize_animal(animal: Animal) -> dict:
    return {
        "tag": animal.tag,
        "species": animal.species,
        "name": animal.name,
        "status": animal.status,
        "farm_id": str(animal.farm_id) if animal.farm_id else None,
    }


@router.post("/push", response_model=SyncPushResponse)
def sync_push(
    payload: SyncPushRequest,
    tenant_context: TenantContext = Depends(require_module("ANIMALS")),
    db: Session = Depends(get_tenant_db),
) -> SyncPushResponse:
    results: list[SyncChangeResult] = []

    for change in payload.changes:
        existing_event = db.get(SyncEvent, change.event_id)
        if existing_event is not None:
            # Idempotent replay: the client retried a push whose response
            # it never saw. Return the previously recorded outcome rather
            # than re-applying (or double-counting) the change.
            results.append(
                SyncChangeResult(
                    event_id=change.event_id,
                    entity_id=change.entity_id,
                    status="REPLAYED",
                    **{k: v for k, v in existing_event.result.items() if k in {"current_version", "reason"}},
                )
            )
            continue

        model = _ENTITY_MODELS.get(change.entity_type)
        if model is None:
            result = SyncChangeResult(
                event_id=change.event_id,
                entity_id=change.entity_id,
                status="REJECTED",
                reason=f"Unsupported entity_type '{change.entity_type}'",
            )
            _record_sync_event(db, tenant_context, change, result)
            results.append(result)
            continue

        result = _apply_change(db, tenant_context, model, change)
        _record_sync_event(db, tenant_context, change, result)
        results.append(result)

    cursor = datetime.now(timezone.utc).isoformat()
    return SyncPushResponse(results=results, cursor=cursor)


def _apply_change(db: Session, tenant_context: TenantContext, model, change) -> SyncChangeResult:
    if not tenant_context.has_permission("ANIMALS", change.operation.value.lower()):
        return SyncChangeResult(
            event_id=change.event_id,
            entity_id=change.entity_id,
            status="REJECTED",
            reason="PERMISSION_DENIED",
        )

    existing = db.get(model, change.entity_id)

    if change.operation == SyncOperation.CREATE:
        if existing is not None:
            return SyncChangeResult(
                event_id=change.event_id,
                entity_id=change.entity_id,
                status="CONFLICT",
                current_version=existing.version,
                reason="Entity already exists",
            )
        farm_id = change.payload.get("farm_id")
        if farm_id and not tenant_context.has_farm_access(uuid.UUID(farm_id)):
            return SyncChangeResult(
                event_id=change.event_id,
                entity_id=change.entity_id,
                status="REJECTED",
                reason="FARM_SCOPE_DENIED",
            )
        row = model(
            id=change.entity_id,
            tenant_id=tenant_context.tenant_id,
            farm_id=uuid.UUID(farm_id) if farm_id else None,
            origin_device_id=tenant_context.device_id,
            last_modified_by=tenant_context.membership_id,
            **{k: v for k, v in change.payload.items() if k not in {"farm_id"}},
        )
        db.add(row)
        db.flush()
        return SyncChangeResult(
            event_id=change.event_id,
            entity_id=change.entity_id,
            status="APPLIED",
            current_version=row.version,
        )

    if existing is None or existing.deleted_at is not None:
        return SyncChangeResult(
            event_id=change.event_id, entity_id=change.entity_id, status="REJECTED", reason="NOT_FOUND"
        )

    if change.base_version is not None and existing.version != change.base_version:
        # Master-record conflict: the tablet edited a stale copy. Return a
        # structured conflict rather than silently overwriting — the
        # tablet decides whether to retry against the current version.
        return SyncChangeResult(
            event_id=change.event_id,
            entity_id=change.entity_id,
            status="CONFLICT",
            current_version=existing.version,
            reason="Version mismatch",
        )

    if change.operation == SyncOperation.DELETE:
        existing.deleted_at = datetime.now(timezone.utc)
    else:
        for key, value in change.payload.items():
            if key in {"farm_id", "id", "tenant_id"}:
                continue
            if hasattr(existing, key):
                setattr(existing, key, value)

    existing.version += 1
    existing.last_modified_by = tenant_context.membership_id
    existing.origin_device_id = tenant_context.device_id
    db.flush()
    return SyncChangeResult(
        event_id=change.event_id,
        entity_id=change.entity_id,
        status="APPLIED",
        current_version=existing.version,
    )


def _record_sync_event(db: Session, tenant_context: TenantContext, change, result: SyncChangeResult) -> None:
    db.add(
        SyncEvent(
            id=change.event_id,
            tenant_id=tenant_context.tenant_id,
            device_id=tenant_context.device_id,
            entity_type=change.entity_type,
            entity_id=str(change.entity_id),
            operation=change.operation,
            applied_at=datetime.now(timezone.utc),
            result=result.model_dump(mode="json"),
        )
    )
    db.flush()


@router.get("/pull", response_model=SyncPullResponse)
def sync_pull(
    cursor: str | None = Query(default=None),
    tenant_context: TenantContext = Depends(require_module("ANIMALS")),
    db: Session = Depends(get_tenant_db),
) -> SyncPullResponse:
    stmt = select(Animal).order_by(Animal.updated_at.asc()).limit(_MAX_PULL_BATCH)
    if cursor:
        stmt = stmt.where(Animal.updated_at > datetime.fromisoformat(cursor))

    rows = db.execute(stmt).scalars().all()
    visible = [r for r in rows if r.farm_id is None or tenant_context.has_farm_access(r.farm_id)]

    items = [
        SyncPullResponseItem(
            entity_type="animal",
            entity_id=row.id,
            version=row.version,
            deleted=row.deleted_at is not None,
            updated_at=row.updated_at,
            payload=_serialize_animal(row),
        )
        for row in visible
    ]
    now_iso = datetime.now(timezone.utc).isoformat()
    new_cursor = rows[-1].updated_at.isoformat() if rows else (cursor or now_iso)
    return SyncPullResponse(changes=items, cursor=new_cursor)
