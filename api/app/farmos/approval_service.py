"""Employee work that waits for the farm manager (approval_models.py;
OrigamiFarmOS backend/app/services/approval_service.py, PR 23).

The farm's rule: employees enter the day's data — milk, feedings — but do
not buy feed, make a numbered mix or change a record on file without the
farm manager. Buying goes through purchase requisitions (procurement);
a mix or a correction made by someone without approval on its module is
kept here as a request:

  create  — the employee's version is checked now (so a mistake is heard
            while they are still at the cow) and stored; stock, mixes and
            records are untouched; the bell tells the managers.
  approve — someone with *approve* on the module runs the same service a
            manager would use directly; who asked and who approved are
            recorded on the result.
  reject  — with a reason; nothing changes. The employee is told either way.
  cancel  — the employee withdraws a request still pending.

The people are control-plane users: names are resolved through
routing_service.NameCache, never stored on the request.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.enums import ActorType
from app.farmos import duplicates, milk_service
from app.farmos import feed_batch_service as batches
from app.farmos import feed_models as fm
from app.farmos import feed_performance_service as perf
from app.farmos import feeding_program_service as programs
from app.farmos import routing_service as routing
from app.farmos.approval_models import APPROVAL_STATUSES, ApprovalRequest
from app.farmos.approval_schemas import FeedingCorrectionIn, MilkCorrectionIn
from app.farmos.deps import AccessContext, ensure_stock_override
from app.farmos.feed_catalog import FeedError
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.feed_schemas import QuickMixIn
from app.farmos.livestock_service import ensure_utc, now_utc
from app.farmos.permissions import MODULE_LABELS
from app.farmos.record_time import not_in_future
from app.farmos.routing_service import NameCache
from app.tenant_api.models import Animal

KIND_MODULE = {
    "feed_mix": "feed_nutrition",
    "milk_correction": "milk_production",
    "feeding_correction": "feed_nutrition",
}
ROUTE = "approval_request"
BELL_SOURCE = "approval_request"


class ApprovalError(FeedError):
    pass


def get(db: Session, request_id: str | uuid.UUID | None) -> ApprovalRequest:
    pk = parse_uuid(request_id)
    req = db.get(ApprovalRequest, pk) if pk else None
    if req is None or req.deleted_at is not None:
        raise ApprovalError("Request not found", 404)
    return req


def can_decide(access: AccessContext, req: ApprovalRequest) -> bool:
    return access.has(req.module_code, "approve")


def decided_modules(access: AccessContext) -> list[str]:
    """The modules whose requests this person decides."""
    return [m for m in sorted(set(KIND_MODULE.values())) if access.has(m, "approve")]


def pending_for(db: Session, target_id: uuid.UUID) -> ApprovalRequest | None:
    return db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.target_id == target_id,
            ApprovalRequest.status == "pending",
            ApprovalRequest.deleted_at.is_(None),
        )
    )


def ensure_none_pending(db: Session, target_id: uuid.UUID) -> None:
    """A record with a correction waiting is decided through that request,
    so two versions of it never compete."""
    waiting = pending_for(db, target_id)
    if waiting is not None:
        who = NameCache().get(waiting.requested_by) or "An employee"
        raise ApprovalError(f"{who} asked to correct this record; approve or reject that request first.", 409)


def list_requests(
    db: Session, access: AccessContext, *, status: str | None, mine: bool, limit: int
) -> list[ApprovalRequest]:
    """A manager sees the requests of every module they approve (and their
    own); anyone else sees only their own. [mine] limits to one's own."""
    if status is not None and status not in APPROVAL_STATUSES:
        raise ApprovalError(f"status must be one of {list(APPROVAL_STATUSES)}")
    stmt = select(ApprovalRequest).where(ApprovalRequest.deleted_at.is_(None))
    if status:
        stmt = stmt.where(ApprovalRequest.status == status)
    decides = decided_modules(access)
    if mine or not decides:
        stmt = stmt.where(ApprovalRequest.requested_by == access.user_id)
    else:
        stmt = stmt.where(
            or_(ApprovalRequest.module_code.in_(decides), ApprovalRequest.requested_by == access.user_id)
        )
    return list(db.scalars(stmt.order_by(ApprovalRequest.requested_at.desc()).limit(limit)))


def _parse(model: type[BaseModel], payload: dict) -> Any:
    """The employee's payload in the shape the direct endpoint takes; a
    shape error is answered as the endpoint would answer it (422)."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" if e["loc"] else e["msg"]
            for e in exc.errors()
        )
        raise ApprovalError(f"The request is not complete: {problems}") from None


def _audit(
    control_db: Session,
    access: AccessContext,
    req: ApprovalRequest,
    *,
    action: str,
    summary: str,
    changes: dict | None = None,
) -> None:
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action=action,
        entity_type="approval_request",
        entity_id=str(req.id),
        module_code=req.module_code,
        summary=summary,
        changes=changes,
    )


# ------------------------------------------------------------------ create
def create(
    db: Session,
    control_db: Session,
    access: AccessContext,
    *,
    kind: str,
    payload: dict,
    reason: str | None,
    target_id: str | None,
) -> ApprovalRequest:
    if kind not in KIND_MODULE:
        raise ApprovalError(f"kind must be one of {list(KIND_MODULE)}")
    module = KIND_MODULE[kind]
    if not access.has(module, "create"):
        raise ApprovalError(
            f"You do not have permission to create in {MODULE_LABELS[module]}. "
            "Ask a farm manager to grant it.",
            403,
        )
    reason = (reason or "").strip() or None
    subject_type: str | None = None
    subject_id: uuid.UUID | None = None
    if kind == "feed_mix":
        summary, body = _check_mix(db, payload)
    elif kind == "milk_correction":
        if not reason:
            raise ApprovalError("Say why the milk record should be corrected.")
        summary, body, animal = _check_milk(db, target_id, payload)
        subject_type, subject_id = "animal", animal.id
    else:
        if not reason:
            raise ApprovalError("Say why the feeding should be corrected.")
        summary, body, event = _check_feeding(db, target_id, payload)
        subject_type, subject_id = event.subject_type, event.subject_id
    target_pk = parse_uuid(target_id) if target_id else None
    if target_pk is not None:
        ensure_none_pending(db, target_pk)
    req = ApprovalRequest(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        kind=kind,
        module_code=module,
        status="pending",
        subject_type=subject_type,
        subject_id=subject_id,
        target_id=target_pk,
        summary=summary[:300],
        payload=body,
        reason=reason,
        requested_by=access.user_id,
        requested_at=now_utc(),
    )
    db.add(req)
    db.flush()
    _audit(
        control_db,
        access,
        req,
        action="approval_request.created",
        summary=f"{access.display_name} asked for approval: {req.summary}",
        changes={"kind": kind, "target_id": target_id, "reason": reason},
    )
    # The managers hear of it on the bell (routing decides who a route
    # reaches; the bell itself is farm-wide here, one entry per request).
    routing.notify_bell(
        db,
        access.tenant_id,
        module_code=module,
        notification_type="approval_request",
        title=f"Waiting for your approval: {req.summary}",
        description=f"Asked by {access.display_name}." + (f" Reason: {reason}" if reason else ""),
        priority="high",
        entity_type="approval_request",
        entity_id=str(req.id),
        source_type=BELL_SOURCE,
        source_id=str(req.id),
        actor=access.membership_id,
    )
    return req


def _product(db: Session, product_id: str | None, what: str) -> fm.FeedProduct:
    pk = parse_uuid(product_id)
    product = db.get(fm.FeedProduct, pk) if pk else None
    if product is None or product.deleted_at is not None:
        raise ApprovalError(f"{what} not found", 404)
    return product


def _check_mix(db: Session, payload: dict) -> tuple[str, dict]:
    mix: QuickMixIn = _parse(QuickMixIn, payload)
    not_in_future(mix.produced_at, "A mix")
    if not mix.components:
        raise ApprovalError("A mix needs at least one ingredient.")
    names = [_product(db, c.feed_product_id, "Ingredient").name for c in mix.components]
    if mix.feed_product_id:
        output = _product(db, mix.feed_product_id, "Feed").name
    elif mix.new_product_name and mix.new_product_name.strip():
        output = mix.new_product_name.strip()
    else:
        raise ApprovalError("Say which feed the mix makes.")
    total = sum(c.actual_quantity for c in mix.components)
    summary = f"Mix of {output}: {total:g} {mix.unit} from {', '.join(names)}"
    return summary, mix.model_dump(mode="json")


def _check_milk(db: Session, target_id: str | None, payload: dict) -> tuple[str, dict, Animal]:
    if not target_id:
        raise ApprovalError("Name the milk record to correct.")
    record, animal = milk_service.get_record(db, target_id)
    body: MilkCorrectionIn = _parse(MilkCorrectionIn, {**payload, "reason": "-"})
    changes = body.model_dump(exclude={"reason"}, exclude_none=True)
    new, new_animal = milk_service.validate_correction(db, record, changes)
    was_at = ensure_utc(record.recorded_at)
    assert was_at is not None
    when = was_at.date().isoformat()
    parts = []
    if new["liters"] != float(record.liters):
        parts.append(f"{float(record.liters):g} L → {float(new['liters']):g} L")
    if new["session"] != record.session:
        parts.append(f"{record.session} → {new['session']}")
    if new["destination"] != record.destination:
        parts.append(f"{record.destination} → {new['destination']}")
    if new["recorded_at"].date() != was_at.date():
        parts.append(f"{when} → {new['recorded_at'].date().isoformat()}")
    if new_animal.id != animal.id:
        parts.append(f"{animal.name} → {new_animal.name}")
    summary = f"Correct {animal.name}'s {record.session} milk of {when}: " + ", ".join(parts)
    return summary, body.model_dump(mode="json", exclude={"reason"}, exclude_none=True), animal


def _check_feeding(db: Session, target_id: str | None, payload: dict) -> tuple[str, dict, fm.FeedingEvent]:
    if not target_id:
        raise ApprovalError("Name the feeding to correct.")
    event = _feeding_event(db, target_id)
    body: FeedingCorrectionIn = _parse(FeedingCorrectionIn, {**payload, "reason": "-"})
    subject = programs.load_subject(db, event.subject_type, str(event.subject_id))
    for c in body.components:
        _product(db, c.feed_product_id, "Feed")
    before = ", ".join(f"{c.quantity_offered:g} {c.unit}" for c in event.components) or "—"
    occurred = ensure_utc(event.occurred_at)
    assert occurred is not None
    when = occurred.date().isoformat()
    name = getattr(subject, "name", str(event.subject_id))
    if body.components:
        after = ", ".join(f"{c.quantity_offered:g} {c.unit or 'kg'}" for c in body.components)
        summary = f"Correct the feeding of {name} on {when}: {before} → {after}"
    else:
        summary = f"Undo the feeding of {name} on {when} ({before})"
    return summary, body.model_dump(mode="json", exclude={"reason"}), event


def _feeding_event(db: Session, event_id: str | uuid.UUID | None) -> fm.FeedingEvent:
    pk = parse_uuid(event_id)
    event = db.get(fm.FeedingEvent, pk) if pk else None
    if event is None or event.deleted_at is not None:
        raise ApprovalError("Feeding event not found", 404)
    if event.status != "recorded":
        raise ApprovalError("This feeding was already undone or corrected.", 409)
    return event


# ------------------------------------------------------------------ decide
def approve(
    db: Session,
    control_db: Session,
    access: AccessContext,
    req: ApprovalRequest,
    *,
    note: str | None = None,
    allow_negative: bool = False,
    confirm_duplicate: bool = False,
) -> ApprovalRequest:
    _ensure_decidable(access, req)
    if req.kind == "feed_mix":
        mix: QuickMixIn = _parse(QuickMixIn, req.payload)
        override = allow_negative or mix.allow_negative
        ensure_stock_override(access, override, "feed_nutrition")
        # Approving the mix also approves the recipe a first mix of a feed
        # creates (can_approve); the mix stays the employee's work: they are
        # its operator, the approver mixed it on record.
        batch, match = batches.quick_mix(
            db,
            access,
            feed_product_id=mix.feed_product_id,
            new_product_name=mix.new_product_name,
            components=[c.model_dump() for c in mix.components],
            output_quantity=mix.output_quantity,
            unit=mix.unit,
            produced_at=mix.produced_at,
            intended_species_code=mix.intended_species_code,
            notes=mix.notes,
            allow_negative=override,
            can_approve=True,
            confirm_duplicate=confirm_duplicate or mix.confirm_duplicate,
        )
        batch.operator_id = req.requested_by
        perf.on_batch_completed(db, access, batch)
        duplicates.log_confirmed(
            control_db,
            access,
            entity_type="feed_batch",
            entity_id=str(batch.id),
            match_id=match.id if match else None,
            what=f"mix:{batch.mix_code}",
            module_code="feed_nutrition",
        )
        req.result_type, req.result_id = "feed_batch", batch.id
    elif req.kind == "milk_correction":
        record, _ = milk_service.get_record(db, req.target_id)
        record = milk_service.apply_correction(
            db,
            control_db,
            access,
            record,
            dict(req.payload),
            reason=req.reason or "",
            requested_by=req.requested_by,
            approval_request_id=req.id,
        )
        req.result_type, req.result_id = "milk_record", record.id
    else:
        event = _feeding_event(db, req.target_id)
        body: FeedingCorrectionIn = _parse(FeedingCorrectionIn, {**req.payload, "reason": req.reason or ""})
        result = correct_feeding(
            db,
            control_db,
            access,
            event,
            body,
            requested_by=req.requested_by,
            allow_negative=allow_negative or body.allow_negative,
            approval_request_id=req.id,
        )
        req.result_type, req.result_id = "feeding_event", result.id
    return _decided(db, control_db, access, req, "approved", note)


def reject(
    db: Session, control_db: Session, access: AccessContext, req: ApprovalRequest, *, note: str | None
) -> ApprovalRequest:
    _ensure_decidable(access, req)
    if not note or not note.strip():
        raise ApprovalError("Say why the request is rejected, so the employee knows.")
    return _decided(db, control_db, access, req, "rejected", note.strip())


def cancel(db: Session, control_db: Session, access: AccessContext, req: ApprovalRequest) -> ApprovalRequest:
    if req.status != "pending":
        raise ApprovalError(f"This request is already {req.status}.", 409)
    if req.requested_by != access.user_id and not can_decide(access, req):
        raise ApprovalError("Only the person who asked can withdraw this request.", 403)
    req.status = "cancelled"
    req.decided_by, req.decided_at = access.user_id, now_utc()
    req.version += 1
    req.last_modified_by = access.membership_id
    db.flush()
    routing.clear_bell(db, source_type=BELL_SOURCE, source_id=str(req.id), at=now_utc())
    _audit(
        control_db,
        access,
        req,
        action="approval_request.cancelled",
        summary=f"{access.display_name} withdrew the request: {req.summary}",
        changes={"kind": req.kind, "status": "cancelled"},
    )
    return req


def _ensure_decidable(access: AccessContext, req: ApprovalRequest) -> None:
    if req.status != "pending":
        raise ApprovalError(f"This request is already {req.status}.", 409)
    if not can_decide(access, req):
        raise ApprovalError(
            f"Approving this needs {MODULE_LABELS[req.module_code]} approval — the farm manager.", 403
        )


def _decided(
    db: Session,
    control_db: Session,
    access: AccessContext,
    req: ApprovalRequest,
    decision: str,
    note: str | None,
) -> ApprovalRequest:
    at = now_utc()
    req.status = decision
    req.decided_by, req.decided_at = access.user_id, at
    req.decision_note = (note or "").strip() or None
    req.version += 1
    req.last_modified_by = access.membership_id
    db.flush()
    _audit(
        control_db,
        access,
        req,
        action=f"approval_request.{decision}",
        summary=f"{access.display_name} {decision} the request: {req.summary}",
        changes={
            "kind": req.kind,
            "status": decision,
            "note": req.decision_note,
            "result_type": req.result_type,
            "result_id": str(req.result_id) if req.result_id else None,
        },
    )
    # The waiting entry leaves the managers' bell; the person who asked
    # hears the answer.
    routing.clear_bell(db, source_type=BELL_SOURCE, source_id=str(req.id), at=at)
    if req.requested_by != access.user_id:
        verb = "approved" if decision == "approved" else "not approved"
        routing.notify_bell(
            db,
            access.tenant_id,
            module_code=req.module_code,
            notification_type="approval_decision",
            title=f"{access.display_name} {verb}: {req.summary}",
            description=req.decision_note or ("Recorded." if decision == "approved" else None),
            priority="medium",
            entity_type="approval_request",
            entity_id=str(req.id),
            source_type=BELL_SOURCE,
            source_id=f"{req.id}|{req.requested_by}",
            actor=access.membership_id,
        )
    return req


# -------------------------------------------------------- feeding correction
def correct_feeding(
    db: Session,
    control_db: Session,
    access: AccessContext,
    event: fm.FeedingEvent,
    body: FeedingCorrectionIn,
    *,
    requested_by: uuid.UUID | None = None,
    allow_negative: bool = False,
    approval_request_id: uuid.UUID | None = None,
) -> fm.FeedingEvent:
    """Undoes [event] (its stock returns to its lots) and, when [body]
    names what was really fed, records that in its place — same subject,
    time and kind of feeding. Returns the replacement, or the reversal."""
    reason = (body.reason or "").strip()
    if not reason:
        raise ApprovalError("Say why the feeding is corrected.")
    ensure_stock_override(access, allow_negative, "feed_nutrition")
    asked = NameCache().get(requested_by) if requested_by else None
    reversal = programs.reverse_feeding(
        db,
        access,
        event,
        reason=reason + (f" (asked by {asked}, approved by {access.display_name})" if asked else ""),
    )
    result = reversal
    subject = programs.subject_row(db, event.subject_type, event.subject_id)
    if body.components:
        if subject is None or subject.deleted_at is not None:
            raise ApprovalError(f"{event.subject_type.capitalize()} not found", 404)
        result = programs.record_feeding(
            db,
            access,
            subject_type=event.subject_type,
            subject=subject,
            components=[c.model_dump() for c in body.components],
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            head_count=event.head_count,
            notes=f"Correction of {event.id}: {reason}",
            allow_negative=allow_negative,
        )
        if requested_by is not None:
            # The replacement is the employee's record, approved by the manager.
            result.recorded_by = requested_by
    if subject is not None:
        perf.on_feeding_recorded(db, access, event.subject_type, subject)
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action="feeding_event.corrected",
        entity_type=event.subject_type,
        entity_id=str(event.subject_id),
        module_code="feed_nutrition",
        summary=f"Corrected feeding {event.id}: {reason}"[:500],
        changes={
            "feeding_event_id": str(event.id),
            "reversal_id": str(reversal.id),
            "replacement_id": str(result.id) if result is not reversal else None,
            "reason": reason,
            "requested_by": str(requested_by) if requested_by else None,
            "approval_request_id": str(approval_request_id) if approval_request_id else None,
        },
    )
    return result


# ------------------------------------------------------------------- output
def to_dict(req: ApprovalRequest, names: NameCache) -> dict:
    requested_at = ensure_utc(req.requested_at)
    decided_at = ensure_utc(req.decided_at)
    return {
        "id": str(req.id),
        "kind": req.kind,
        "module_code": req.module_code,
        "status": req.status,
        "summary": req.summary,
        "subject_type": req.subject_type,
        "subject_id": str(req.subject_id) if req.subject_id else None,
        "target_id": str(req.target_id) if req.target_id else None,
        "payload": req.payload,
        "reason": req.reason,
        "requested_by": str(req.requested_by),
        "requested_by_name": names.get(req.requested_by),
        "requested_at": requested_at.isoformat() if requested_at else None,
        "decided_by": str(req.decided_by) if req.decided_by else None,
        "decided_by_name": names.get(req.decided_by),
        "decided_at": decided_at.isoformat() if decided_at else None,
        "decision_note": req.decision_note,
        "result_type": req.result_type,
        "result_id": str(req.result_id) if req.result_id else None,
    }
