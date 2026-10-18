"""The generic feed architecture's API (OrigamiFarmOS docs/GENERIC-FEED-ARCHITECTURE.md
§15, §31): products and lots, formulas and numbered mixes, nutrient
profiles, feeding programs and the resolver, assignments and feeding
events, usage policy, allocations, days of cover / forecast / reorder,
reconciliation and costs.

Every write goes through the services, which refuse with a sentence a
farmer can act on (`FeedError` is an HTTPException). People's names are
resolved here from the control database, since the tenant session cannot
see it.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType, MembershipStatus
from app.farmos import feed_batch_service as batches
from app.farmos import feed_catalog as catalog
from app.farmos import feed_forecast_service as forecast
from app.farmos import feed_inventory_service as inv
from app.farmos import feed_models as fm
from app.farmos import feed_policy_service as policy
from app.farmos import feeding_program_service as programs
from app.farmos import livestock_service
from app.farmos.deps import AccessContext, get_farmos_tenant_db, require_permission
from app.farmos.feed_catalog import FeedError
from app.farmos.feed_schemas import (
    AdjustmentCreate,
    AllocationCreate,
    AllocationOut,
    AllocationTransfer,
    AssignmentCreate,
    AssignmentOut,
    BatchComplete,
    BatchOut,
    BatchQuarantine,
    BatchStart,
    FeedAvailabilityOut,
    FeedingEventCreate,
    FeedingEventOut,
    FeedingEventReverse,
    FeedLotOut,
    FeedProductCreate,
    FeedProductOut,
    FeedProductUpdate,
    FormulaCreate,
    FormulaOut,
    FormulaVersionCreate,
    FormulaVersionOut,
    LotReceive,
    LotStatusUpdate,
    MixSettingsOut,
    MixSettingsUpdate,
    NutrientOut,
    NutrientProfileCreate,
    NutrientProfileOut,
    PolicyDecisionOut,
    PolicyOut,
    PolicySet,
    PolicyValidate,
    ProgramCreate,
    ProgramOut,
    ProgramResolveRequest,
    ProgramVersionCreate,
    ProgramVersionOut,
    ReconciliationClose,
    ReconciliationCreate,
    ReconciliationOut,
    ReorderAcknowledge,
    ReorderPolicyOut,
    ReorderPolicyUpsert,
)
from app.farmos.livestock_service import now_utc
from app.tenant_api.models import InventoryItem
from app.tenants.models import TenantMembership

router = APIRouter()

_view = require_permission("feed_nutrition", "view")
_create = require_permission("feed_nutrition", "create")
_edit = require_permission("feed_nutrition", "edit")
_approve = require_permission("feed_nutrition", "approve")
_configure = require_permission("feed_nutrition", "configure")


# ------------------------------------------------------------------ helpers
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
        module_code="feed_nutrition",
        summary=summary,
        changes=changes,
    )


def _check_operator(control_db: Session, access: AccessContext, operator_id: str | None) -> None:
    """§20: the operator is a user of this farm, never free text."""
    if not operator_id:
        return
    pk = inv.parse_uuid(operator_id)
    membership = (
        control_db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == access.tenant_id,
                TenantMembership.user_id == pk,
                TenantMembership.status == MembershipStatus.ACTIVE,
            )
        ).scalar_one_or_none()
        if pk
        else None
    )
    if membership is None:
        raise FeedError("Operator not found on this farm.", 404)


def _s(value: uuid.UUID | None) -> str | None:
    return str(value) if value else None


def _name(db: Session, product_id: uuid.UUID | None) -> str | None:
    if not product_id:
        return None
    p = db.get(fm.FeedProduct, product_id)
    return p.name if p else None


# ------------------------------------------------------------ serialisers
def _lot_out(lot: fm.FeedLot) -> dict:
    inv.refresh_lot_status(lot)
    return {
        "id": str(lot.id),
        "feed_product_id": str(lot.feed_product_id),
        "lot_code": lot.lot_code,
        "source_type": lot.source_type,
        "supplier_id": _s(lot.supplier_id),
        "supplier_label": lot.supplier_label,
        "feed_batch_id": _s(lot.feed_batch_id),
        "received_at": lot.received_at,
        "expiry_date": lot.expiry_date,
        "ordered_quantity": lot.ordered_quantity,
        "received_quantity": lot.received_quantity,
        "accepted_quantity": lot.accepted_quantity,
        "rejected_quantity": lot.rejected_quantity,
        "quantity_on_hand": round(lot.quantity_on_hand, 3),
        "unit": lot.unit,
        "unit_cost": lot.unit_cost,
        "status": lot.status,
        "reference": lot.reference,
        "notes": lot.notes,
    }


def _product_out(db: Session, product: fm.FeedProduct) -> dict:
    avail = inv.availability(db, product)
    has_formula = (
        db.scalar(
            select(fm.FeedFormulaVersion.id)
            .join(fm.FeedFormula)
            .where(
                fm.FeedFormula.feed_product_id == product.id,
                fm.FeedFormula.status == "active",
                fm.FeedFormulaVersion.status == "active",
            )
        )
        is not None
    )
    return {
        "id": str(product.id),
        "farm_id": str(product.tenant_id),
        "code": product.code,
        "name": product.name,
        "name_ar": product.name_ar,
        "source_type": product.source_type,
        "is_ingredient": product.is_ingredient,
        "is_feedable": product.is_feedable,
        "category": product.category,
        "unit": product.unit,
        "inventory_item_id": str(product.inventory_item_id),
        "default_unit_cost": product.default_unit_cost,
        "status": product.status,
        "notes": product.notes,
        "availability": avail.to_dict(),
        "policy_names": [p.name for p in policy.policies_for(db, product)],
        "has_active_formula": has_formula,
    }


def _version_out(db: Session, v: fm.FeedFormulaVersion) -> dict:
    return {
        "id": str(v.id),
        "formula_id": str(v.formula_id),
        "version": v.version_number,
        "status": v.status,
        "batch_size": v.batch_size,
        "unit": v.unit,
        "effective_from": v.effective_from,
        "effective_to": v.effective_to,
        "locked": v.locked,
        "notes": v.notes,
        "created_at": v.created_at,
        "components": [
            {
                "id": str(c.id),
                "feed_product_id": str(c.feed_product_id),
                "product_name": _name(db, c.feed_product_id),
                "target_quantity": c.target_quantity,
                "unit": c.unit,
                "target_percentage": c.target_percentage,
                "sort_order": c.sort_order,
            }
            for c in v.components
        ],
        "planned_cost": batches.planned_cost(db, v, v.batch_size),
    }


def _formula_out(db: Session, f: fm.FeedFormula) -> dict:
    active = next((v.version_number for v in f.versions if v.status == "active"), None)
    return {
        "id": str(f.id),
        "farm_id": str(f.tenant_id),
        "code": f.code,
        "name": f.name,
        "feed_product_id": str(f.feed_product_id),
        "product_name": _name(db, f.feed_product_id),
        "species_code": f.species_code,
        "status": f.status,
        "description": f.description,
        "active_version": active,
        "versions": [_version_out(db, v) for v in f.versions],
    }


def _batch_out(db: Session, control_db: Session, b: fm.FeedBatch) -> dict:
    version = db.get(fm.FeedFormulaVersion, b.formula_version_id) if b.formula_version_id else None
    comps = []
    for c in b.components:
        lot = db.get(fm.FeedLot, c.lot_id) if c.lot_id else None
        comps.append(
            {
                "id": str(c.id),
                "feed_product_id": str(c.feed_product_id),
                "product_name": _name(db, c.feed_product_id),
                "lot_id": _s(c.lot_id),
                "lot_code": lot.lot_code if lot else None,
                "target_quantity": c.target_quantity,
                "actual_quantity": c.actual_quantity,
                "unit": c.unit,
                "unit_cost": c.unit_cost,
                "cost": c.cost,
            }
        )
    out_lot = db.get(fm.FeedLot, b.output_lot_id) if b.output_lot_id else None
    operator = b.operator_id or b.mixed_by
    names = livestock_service.user_names(control_db, [operator])
    return {
        "id": str(b.id),
        "farm_id": str(b.tenant_id),
        "feed_product_id": str(b.feed_product_id),
        "product_name": _name(db, b.feed_product_id),
        "formula_version_id": _s(b.formula_version_id),
        "formula_code": version.formula.code if version else None,
        "formula_version": version.version_number if version else None,
        "batch_code": b.batch_code,
        "mix_number": b.mix_number,
        "mix_code": b.mix_code,
        "intended_species_code": b.intended_species_code,
        "intended_management_profile": b.intended_management_profile,
        "production_date": b.production_date,
        "use_by_date": b.use_by_date,
        "operator_id": _s(operator),
        "operator_name": names.get(operator) if operator else None,
        "mixer_asset_id": _s(b.mixer_asset_id),
        "mixer_asset_name": None,
        "status": b.status,
        "target_quantity": b.target_quantity,
        "actual_quantity": b.actual_quantity,
        "unit": b.unit,
        "planned_cost": b.planned_cost,
        "actual_cost": b.actual_cost,
        "unit_cost": b.unit_cost,
        "output_lot_id": _s(b.output_lot_id),
        "output_lot_code": out_lot.lot_code if out_lot else None,
        "started_at": b.started_at,
        "produced_at": b.produced_at,
        "mixed_by": _s(b.mixed_by),
        "notes": b.notes,
        "components": comps,
        "variance": batches.batch_variance(b) if b.status in ("completed", "quarantined") else [],
    }


def _program_version_out(db: Session, v: fm.FeedingProgramVersion) -> dict:
    return {
        "id": str(v.id),
        "program_id": str(v.program_id),
        "version": v.version_number,
        "status": v.status,
        "feedings_per_day": v.feedings_per_day,
        "effective_from": v.effective_from,
        "effective_to": v.effective_to,
        "locked": v.locked,
        "notes": v.notes,
        "components": [
            {
                "id": str(c.id),
                "feed_product_id": str(c.feed_product_id),
                "product_name": _name(db, c.feed_product_id),
                "quantity_per_head": c.quantity_per_head,
                "unit": c.unit,
                "frequency": c.frequency,
                "timing": c.timing,
                "notes": c.notes,
                "daily_per_head": c.quantity_per_head
                * (v.feedings_per_day if c.frequency == "per_feeding" else 1),
            }
            for c in v.components
        ],
        "rules": [
            {
                "id": str(r.id),
                "species_code": r.species_code,
                "sex": r.sex,
                "life_stage": r.life_stage,
                "management_profile": r.management_profile,
                "reproductive_state": r.reproductive_state,
                "lactation_state": r.lactation_state,
                "production_metric": r.production_metric,
                "production_min": r.production_min,
                "production_max": r.production_max,
                "weight_min": r.weight_min,
                "weight_max": r.weight_max,
                "age_min_days": r.age_min_days,
                "age_max_days": r.age_max_days,
                "priority": r.priority,
                "notes": r.notes,
            }
            for r in v.rules
        ],
    }


def _program_out(db: Session, p: fm.FeedingProgram) -> dict:
    active = next((v.version_number for v in p.versions if v.status == "active"), None)
    version_ids = [v.id for v in p.versions]
    count = 0
    if version_ids:
        rows = db.scalars(
            select(fm.FeedingAssignment).where(
                fm.FeedingAssignment.program_version_id.in_(version_ids),
                fm.FeedingAssignment.status == "active",
            )
        ).all()
        count = len(programs._active(rows))
    return {
        "id": str(p.id),
        "farm_id": str(p.tenant_id),
        "code": p.code,
        "name": p.name,
        "name_ar": p.name_ar,
        "category": p.category,
        "status": p.status,
        "description": p.description,
        "active_version": active,
        "versions": [_program_version_out(db, v) for v in p.versions],
        "subjects_assigned": count,
    }


def _assignment_out(db: Session, a: fm.FeedingAssignment) -> dict:
    v = db.get(fm.FeedingProgramVersion, a.program_version_id) if a.program_version_id else None
    return {
        "id": str(a.id),
        "farm_id": str(a.tenant_id),
        "subject_type": a.subject_type,
        "subject_id": str(a.subject_id),
        "program_version_id": _s(a.program_version_id),
        "program_id": _s(v.program_id) if v else None,
        "program_name": v.program.name if v else None,
        "program_version": v.version_number if v else None,
        "assignment_type": a.assignment_type,
        "feed_product_id": _s(a.feed_product_id),
        "product_name": _name(db, a.feed_product_id),
        "quantity_per_head": a.quantity_per_head,
        "unit": a.unit,
        "reason": a.reason,
        "valid_from": a.valid_from,
        "valid_to": a.valid_to,
        "status": a.status,
        "created_by": _s(a.created_by),
        "created_at": a.created_at,
        "ended_at": a.ended_at,
    }


def _allocation_out(db: Session, a: fm.FeedAllocation) -> dict:
    return {
        "id": str(a.id),
        "farm_id": str(a.tenant_id),
        "feed_product_id": str(a.feed_product_id),
        "product_name": _name(db, a.feed_product_id),
        "lot_id": _s(a.lot_id),
        "species_code": a.species_code,
        "subject_type": a.subject_type,
        "subject_id": _s(a.subject_id),
        "feeding_program_id": _s(a.feeding_program_id),
        "location_label": a.location_label,
        "cost_centre": a.cost_centre,
        "purpose": a.purpose,
        "allocated_quantity": a.allocated_quantity,
        "consumed_quantity": a.consumed_quantity,
        "remaining_quantity": inv.allocation_remaining(a),
        "unit": a.unit,
        "transferable": a.transferable,
        "status": a.status,
        "created_by": _s(a.created_by),
        "created_at": a.created_at,
        "released_at": a.released_at,
    }


def _policy_out(p: fm.FeedUsagePolicy) -> dict:
    return {
        "id": str(p.id),
        "feed_product_id": _s(p.feed_product_id),
        "lot_id": _s(p.lot_id),
        "name": p.name,
        "requires_approved_formula": p.requires_approved_formula,
        "cross_species_transfer_allowed": p.cross_species_transfer_allowed,
        "status": p.status,
        "effective_from": p.effective_from,
        "effective_to": p.effective_to,
        "notes": p.notes,
        "rules": [
            {
                "effect": r.effect,
                "species_code": r.species_code,
                "management_profile": r.management_profile,
                "life_stage": r.life_stage,
                "reproductive_state": r.reproductive_state,
                "max_inclusion_pct": r.max_inclusion_pct,
                "min_age_days": r.min_age_days,
                "max_age_days": r.max_age_days,
                "reason": r.reason,
            }
            for r in p.rules
        ],
    }


def _reorder_policy_out(p: fm.FeedReorderPolicy) -> dict:
    return {
        "id": str(p.id),
        "farm_id": str(p.tenant_id),
        "feed_product_id": str(p.feed_product_id),
        "location_label": p.location_label,
        "minimum_stock": p.minimum_stock,
        "reorder_point": p.reorder_point,
        "safety_stock": p.safety_stock,
        "preferred_reorder_quantity": p.preferred_reorder_quantity,
        "maximum_stock": p.maximum_stock,
        "lead_time_days": p.lead_time_days,
        "cover_margin_days": p.cover_margin_days,
        "variance_threshold_pct": p.variance_threshold_pct,
        "active": p.active,
        "unit": p.unit,
    }


def _reconciliation_out(db: Session, r: fm.FeedReconciliation) -> dict:
    return {
        "id": str(r.id),
        "farm_id": str(r.tenant_id),
        "feed_product_id": str(r.feed_product_id),
        "product_name": _name(db, r.feed_product_id),
        "lot_id": _s(r.lot_id),
        "period_from": r.period_from,
        "period_to": r.period_to,
        "opening_quantity": r.opening_quantity,
        "received_quantity": r.received_quantity,
        "issued_to_batches": r.issued_to_batches,
        "issued_to_feeding": r.issued_to_feeding,
        "other_issued_quantity": r.other_issued_quantity,
        "waste_quantity": r.waste_quantity,
        "returned_quantity": r.returned_quantity,
        "adjustment_quantity": r.adjustment_quantity,
        "expected_closing_quantity": r.expected_closing_quantity,
        "counted_closing_quantity": r.counted_closing_quantity,
        "variance_quantity": r.variance_quantity,
        "variance_pct": r.variance_pct,
        "unit": r.unit,
        "status": r.status,
        "explanation": r.explanation,
        "created_at": r.created_at,
        "closed_at": r.closed_at,
    }


def _usage_with_names(db: Session, control_db: Session, batch: fm.FeedBatch) -> dict:
    usage = batches.mix_usage(db, batch)
    ids = [inv.parse_uuid(usage["operator_id"])] + [inv.parse_uuid(i["recorded_by"]) for i in usage["issues"]]
    names = livestock_service.user_names(control_db, ids)
    op = inv.parse_uuid(usage["operator_id"])
    usage["operator_name"] = names.get(op) if op else None
    for issue in usage["issues"]:
        rb = inv.parse_uuid(issue["recorded_by"])
        issue["recorded_by_name"] = names.get(rb) if rb else None
    return usage


# --------------------------------------------------------------- products
@router.get("/feed-products", response_model=list[FeedProductOut])
def list_feed_products(
    include_inactive: bool = False,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(fm.FeedProduct).where(fm.FeedProduct.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(fm.FeedProduct.status == "active")
    rows = list(db.scalars(stmt.order_by(fm.FeedProduct.name)))
    return [_product_out(db, p) for p in rows]


@router.post("/feed-products", response_model=FeedProductOut, status_code=201)
def create_feed_product(
    payload: FeedProductCreate,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    product = inv.create_product(
        db, access.tenant_id, **payload.model_dump(), actor=access.membership_id, actor_user=access.user_id
    )
    _audit(
        control_db,
        access,
        action="feed_product.created",
        entity_type="feed_product",
        entity_id=str(product.id),
        summary=f"Created feed product {product.name} ({product.code})",
    )
    return _product_out(db, product)


@router.get("/feed-products/{product_id}", response_model=FeedProductOut)
def get_feed_product(
    product_id: str, _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return _product_out(db, inv.get_product(db, product_id))


@router.patch("/feed-products/{product_id}", response_model=FeedProductOut)
def update_feed_product(
    product_id: str,
    payload: FeedProductUpdate,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    product = inv.get_product(db, product_id)
    changes = payload.model_dump(exclude_unset=True)
    if "is_ingredient" in changes or "is_feedable" in changes:
        if not (
            changes.get("is_ingredient", product.is_ingredient)
            or changes.get("is_feedable", product.is_feedable)
        ):
            raise FeedError("A feed product must be an ingredient, feedable, or both.")
    audit_changes: dict[str, Any] = {}
    for k, v in changes.items():
        before = getattr(product, k)
        if before != v:
            audit_changes[k] = {"from": before, "to": v}
        setattr(product, k, v)
    if "name" in changes:
        item = db.get(InventoryItem, product.inventory_item_id)
        if item is not None:
            item.name = product.name
    product.version += 1
    product.last_modified_by = access.membership_id
    db.flush()
    if audit_changes:
        _audit(
            control_db,
            access,
            action="feed_product.updated",
            entity_type="feed_product",
            entity_id=str(product.id),
            summary=f"Updated feed product {product.name}",
            changes=audit_changes,
        )
    return _product_out(db, product)


@router.get("/feed-products/{product_id}/usage-policy", response_model=list[PolicyOut])
def get_usage_policy(
    product_id: str, _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    product = inv.get_product(db, product_id)
    return [_policy_out(p) for p in policy.policies_for(db, product)]


@router.put("/feed-products/{product_id}/usage-policy", response_model=PolicyOut)
def set_usage_policy(
    product_id: str,
    payload: PolicySet,
    access: AccessContext = Depends(_approve),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    product = inv.get_product(db, product_id)
    lot = inv.get_lot(db, payload.lot_id) if payload.lot_id else None
    row = policy.set_policy(
        db,
        product,
        name=payload.name,
        rules=[r.model_dump() for r in payload.rules],
        requires_approved_formula=payload.requires_approved_formula,
        cross_species_transfer_allowed=payload.cross_species_transfer_allowed,
        lot=lot,
        notes=payload.notes,
        actor=access.membership_id,
    )
    _audit(
        control_db,
        access,
        action="feed_usage_policy.set",
        entity_type="feed_product",
        entity_id=str(product.id),
        summary=f"Set usage policy '{payload.name}' on {product.name}"
        + (f" (lot {lot.lot_code})" if lot else ""),
    )
    return _policy_out(row)


@router.post("/feed-products/{product_id}/usage-policy/validate", response_model=PolicyDecisionOut)
def validate_usage(
    product_id: str,
    payload: PolicyValidate,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    product = inv.get_product(db, product_id)
    lot = inv.get_lot(db, payload.lot_id) if payload.lot_id else None
    if payload.subject_type and payload.subject_id:
        subject = programs.load_subject(db, payload.subject_type, payload.subject_id)
        target = programs.subject_state(db, payload.subject_type, subject).policy_target()
    else:
        target = policy.Target(
            species_code=payload.species_code,
            management_profile=payload.management_profile,
            life_stage=payload.life_stage,
            reproductive_state=payload.reproductive_state,
            age_days=payload.age_days,
        )
    return policy.evaluate(
        db,
        product,
        target,
        lot=lot,
        inclusion_pct=payload.inclusion_pct,
        through_formula=payload.through_formula,
    ).to_dict()


# ------------------------------------------------------------------- lots
@router.get("/feed-lots", response_model=list[FeedLotOut])
def list_lots(
    feed_product_id: str | None = None,
    lot_status: str | None = Query(None, alias="status"),
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(fm.FeedLot).where(fm.FeedLot.deleted_at.is_(None))
    if feed_product_id:
        pk = inv.parse_uuid(feed_product_id)
        if pk is None:
            return []
        stmt = stmt.where(fm.FeedLot.feed_product_id == pk)
    rows = list(db.scalars(stmt.order_by(fm.FeedLot.received_at.desc())))
    out = [_lot_out(lot) for lot in rows]
    if lot_status:
        out = [o for o in out if o["status"] == lot_status]
    return out


@router.post("/feed-lots/receive", response_model=FeedLotOut, status_code=201)
def receive_lot(
    payload: LotReceive,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    product = inv.get_product(db, payload.feed_product_id)
    lot = inv.receive(
        db,
        product,
        **payload.model_dump(exclude={"feed_product_id"}),
        actor=access.membership_id,
        actor_user=access.user_id,
    )
    _audit(
        control_db,
        access,
        action="feed_lot.received",
        entity_type="feed_lot",
        entity_id=str(lot.id),
        summary=f"Received {lot.accepted_quantity:g} {lot.unit} of {product.name} as lot {lot.lot_code}",
    )
    return _lot_out(lot)


@router.patch("/feed-lots/{lot_id}/status", response_model=FeedLotOut)
def update_lot_status(
    lot_id: str,
    payload: LotStatusUpdate,
    access: AccessContext = Depends(_approve),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    lot = inv.get_lot(db, lot_id)
    before = lot.status
    inv.set_lot_status(db, lot, payload.status, actor=access.membership_id)
    _audit(
        control_db,
        access,
        action="feed_lot.status_changed",
        entity_type="feed_lot",
        entity_id=str(lot.id),
        summary=f"Lot {lot.lot_code}: {before} → {lot.status}"
        + (f" ({payload.reason})" if payload.reason else ""),
        changes={"status": {"from": before, "to": lot.status}, "reason": payload.reason},
    )
    return _lot_out(lot)


@router.get("/feed-lots/{lot_id}/trace")
def trace_lot(
    lot_id: str, _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return inv.lot_trace(db, inv.get_lot(db, lot_id))


@router.post("/feed-inventory/adjustments", status_code=201)
def post_adjustment(
    payload: AdjustmentCreate,
    access: AccessContext = Depends(_approve),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    product = inv.get_product(db, payload.feed_product_id)
    lot = inv.get_lot(db, payload.lot_id) if payload.lot_id else None
    tx = inv.adjust(
        db,
        product,
        lot=lot,
        delta=payload.delta,
        reason=payload.reason,
        explanation=payload.explanation,
        actor=access.membership_id,
    )
    _audit(
        control_db,
        access,
        action="feed_inventory.adjusted",
        entity_type="feed_product",
        entity_id=str(product.id),
        summary=(
            f"{payload.reason}: {payload.delta:+g} {product.unit} of {product.name} — {payload.explanation}"
        ),
    )
    return {
        "transaction_id": str(tx.id),
        "feed_product_id": str(product.id),
        "delta": payload.delta,
        "unit": product.unit,
        "availability": inv.availability(db, product).to_dict(),
    }


# --------------------------------------------------------------- formulas
@router.get("/feed-formulas", response_model=list[FormulaOut])
def list_formulas(
    _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    rows = db.scalars(
        select(fm.FeedFormula).where(fm.FeedFormula.deleted_at.is_(None)).order_by(fm.FeedFormula.name)
    ).all()
    return [_formula_out(db, f) for f in rows]


@router.post("/feed-formulas", response_model=FormulaOut, status_code=201)
def create_formula(
    payload: FormulaCreate,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    formula = batches.create_formula(
        db,
        access,
        code=payload.code,
        name=payload.name,
        feed_product_id=payload.feed_product_id,
        species_code=payload.species_code,
        description=payload.description,
        batch_size=payload.batch_size,
        unit=payload.unit,
        components=[c.model_dump() for c in payload.components],
        notes=payload.notes,
        activate=payload.activate,
    )
    _audit(
        control_db,
        access,
        action="feed_formula.created",
        entity_type="feed_formula",
        entity_id=str(formula.id),
        summary=f"Created formula {formula.name} ({formula.code}) v1",
    )
    return _formula_out(db, formula)


def _formula_or_404(db: Session, formula_id: str) -> fm.FeedFormula:
    pk = inv.parse_uuid(formula_id)
    f = db.get(fm.FeedFormula, pk) if pk else None
    if f is None or f.deleted_at is not None:
        raise FeedError("Formula not found", 404)
    return f


def _formula_version(db: Session, formula_id: str, version: int) -> fm.FeedFormulaVersion:
    f = _formula_or_404(db, formula_id)
    v = next((v for v in f.versions if v.version_number == version), None)
    if v is None:
        raise FeedError(f"{f.name} has no version {version}", 404)
    return v


@router.get("/feed-formulas/{formula_id}", response_model=FormulaOut)
def get_formula(
    formula_id: str, _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return _formula_out(db, _formula_or_404(db, formula_id))


@router.post("/feed-formulas/{formula_id}/versions", response_model=FormulaVersionOut, status_code=201)
def add_formula_version(
    formula_id: str,
    payload: FormulaVersionCreate,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    formula = _formula_or_404(db, formula_id)
    version = batches.add_version(
        db,
        access,
        formula,
        batch_size=payload.batch_size,
        unit=payload.unit,
        components=[c.model_dump() for c in payload.components],
        notes=payload.notes,
        activate=payload.activate,
    )
    _audit(
        control_db,
        access,
        action="feed_formula.version_added",
        entity_type="feed_formula",
        entity_id=str(formula.id),
        summary=f"Added {formula.name} v{version.version_number}"
        + (" (active)" if version.status == "active" else ""),
    )
    return _version_out(db, version)


@router.get("/feed-formulas/{formula_id}/versions/{version}", response_model=FormulaVersionOut)
def get_formula_version(
    formula_id: str,
    version: int,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    return _version_out(db, _formula_version(db, formula_id, version))


@router.post("/feed-formulas/{formula_id}/versions/{version}/activate", response_model=FormulaVersionOut)
def activate_formula_version(
    formula_id: str,
    version: int,
    access: AccessContext = Depends(_approve),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    v = _formula_version(db, formula_id, version)
    batches.activate_version(db, access, v)
    db.flush()
    _audit(
        control_db,
        access,
        action="feed_formula.version_activated",
        entity_type="feed_formula",
        entity_id=str(v.formula_id),
        summary=f"Activated {v.formula.name} v{v.version_number}",
    )
    return _version_out(db, v)


@router.get("/feed-formulas/{formula_id}/versions/{version}/scale")
def scale_formula_version(
    formula_id: str,
    version: int,
    batch_size: float = Query(gt=0),
    unit: str | None = None,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    v = _formula_version(db, formula_id, version)
    lines = batches.scale(v, batch_size, unit)
    for line in lines:
        product = db.get(fm.FeedProduct, uuid.UUID(line["feed_product_id"]))
        line["product_name"] = product.name if product else None
        line["available"] = inv.availability(db, product).available if product else None
    return {
        "formula_version_id": str(v.id),
        "batch_size": batch_size,
        "unit": unit or v.unit,
        "components": lines,
        "planned_cost": batches.planned_cost(db, v, batch_size, unit),
    }


@router.get("/feed-formulas/{formula_id}/versions/{version}/nutrients")
def formula_version_nutrients(
    formula_id: str,
    version: int,
    basis: str = "as_fed",
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    return batches.formula_nutrients(db, _formula_version(db, formula_id, version), basis=basis)


# ---------------------------------------------------------------- batches
@router.get("/feed-batches", response_model=list[BatchOut])
def list_batches(
    batch_status: str | None = Query(None, alias="status"),
    mix_number: int | None = None,
    search: str | None = None,
    intended_species_code: str | None = None,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> list[dict]:
    """Numbered mixes, newest first. `mix_number` finds one; `search`
    matches the mix code or batch code; `intended_species_code` filters by
    intended use — a classification, never a separate list."""
    stmt = select(fm.FeedBatch).where(fm.FeedBatch.deleted_at.is_(None))
    if batch_status:
        stmt = stmt.where(fm.FeedBatch.status == batch_status)
    if mix_number is not None:
        stmt = stmt.where(fm.FeedBatch.mix_number == mix_number)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where((fm.FeedBatch.mix_code.ilike(like)) | (fm.FeedBatch.batch_code.ilike(like)))
    if intended_species_code:
        stmt = stmt.where(fm.FeedBatch.intended_species_code == intended_species_code)
    return [_batch_out(db, control_db, b) for b in db.scalars(stmt.order_by(fm.FeedBatch.mix_number.desc()))]


@router.post("/feed-batches", response_model=BatchOut, status_code=201)
def start_batch(
    payload: BatchStart,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    _check_operator(control_db, access, payload.operator_id)
    batch = batches.start_batch(
        db,
        access,
        formula_version_id=payload.formula_version_id,
        formula_id=payload.formula_id,
        batch_code=payload.batch_code,
        target_quantity=payload.target_quantity,
        unit=payload.unit,
        notes=payload.notes,
        intended_species_code=payload.intended_species_code,
        intended_management_profile=payload.intended_management_profile,
        mixer_asset_id=payload.mixer_asset_id,
        operator_id=payload.operator_id,
        production_date=payload.production_date,
    )
    _audit(
        control_db,
        access,
        action="feed_batch.started",
        entity_type="feed_batch",
        entity_id=str(batch.id),
        summary=f"Started mix {batch.mix_code} ({batch.target_quantity:g} {batch.unit})",
    )
    return _batch_out(db, control_db, batch)


@router.get("/feed-mix-settings", response_model=MixSettingsOut)
def get_mix_settings(
    access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    """The farm's one mix sequence (§20): last number, next number and the
    display format the next mix code will take."""
    return batches.mix_settings_dict(batches.mix_settings(db, access.tenant_id))


@router.put("/feed-mix-settings", response_model=MixSettingsOut)
def update_mix_settings(
    payload: MixSettingsUpdate,
    access: AccessContext = Depends(_configure),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Changes the display format for future mixes. The number is the
    identity: it is never restarted, and existing mixes keep their codes."""
    seq = batches.set_mix_code_format(db, access, code_format=payload.code_format)
    db.flush()
    _audit(
        control_db,
        access,
        action="feed_mix_settings.updated",
        entity_type="farm",
        entity_id=str(access.tenant_id),
        summary=f"Mix code format set to {seq.code_format}",
        changes={"code_format": seq.code_format},
    )
    return batches.mix_settings_dict(seq)


@router.get("/feed-mixes/{mix_number}")
def mix_by_number(
    mix_number: int,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """The numbered mix's whole life, looked up by the number the farm
    uses: ingredients and their lots, output lot, every dated issue to an
    animal or group, waste and corrections, ledger-derived remaining
    stock, first/last use, exposure."""
    batch = batches.batch_by_mix_number(db, mix_number)
    if batch is None:
        raise FeedError(f"No mix number {mix_number} on this farm.", 404)
    return _usage_with_names(db, control_db, batch)


def _batch_or_404(db: Session, batch_id: str) -> fm.FeedBatch:
    pk = inv.parse_uuid(batch_id)
    b = db.get(fm.FeedBatch, pk) if pk else None
    if b is None or b.deleted_at is not None:
        raise FeedError("Batch not found", 404)
    return b


@router.get("/feed-batches/{batch_id}", response_model=BatchOut)
def get_batch(
    batch_id: str,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    return _batch_out(db, control_db, _batch_or_404(db, batch_id))


@router.get("/feed-batches/{batch_id}/usage")
def batch_usage(
    batch_id: str,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Same as GET /feed-mixes/{mix_number}, by batch id."""
    return _usage_with_names(db, control_db, _batch_or_404(db, batch_id))


@router.post("/feed-batches/{batch_id}/complete", response_model=BatchOut)
def complete_batch(
    batch_id: str,
    payload: BatchComplete,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    batch = _batch_or_404(db, batch_id)
    _check_operator(control_db, access, payload.operator_id)
    batches.complete_batch(
        db,
        access,
        batch,
        actuals=[a.model_dump() for a in payload.actuals],
        actual_quantity=payload.actual_quantity,
        produced_at=payload.produced_at,
        lot_code=payload.lot_code,
        expiry_date=payload.expiry_date,
        notes=payload.notes,
        allow_negative=payload.allow_negative,
        operator_id=payload.operator_id,
        mixer_asset_id=payload.mixer_asset_id,
    )
    _audit(
        control_db,
        access,
        action="feed_batch.completed",
        entity_type="feed_batch",
        entity_id=str(batch.id),
        summary=f"Completed mix {batch.mix_code}: {batch.actual_quantity:g} {batch.unit}",
    )
    return _batch_out(db, control_db, batch)


@router.post("/feed-batches/{batch_id}/quarantine", response_model=BatchOut)
def quarantine_batch(
    batch_id: str,
    payload: BatchQuarantine,
    access: AccessContext = Depends(_approve),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    batch = _batch_or_404(db, batch_id)
    batches.quarantine_batch(db, access, batch, reason=payload.reason)
    db.flush()
    _audit(
        control_db,
        access,
        action="feed_batch.quarantined",
        entity_type="feed_batch",
        entity_id=str(batch.id),
        summary=f"Quarantined mix {batch.mix_code}: {payload.reason}",
    )
    return _batch_out(db, control_db, batch)


# -------------------------------------------------------------- nutrients
@router.get("/feed-nutrients", response_model=list[NutrientOut])
def list_nutrients(_access: AccessContext = Depends(_view)) -> list[dict]:
    return sorted(catalog.NUTRIENT_BY_CODE.values(), key=lambda n: n["sort_order"])


@router.get("/feed-nutrient-profiles", response_model=list[NutrientProfileOut])
def list_nutrient_profiles(
    subject_type: str,
    subject_id: str,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    pk = inv.parse_uuid(subject_id)
    if pk is None:
        return []
    rows = db.scalars(
        select(fm.FeedNutrientProfile)
        .where(fm.FeedNutrientProfile.subject_type == subject_type, fm.FeedNutrientProfile.subject_id == pk)
        .order_by(fm.FeedNutrientProfile.effective_at.desc())
    ).all()
    return [batches.profile_to_dict(p) for p in rows]


@router.post("/feed-nutrient-profiles", response_model=NutrientProfileOut, status_code=201)
def create_nutrient_profile(
    payload: NutrientProfileCreate,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    profile = batches.record_profile(
        db,
        access,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
        basis=payload.basis,
        source_type=payload.source_type,
        values=[v.model_dump() for v in payload.values],
        reference=payload.reference,
        effective_at=payload.effective_at,
    )
    return batches.profile_to_dict(profile)


# --------------------------------------------------------------- programs
@router.get("/feeding-programs", response_model=list[ProgramOut])
def list_programs(
    _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    rows = db.scalars(
        select(fm.FeedingProgram)
        .where(fm.FeedingProgram.deleted_at.is_(None))
        .order_by(fm.FeedingProgram.name)
    ).all()
    return [_program_out(db, p) for p in rows]


@router.post("/feeding-programs", response_model=ProgramOut, status_code=201)
def create_program(
    payload: ProgramCreate,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    program = programs.create_program(
        db,
        access,
        code=payload.code,
        name=payload.name,
        name_ar=payload.name_ar,
        category=payload.category,
        description=payload.description,
        feedings_per_day=payload.feedings_per_day,
        components=[c.model_dump() for c in payload.components],
        rules=[r.model_dump() for r in payload.rules],
        notes=payload.notes,
        activate=payload.activate,
    )
    _audit(
        control_db,
        access,
        action="feeding_program.created",
        entity_type="feeding_program",
        entity_id=str(program.id),
        summary=f"Created feeding program {program.name} ({program.code})",
    )
    return _program_out(db, program)


def _program_or_404(db: Session, program_id: str) -> fm.FeedingProgram:
    pk = inv.parse_uuid(program_id)
    p = db.get(fm.FeedingProgram, pk) if pk else None
    if p is None or p.deleted_at is not None:
        raise FeedError("Feeding program not found", 404)
    return p


@router.get("/feeding-programs/resolve")
def resolve_programs_get(_access: AccessContext = Depends(_view)) -> dict:
    raise FeedError("Use POST /feeding-programs/resolve with a subject or a described animal.", 405)


@router.post("/feeding-programs/resolve")
def resolve_programs(
    payload: ProgramResolveRequest,
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    """§14: eligible programs, the best match, the matching rule and why,
    warnings, and whether a review is required — for a real subject or a
    described one."""
    if payload.subject_type and payload.subject_id:
        subject = programs.load_subject(db, payload.subject_type, payload.subject_id)
        state = programs.subject_state(db, payload.subject_type, subject)
        assignment, _, _ = programs.effective_program(db, payload.subject_type, subject)
        current = None
        if assignment is not None and assignment.program_version_id:
            v = db.get(fm.FeedingProgramVersion, assignment.program_version_id)
            current = v.program_id if v else None
        return programs.resolve_state(db, state, current_program_id=current)
    if not payload.species_code:
        raise FeedError("Give a subject (subject_type + subject_id) or at least a species_code.")
    livestock_service.species_or_422(payload.species_code)
    sex = {"F": "F", "FEMALE": "F", "M": "M", "MALE": "M"}.get((payload.sex or "").upper())
    state = programs.SubjectState(
        subject_type="hypothetical",
        subject_id="",
        name=f"a {payload.species_code.replace('_', ' ')}",
        species_code=payload.species_code,
        sex=sex,
        life_stage=payload.life_stage or "adult",
        management_profile=payload.management_profile,
        reproductive_state=payload.reproductive_state or ("open" if sex == "F" else None),
        lactation_state=payload.lactation_state or ("dry" if sex == "F" else None),
        age_days=payload.age_days,
        weight_kg=payload.weight_kg,
        production=payload.production,
        head_count=payload.head_count,
    )
    return programs.resolve_state(db, state)


@router.get("/feeding-programs/{program_id}", response_model=ProgramOut)
def get_program(
    program_id: str, _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return _program_out(db, _program_or_404(db, program_id))


@router.post("/feeding-programs/{program_id}/versions", response_model=ProgramVersionOut, status_code=201)
def add_program_version(
    program_id: str,
    payload: ProgramVersionCreate,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    program = _program_or_404(db, program_id)
    version = programs.add_program_version(
        db,
        access,
        program,
        feedings_per_day=payload.feedings_per_day,
        components=[c.model_dump() for c in payload.components],
        rules=[r.model_dump() for r in payload.rules],
        notes=payload.notes,
        activate=payload.activate,
    )
    _audit(
        control_db,
        access,
        action="feeding_program.version_added",
        entity_type="feeding_program",
        entity_id=str(program.id),
        summary=f"Added {program.name} v{version.version_number}",
    )
    return _program_version_out(db, version)


@router.post("/feeding-programs/{program_id}/versions/{version}/activate", response_model=ProgramVersionOut)
def activate_program_version(
    program_id: str,
    version: int,
    access: AccessContext = Depends(_approve),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    program = _program_or_404(db, program_id)
    v = next((v for v in program.versions if v.version_number == version), None)
    if v is None:
        raise FeedError(f"{program.name} has no version {version}", 404)
    programs.activate_program_version(db, access, v)
    db.flush()
    _audit(
        control_db,
        access,
        action="feeding_program.version_activated",
        entity_type="feeding_program",
        entity_id=str(program.id),
        summary=f"Activated {program.name} v{v.version_number}",
    )
    return _program_version_out(db, v)


# ------------------------------------------------------------ assignments
@router.get("/livestock-subjects/{subject_id}/feeding-plan")
def feeding_plan(
    subject_id: str, _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    subject_type, subject = programs.find_subject(db, subject_id)
    return programs.effective_plan(db, subject_type, subject)


@router.get("/livestock-subjects/{subject_id}/feeding-assignments", response_model=list[AssignmentOut])
def list_assignments(
    subject_id: str,
    include_ended: bool = False,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    subject_type, subject = programs.find_subject(db, subject_id)
    stmt = select(fm.FeedingAssignment).where(
        fm.FeedingAssignment.subject_type == subject_type, fm.FeedingAssignment.subject_id == subject.id
    )
    if not include_ended:
        stmt = stmt.where(fm.FeedingAssignment.status == "active")
    return [_assignment_out(db, a) for a in db.scalars(stmt.order_by(fm.FeedingAssignment.created_at.desc()))]


@router.post(
    "/livestock-subjects/{subject_id}/feeding-assignments", response_model=AssignmentOut, status_code=201
)
def create_assignment(
    subject_id: str,
    payload: AssignmentCreate,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    subject_type, subject = programs.find_subject(db, subject_id)
    if (
        payload.assignment_type == "restriction"
        and not access.has("feed_nutrition", "approve")
        and not access.has("animal_health", "create")
    ):
        raise FeedError(
            "A feed restriction is a veterinary or management decision; "
            "it needs Animal Health or feed approval rights.",
            403,
        )
    row = programs.assign(db, access, subject_type, subject, **payload.model_dump())
    _audit(
        control_db,
        access,
        action="feeding_assignment.created",
        entity_type=subject_type,
        entity_id=str(subject.id),
        summary=(
            f"{payload.assignment_type.capitalize()} feeding assignment "
            f"for {getattr(subject, 'name', subject.id)}"
        ),
    )
    return _assignment_out(db, row)


@router.delete(
    "/livestock-subjects/{subject_id}/feeding-assignments/{assignment_id}", response_model=AssignmentOut
)
def end_assignment(
    subject_id: str,
    assignment_id: str,
    reason: str | None = None,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    subject_type, subject = programs.find_subject(db, subject_id)
    pk = inv.parse_uuid(assignment_id)
    a = db.get(fm.FeedingAssignment, pk) if pk else None
    if a is None or a.subject_id != subject.id:
        raise FeedError("Assignment not found", 404)
    programs.end_assignment(db, access, a, reason=reason)
    db.flush()
    _audit(
        control_db,
        access,
        action="feeding_assignment.ended",
        entity_type=subject_type,
        entity_id=str(subject.id),
        summary=f"Ended {a.assignment_type} feeding assignment for {getattr(subject, 'name', subject.id)}",
    )
    return _assignment_out(db, a)


@router.get("/livestock-subjects/{subject_id}/feeding-history", response_model=list[FeedingEventOut])
def feeding_history(
    subject_id: str,
    days: int = Query(30, ge=1, le=365),
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    subject_type, subject = programs.find_subject(db, subject_id)
    return [
        programs.event_to_dict(e) for e in programs.feeding_history(db, subject_type, subject.id, days=days)
    ]


# ----------------------------------------------------------------- events
@router.post("/feeding-events", response_model=FeedingEventOut, status_code=201)
def record_feeding_event(
    payload: FeedingEventCreate,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    subject = programs.load_subject(db, payload.subject_type, payload.subject_id)
    event = programs.record_feeding(
        db,
        access,
        subject_type=payload.subject_type,
        subject=subject,
        components=[c.model_dump() for c in payload.components],
        event_type=payload.event_type,
        occurred_at=payload.occurred_at,
        head_count=payload.head_count,
        notes=payload.notes,
        allow_negative=payload.allow_negative,
    )
    _audit(
        control_db,
        access,
        action="feeding_event.recorded",
        entity_type=payload.subject_type,
        entity_id=str(subject.id),
        summary=f"Recorded {event.event_type} feeding for {getattr(subject, 'name', subject.id)}",
    )
    return programs.event_to_dict(event)


@router.get("/feeding-events", response_model=list[FeedingEventOut])
def list_feeding_events(
    days: int = Query(7, ge=1, le=365),
    subject_type: str | None = None,
    subject_id: str | None = None,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    since = now_utc() - timedelta(days=days)
    stmt = select(fm.FeedingEvent).where(fm.FeedingEvent.occurred_at >= since)
    if subject_type:
        stmt = stmt.where(fm.FeedingEvent.subject_type == subject_type)
    if subject_id:
        pk = inv.parse_uuid(subject_id)
        if pk is None:
            return []
        stmt = stmt.where(fm.FeedingEvent.subject_id == pk)
    return [programs.event_to_dict(e) for e in db.scalars(stmt.order_by(fm.FeedingEvent.occurred_at.desc()))]


@router.post("/feeding-events/{event_id}/reverse", response_model=FeedingEventOut, status_code=201)
def reverse_feeding_event(
    event_id: str,
    payload: FeedingEventReverse,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    pk = inv.parse_uuid(event_id)
    event = db.get(fm.FeedingEvent, pk) if pk else None
    if event is None:
        raise FeedError("Feeding event not found", 404)
    reversal = programs.reverse_feeding(db, access, event, reason=payload.reason)
    _audit(
        control_db,
        access,
        action="feeding_event.reversed",
        entity_type=event.subject_type,
        entity_id=str(event.subject_id),
        summary=f"Reversed feeding {event.id}: {payload.reason}",
    )
    return programs.event_to_dict(reversal)


@router.get("/feeding-plan/today")
def feeding_plan_today(
    _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return programs.daily_plan(db)


# --------------------------------------------------------------- inventory
@router.get("/feed-inventory/availability", response_model=list[FeedAvailabilityOut])
def inventory_availability(
    feed_product_id: str | None = None,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(fm.FeedProduct).where(
        fm.FeedProduct.status == "active", fm.FeedProduct.deleted_at.is_(None)
    )
    if feed_product_id:
        pk = inv.parse_uuid(feed_product_id)
        if pk is None:
            return []
        stmt = stmt.where(fm.FeedProduct.id == pk)
    return [inv.availability(db, p).to_dict() for p in db.scalars(stmt.order_by(fm.FeedProduct.name))]


@router.get("/feed-inventory/days-of-cover")
def days_of_cover(
    _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    return forecast.cover_for_products(db)


@router.get("/feed-inventory/forecast")
def demand_forecast(
    horizon_days: int = Query(14, ge=1, le=180),
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    return forecast.forecast(db, access, horizon_days=horizon_days, persist=True)


@router.get("/feed-inventory/reorder-recommendations")
def reorder_recommendations(
    access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    return forecast.reorder_recommendations(db, access)


@router.post("/feed-inventory/reorder-recommendations/{product_id}/acknowledge", status_code=201)
def acknowledge_reorder(
    product_id: str,
    payload: ReorderAcknowledge,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    product = inv.get_product(db, product_id)
    task = forecast.acknowledge_reorder(
        db, access, product, quantity=payload.quantity, note=payload.note, assigned_to=payload.assigned_to
    )
    _audit(
        control_db,
        access,
        action="feed_reorder.acknowledged",
        entity_type="feed_product",
        entity_id=str(product.id),
        summary=f"Reorder acknowledged for {product.name}: task {task.title}",
    )
    return {
        "task_id": str(task.id),
        "title": task.title,
        "status": task.status,
        "feed_product_id": str(product.id),
    }


@router.get("/feed-reorder-policies", response_model=list[ReorderPolicyOut])
def list_reorder_policies(
    _access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    return [_reorder_policy_out(p) for p in db.scalars(select(fm.FeedReorderPolicy))]


@router.put("/feed-reorder-policies", response_model=ReorderPolicyOut)
def upsert_reorder_policy(
    payload: ReorderPolicyUpsert,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    product = inv.get_product(db, payload.feed_product_id)
    row = db.scalar(
        select(fm.FeedReorderPolicy).where(
            fm.FeedReorderPolicy.feed_product_id == product.id,
            fm.FeedReorderPolicy.location_label == payload.location_label,
        )
    )
    if row is None:
        row = fm.FeedReorderPolicy(
            tenant_id=access.tenant_id,
            last_modified_by=access.membership_id,
            feed_product_id=product.id,
            unit=product.unit,
        )
        db.add(row)
    for k, v in payload.model_dump(exclude={"feed_product_id"}).items():
        setattr(row, k, v)
    row.last_modified_by = access.membership_id
    item = db.get(InventoryItem, product.inventory_item_id)
    if item is not None and payload.reorder_point is not None:
        item.reorder_level = payload.reorder_point
    db.flush()
    return _reorder_policy_out(row)


# ------------------------------------------------------------ allocations
@router.get("/feed-allocations", response_model=list[AllocationOut])
def list_allocations(
    feed_product_id: str | None = None,
    alloc_status: str | None = Query("active", alias="status"),
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(fm.FeedAllocation)
    if feed_product_id:
        pk = inv.parse_uuid(feed_product_id)
        if pk is None:
            return []
        stmt = stmt.where(fm.FeedAllocation.feed_product_id == pk)
    if alloc_status and alloc_status != "all":
        stmt = stmt.where(fm.FeedAllocation.status == alloc_status)
    return [_allocation_out(db, a) for a in db.scalars(stmt.order_by(fm.FeedAllocation.created_at.desc()))]


@router.post("/feed-allocations", response_model=AllocationOut, status_code=201)
def create_allocation(
    payload: AllocationCreate,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    product = inv.get_product(db, payload.feed_product_id)
    alloc = inv.allocate(
        db,
        product,
        **payload.model_dump(exclude={"feed_product_id"}),
        actor=access.membership_id,
        actor_user=access.user_id,
    )
    _audit(
        control_db,
        access,
        action="feed_allocation.created",
        entity_type="feed_product",
        entity_id=str(product.id),
        summary=f"Reserved {alloc.allocated_quantity:g} {alloc.unit} of {product.name}",
    )
    return _allocation_out(db, alloc)


def _allocation_or_404(db: Session, allocation_id: str) -> fm.FeedAllocation:
    pk = inv.parse_uuid(allocation_id)
    a = db.get(fm.FeedAllocation, pk) if pk else None
    if a is None:
        raise FeedError("Allocation not found", 404)
    return a


@router.post("/feed-allocations/{allocation_id}/release", response_model=AllocationOut)
def release_allocation(
    allocation_id: str,
    reason: str | None = None,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    a = _allocation_or_404(db, allocation_id)
    inv.release_allocation(db, a, actor=access.membership_id)
    db.flush()
    _audit(
        control_db,
        access,
        action="feed_allocation.released",
        entity_type="feed_product",
        entity_id=str(a.feed_product_id),
        summary=f"Released allocation {a.id}" + (f": {reason}" if reason else ""),
    )
    return _allocation_out(db, a)


@router.post("/feed-allocations/{allocation_id}/transfer", response_model=AllocationOut, status_code=201)
def transfer_allocation(
    allocation_id: str,
    payload: AllocationTransfer,
    access: AccessContext = Depends(_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    a = _allocation_or_404(db, allocation_id)
    product = inv.get_product(db, a.feed_product_id)
    moved = inv.transfer_allocation(
        db,
        a,
        quantity=payload.quantity,
        species_code=payload.species_code,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
        feeding_program_id=payload.feeding_program_id,
        purpose=payload.purpose,
        authorised=access.has("feed_nutrition", "approve"),
        cross_species_allowed=policy.cross_species_transfer_allowed(db, product),
        actor=access.membership_id,
        actor_user=access.user_id,
    )
    _audit(
        control_db,
        access,
        action="feed_allocation.transferred",
        entity_type="feed_product",
        entity_id=str(product.id),
        summary=f"Transferred {moved.allocated_quantity:g} {moved.unit} of {product.name}",
    )
    return _allocation_out(db, moved)


# --------------------------------------------------------- reconciliation
@router.get("/feed-reconciliations", response_model=list[ReconciliationOut])
def list_reconciliations(
    rec_status: str | None = Query(None, alias="status"),
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(fm.FeedReconciliation)
    if rec_status:
        stmt = stmt.where(fm.FeedReconciliation.status == rec_status)
    return [
        _reconciliation_out(db, r) for r in db.scalars(stmt.order_by(fm.FeedReconciliation.created_at.desc()))
    ]


@router.post("/feed-reconciliations", response_model=ReconciliationOut, status_code=201)
def create_reconciliation(
    payload: ReconciliationCreate,
    access: AccessContext = Depends(_create),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    product = inv.get_product(db, payload.feed_product_id)
    lot = inv.get_lot(db, payload.lot_id) if payload.lot_id else None
    rec = forecast.reconcile(
        db,
        access,
        product,
        lot=lot,
        period_from=payload.period_from,
        period_to=payload.period_to,
        counted_closing_quantity=payload.counted_closing_quantity,
        explanation=payload.explanation,
    )
    threshold = forecast.variance_threshold(db, product.id)
    flagged = rec.variance_pct is not None and abs(rec.variance_pct) > threshold
    _audit(
        control_db,
        access,
        action="feed_reconciliation.created",
        entity_type="feed_product",
        entity_id=str(product.id),
        summary=(
            f"Reconciled {product.name}: variance "
            f"{rec.variance_quantity if rec.variance_quantity is not None else 'n/a'}"
            + (" — above threshold" if flagged else "")
        ),
    )
    return _reconciliation_out(db, rec)


def _reconciliation_or_404(db: Session, reconciliation_id: str) -> fm.FeedReconciliation:
    pk = inv.parse_uuid(reconciliation_id)
    rec = db.get(fm.FeedReconciliation, pk) if pk else None
    if rec is None:
        raise FeedError("Reconciliation not found", 404)
    return rec


@router.get("/feed-reconciliations/{reconciliation_id}", response_model=ReconciliationOut)
def get_reconciliation(
    reconciliation_id: str,
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    return _reconciliation_out(db, _reconciliation_or_404(db, reconciliation_id))


@router.post("/feed-reconciliations/{reconciliation_id}/close", response_model=ReconciliationOut)
def close_reconciliation(
    reconciliation_id: str,
    payload: ReconciliationClose,
    access: AccessContext = Depends(_approve),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    rec = _reconciliation_or_404(db, reconciliation_id)
    forecast.close_reconciliation(
        db, access, rec, post_adjustment=payload.post_adjustment, explanation=payload.explanation
    )
    db.flush()
    _audit(
        control_db,
        access,
        action="feed_reconciliation.closed",
        entity_type="feed_product",
        entity_id=str(rec.feed_product_id),
        summary=f"Closed reconciliation {rec.id}"
        + (" with a posted adjustment" if payload.post_adjustment else ""),
    )
    return _reconciliation_out(db, rec)


# ------------------------------------------------------------------ costs
@router.get("/feed-costs")
def feed_costs(
    days: int = Query(30, ge=1, le=365),
    _access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict:
    return forecast.cost_summary(db, days=days)
