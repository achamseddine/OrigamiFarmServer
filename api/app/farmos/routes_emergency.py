"""Emergency protocol API (OrigamiFarmOS
database/CLINICAL-DECISION-SUPPORT-EMERGENCY-PROTOCOLS.md).

Anyone with animal-health view reads; anyone with animal-health create
can assess, start an approved protocol, prepare and confirm its steps,
reassess and escalate — that is the point of care. Drafting protocols
needs animal-health configure (or edit). Approving a version is clinical:
a veterinarian only, with a reference.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import emergency_models as em
from app.farmos import emergency_service as es
from app.farmos.deps import (
    AccessContext,
    get_farmos_tenant_db,
    require_any_permission,
    require_diagnostic_role,
    require_permission,
)
from app.farmos.emergency_schemas import (
    ApproveVersionIn,
    AssessmentIn,
    ConfirmStepIn,
    EscalateIn,
    OfflineRunIn,
    PrepareStepIn,
    ProtocolIn,
    ProtocolVersionIn,
    ReassessIn,
    ResolveRunIn,
    SkipStepIn,
    StartRunIn,
    WithdrawVersionIn,
)
from app.farmos.feed_catalog import FeedError
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.routing_service import NameCache

router = APIRouter(prefix="/emergency")

_view = require_permission("animal_health", "view")
_act = require_permission("animal_health", "create")
_configure = require_any_permission(("animal_health", "configure"), ("animal_health", "edit"))


def _audit(
    control_db: Session,
    access: AccessContext,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
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
        entity_type=entity_type,
        entity_id=entity_id,
        module_code="animal_health",
        summary=summary,
        changes=changes,
    )


def _keep_escalation(db: Session, call: Callable[[], Any]) -> Any:
    """An engine refusal that escalated first (a withdrawn version, a
    blocked dose) must keep that escalation: commit what was written,
    then answer with the refusal. The tenant session would otherwise roll
    the escalation back with the failed request."""
    try:
        return call()
    except FeedError:
        db.commit()
        raise


@router.get("/summary")
def emergency_summary(
    access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return es.summary(db)


@router.get("/sign-codes")
def list_sign_codes(
    access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    """The sign vocabulary the tablet offers: the standard list plus any
    code a current approved protocol triggers on."""
    return es.sign_codes(db)


@router.get("/offline-package")
def offline_package(
    access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    """Only offline-eligible approved versions, signed by content, with the
    medicine mapping and an eligible-stock snapshot (§14)."""
    return {**es.offline_package(db, NameCache()), "farm_id": str(access.tenant_id)}


@router.post("/offline-runs", status_code=201)
def record_offline_run(
    payload: OfflineRunIn,
    access: AccessContext = Depends(_act),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Sync of an emergency executed on the tablet from the offline
    package (§14): revalidated against the current rules, recorded as it
    happened, every difference listed and escalated — never silently
    changed. Idempotent on `operation_id`."""
    body = payload.model_dump(mode="json")
    out = _keep_escalation(db, lambda: es.record_offline_run(db, access, body, NameCache()))
    if not out["replayed"]:
        _audit(
            control_db,
            access,
            action="emergency_offline_run.synced",
            entity_type="emergency_assessment",
            entity_id=out["assessment"]["id"],
            summary=f"Offline emergency {payload.operation_id} from device "
            f"{payload.device_id or '?'} revalidated: "
            f"{len(out['discrepancies'])} discrepancy(ies)",
            changes={
                "operation_id": payload.operation_id,
                "device_id": payload.device_id,
                "run_id": out["run"]["id"] if out["run"] else None,
                "discrepancies": out["discrepancies"],
            },
        )
    return out


@router.post("/reassessments/check")
def check_reassessments(
    access: AccessContext = Depends(_act),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """The reassessment check the scheduler runs: one task per overdue
    run, to the person who started it."""
    out = es.check_overdue_reassessments(db, access)
    if out["tasks_opened"]:
        _audit(
            control_db,
            access,
            action="emergency_reassessments.checked",
            entity_type="farm",
            entity_id=str(access.tenant_id),
            summary=f"{out['overdue']} overdue reassessment(s); "
            f"{out['tasks_opened']} reminder task(s) opened",
        )
    return out


# -------------------------------------------------------------- protocols
@router.get("/protocols")
def list_protocols(
    include_withdrawn: bool = False,
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(em.EmergencyProtocol).where(em.EmergencyProtocol.deleted_at.is_(None))
    if not include_withdrawn:
        stmt = stmt.where(em.EmergencyProtocol.status != "withdrawn")
    names = NameCache()
    return [es.protocol_dict(db, p, names) for p in db.scalars(stmt.order_by(em.EmergencyProtocol.title))]


@router.post("/protocols", status_code=201)
def create_protocol(
    payload: ProtocolIn,
    access: AccessContext = Depends(_configure),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """A protocol with its first draft version. Nothing is operational
    until a veterinarian approves the version."""
    p = es.create_protocol(
        db,
        access,
        code=payload.code,
        title=payload.title,
        condition_family_code=payload.condition_family_code,
        species_code=payload.species_code,
        subject_scope=payload.subject_scope,
        version=payload.version.model_dump(),
    )
    _audit(
        control_db,
        access,
        action="emergency_protocol.created",
        entity_type="emergency_protocol",
        entity_id=str(p.id),
        summary=f"Drafted emergency protocol {p.code} — {p.title} (v1, {len(payload.version.steps)} steps)",
        changes={"code": p.code, "species_code": p.species_code, "subject_scope": p.subject_scope},
    )
    return es.protocol_dict(db, p, NameCache())


@router.get("/protocols/{protocol_id}")
def get_protocol(
    protocol_id: str, access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return es.protocol_dict(db, es.get_protocol(db, protocol_id), NameCache())


@router.post("/protocols/{protocol_id}/versions", status_code=201)
def add_version(
    protocol_id: str,
    payload: ProtocolVersionIn,
    access: AccessContext = Depends(_configure),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    p = es.get_protocol(db, protocol_id)
    v = es.add_version(db, access, p, **payload.model_dump())
    _audit(
        control_db,
        access,
        action="emergency_protocol_version.drafted",
        entity_type="emergency_protocol",
        entity_id=str(p.id),
        summary=f"Drafted {p.code} v{v.version_no} ({len(payload.steps)} steps)",
        changes={"version_id": str(v.id), "version_no": v.version_no},
    )
    return es.version_dict(db, v, NameCache())


def _version_of(db: Session, protocol_id: str, version_id: str) -> em.EmergencyProtocolVersion:
    v = es.get_version(db, version_id)
    if v.emergency_protocol_id != parse_uuid(protocol_id):
        raise es.EmergencyError("Version does not belong to this protocol", 404)
    return v


@router.post("/protocols/{protocol_id}/versions/{version_id}/approve")
def approve_version(
    protocol_id: str,
    version_id: str,
    payload: ApproveVersionIn,
    access: AccessContext = Depends(require_diagnostic_role),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Veterinary approval (§3). The service refuses anyone but a
    veterinarian; the approval is written once."""
    v = _version_of(db, protocol_id, version_id)
    es.approve_version(
        db, access, v, approval_reference=payload.approval_reference, effective_from=payload.effective_from
    )
    _audit(
        control_db,
        access,
        action="emergency_protocol_version.approved",
        entity_type="emergency_protocol",
        entity_id=str(v.emergency_protocol_id),
        summary=f"Approved {v.protocol.code} v{v.version_no} (ref {v.approval_reference})",
        changes={
            "version_id": str(v.id),
            "version_no": v.version_no,
            "approval_reference": v.approval_reference,
        },
    )
    return es.protocol_dict(db, v.protocol, NameCache())


@router.post("/protocols/{protocol_id}/versions/{version_id}/withdraw")
def withdraw_version(
    protocol_id: str,
    version_id: str,
    payload: WithdrawVersionIn,
    access: AccessContext = Depends(_configure),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    v = _version_of(db, protocol_id, version_id)
    es.withdraw_version(db, access, v, reason=payload.reason)
    _audit(
        control_db,
        access,
        action="emergency_protocol_version.withdrawn",
        entity_type="emergency_protocol",
        entity_id=str(v.emergency_protocol_id),
        summary=f"Withdrew {v.protocol.code} v{v.version_no}: {payload.reason}",
        changes={"version_id": str(v.id), "reason": payload.reason},
    )
    return es.protocol_dict(db, v.protocol, NameCache())


# ------------------------------------------------------------ assessments
@router.get("/assessments")
def list_assessments(
    assessment_status: str | None = Query(None, alias="status"),
    subject_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(em.EmergencyAssessment).where(em.EmergencyAssessment.deleted_at.is_(None))
    if assessment_status == "active":
        stmt = stmt.where(em.EmergencyAssessment.status.in_(("open", "protocol_started", "escalated")))
    elif assessment_status:
        stmt = stmt.where(em.EmergencyAssessment.status == assessment_status)
    if subject_id:
        pk = parse_uuid(subject_id)
        if pk is None:
            return []
        stmt = stmt.where(
            (em.EmergencyAssessment.animal_id == pk) | (em.EmergencyAssessment.animal_group_id == pk)
        )
    names = NameCache()
    rows = db.scalars(stmt.order_by(em.EmergencyAssessment.started_at.desc()).limit(limit))
    return [es.assessment_dict(db, a, names) for a in rows]


@router.post("/assessments", status_code=201)
def create_assessment(
    payload: AssessmentIn,
    access: AccessContext = Depends(_act),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Triage from the worker's signs (§12): matches current approved
    protocols, checks the animal's facts, ranks urgency, selects or
    escalates — and explains itself."""
    a = es.assess(
        db,
        access,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
        signs=[s.model_dump() for s in payload.signs],
        source=payload.source,
        notes=payload.notes,
    )
    out = es.assessment_dict(db, a, NameCache())
    _audit(
        control_db,
        access,
        action="emergency_assessment.created",
        entity_type="emergency_assessment",
        entity_id=str(a.id),
        summary=f"Emergency triage of {payload.subject_type} {out['subject_name'] or payload.subject_id}: "
        f"{a.triage_level}, {a.status}",
        changes={
            "signs": sorted(s["code"] for s in a.observed_signs),
            "triage": a.triage_level,
            "status": a.status,
            "selected_match_id": out["selected_match_id"],
            "escalation_reasons": a.escalation_reasons,
        },
    )
    return out


@router.get("/assessments/{assessment_id}")
def get_assessment(
    assessment_id: str, access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return es.assessment_dict(db, es.get_assessment(db, assessment_id), NameCache())


@router.post("/assessments/{assessment_id}/start", status_code=201)
def start_run(
    assessment_id: str,
    payload: StartRunIn | None = None,
    access: AccessContext = Depends(_act),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Starts the matched approved protocol after the person confirms;
    notifies the manager / veterinarian as the version requires."""
    a = es.get_assessment(db, assessment_id)
    run = _keep_escalation(
        db,
        lambda: es.start_run(
            db,
            access,
            a,
            match_id=payload.match_id if payload else None,
            confirmed=payload.confirmed if payload else True,
        ),
    )
    out = es.run_dict(db, run, NameCache())
    _audit(
        control_db,
        access,
        action="emergency_run.started",
        entity_type="emergency_protocol_run",
        entity_id=str(run.id),
        summary=f"Started {out['protocol_code']} v{out['version_no']} "
        f"for {out['subject_name'] or out['subject_id']}",
        changes={
            "assessment_id": str(a.id),
            "health_case_id": out["health_case_id"],
            "steps": len(out["steps"]),
        },
    )
    return out


@router.post("/assessments/{assessment_id}/close")
def close_assessment(
    assessment_id: str,
    payload: ResolveRunIn,
    access: AccessContext = Depends(_act),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    a = es.close_assessment(db, access, es.get_assessment(db, assessment_id), outcome=payload.outcome)
    _audit(
        control_db,
        access,
        action="emergency_assessment.closed",
        entity_type="emergency_assessment",
        entity_id=str(a.id),
        summary=f"Closed emergency assessment: {payload.outcome}",
        changes={"outcome": payload.outcome},
    )
    return es.assessment_dict(db, a, NameCache())


# ------------------------------------------------------------------- runs
@router.get("/runs")
def list_runs(
    run_status: str | None = Query(None, alias="status"),
    subject_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(em.EmergencyProtocolRun).where(em.EmergencyProtocolRun.deleted_at.is_(None))
    if run_status == "active":
        stmt = stmt.where(em.EmergencyProtocolRun.status.in_(("in_progress", "awaiting_reassessment")))
    elif run_status:
        stmt = stmt.where(em.EmergencyProtocolRun.status == run_status)
    if subject_id:
        pk = parse_uuid(subject_id)
        if pk is None:
            return []
        stmt = stmt.where(
            (em.EmergencyProtocolRun.animal_id == pk) | (em.EmergencyProtocolRun.animal_group_id == pk)
        )
    names = NameCache()
    rows = db.scalars(stmt.order_by(em.EmergencyProtocolRun.started_at.desc()).limit(limit))
    return [es.run_dict(db, r, names) for r in rows]


@router.get("/runs/{run_id}")
def get_run(
    run_id: str, access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return es.run_dict(db, es.get_run(db, run_id), NameCache())


@router.post("/runs/{run_id}/steps/{step_id}/prepare")
def prepare_step(
    run_id: str,
    step_id: str,
    payload: PrepareStepIn | None = None,
    access: AccessContext = Depends(_act),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Presents the step. A medication step shows the approved dose from
    the approved rule and the exact eligible lot, or blocks and escalates."""
    run = es.get_run(db, run_id)
    rs = es.prepare_step(
        db,
        access,
        run,
        step_id,
        weight_kg=payload.weight_kg if payload else None,
        head_count=payload.head_count if payload else None,
    )
    _audit(
        control_db,
        access,
        action="emergency_step.prepared",
        entity_type="emergency_protocol_run",
        entity_id=str(run.id),
        summary=f"Step {rs.step.step_no} ({rs.step.step_type}) {rs.status}"
        + (": " + "; ".join(rs.result_payload.get("problems", [])) if rs.status == "blocked" else ""),
        changes={
            "run_step_id": str(rs.id),
            "status": rs.status,
            "dose_quantity": rs.result_payload.get("dose_quantity"),
            "lot_code": rs.result_payload.get("lot_code"),
            "weight_used_kg": rs.result_payload.get("weight_used_kg"),
        },
    )
    return es.run_dict(db, run, NameCache())


@router.post("/runs/{run_id}/steps/{step_id}/confirm")
def confirm_step(
    run_id: str,
    step_id: str,
    payload: ConfirmStepIn | None = None,
    access: AccessContext = Depends(_act),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """The person confirms the actual action; a medication step consumes
    the exact lot through the pharmacy and records the prescription's
    provenance."""
    run = es.get_run(db, run_id)
    p = payload or ConfirmStepIn()
    rs = _keep_escalation(
        db,
        lambda: es.confirm_step(
            db,
            access,
            run,
            step_id,
            weight_kg=p.weight_kg,
            head_count=p.head_count,
            lot_id=p.lot_id,
            result=p.result,
            notes=p.notes,
        ),
    )
    _audit(
        control_db,
        access,
        action="emergency_step.confirmed",
        entity_type="emergency_protocol_run",
        entity_id=str(run.id),
        summary=f"Confirmed step {rs.step.step_no} ({rs.step.step_type}); run {run.status}",
        changes={
            "run_step_id": str(rs.id),
            "administration_id": str(rs.medication_administration_id)
            if rs.medication_administration_id
            else None,
            "run_status": run.status,
        },
    )
    return es.run_dict(db, run, NameCache())


@router.post("/runs/{run_id}/steps/{step_id}/skip")
def skip_step(
    run_id: str,
    step_id: str,
    payload: SkipStepIn,
    access: AccessContext = Depends(_act),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    run = es.get_run(db, run_id)
    rs = es.skip_step(db, access, run, step_id, reason=payload.reason)
    _audit(
        control_db,
        access,
        action="emergency_step.skipped",
        entity_type="emergency_protocol_run",
        entity_id=str(run.id),
        summary=f"Skipped step {rs.step.step_no}: {payload.reason}",
        changes={"run_step_id": str(rs.id), "reason": payload.reason},
    )
    return es.run_dict(db, run, NameCache())


@router.post("/runs/{run_id}/reassess", status_code=201)
def reassess(
    run_id: str,
    payload: ReassessIn,
    access: AccessContext = Depends(_act),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    run = es.get_run(db, run_id)
    follow, run = es.reassess_run(
        db, access, run, signs=[s.model_dump() for s in payload.signs], notes=payload.notes
    )
    _audit(
        control_db,
        access,
        action="emergency_run.reassessed",
        entity_type="emergency_protocol_run",
        entity_id=str(run.id),
        summary=f"Reassessed: run {run.status}"
        + (f" ({run.escalation_reason})" if run.status == "escalated" else ""),
        changes={"assessment_id": str(follow.id), "run_status": run.status},
    )
    names = NameCache()
    return {"assessment": es.assessment_dict(db, follow, names), "run": es.run_dict(db, run, names)}


@router.post("/runs/{run_id}/escalate")
def escalate(
    run_id: str,
    payload: EscalateIn,
    access: AccessContext = Depends(_act),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    run = es.escalate_run(db, access, es.get_run(db, run_id), reason=payload.reason)
    _audit(
        control_db,
        access,
        action="emergency_run.escalated",
        entity_type="emergency_protocol_run",
        entity_id=str(run.id),
        summary=f"Escalated: {payload.reason}",
        changes={"reason": payload.reason},
    )
    return es.run_dict(db, run, NameCache())


@router.post("/runs/{run_id}/resolve")
def resolve(
    run_id: str,
    payload: ResolveRunIn,
    access: AccessContext = Depends(_act),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    run = es.resolve_run(db, access, es.get_run(db, run_id), outcome=payload.outcome)
    _audit(
        control_db,
        access,
        action="emergency_run.resolved",
        entity_type="emergency_protocol_run",
        entity_id=str(run.id),
        summary=f"Resolved: {payload.outcome}",
        changes={"outcome": payload.outcome},
    )
    return es.run_dict(db, run, NameCache())
