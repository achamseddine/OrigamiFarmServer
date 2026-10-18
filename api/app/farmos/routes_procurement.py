"""Purchase requisitions (OrigamiFarmOS database/PROCUREMENT-SCHEMA.md §5–§6).

Anyone with inventory view reads; inventory create opens and submits a
draft; approval and rejection need a manager, the owner, or inventory
approve — the service snapshots the authority on the decision. Nothing
here orders or receives stock.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import procurement_service as procurement
from app.farmos.deps import AccessContext, get_access_context, get_farmos_tenant_db, require_permission
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.pharmacy_schemas import RequisitionCancel, RequisitionDecision, RequisitionIn
from app.farmos.procurement_models import PurchaseRequisition
from app.farmos.routing_service import NameCache

router = APIRouter(prefix="/procurement")

_view = require_permission("inventory", "view")
_create = require_permission("inventory", "create")
_edit = require_permission("inventory", "edit")


def _audit(
    control_db: Session, access: AccessContext, *, action: str, req: PurchaseRequisition, summary: str
) -> None:
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action=action,
        entity_type="purchase_requisition",
        entity_id=str(req.id),
        module_code="inventory",
        summary=summary,
        changes={"requisition_no": req.requisition_no, "status": req.status},
    )


@router.get("/requisitions")
def list_requisitions(
    req_status: str | None = Query(None, alias="status"),
    inventory_item_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(PurchaseRequisition).where(PurchaseRequisition.deleted_at.is_(None))
    if req_status == "open":
        stmt = stmt.where(PurchaseRequisition.status.in_(("draft", "submitted", "approved", "ordered")))
    elif req_status:
        stmt = stmt.where(PurchaseRequisition.status == req_status)
    rows = list(db.scalars(stmt.order_by(PurchaseRequisition.requested_at.desc()).limit(limit)))
    if inventory_item_id:
        pk = parse_uuid(inventory_item_id)
        rows = [r for r in rows if any(line.inventory_item_id == pk for line in r.lines)]
    names = NameCache()
    return [procurement.requisition_dict(db, r, names) for r in rows]


@router.post("/requisitions", status_code=201)
def create_requisition(
    payload: RequisitionIn,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """A manual DRAFT requisition. Submitting and approving are separate
    steps; an approved requisition is not a purchase."""
    req = procurement.create_requisition(
        db,
        access,
        lines=[line.model_dump() for line in payload.lines],
        requested_by=access.user_id,
        purpose_code=payload.purpose_code or "manual",
        needed_by=payload.needed_by,
        justification=payload.justification,
        source_type="manual",
        currency=payload.currency,
    )
    _audit(
        control_db,
        access,
        action="requisition.created",
        req=req,
        summary=f"Draft requisition {req.requisition_no} opened",
    )
    return procurement.requisition_dict(db, req, NameCache())


@router.get("/requisitions/{requisition_id}")
def get_requisition(
    requisition_id: str, access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return procurement.requisition_dict(db, procurement.get_requisition(db, requisition_id), NameCache())


@router.post("/requisitions/{requisition_id}/submit")
def submit_requisition(
    requisition_id: str,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    req = procurement.submit(db, access, procurement.get_requisition(db, requisition_id))
    _audit(
        control_db,
        access,
        action="requisition.submitted",
        req=req,
        summary=f"Requisition {req.requisition_no} submitted",
    )
    return procurement.requisition_dict(db, req, NameCache())


@router.post("/requisitions/{requisition_id}/approve")
def approve_requisition(
    requisition_id: str,
    payload: RequisitionDecision | None = None,
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Approval with the deciding authority snapshotted (§6). Refused for
    anyone without it."""
    req = procurement.decide(
        db,
        access,
        procurement.get_requisition(db, requisition_id),
        decision="approved",
        comments=payload.comments if payload else None,
    )
    _audit(
        control_db,
        access,
        action="requisition.approved",
        req=req,
        summary=f"Requisition {req.requisition_no} approved",
    )
    return procurement.requisition_dict(db, req, NameCache())


@router.post("/requisitions/{requisition_id}/reject")
def reject_requisition(
    requisition_id: str,
    payload: RequisitionDecision,
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    req = procurement.decide(
        db,
        access,
        procurement.get_requisition(db, requisition_id),
        decision="rejected",
        comments=payload.comments,
    )
    _audit(
        control_db,
        access,
        action="requisition.rejected",
        req=req,
        summary=f"Requisition {req.requisition_no} rejected: {payload.comments}",
    )
    return procurement.requisition_dict(db, req, NameCache())


@router.post("/requisitions/{requisition_id}/cancel")
def cancel_requisition(
    requisition_id: str,
    payload: RequisitionCancel,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    req = procurement.cancel(
        db, access, procurement.get_requisition(db, requisition_id), reason=payload.reason
    )
    _audit(
        control_db,
        access,
        action="requisition.cancelled",
        req=req,
        summary=f"Requisition {req.requisition_no} cancelled: {payload.reason}",
    )
    return procurement.requisition_dict(db, req, NameCache())
