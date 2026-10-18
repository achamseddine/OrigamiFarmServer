"""Purchase requisitions (OrigamiFarmOS database/PROCUREMENT-SCHEMA.md §5–§6).

A requisition is a request to buy: lines of inventory items in a unit,
with an estimated price and a preferred supplier. A replenishment rule
may open one DRAFT per unresolved shortage; a person submits it; a
manager approves or rejects it, and the authority behind the decision is
snapshotted. Nothing here orders or receives stock — an approved
requisition is not a purchase (§1) — but submitted and approved lines
count as *confirmed incoming* for the pharmacy's reorder arithmetic, and
a receipt that names the requisition closes it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.farmos import routing_service as routing
from app.farmos import uom_service
from app.farmos.deps import AccessContext
from app.farmos.feed_catalog import FeedError
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.livestock_service import ensure_utc, now_utc
from app.farmos.procurement_models import (
    APPROVAL_DECISIONS,
    ProcurementApproval,
    PurchaseRequisition,
    PurchaseRequisitionLine,
)
from app.farmos.routing_service import NameCache
from app.tenant_api.models import InventoryItem, Task

INCOMING_STATUSES = ("submitted", "approved", "ordered")
TASK_SOURCE = "purchase_requisition"


class ProcurementError(FeedError):
    pass


def get_requisition(db: Session, requisition_id: str | uuid.UUID | None) -> PurchaseRequisition:
    pk = parse_uuid(requisition_id)
    r = db.get(PurchaseRequisition, pk) if pk else None
    if r is None or r.deleted_at is not None:
        raise ProcurementError("Requisition not found", 404)
    return r


def next_requisition_no(db: Session) -> str:
    n = (db.scalar(select(func.count(PurchaseRequisition.id))) or 0) + 1
    while db.scalar(
        select(PurchaseRequisition.id).where(PurchaseRequisition.requisition_no == f"REQ-{n:06d}")
    ):
        n += 1
    return f"REQ-{n:06d}"


def open_for_source(db: Session, source_type: str, source_reference_id: str) -> PurchaseRequisition | None:
    """The unresolved requisition a shortage already opened, if any —
    never a duplicate draft for the same shortage (notification rules)."""
    return db.scalar(
        select(PurchaseRequisition).where(
            PurchaseRequisition.source_type == source_type,
            PurchaseRequisition.source_reference_id == source_reference_id,
            PurchaseRequisition.status.in_(("draft",) + INCOMING_STATUSES),
            PurchaseRequisition.deleted_at.is_(None),
        )
    )


def create_requisition(
    db: Session,
    access: AccessContext,
    *,
    lines: list[dict],
    requested_by: uuid.UUID | None = None,
    purpose_code: str | None = None,
    needed_by: datetime | None = None,
    justification: str | None = None,
    source_type: str | None = None,
    source_reference_id: str | None = None,
    currency: str | None = None,
    task_priority: str = "medium",
    task_source_type: str = TASK_SOURCE,
    task_source_id: str | None = None,
) -> PurchaseRequisition:
    if not lines:
        raise ProcurementError("A requisition needs at least one line.")
    buyer = routing.primary_recipient(access.tenant_id, "procurement_requisition", db=db)
    req = PurchaseRequisition(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        requisition_no=next_requisition_no(db),
        requested_by=requested_by or (buyer.user_id if buyer else access.user_id),
        requested_at=now_utc(),
        needed_by=ensure_utc(needed_by),
        purpose_code=purpose_code,
        status="draft",
        justification=justification,
        source_type=source_type,
        source_reference_id=source_reference_id,
        currency=currency,
    )
    total, priced = 0.0, False
    for i, line in enumerate(lines, start=1):
        item_pk = parse_uuid(line.get("inventory_item_id"))
        item = db.get(InventoryItem, item_pk) if item_pk else None
        if item is None or item.deleted_at is not None:
            raise ProcurementError("Inventory item not found", 404)
        qty = float(line.get("requested_quantity") or 0)
        if qty <= 0:
            raise ProcurementError(f"Requested quantity for {item.name} must be greater than zero.")
        unit = uom_service.require(db, line.get("uom_code") or item.unit, what="unit")
        price = line.get("estimated_unit_price")
        if price is None and item.unit_cost is not None and uom_service.compatible(db, unit, item.unit):
            price = round(float(item.unit_cost) * uom_service.convert(db, 1, unit, item.unit), 6)
        req.lines.append(
            PurchaseRequisitionLine(
                tenant_id=access.tenant_id,
                last_modified_by=access.membership_id,
                line_no=i,
                inventory_item_id=item.id,
                requested_quantity=qty,
                uom_code=unit,
                estimated_unit_price=price,
                preferred_supplier_id=line.get("preferred_supplier_id"),
                specification=line.get("specification"),
                status="open",
                source_recommendation_id=line.get("source_recommendation_id"),
            )
        )
        if price is not None:
            total += float(price) * qty
            priced = True
    req.total_estimated_amount = round(total, 4) if priced else None
    db.add(req)
    db.flush()
    first = req.lines[0]
    item = db.get(InventoryItem, first.inventory_item_id)
    summary = f"{item.name if item else 'item'} × {first.requested_quantity:g} {first.uom_code}" + (
        f" and {len(req.lines) - 1} more line(s)" if len(req.lines) > 1 else ""
    )
    task = Task(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        title=f"Draft requisition {req.requisition_no}: {summary}"[:300],
        description=(
            f"DRAFT purchase requisition ({purpose_code or 'manual'}). {justification or ''} "
            "Review, submit and approve before any order is placed."
        ).strip()[:1500],
        assigned_to=buyer.user_id if buyer else None,
        due_at=ensure_utc(needed_by) or (now_utc() + timedelta(days=3)),
        priority=task_priority,
        status="open",
        source_type=task_source_type,
        source_id=task_source_id or str(req.id),
    )
    db.add(task)
    db.flush()
    req.task_id = task.id
    return req


def _close_task(db: Session, req: PurchaseRequisition) -> None:
    if req.task_id:
        t = db.get(Task, req.task_id)
        if t is not None and t.status == "open":
            t.status = "done"


def submit(db: Session, access: AccessContext, req: PurchaseRequisition) -> PurchaseRequisition:
    if req.status != "draft":
        raise ProcurementError(
            f"Requisition {req.requisition_no} is {req.status}; only a draft can be submitted."
        )
    req.status = "submitted"
    req.submitted_at = now_utc()
    req.last_modified_by = access.membership_id
    req.version += 1
    _on_incoming_changed(db, access, req)
    return req


def authority(access: AccessContext) -> dict:
    """Who may approve (§6): a manager or owner, or a person granted
    inventory approve. Snapshotted on the approval row at decision time."""
    role_ok = access.role in ("owner", "manager")
    perm_ok = access.has("inventory", "approve")
    return {
        "user_id": str(access.user_id),
        "role": access.role,
        "basis": "role" if role_ok else ("inventory.approve" if perm_ok else None),
        "decided_at": now_utc().isoformat(),
    }


def decide(
    db: Session,
    access: AccessContext,
    req: PurchaseRequisition,
    *,
    decision: str,
    comments: str | None = None,
) -> PurchaseRequisition:
    if decision not in APPROVAL_DECISIONS:
        raise ProcurementError("decision must be approved or rejected")
    if req.status not in ("draft", "submitted"):
        raise ProcurementError(f"Requisition {req.requisition_no} is {req.status} and cannot be decided.")
    snapshot = authority(access)
    if snapshot["basis"] is None:
        raise ProcurementError(
            "Approving a purchase requisition needs a farm manager, the owner, "
            "or inventory approve permission.",
            403,
        )
    if decision == "rejected" and not (comments and comments.strip()):
        raise ProcurementError("Say why the requisition is rejected.")
    at = now_utc()
    db.add(
        ProcurementApproval(
            tenant_id=access.tenant_id,
            last_modified_by=access.membership_id,
            document_type="purchase_requisition",
            document_id=req.id,
            approval_stage="manager",
            decision=decision,
            decided_by=access.user_id,
            decided_at=at,
            authority_snapshot=snapshot,
            comments=comments,
        )
    )
    req.status = decision
    req.decided_at = at
    req.decided_by = access.user_id
    req.last_modified_by = access.membership_id
    req.version += 1
    for line in req.lines:
        line.status = decision
    _close_task(db, req)
    _on_incoming_changed(db, access, req)
    return req


def cancel(
    db: Session, access: AccessContext, req: PurchaseRequisition, *, reason: str
) -> PurchaseRequisition:
    if req.status in ("received", "cancelled"):
        return req
    if req.status == "ordered":
        raise ProcurementError("An ordered requisition is cancelled through its purchase order.")
    if not reason or not reason.strip():
        raise ProcurementError("Say why the requisition is cancelled.")
    req.status = "cancelled"
    req.last_modified_by = access.membership_id
    req.version += 1
    for line in req.lines:
        line.status = "cancelled"
    _close_task(db, req)
    _on_incoming_changed(db, access, req)
    return req


def mark_received(
    db: Session,
    access: AccessContext,
    req: PurchaseRequisition,
    *,
    inventory_item_id: uuid.UUID | None = None,
) -> PurchaseRequisition:
    """A goods receipt that names the requisition closes its line(s); when
    every line is received the requisition is received. Receipt of stock
    itself is the inventory module's fact."""
    if req.status not in INCOMING_STATUSES and req.status != "draft":
        return req
    for line in req.lines:
        if inventory_item_id is None or line.inventory_item_id == inventory_item_id:
            line.status = "received"
    if all(line.status in ("received", "cancelled", "rejected") for line in req.lines):
        req.status = "received"
        _close_task(db, req)
    req.last_modified_by = access.membership_id
    req.version += 1
    _on_incoming_changed(db, access, req)
    return req


def incoming_quantity(db: Session, inventory_item_id: uuid.UUID, unit: str) -> float:
    """Confirmed incoming supply for the item, in `unit` (§9 of the
    pharmacy schema: recommended = target − eligible − confirmed incoming)."""
    total = 0.0
    rows = db.scalars(
        select(PurchaseRequisitionLine)
        .join(PurchaseRequisition, PurchaseRequisition.id == PurchaseRequisitionLine.requisition_id)
        .where(
            PurchaseRequisition.status.in_(INCOMING_STATUSES),
            PurchaseRequisitionLine.inventory_item_id == inventory_item_id,
            PurchaseRequisitionLine.status.in_(("open", "approved", "ordered")),
        )
    ).all()
    for line in rows:
        try:
            total += uom_service.convert(db, line.requested_quantity, line.uom_code, unit)
        except FeedError:  # a line in an unconvertible unit does not count as incoming
            continue
    return round(total, 6)


def _on_incoming_changed(db: Session, access: AccessContext, req: PurchaseRequisition) -> None:
    """Procurement incoming-supply change re-evaluates the pharmacy
    policies of the medicines on the requisition (notification rules)."""
    from app.farmos import pharmacy_service as ph  # local import: the pharmacy imports this module
    from app.farmos.pharmacy_models import MedicineProduct

    for line in req.lines:
        product = db.get(MedicineProduct, line.inventory_item_id)
        if product is not None and product.deleted_at is None:
            ph.evaluate_item(db, access, product)


def requisition_dict(db: Session, r: PurchaseRequisition, names: NameCache) -> dict:
    approvals = db.scalars(
        select(ProcurementApproval)
        .where(
            ProcurementApproval.document_type == "purchase_requisition",
            ProcurementApproval.document_id == r.id,
        )
        .order_by(ProcurementApproval.decided_at)
    ).all()
    lines: list[dict[str, Any]] = []
    for line in r.lines:
        item = db.get(InventoryItem, line.inventory_item_id)
        lines.append(
            {
                "id": str(line.id),
                "line_no": line.line_no,
                "inventory_item_id": str(line.inventory_item_id),
                "item_name": item.name if item else None,
                "requested_quantity": line.requested_quantity,
                "uom_code": line.uom_code,
                "estimated_unit_price": line.estimated_unit_price,
                "preferred_supplier_id": line.preferred_supplier_id,
                "preferred_supplier_name": None,
                "specification": line.specification,
                "status": line.status,
                "source_recommendation_id": line.source_recommendation_id,
            }
        )
    return {
        "id": str(r.id),
        "farm_id": str(r.tenant_id),
        "requisition_no": r.requisition_no,
        "requested_by": str(r.requested_by) if r.requested_by else None,
        "requested_by_name": names.get(r.requested_by),
        "requested_at": r.requested_at,
        "needed_by": r.needed_by,
        "purpose_code": r.purpose_code,
        "status": r.status,
        "justification": r.justification,
        "source_type": r.source_type,
        "source_reference_id": r.source_reference_id,
        "total_estimated_amount": r.total_estimated_amount,
        "currency": r.currency,
        "submitted_at": r.submitted_at,
        "decided_at": r.decided_at,
        "decided_by": str(r.decided_by) if r.decided_by else None,
        "task_id": str(r.task_id) if r.task_id else None,
        "created_at": r.created_at,
        "lines": lines,
        "approvals": [
            {
                "id": str(a.id),
                "decision": a.decision,
                "decided_by": str(a.decided_by) if a.decided_by else None,
                "decided_at": a.decided_at,
                "authority": a.authority_snapshot,
                "comments": a.comments,
            }
            for a in approvals
        ],
    }
