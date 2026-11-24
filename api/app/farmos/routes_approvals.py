"""Employee work waiting for the farm manager (approval_service.py;
OrigamiFarmOS backend/app/api/v1/approvals.py, PR 23).

Anyone who may create in the request's module sends one; the list shows a
manager everything waiting on the modules they approve, and an employee
their own requests with how each was decided. Approving needs *approve*
on the module and runs the change as the manager.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.db import get_control_db
from app.farmos import approval_service as approvals
from app.farmos.approval_schemas import ApprovalDecisionIn, ApprovalRequestIn
from app.farmos.deps import AccessContext, get_access_context, get_farmos_tenant_db
from app.farmos.routing_service import NameCache

router = APIRouter(prefix="/approval-requests")


@router.get("")
def list_requests(
    req_status: str | None = Query(None, alias="status"),
    mine: bool = False,
    limit: int = Query(100, ge=1, le=500),
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    """[status] pending | approved | rejected | cancelled (all when unset).
    A manager sees the requests of every module they approve (and their
    own); anyone else sees only their own. [mine] limits to one's own."""
    rows = approvals.list_requests(db, access, status=req_status, mine=mine, limit=limit)
    names = NameCache()
    return [approvals.to_dict(r, names) for r in rows]


@router.post("", status_code=201)
def create_request(
    payload: ApprovalRequestIn,
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    req = approvals.create(
        db,
        control_db,
        access,
        kind=payload.kind,
        payload=payload.payload,
        reason=payload.reason,
        target_id=payload.target_id,
    )
    return approvals.to_dict(req, NameCache())


@router.get("/{request_id}")
def get_request(
    request_id: str,
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    req = approvals.get(db, request_id)
    if req.requested_by != access.user_id and not approvals.can_decide(access, req):
        raise approvals.ApprovalError("Request not found", 404)
    return approvals.to_dict(req, NameCache())


@router.post("/{request_id}/approve")
def approve_request(
    request_id: str,
    payload: ApprovalDecisionIn | None = None,
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    body = payload or ApprovalDecisionIn()
    req = approvals.approve(
        db,
        control_db,
        access,
        approvals.get(db, request_id),
        note=body.note,
        allow_negative=body.allow_negative,
        confirm_duplicate=body.confirm_duplicate,
    )
    return approvals.to_dict(req, NameCache())


@router.post("/{request_id}/reject")
def reject_request(
    request_id: str,
    payload: ApprovalDecisionIn,
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    req = approvals.reject(db, control_db, access, approvals.get(db, request_id), note=payload.note)
    return approvals.to_dict(req, NameCache())


@router.post("/{request_id}/cancel")
def cancel_request(
    request_id: str,
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    req = approvals.cancel(db, control_db, access, approvals.get(db, request_id))
    return approvals.to_dict(req, NameCache())
