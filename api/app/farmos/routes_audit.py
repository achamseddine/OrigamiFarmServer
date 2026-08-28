"""GET /audit — Audit History (tech spec §23). Gated on Reports/view:
seeing who changed what across the farm is a supervisory capability, not
something every employee holds by default. Reads the same control-plane
audit_event table platform admin actions use (app/audit/), filtered to
this farm's own tenant_id and to the FarmOS-populated rows (module_code
is set only by FarmOS mutation routes — see app/audit/service.py).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserIdentity
from app.common.db import get_control_db
from app.farmos.deps import AccessContext, require_permission
from app.farmos.schemas import AuditEventOut

router = APIRouter()


@router.get("/audit", response_model=list[AuditEventOut])
def list_audit_events(
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    module: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    access: AccessContext = Depends(require_permission("reports", "view")),
    db: Session = Depends(get_control_db),
) -> list[AuditEventOut]:
    stmt = select(AuditEvent).where(
        AuditEvent.tenant_id == access.tenant_id, AuditEvent.module_code.is_not(None)
    )
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    if module:
        stmt = stmt.where(AuditEvent.module_code == module)
    if user_id:
        stmt = stmt.where(AuditEvent.actor_id == uuid.UUID(user_id))
    rows = db.execute(stmt.order_by(AuditEvent.created_at.desc()).limit(limit)).scalars().all()

    actor_ids = {row.actor_id for row in rows if row.actor_id is not None}
    names: dict[str, str] = {}
    if actor_ids:
        users = db.execute(select(UserIdentity).where(UserIdentity.id.in_(actor_ids))).scalars()
        names = {str(u.id): u.display_name for u in users}

    return [
        AuditEventOut(
            id=str(row.id),
            user_id=str(row.actor_id) if row.actor_id else "",
            user_name=names.get(str(row.actor_id)) if row.actor_id else None,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id or "",
            module_code=row.module_code,
            summary=row.summary,
            changes_json=row.changes_json,
            metadata_json=row.metadata_json or {},
            device=row.device,
            timestamp=row.created_at,
        )
        for row in rows
    ]
