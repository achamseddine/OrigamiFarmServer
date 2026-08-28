"""GET /recommendations (re-evaluates rules against current farm state by
default, then returns what's stored) and PATCH .../decision — accept,
reject, or postpone. Tech spec §15 lifecycle: Generated -> Reviewed ->
Accepted/Rejected/Postponed -> ... Every decision is itself an event, so
the trail is never lost.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos.deps import AccessContext, check_farm_id, get_farmos_tenant_db, require_permission
from app.farmos.finance_models import Recommendation
from app.farmos.recommendations import refresh_recommendations
from app.farmos.schemas import EvidenceItem, RecommendationDecision, RecommendationOut

router = APIRouter()

_DECISION_TO_STATUS = {"accept": "accepted", "reject": "rejected", "postpone": "postponed"}


def _to_recommendation_out(r: Recommendation, tenant_id: uuid.UUID) -> RecommendationOut:
    return RecommendationOut(
        id=str(r.id),
        farm_id=str(tenant_id),
        category=r.category,
        priority=r.priority,
        title=r.title,
        entity_type=r.entity_type,
        entity_id=r.entity_id,
        entity_label=r.entity_label,
        confidence=float(r.confidence),
        rationale=r.rationale,
        suggested_action=r.suggested_action,
        status=r.status,
        rule_id=r.rule_id,
        generated_at=r.generated_at,
        evidence=[EvidenceItem(**item) for item in (r.evidence or [])],
    )


@router.get("/recommendations", response_model=list[RecommendationOut])
def list_recommendations(
    farm_id: str = Query(...),
    category: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status_filter"),
    refresh: bool = Query(default=True),
    access: AccessContext = Depends(require_permission("ai_intelligence", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[RecommendationOut]:
    check_farm_id(farm_id, access)
    if refresh:
        refresh_recommendations(db, access.tenant_id)

    stmt = select(Recommendation).where(Recommendation.deleted_at.is_(None))
    if category:
        stmt = stmt.where(Recommendation.category == category)
    if status_filter:
        stmt = stmt.where(Recommendation.status == status_filter)
    rows = db.execute(stmt.order_by(Recommendation.generated_at.desc())).scalars().all()
    return [_to_recommendation_out(row, access.tenant_id) for row in rows]


@router.patch("/recommendations/{recommendation_id}/decision", response_model=RecommendationOut)
def decide_recommendation(
    recommendation_id: str,
    payload: RecommendationDecision,
    access: AccessContext = Depends(require_permission("ai_intelligence", "approve")),
    db: Session = Depends(get_farmos_tenant_db),
) -> RecommendationOut:
    try:
        pk = uuid.UUID(recommendation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Recommendation not found.") from exc
    recommendation = db.get(Recommendation, pk)
    if recommendation is None or recommendation.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Recommendation not found.")

    new_status = _DECISION_TO_STATUS.get(payload.decision)
    if new_status is None:
        raise HTTPException(
            status_code=422, detail="Decision must be one of: accept, reject, postpone."
        )

    recommendation.status = new_status
    recommendation.decided_by = uuid.UUID(payload.decided_by)
    recommendation.decided_at = datetime.now(timezone.utc)
    recommendation.decision_note = payload.note
    recommendation.version += 1
    recommendation.last_modified_by = access.membership_id
    db.flush()
    return _to_recommendation_out(recommendation, access.tenant_id)
