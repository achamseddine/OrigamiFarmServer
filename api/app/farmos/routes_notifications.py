from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.farmos.deps import AccessContext, get_access_context, get_farmos_tenant_db
from app.farmos.farm_models import Notification
from app.farmos.schemas import NotificationOut, NotificationsPage

router = APIRouter()


def _to_notification_out(n: Notification) -> NotificationOut:
    return NotificationOut(
        id=str(n.id),
        module_code=n.module_code,
        notification_type=n.notification_type,
        title=n.title,
        description=n.description,
        priority=n.priority,
        entity_type=n.entity_type,
        entity_id=n.entity_id,
        source_type=n.source_type,
        source_id=n.source_id,
        read_at=n.read_at,
        created_at=n.created_at,
    )


@router.get("/notifications", response_model=NotificationsPage)
def list_notifications(
    unread_only: bool = Query(default=False),
    module: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    _access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
) -> NotificationsPage:
    base = select(Notification).where(Notification.deleted_at.is_(None))
    if module:
        base = base.where(Notification.module_code == module)

    unread_count = db.execute(
        select(func.count()).select_from(
            base.where(Notification.read_at.is_(None)).subquery()
        )
    ).scalar_one()
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()

    stmt = base
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = db.execute(stmt.order_by(Notification.created_at.desc()).limit(limit)).scalars().all()

    return NotificationsPage(
        unread_count=unread_count,
        total=total,
        notifications=[_to_notification_out(row) for row in rows],
    )


@router.post("/notifications/read-all")
def mark_all_read(
    module: str | None = Query(default=None),
    _access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    stmt = select(Notification).where(
        Notification.deleted_at.is_(None), Notification.read_at.is_(None)
    )
    if module:
        stmt = stmt.where(Notification.module_code == module)
    rows = db.execute(stmt).scalars().all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.read_at = now
    db.flush()
    return {"marked_read": len(rows)}


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: str,
    _access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
) -> NotificationOut:
    try:
        pk = uuid.UUID(notification_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Notification not found.") from exc
    notification = db.get(Notification, pk)
    if notification is None or notification.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        db.flush()
    return _to_notification_out(notification)
