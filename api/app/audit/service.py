"""The only writer of audit_event rows. Every privileged state change in
the control plane should call record_audit_event in the same DB
transaction as the change itself, so the audit trail can never drift from
what actually happened (both commit together, or neither does).
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.common.enums import ActorType
from app.common.logging import get_correlation_id


def record_audit_event(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    actor_type: ActorType,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    actor_role: str | None = None,
    tenant_id: uuid.UUID | None = None,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
    ip_address: str | None = None,
    session_id: str | None = None,
    module_code: str | None = None,
    summary: str | None = None,
    changes: dict | None = None,
    metadata: dict | None = None,
    device: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_id=actor_id,
        actor_type=actor_type,
        actor_role=actor_role,
        tenant_id=tenant_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_summary=before,
        after_summary=after,
        reason=reason,
        correlation_id=get_correlation_id() or None,
        ip_address=ip_address,
        session_id=session_id,
        module_code=module_code,
        summary=summary,
        changes_json=changes,
        metadata_json=metadata or {},
        device=device,
    )
    db.add(event)
    db.flush()
    return event
