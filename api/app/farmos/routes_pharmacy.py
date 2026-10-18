"""Farm pharmacy API (OrigamiFarmOS database/MEDICINE-PHARMACY-SCHEMA.md).

Reads need inventory or animal-health view; stock writes need inventory
create/edit; thresholds need inventory configure (or edit); closing an
alert by hand needs inventory approve; an administration needs animal-
health create and, for a prescription-only medicine, a recorded
treatment or an approved protocol step. Stocking a medicine never
authorises its use.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import pharmacy_catalog as pc
from app.farmos import pharmacy_service as ph
from app.farmos import procurement_service as procurement
from app.farmos.deps import AccessContext, get_farmos_tenant_db, require_any_permission, require_permission
from app.farmos.pharmacy_models import (
    InventoryLot,
    InventoryReservation,
    MedicationAdministration,
    PharmacyStockAlert,
    PharmacyStockPolicy,
)
from app.farmos.pharmacy_schemas import (
    AdministrationReverse,
    MedicationAdministrationIn,
    MedicineLotOpen,
    MedicineLotReceive,
    MedicineLotStatus,
    MedicineLotStorage,
    MedicineProductIn,
    MedicineProductPatch,
    PharmacyAdjustment,
    PharmacyAlertClose,
    PharmacyPolicyIn,
    ReservationIn,
    ReservationRelease,
    TransferIn,
)
from app.farmos.routing_service import NameCache
from app.tenant_api.models import Task

router = APIRouter(prefix="/pharmacy")

_view = require_any_permission(("inventory", "view"), ("animal_health", "view"))
_stock = require_any_permission(("inventory", "create"), ("inventory", "edit"))
_stock_edit = require_permission("inventory", "edit")
_configure = require_any_permission(("inventory", "configure"), ("inventory", "edit"))
_approve = require_permission("inventory", "approve")
_administer = require_permission("animal_health", "create")
_health_edit = require_permission("animal_health", "edit")
_reserve = require_any_permission(("inventory", "edit"), ("animal_health", "create"))


def _audit(
    control_db: Session,
    access: AccessContext,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    summary: str,
    module_code: str = "inventory",
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
        module_code=module_code,
        summary=summary,
        changes=changes,
    )


# ---------------------------------------------------------------- summary
@router.get("/summary")
def pharmacy_summary(
    access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return ph.summary(db, NameCache())


@router.post("/evaluate")
def evaluate(
    access: AccessContext = Depends(_stock),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """The pharmacy scan (notification rules §Trigger model): every
    medicine against its lots' expiry and the farm's thresholds."""
    out = ph.evaluate_farm(db, access)
    _audit(
        control_db,
        access,
        action="pharmacy.evaluated",
        entity_type="farm",
        entity_id=str(access.tenant_id),
        summary=f"Pharmacy scan: {out['medicines']} medicines, {out['open_alerts']} open alerts",
    )
    return out


@router.get("/categories")
def list_categories(access: AccessContext = Depends(_view)) -> list[dict]:
    return [{"id": c["code"], **c} for c in pc.CATEGORY_BY_CODE.values()]


@router.get("/ingredients")
def list_ingredients(
    access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    return ph.ingredient_catalog(db)


# -------------------------------------------------------------- medicines
@router.get("/medicines")
def list_medicines(
    include_inactive: bool = False,
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    """The essential medicine dashboard (§10): eligible against on hand,
    thresholds, lots, earliest expiry, storage exceptions, open reorder
    and the OK / LOW / CRITICAL / OUT status."""
    return ph.dashboard(db, NameCache(), include_inactive=include_inactive)


@router.post("/medicines", status_code=201)
def create_medicine(
    payload: MedicineProductIn,
    access: AccessContext = Depends(_stock),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    body = payload.model_dump()
    body["ingredients"] = [i.model_dump() for i in payload.ingredients]
    product = ph.create_product(db, access, **body)
    item = ph.item_of(db, product)
    _audit(
        control_db,
        access,
        action="medicine.created",
        entity_type="medicine_product",
        entity_id=str(product.id),
        summary=f"Added medicine {item.name} ({product.dosage_form})",
        changes={
            "prescription_required": product.prescription_required,
            "categories": product.category_codes,
        },
    )
    return ph.product_dict(db, product, NameCache(), with_lots=True)


@router.get("/medicines/{item_id}")
def get_medicine(
    item_id: str, access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> dict:
    return ph.product_dict(db, ph.get_product(db, item_id), NameCache(), with_lots=True)


@router.patch("/medicines/{item_id}")
def update_medicine(
    item_id: str,
    payload: MedicineProductPatch,
    access: AccessContext = Depends(_stock_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("ingredients") is not None and payload.ingredients is not None:
        changes["ingredients"] = [i.model_dump() for i in payload.ingredients]
    product = ph.update_product(db, access, ph.get_product(db, item_id), changes)
    _audit(
        control_db,
        access,
        action="medicine.updated",
        entity_type="medicine_product",
        entity_id=str(product.id),
        summary=f"Updated medicine {ph.item_of(db, product).name}",
        changes={"changed": sorted(k for k, v in changes.items() if v is not None)},
    )
    return ph.product_dict(db, product, NameCache(), with_lots=True)


# ------------------------------------------------------------------- lots
@router.get("/lots")
def list_lots(
    inventory_item_id: str | None = None,
    lot_status: str | None = Query(None, alias="status"),
    eligible_only: bool = False,
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(InventoryLot).where(InventoryLot.deleted_at.is_(None))
    if inventory_item_id:
        product = ph.get_product(db, inventory_item_id)
        stmt = stmt.where(InventoryLot.inventory_item_id == product.inventory_item_id)
    rows = [
        ph.lot_dict(db, lot)
        for lot in db.scalars(stmt.order_by(InventoryLot.expiry_date, InventoryLot.received_at))
    ]
    if lot_status:
        rows = [r for r in rows if r["status"] == lot_status]
    if eligible_only:
        rows = [r for r in rows if r["eligible"]]
    return rows


@router.post("/lots/receive", status_code=201)
def receive_lot(
    payload: MedicineLotReceive,
    access: AccessContext = Depends(_stock),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    product = ph.get_product(db, payload.inventory_item_id)
    body = payload.model_dump(exclude={"inventory_item_id"})
    lot = ph.receive_lot(db, access, product, **body)
    _audit(
        control_db,
        access,
        action="medicine_lot.received",
        entity_type="inventory_lot",
        entity_id=str(lot.id),
        summary=f"Received lot {lot.lot_code} of {ph.item_of(db, product).name}: "
        f"{lot.accepted_quantity:g} {lot.unit}",
        changes={
            "received": lot.received_quantity,
            "accepted": lot.accepted_quantity,
            "rejected": lot.rejected_quantity,
            "expiry_date": lot.expiry_date.isoformat() if lot.expiry_date else None,
        },
    )
    return ph.lot_dict(db, lot)


@router.patch("/lots/{lot_id}/status")
def set_lot_status(
    lot_id: str,
    payload: MedicineLotStatus,
    access: AccessContext = Depends(_stock_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    lot = ph.get_lot(db, lot_id)
    previous = lot.status
    ph.set_lot_status(
        db, access, lot, payload.status, reason=payload.reason, recall_reference=payload.recall_reference
    )
    _audit(
        control_db,
        access,
        action="medicine_lot.status_changed",
        entity_type="inventory_lot",
        entity_id=str(lot.id),
        summary=f"Lot {lot.lot_code}: {previous} → {lot.status}"
        + (f" ({payload.reason})" if payload.reason else ""),
        changes={
            "from": previous,
            "to": lot.status,
            "reason": payload.reason,
            "recall_reference": payload.recall_reference,
        },
    )
    return ph.lot_dict(db, lot)


@router.patch("/lots/{lot_id}/storage")
def set_lot_storage(
    lot_id: str,
    payload: MedicineLotStorage,
    access: AccessContext = Depends(_stock_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    lot = ph.set_storage_status(
        db,
        access,
        ph.get_lot(db, lot_id),
        storage_status=payload.storage_status,
        cold_chain_exception=payload.cold_chain_exception,
        note=payload.note,
    )
    _audit(
        control_db,
        access,
        action="medicine_lot.storage_changed",
        entity_type="inventory_lot",
        entity_id=str(lot.id),
        summary=f"Lot {lot.lot_code}: storage {payload.storage_status}, "
        f"cold chain exception {payload.cold_chain_exception}",
        changes={
            "storage_status": payload.storage_status,
            "cold_chain_exception": payload.cold_chain_exception,
            "note": payload.note,
        },
    )
    return ph.lot_dict(db, lot)


@router.post("/lots/{lot_id}/open")
def open_lot(
    lot_id: str,
    payload: MedicineLotOpen | None = None,
    access: AccessContext = Depends(_stock),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    lot = ph.open_lot(db, access, ph.get_lot(db, lot_id), opened_at=payload.opened_at if payload else None)
    _audit(
        control_db,
        access,
        action="medicine_lot.opened",
        entity_type="inventory_lot",
        entity_id=str(lot.id),
        summary=f"Lot {lot.lot_code} opened"
        + (f"; use by {lot.use_by_after_opening:%Y-%m-%d}" if lot.use_by_after_opening else ""),
    )
    return ph.lot_dict(db, lot)


@router.post("/adjustments", status_code=201)
def adjust_stock(
    payload: PharmacyAdjustment,
    access: AccessContext = Depends(_stock_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    product = ph.get_product(db, payload.inventory_item_id)
    lot = ph.get_lot(db, payload.lot_id)
    mv = ph.adjust(
        db,
        access,
        product,
        lot=lot,
        delta=payload.delta,
        reason=payload.reason,
        explanation=payload.explanation,
    )
    _audit(
        control_db,
        access,
        action="pharmacy.adjusted",
        entity_type="inventory_lot",
        entity_id=str(lot.id),
        summary=f"Adjusted lot {lot.lot_code} by {payload.delta:g} {lot.unit} ({payload.reason}): "
        f"{payload.explanation}",
        changes={"delta": payload.delta, "reason": payload.reason, "explanation": payload.explanation},
    )
    return {
        "transaction_id": str(mv.id),
        "lot": ph.lot_dict(db, lot),
        "medicine": ph.product_dict(db, product, NameCache()),
    }


@router.post("/transfers", status_code=201)
def transfer_stock(
    payload: TransferIn,
    access: AccessContext = Depends(_stock_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Moves part of a lot to another location as a paired OUT / IN
    (INVENTORY-SCHEMA §4). On-hand is unchanged; per-location policies
    are re-evaluated."""
    product = ph.get_product(db, payload.inventory_item_id)
    lot = ph.get_lot(db, payload.lot_id)
    out = ph.transfer(
        db,
        access,
        product,
        lot=lot,
        quantity=payload.quantity,
        to_location_id=payload.to_location_id,
        from_location_id=payload.from_location_id,
        reason=payload.reason,
    )
    _audit(
        control_db,
        access,
        action="pharmacy.transferred",
        entity_type="inventory_lot",
        entity_id=str(lot.id),
        summary=f"Moved {payload.quantity:g} {out['unit']} of lot {lot.lot_code} "
        f"from {out['from_location_id']} to {out['to_location_id']}",
        changes={k: out[k] for k in ("correlation_id", "from_location_id", "to_location_id", "quantity")},
    )
    return {**out, "lot": ph.lot_dict(db, lot)}


# ------------------------------------------------------------ reservations
@router.get("/reservations")
def list_reservations(
    inventory_item_id: str | None = None,
    reservation_status: str | None = Query("active", alias="status"),
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(InventoryReservation).where(InventoryReservation.deleted_at.is_(None))
    if inventory_item_id:
        product = ph.get_product(db, inventory_item_id)
        stmt = stmt.where(InventoryReservation.inventory_item_id == product.inventory_item_id)
    if reservation_status and reservation_status != "all":
        stmt = stmt.where(InventoryReservation.status == reservation_status)
    return [
        ph.reservation_dict(db, r) for r in db.scalars(stmt.order_by(InventoryReservation.created_at.desc()))
    ]


@router.post("/reservations", status_code=201)
def reserve_stock(
    payload: ReservationIn,
    access: AccessContext = Depends(_reserve),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Holds eligible stock for a purpose (INVENTORY-SCHEMA §5): it stops
    counting as available to everyone else until released or consumed."""
    product = ph.get_product(db, payload.inventory_item_id)
    lot = ph.get_lot(db, payload.lot_id) if payload.lot_id else None
    r = ph.reserve(
        db,
        access,
        product,
        quantity=payload.quantity,
        unit=payload.unit,
        lot=lot,
        location_id=payload.location_id,
        purpose_type=payload.purpose_type,
        purpose_id=payload.purpose_id,
        purpose_label=payload.purpose_label,
        valid_to=payload.valid_to,
    )
    _audit(
        control_db,
        access,
        action="reservation.created",
        entity_type="inventory_reservation",
        entity_id=str(r.id),
        summary=f"Reserved {r.quantity:g} {r.unit} of {ph.item_of(db, product).name} for {r.purpose_type}",
        changes={
            "quantity": r.quantity,
            "lot_id": str(r.inventory_lot_id) if r.inventory_lot_id else None,
            "purpose_type": r.purpose_type,
            "purpose_id": r.purpose_id,
        },
    )
    return ph.reservation_dict(db, r)


@router.post("/reservations/{reservation_id}/release")
def release_reservation(
    reservation_id: str,
    payload: ReservationRelease | None = None,
    access: AccessContext = Depends(_reserve),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    r = ph.release_reservation(
        db, access, ph.get_reservation(db, reservation_id), reason=payload.reason if payload else None
    )
    _audit(
        control_db,
        access,
        action="reservation.released",
        entity_type="inventory_reservation",
        entity_id=str(r.id),
        summary=f"Released reservation ({r.release_reason})",
        changes={"remaining": ph.reservation_remaining(r)},
    )
    return ph.reservation_dict(db, r)


# --------------------------------------------------------------- policies
@router.get("/policies")
def list_policies(
    access: AccessContext = Depends(_view), db: Session = Depends(get_farmos_tenant_db)
) -> list[dict]:
    rows = db.scalars(select(PharmacyStockPolicy).where(PharmacyStockPolicy.deleted_at.is_(None)))
    return [p for p in (ph.policy_dict(row) for row in rows) if p is not None]


@router.put("/policies/{item_id}")
def upsert_policy(
    item_id: str,
    payload: PharmacyPolicyIn,
    access: AccessContext = Depends(_configure),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """The farm manager's minimum / critical / target per medicine (and
    optionally per location). Thresholds are configuration, never advice."""
    product = ph.get_product(db, item_id)
    body = payload.model_dump()
    location_id = body.pop("location_id")
    policy = ph.upsert_policy(db, access, product, location_id=location_id, **body)
    _audit(
        control_db,
        access,
        action="pharmacy_policy.set",
        entity_type="medicine_product",
        entity_id=str(product.id),
        summary=f"Stock policy v{policy.policy_version} for {ph.item_of(db, product).name}: "
        f"min {policy.minimum_stock_base}, "
        f"critical {policy.critical_stock_base}, target {policy.target_stock_base}",
        changes={
            "policy_id": str(policy.id),
            "version": policy.policy_version,
            "essential": policy.essential,
            "minimum": policy.minimum_stock_base,
            "critical": policy.critical_stock_base,
            "target": policy.target_stock_base,
            "location_id": location_id,
        },
    )
    return {"policy": ph.policy_dict(policy), "medicine": ph.product_dict(db, product, NameCache())}


# ----------------------------------------------------------------- alerts
@router.get("/alerts")
def list_alerts(
    alert_status: str | None = Query("open", alias="status"),
    inventory_item_id: str | None = None,
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(PharmacyStockAlert).where(PharmacyStockAlert.deleted_at.is_(None))
    if alert_status == "open":
        stmt = stmt.where(PharmacyStockAlert.status != "resolved")
    elif alert_status and alert_status != "all":
        stmt = stmt.where(PharmacyStockAlert.status == alert_status)
    if inventory_item_id:
        product = ph.get_product(db, inventory_item_id)
        stmt = stmt.where(PharmacyStockAlert.inventory_item_id == product.inventory_item_id)
    return [ph.alert_dict(db, a) for a in db.scalars(stmt.order_by(PharmacyStockAlert.detected_at.desc()))]


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: str,
    access: AccessContext = Depends(_stock),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    a = ph.acknowledge_alert(db, access, ph.get_alert(db, alert_id))
    _audit(
        control_db,
        access,
        action="pharmacy_alert.acknowledged",
        entity_type="pharmacy_stock_alert",
        entity_id=str(a.id),
        summary=f"Acknowledged {a.alert_type} alert",
    )
    return ph.alert_dict(db, a)


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: str,
    payload: PharmacyAlertClose,
    access: AccessContext = Depends(_approve),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    a = ph.resolve_alert_manually(db, access, ph.get_alert(db, alert_id), note=payload.note)
    _audit(
        control_db,
        access,
        action="pharmacy_alert.resolved",
        entity_type="pharmacy_stock_alert",
        entity_id=str(a.id),
        summary=f"Closed {a.alert_type} alert by hand: {a.resolution_note}",
        changes={"note": a.resolution_note, "manual": True},
    )
    return ph.alert_dict(db, a)


@router.post("/alerts/{alert_id}/requisition")
def create_requisition(
    alert_id: str,
    access: AccessContext = Depends(_stock),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """Opens one DRAFT purchase requisition for the shortage (§9,
    PROCUREMENT §5), with a task for the buyer. Calling it again returns
    the same open draft; nothing here approves a purchase."""
    alert = ph.get_alert(db, alert_id)
    req, created = ph.create_requisition(db, access, alert)
    if created:
        _audit(
            control_db,
            access,
            action="requisition.created",
            entity_type="purchase_requisition",
            entity_id=str(req.id),
            summary=f"Draft requisition {req.requisition_no} opened for pharmacy alert {alert.alert_type}",
            changes={"alert_id": str(alert.id), "requisition_no": req.requisition_no, "draft": True},
        )
    task = db.get(Task, req.task_id) if req.task_id else None
    return {
        "requisition": procurement.requisition_dict(db, req, NameCache()),
        "requisition_id": str(req.id),
        "requisition_no": req.requisition_no,
        "task_id": str(req.task_id) if req.task_id else None,
        "title": task.title if task else None,
        "status": req.status,
        "created": created,
        "alert": ph.alert_dict(db, alert),
    }


# -------------------------------------------------------- administrations
@router.get("/administrations")
def list_administrations(
    subject_id: str | None = None,
    inventory_item_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    access: AccessContext = Depends(_view),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict]:
    stmt = select(MedicationAdministration).where(MedicationAdministration.deleted_at.is_(None))
    if subject_id:
        pk = ph.parse_uuid(subject_id)
        if pk is None:
            return []
        stmt = stmt.where(MedicationAdministration.subject_id == pk)
    if inventory_item_id:
        product = ph.get_product(db, inventory_item_id)
        stmt = stmt.where(MedicationAdministration.inventory_item_id == product.inventory_item_id)
    names = NameCache()
    rows = db.scalars(stmt.order_by(MedicationAdministration.administered_at.desc()).limit(limit))
    return [ph.administration_dict(db, a, names) for a in rows]


@router.post("/administrations", status_code=201)
def record_administration(
    payload: MedicationAdministrationIn,
    access: AccessContext = Depends(_administer),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    """The dose actually given, bound to the exact lot (§11): validates
    the treatment context and the lot, posts the consumption, applies the
    product's authorised withdrawal and re-evaluates the thresholds."""
    adm = ph.administer(db, access, **payload.model_dump())
    out = ph.administration_dict(db, adm, NameCache())
    _audit(
        control_db,
        access,
        action="administration.recorded",
        entity_type="medication_administration",
        entity_id=str(adm.id),
        module_code="animal_health",
        summary=f"Administered {adm.dose_quantity:g} {adm.dose_unit} {out['medicine_name']} "
        f"({adm.route_code}) to "
        f"{adm.subject_type} {out['subject_name'] or adm.subject_id} from lot {out['lot_code']}",
        changes={
            "lot_id": str(adm.inventory_lot_id),
            "quantity_consumed": adm.quantity_consumed,
            "unit": adm.unit,
            "treatment_id": out["treatment_id"],
            "withdrawal_milk_until": adm.withdrawal_milk_until.isoformat()
            if adm.withdrawal_milk_until
            else None,
            "withdrawal_meat_until": adm.withdrawal_meat_until.isoformat()
            if adm.withdrawal_meat_until
            else None,
        },
    )
    return out


@router.post("/administrations/{administration_id}/reverse", status_code=201)
def reverse_administration(
    administration_id: str,
    payload: AdministrationReverse,
    access: AccessContext = Depends(_health_edit),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict:
    adm = ph.reverse_administration(
        db, access, ph.get_administration(db, administration_id), reason=payload.reason
    )
    _audit(
        control_db,
        access,
        action="administration.reversed",
        entity_type="medication_administration",
        entity_id=str(adm.id),
        module_code="animal_health",
        summary=f"Reversed an administration: {adm.reversal_reason}",
        changes={"reason": adm.reversal_reason},
    )
    return ph.administration_dict(db, adm, NameCache())
