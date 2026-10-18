"""Medicine & farm pharmacy management (OrigamiFarmOS
database/MEDICINE-PHARMACY-SCHEMA.md, database/MEDICINE-NOTIFICATION-RULES.md).

What the farm owns, in which lot, until when, what is *eligible* to use,
what must always be kept, and when to reorder — maintained even when no
animal is sick. Inventory stays the ledger: a medicine lot's on-hand
moves only with a posted `inventory_movement` row, and eligibility is
computed from lot facts on read:

    eligible = on hand − quarantined − blocked − recalled − expired
               − storage-noncompliant − past its after-opening use-by
               − reserved for another purpose

Stock alerts are logistics findings, never treatment advice: "fever
support stock low" means the configured product is below the farm's own
policy. Stocking a medicine never authorises its use; an administration
is bound to a prescription (treatment) or an approved protocol step when
the product requires one, consumes the exact lot, applies the product's
authorised withdrawal rule and re-evaluates the thresholds at once.

Every query runs in the tenant-scoped (RLS) session. Audit events are the
router's; this module keeps the farm-wide bell in step.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.farmos import feeding_program_service as programs
from app.farmos import pharmacy_catalog as pc
from app.farmos import procurement_service as procurement
from app.farmos import routing_service as routing
from app.farmos import uom_service
from app.farmos.deps import AccessContext
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.livestock_models import AnimalGroup
from app.farmos.livestock_service import ensure_utc, now_utc
from app.farmos.pharmacy_catalog import PharmacyError
from app.farmos.pharmacy_models import (
    InventoryLot,
    InventoryReservation,
    MedicationAdministration,
    MedicineProduct,
    MedicineProductIngredient,
    PharmacyStockAlert,
    PharmacyStockPolicy,
)
from app.farmos.production_models import Treatment
from app.farmos.routing_service import NameCache
from app.tenant_api.models import Animal, InventoryItem, InventoryMovement, Task

EPS = 0.0005
GROUP_ENTITY_TYPES = ("flock", "group")


def _f(value: Any) -> float:
    return float(value or 0)


def _s(value: uuid.UUID | None) -> str | None:
    return str(value) if value else None


# -------------------------------------------------------------- products
def get_product(db: Session, item_id: str | uuid.UUID | None) -> MedicineProduct:
    pk = parse_uuid(item_id)
    p = db.get(MedicineProduct, pk) if pk else None
    if p is None or p.deleted_at is not None:
        raise PharmacyError("Medicine not found", 404)
    return p


def item_of(db: Session, product: MedicineProduct) -> InventoryItem:
    item = db.get(InventoryItem, product.inventory_item_id)
    if item is None or item.deleted_at is not None:
        raise PharmacyError("The medicine's inventory item no longer exists", 404)
    return item


def _apply_product_fields(db: Session, access: AccessContext, product: MedicineProduct, fields: dict) -> None:
    simple = {
        "generic_name",
        "brand_name",
        "dosage_form",
        "strength_value",
        "strength_uom",
        "strength_basis",
        "administration_routes",
        "prescription_required",
        "antimicrobial",
        "controlled_medicine",
        "cold_chain_required",
        "storage_min_c",
        "storage_max_c",
        "opened_shelf_life_days",
        "default_pack_size",
        "pack_uom",
        "manufacturer_name",
        "authorization_reference",
        "species_codes",
        "lot_tracking_exception",
        "active",
    }
    for k, v in fields.items():
        if k in simple and v is not None:
            setattr(product, k, v)
    # Units are references (FOUNDATION uom): the code must exist.
    product.strength_uom = uom_service.optional(db, product.strength_uom, what="strength unit")
    product.pack_uom = uom_service.optional(db, product.pack_uom, what="pack unit")
    if fields.get("withdrawal_rules") is not None:
        rules = dict(fields["withdrawal_rules"])
        for key in ("milk_days", "meat_days", "egg_days"):
            if key in rules and (rules[key] is None or float(rules[key]) < 0):
                raise PharmacyError(f"withdrawal_rules.{key} must be zero or more days")
        product.withdrawal_rules_json = rules
    if product.dosage_form not in pc.DOSAGE_FORMS:
        raise PharmacyError(f"dosage_form must be one of {list(pc.DOSAGE_FORMS)}")
    for r in product.administration_routes or []:
        if r not in pc.ROUTES:
            raise PharmacyError(f"administration route '{r}' must be one of {list(pc.ROUTES)}")
    if fields.get("category_codes") is not None:
        product.category_codes = [pc.category(code)["code"] for code in fields["category_codes"]]
    if fields.get("ingredients") is not None:
        for row in list(product.ingredients):
            db.delete(row)
        product.ingredients.clear()
        seen: set[str] = set()
        for ing in fields["ingredients"]:
            ing = ing if isinstance(ing, dict) else ing.model_dump()
            code = pc.ingredient_code(ing["code"])
            if code in seen:
                continue
            seen.add(code)
            product.ingredients.append(
                MedicineProductIngredient(
                    tenant_id=access.tenant_id,
                    last_modified_by=access.membership_id,
                    ingredient_code=code,
                    ingredient_name=pc.ingredient_name(ing["code"], ing.get("name")),
                    concentration_value=ing.get("concentration_value"),
                    concentration_uom=uom_service.optional(
                        db, ing.get("concentration_uom"), what="concentration unit"
                    ),
                    concentration_basis=ing.get("concentration_basis"),
                )
            )


def create_product(
    db: Session,
    access: AccessContext,
    *,
    inventory_item_id: str | None = None,
    name: str | None = None,
    unit: str | None = None,
    supplier_label: str | None = None,
    unit_cost: float | None = None,
    **fields: Any,
) -> MedicineProduct:
    """A medicine the farm stocks (§2): over an existing inventory item or
    a new one. The item is the stock truth; this row adds what the
    medicine is and how it must be handled."""
    if inventory_item_id:
        item_pk = parse_uuid(inventory_item_id)
        item = db.get(InventoryItem, item_pk) if item_pk else None
        if item is None or item.deleted_at is not None:
            raise PharmacyError("Inventory item not found", 404)
        existing = db.get(MedicineProduct, item.id)
        if existing is not None and existing.deleted_at is None:
            raise PharmacyError("This inventory item is already a medicine product")
        if name:
            item.name = name
        item.category = "medicine"
        item.unit = uom_service.require(db, item.unit, what="stock unit")
    else:
        if not name or not name.strip():
            raise PharmacyError("A medicine needs a name")
        item = InventoryItem(
            tenant_id=access.tenant_id,
            last_modified_by=access.membership_id,
            name=name.strip(),
            category="medicine",
            unit=uom_service.require(db, unit or "items", what="stock unit"),
            current_qty=0,
            reorder_level=0,
            supplier_label=supplier_label,
            unit_cost=unit_cost,
        )
        db.add(item)
        db.flush()
    product = MedicineProduct(
        id=item.id,
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        inventory_item_id=item.id,
        created_by=access.user_id,
    )
    db.add(product)
    _apply_product_fields(db, access, product, fields)
    db.flush()
    return product


def update_product(
    db: Session, access: AccessContext, product: MedicineProduct, changes: dict
) -> MedicineProduct:
    _apply_product_fields(db, access, product, changes)
    product.last_modified_by = access.membership_id
    product.version += 1
    db.flush()
    return product


# ------------------------------------------------------------------ lots
def get_lot(db: Session, lot_id: str | uuid.UUID | None) -> InventoryLot:
    pk = parse_uuid(lot_id)
    lot = db.get(InventoryLot, pk) if pk else None
    if lot is None or lot.deleted_at is not None:
        raise PharmacyError("Lot not found", 404)
    return lot


def lot_expired(lot: InventoryLot, at: datetime | None = None) -> bool:
    at = at or now_utc()
    exp = ensure_utc(lot.expiry_date)
    if exp is not None and exp <= at:
        return True
    ub = ensure_utc(lot.use_by_after_opening)
    return ub is not None and ub <= at


def refresh_lot_status(lot: InventoryLot, at: datetime | None = None) -> None:
    """Expiry and depletion are facts, reflected on read (as feed lots do),
    so a lot that expired overnight is ineligible this morning untouched.
    The quality status (INVENTORY-SCHEMA §3) follows."""
    if lot.status == "active" and lot_expired(lot, at):
        lot.status = "expired"
    elif lot.status == "active" and lot.quantity_on_hand <= EPS:
        lot.status = "depleted"
    elif lot.status == "depleted" and lot.quantity_on_hand > EPS:
        lot.status = "active"
    lot.quality_status = pc.QUALITY_FOR_STATUS.get(lot.status, "RELEASED")


def ineligibility(lot: InventoryLot, at: datetime | None = None) -> str | None:
    """Why this lot does not count (§7), or None when it is eligible."""
    refresh_lot_status(lot, at)
    if lot.status in ("quarantined", "blocked", "recalled", "expired"):
        return lot.status
    if lot.status == "depleted" or lot.quantity_on_hand <= 0:
        return "depleted"
    if lot.storage_status != "COMPLIANT":
        return "storage_exception"
    if lot.cold_chain_exception:
        return "cold_chain_exception"
    return None


def _unique_lot_code(db: Session, item_id: uuid.UUID, base: str) -> str:
    code, n = base, 2
    while db.scalar(
        select(InventoryLot.id).where(
            InventoryLot.inventory_item_id == item_id, InventoryLot.lot_code == code
        )
    ):
        code = f"{base}-{n}"
        n += 1
    return code


def _movement(
    db: Session,
    access: AccessContext,
    item: InventoryItem,
    *,
    delta: float,
    unit_cost: float | None,
    reason: str,
    linked_entity_type: str | None,
    linked_entity_id: str | None,
    lot_id: uuid.UUID | None,
    occurred_at: datetime,
    location_id: str | None = None,
    correlation_id: uuid.UUID | None = None,
    touch_item: bool = True,
) -> InventoryMovement:
    """A posted ledger row; the item balance follows it (never the reverse)."""
    row = InventoryMovement(
        tenant_id=item.tenant_id,
        last_modified_by=access.membership_id,
        inventory_item_id=item.id,
        quantity_delta=delta,
        reason=reason,
        linked_entity_type=linked_entity_type,
        linked_entity_id=linked_entity_id,
        occurred_at=occurred_at,
        lot_id=lot_id,
        unit_cost=unit_cost,
        location_id=location_id,
        correlation_id=correlation_id,
    )
    db.add(row)
    if touch_item:
        item.current_qty = round(_f(item.current_qty) + delta, 6)
        item.version += 1
        item.last_modified_by = access.membership_id
    db.flush()
    return row


def receive_lot(
    db: Session,
    access: AccessContext,
    product: MedicineProduct,
    *,
    quantity: float,
    unit: str | None = None,
    lot_code: str | None = None,
    expiry_date: datetime | None = None,
    unit_cost: float | None = None,
    supplier_id: str | None = None,
    supplier_label: str | None = None,
    location_id: str | None = None,
    rejected_quantity: float = 0,
    source_type: str = "purchased",
    reference: str | None = None,
    notes: str | None = None,
    received_at: datetime | None = None,
    storage_status: str = "COMPLIANT",
    cold_chain_exception: bool = False,
    supplier_lot_code: str | None = None,
    manufactured_at: datetime | None = None,
    currency: str | None = None,
    source_reference_type: str | None = None,
    source_reference_id: str | None = None,
) -> InventoryLot:
    """Goods receipt: each receipt records the medicine lot and expiry
    (acceptance 2) unless the product carries a recorded exception. A
    receipt that names its purchase requisition closes it."""
    item = item_of(db, product)
    if quantity <= 0:
        raise PharmacyError("Received quantity must be greater than zero.")
    if rejected_quantity < 0 or rejected_quantity > quantity:
        raise PharmacyError("Rejected quantity must be between zero and the received quantity.")
    if not product.lot_tracking_exception:
        if not lot_code or not lot_code.strip():
            raise PharmacyError("A medicine receipt must record the manufacturer's lot number.")
        if expiry_date is None:
            raise PharmacyError("A medicine receipt must record the expiry date.")
    if storage_status not in pc.STORAGE_STATUSES:
        raise PharmacyError(f"storage_status must be one of {list(pc.STORAGE_STATUSES)}")
    if source_type not in pc.LOT_SOURCE_TYPES:
        raise PharmacyError("source_type must be purchased, opening_balance, donation or return")
    manufactured = ensure_utc(manufactured_at)
    expiry = ensure_utc(expiry_date)
    if manufactured is not None and expiry is not None and manufactured >= expiry:
        raise PharmacyError("The manufacture date must be before the expiry date.")
    supplier_pk = None
    if supplier_id:
        supplier_pk = parse_uuid(supplier_id)
        if supplier_pk is None:
            raise PharmacyError("supplier_id must be an id.")
    stock_unit = uom_service.require(db, item.unit, what="stock unit")
    entered_unit = uom_service.require(db, unit or stock_unit, what="unit")
    qty = uom_service.convert(db, quantity, entered_unit, stock_unit)
    rejected = uom_service.convert(db, rejected_quantity, entered_unit, stock_unit)
    requisition = None
    if source_reference_type == "purchase_requisition" and source_reference_id:
        requisition = procurement.get_requisition(db, source_reference_id)
        if not any(line.inventory_item_id == item.id for line in requisition.lines):
            raise PharmacyError(f"Requisition {requisition.requisition_no} has no line for {item.name}.")
    accepted = round(qty - rejected, 6)
    when = ensure_utc(received_at) or now_utc()
    lot = InventoryLot(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        inventory_item_id=item.id,
        lot_code=_unique_lot_code(
            db, item.id, (lot_code or f"{item.name[:20].upper()}-{when:%Y%m%d}").strip()
        ),
        supplier_lot_code=(supplier_lot_code or "").strip() or None,
        manufactured_at=manufactured,
        source_type=source_type,
        source_reference_type=source_reference_type,
        source_reference_id=source_reference_id,
        supplier_id=supplier_pk,
        supplier_label=supplier_label or item.supplier_label,
        location_id=location_id,
        received_at=when,
        expiry_date=expiry,
        received_quantity=qty,
        accepted_quantity=accepted,
        rejected_quantity=rejected,
        quantity_on_hand=accepted,
        unit=stock_unit,
        unit_cost=unit_cost if unit_cost is not None else (float(item.unit_cost) if item.unit_cost else None),
        currency=currency,
        status="active" if accepted > 0 else "depleted",
        quality_status="RELEASED",
        reference=reference,
        notes=notes,
        created_by=access.user_id,
        storage_status=storage_status,
        cold_chain_exception=cold_chain_exception,
    )
    db.add(lot)
    db.flush()
    # `product.lots` is a view over the item's lots; a collection loaded
    # before this receipt would not include it, and eligibility is read
    # from that collection — so forget it and let it reload.
    db.expire(product, ["lots"])
    if accepted > 0:
        _movement(
            db,
            access,
            item,
            delta=accepted,
            unit_cost=lot.unit_cost,
            reason={"purchased": "purchase", "opening_balance": "opening_balance"}.get(
                source_type, source_type
            ),
            linked_entity_type="inventory_lot",
            linked_entity_id=str(lot.id),
            lot_id=lot.id,
            occurred_at=when,
            location_id=location_id,
        )
        if source_type == "purchased":
            item.last_purchase = when
            if unit_cost is not None:
                item.unit_cost = unit_cost
    if requisition is not None:
        procurement.mark_received(db, access, requisition, inventory_item_id=item.id)
    evaluate_item(db, access, product)
    return lot


# ----------------------------------------------------------- reservations
def active_reservations(
    db: Session,
    product: MedicineProduct,
    *,
    lot: InventoryLot | None = None,
    location_id: str | None = None,
) -> list[InventoryReservation]:
    at = now_utc()
    rows = db.scalars(
        select(InventoryReservation).where(
            InventoryReservation.inventory_item_id == product.inventory_item_id,
            InventoryReservation.status == "active",
            InventoryReservation.deleted_at.is_(None),
        )
    ).all()
    out = []
    for r in rows:
        valid_to = ensure_utc(r.valid_to)
        if valid_to is not None and valid_to <= at:
            r.status = "expired"
            r.released_at = at
            r.release_reason = "validity ended"
            continue
        if lot is not None and r.inventory_lot_id not in (None, lot.id):
            continue
        if location_id is not None and r.location_id not in (None, location_id):
            continue
        out.append(r)
    return out


def reservation_remaining(r: InventoryReservation) -> float:
    return max(r.quantity - (r.consumed_quantity or 0), 0.0)


def reserved_quantity(
    db: Session,
    product: MedicineProduct,
    *,
    lot: InventoryLot | None = None,
    location_id: str | None = None,
    exclude: InventoryReservation | None = None,
) -> float:
    """Stock held for another purpose (§7 "reserved for another protected
    purpose"), in the item's unit."""
    item = item_of(db, product)
    total = 0.0
    for r in active_reservations(db, product, lot=lot, location_id=location_id):
        if exclude is not None and r.id == exclude.id:
            continue
        total += uom_service.convert(db, reservation_remaining(r), r.unit, item.unit)
    return round(total, 6)


def reserve(
    db: Session,
    access: AccessContext,
    product: MedicineProduct,
    *,
    quantity: float,
    unit: str | None = None,
    lot: InventoryLot | None = None,
    location_id: str | None = None,
    purpose_type: str,
    purpose_id: str | None = None,
    purpose_label: str | None = None,
    valid_to: datetime | None = None,
) -> InventoryReservation:
    """Holds eligible stock for a purpose (INVENTORY-SCHEMA §5, §8): never
    more than is eligible and unreserved, never ineligible stock."""
    item = item_of(db, product)
    if purpose_type not in pc.RESERVATION_PURPOSES:
        raise PharmacyError(f"purpose_type must be one of {list(pc.RESERVATION_PURPOSES)}")
    if quantity <= 0:
        raise PharmacyError("Reserved quantity must be greater than zero.")
    unit_code = uom_service.require(db, unit or item.unit, what="unit")
    qty = uom_service.convert(db, quantity, unit_code, item.unit)
    if lot is not None:
        if lot.inventory_item_id != product.inventory_item_id:
            raise PharmacyError("That lot belongs to another medicine.")
        why = ineligibility(lot)
        if why is not None:
            raise PharmacyError(
                f"Lot {lot.lot_code} is not eligible ({why.replace('_', ' ')}) and cannot be reserved."
            )
        free = lot.quantity_on_hand - reserved_quantity(db, product, lot=lot)
    else:
        free = stock_breakdown(db, product, location_id=location_id)["eligible"]
    if qty > free + EPS:
        raise PharmacyError(
            f"Only {max(free, 0):g} {item.unit} of {item.name} is eligible and unreserved; "
            f"{qty:g} cannot be held."
        )
    r = InventoryReservation(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        inventory_item_id=item.id,
        inventory_lot_id=lot.id if lot else None,
        location_id=location_id,
        quantity=qty,
        consumed_quantity=0,
        unit=item.unit,
        purpose_type=purpose_type,
        purpose_id=purpose_id,
        purpose_label=purpose_label,
        valid_from=now_utc(),
        valid_to=ensure_utc(valid_to),
        status="active",
        created_by=access.user_id,
    )
    db.add(r)
    db.flush()
    evaluate_item(db, access, product)
    return r


def release_reservation(
    db: Session, access: AccessContext, r: InventoryReservation, *, reason: str | None
) -> InventoryReservation:
    if r.status != "active":
        return r
    r.status = "released"
    r.released_at = now_utc()
    r.release_reason = (reason or "released").strip()
    r.last_modified_by = access.membership_id
    evaluate_item(db, access, get_product(db, r.inventory_item_id))
    return r


def get_reservation(db: Session, reservation_id: str | uuid.UUID | None) -> InventoryReservation:
    pk = parse_uuid(reservation_id)
    r = db.get(InventoryReservation, pk) if pk else None
    if r is None or r.deleted_at is not None:
        raise PharmacyError("Reservation not found", 404)
    return r


def reservation_dict(db: Session, r: InventoryReservation) -> dict:
    item = db.get(InventoryItem, r.inventory_item_id)
    lot = db.get(InventoryLot, r.inventory_lot_id) if r.inventory_lot_id else None
    return {
        "id": str(r.id),
        "inventory_item_id": str(r.inventory_item_id),
        "medicine_name": item.name if item else None,
        "inventory_lot_id": _s(r.inventory_lot_id),
        "lot_code": lot.lot_code if lot else None,
        "location_id": r.location_id,
        "quantity": r.quantity,
        "consumed_quantity": r.consumed_quantity,
        "remaining": reservation_remaining(r),
        "unit": r.unit,
        "purpose_type": r.purpose_type,
        "purpose_id": r.purpose_id,
        "purpose_label": r.purpose_label,
        "valid_from": r.valid_from,
        "valid_to": r.valid_to,
        "status": r.status,
        "created_at": r.created_at,
        "created_by": _s(r.created_by),
        "released_at": r.released_at,
        "release_reason": r.release_reason,
    }


# -------------------------------------------------------------- transfers
def lot_location_balances(db: Session, lot: InventoryLot) -> dict[str | None, float]:
    """The lot's quantity per location, derived from its movements
    (INVENTORY-SCHEMA §7): a movement without a location is at the lot's
    receiving location. Never a location flipped on the lot."""
    balances: dict[str | None, float] = {}
    for mv in db.scalars(
        select(InventoryMovement)
        .where(InventoryMovement.lot_id == lot.id)
        .order_by(InventoryMovement.occurred_at)
    ):
        loc = mv.location_id or lot.location_id
        balances[loc] = round(balances.get(loc, 0.0) + _f(mv.quantity_delta), 6)
    kept = {k: v for k, v in balances.items() if abs(v) > EPS}
    if kept:
        return kept
    return {lot.location_id: lot.quantity_on_hand} if lot.quantity_on_hand > EPS else {}


def lot_quantity_at(db: Session, lot: InventoryLot, location_id: str | None) -> float:
    return lot_location_balances(db, lot).get(location_id, 0.0)


def _default_issue_location(db: Session, lot: InventoryLot) -> str | None:
    balances = {k: v for k, v in lot_location_balances(db, lot).items() if v > EPS}
    if not balances:
        return lot.location_id
    if lot.location_id in balances:
        return lot.location_id
    return max(balances, key=lambda k: balances[k])


def transfer(
    db: Session,
    access: AccessContext,
    product: MedicineProduct,
    *,
    lot: InventoryLot,
    quantity: float,
    to_location_id: str,
    from_location_id: str | None = None,
    reason: str | None = None,
) -> dict:
    """Moves part of a lot between locations as a paired OUT / IN sharing
    one correlation id (INVENTORY-SCHEMA §4). On-hand is unchanged; the
    per-location picture, and any per-location policy, changes."""
    item = item_of(db, product)
    if lot.inventory_item_id != product.inventory_item_id:
        raise PharmacyError("That lot belongs to another medicine.")
    if quantity <= 0:
        raise PharmacyError("Transfer quantity must be greater than zero.")
    if not to_location_id or not to_location_id.strip():
        raise PharmacyError("Name the destination location.")
    source = from_location_id if from_location_id is not None else _default_issue_location(db, lot)
    if source == to_location_id:
        raise PharmacyError("The lot is already at that location.")
    at_source = lot_quantity_at(db, lot, source)
    if quantity > at_source + EPS:
        raise PharmacyError(
            f"Only {at_source:g} {item.unit} of lot {lot.lot_code} is at the source location."
        )
    correlation = uuid.uuid4()
    at = now_utc()
    out_mv = _movement(
        db,
        access,
        item,
        delta=-quantity,
        unit_cost=lot.unit_cost,
        reason="transfer_out",
        linked_entity_type="inventory_lot",
        linked_entity_id=str(lot.id),
        lot_id=lot.id,
        occurred_at=at,
        location_id=source,
        correlation_id=correlation,
        touch_item=False,
    )
    in_mv = _movement(
        db,
        access,
        item,
        delta=quantity,
        unit_cost=lot.unit_cost,
        reason="transfer_in",
        linked_entity_type="inventory_lot",
        linked_entity_id=str(lot.id),
        lot_id=lot.id,
        occurred_at=at,
        location_id=to_location_id,
        correlation_id=correlation,
        touch_item=False,
    )
    evaluate_item(db, access, product)
    return {
        "correlation_id": str(correlation),
        "out_transaction_id": str(out_mv.id),
        "in_transaction_id": str(in_mv.id),
        "from_location_id": source,
        "to_location_id": to_location_id,
        "quantity": quantity,
        "unit": item.unit,
        "balances": lot_location_balances(db, lot),
    }


def set_lot_status(
    db: Session,
    access: AccessContext,
    lot: InventoryLot,
    status: str,
    *,
    reason: str | None,
    recall_reference: str | None = None,
) -> InventoryLot:
    if status not in ("active", "quarantined", "blocked", "recalled"):
        raise PharmacyError("status must be active, quarantined, blocked or recalled")
    if status in ("quarantined", "blocked", "recalled") and not (reason and reason.strip()):
        raise PharmacyError("Say why the lot is being taken out of use.")
    product = get_product(db, lot.inventory_item_id)
    lot.status = status
    lot.quality_status = pc.QUALITY_FOR_STATUS.get(status, "RELEASED")
    lot.last_modified_by = access.membership_id
    lot.version += 1
    if status == "quarantined":
        lot.quarantine_reason = reason
    elif status == "recalled":
        lot.recalled_at = now_utc()
        lot.recall_reference = recall_reference
    elif status == "active":
        lot.quarantine_reason = None
        refresh_lot_status(lot)
    if status in ("quarantined", "blocked", "recalled"):
        # Held stock cannot stay promised to anyone.
        for r in active_reservations(db, product, lot=lot):
            if r.inventory_lot_id == lot.id:
                r.status = "released"
                r.released_at = now_utc()
                r.release_reason = f"lot {status}"
    evaluate_item(db, access, product)
    return lot


def set_storage_status(
    db: Session,
    access: AccessContext,
    lot: InventoryLot,
    *,
    storage_status: str,
    cold_chain_exception: bool,
    note: str | None,
) -> InventoryLot:
    if storage_status not in pc.STORAGE_STATUSES:
        raise PharmacyError(f"storage_status must be one of {list(pc.STORAGE_STATUSES)}")
    lot.storage_status = storage_status
    lot.cold_chain_exception = cold_chain_exception
    lot.storage_note = note
    lot.last_modified_by = access.membership_id
    lot.version += 1
    evaluate_item(db, access, get_product(db, lot.inventory_item_id))
    return lot


def open_lot(
    db: Session, access: AccessContext, lot: InventoryLot, *, opened_at: datetime | None = None
) -> InventoryLot:
    """First use of a multi-dose container: the after-opening shelf life
    (§6) starts now, and the lot stops being eligible when it runs out."""
    product = get_product(db, lot.inventory_item_id)
    if lot.opened_at is not None:
        return lot
    lot.opened_at = ensure_utc(opened_at) or now_utc()
    if product.opened_shelf_life_days:
        lot.use_by_after_opening = lot.opened_at + timedelta(days=product.opened_shelf_life_days)
    lot.last_modified_by = access.membership_id
    lot.version += 1
    refresh_lot_status(lot)
    evaluate_item(db, access, product)
    return lot


def adjust(
    db: Session,
    access: AccessContext,
    product: MedicineProduct,
    *,
    lot: InventoryLot,
    delta: float,
    reason: str,
    explanation: str,
) -> InventoryMovement:
    """An auditable correction on one lot: a count, a breakage, a return.
    Never below zero, never without an explanation."""
    if not explanation or not explanation.strip():
        raise PharmacyError("An adjustment needs an explanation.")
    if delta == 0:
        raise PharmacyError("Adjustment quantity cannot be zero.")
    if reason not in pc.ADJUSTMENT_REASONS:
        raise PharmacyError(f"reason must be one of {list(pc.ADJUSTMENT_REASONS)}")
    if lot.inventory_item_id != product.inventory_item_id:
        raise PharmacyError("That lot belongs to another medicine.")
    if lot.quantity_on_hand + delta < -EPS:
        raise PharmacyError(
            f"Only {lot.quantity_on_hand:g} {lot.unit} on hand in lot {lot.lot_code}; "
            "stock cannot go negative."
        )
    item = item_of(db, product)
    location = _default_issue_location(db, lot) if delta < 0 else lot.location_id
    lot.quantity_on_hand = round(lot.quantity_on_hand + delta, 6)
    lot.last_modified_by = access.membership_id
    lot.version += 1
    refresh_lot_status(lot)
    mv = _movement(
        db,
        access,
        item,
        delta=delta,
        unit_cost=lot.unit_cost,
        reason=reason,
        linked_entity_type="inventory_lot",
        linked_entity_id=str(lot.id),
        lot_id=lot.id,
        occurred_at=now_utc(),
        location_id=location,
    )
    evaluate_item(db, access, product)
    return mv


def lot_dict(db: Session, lot: InventoryLot) -> dict:
    why = ineligibility(lot)
    product = db.get(MedicineProduct, lot.inventory_item_id)
    reserved = reserved_quantity(db, product, lot=lot) if product is not None else 0.0
    return {
        "id": str(lot.id),
        "inventory_item_id": str(lot.inventory_item_id),
        "lot_code": lot.lot_code,
        "supplier_lot_code": lot.supplier_lot_code,
        "source_type": lot.source_type,
        "source_reference_type": lot.source_reference_type,
        "source_reference_id": lot.source_reference_id,
        "manufactured_at": lot.manufactured_at,
        "supplier_id": _s(lot.supplier_id),
        "supplier_label": lot.supplier_label,
        "location_id": lot.location_id,
        "received_at": lot.received_at,
        "expiry_date": lot.expiry_date,
        "received_quantity": lot.received_quantity,
        "accepted_quantity": lot.accepted_quantity,
        "rejected_quantity": lot.rejected_quantity,
        "quantity_on_hand": lot.quantity_on_hand,
        "unit": lot.unit,
        "unit_cost": lot.unit_cost,
        "currency": lot.currency,
        "status": lot.status,
        "quality_status": lot.quality_status,
        "eligible": why is None,
        "ineligible_reason": why,
        "reserved_quantity": reserved,
        "free_quantity": round(max(lot.quantity_on_hand - reserved, 0.0), 6) if why is None else 0.0,
        "location_balances": [
            {"location_id": k, "quantity": v} for k, v in lot_location_balances(db, lot).items()
        ],
        "reference": lot.reference,
        "notes": lot.notes,
        "opened_at": lot.opened_at,
        "use_by_after_opening": lot.use_by_after_opening,
        "storage_status": lot.storage_status,
        "cold_chain_exception": bool(lot.cold_chain_exception),
        "storage_note": lot.storage_note,
        "quarantine_reason": lot.quarantine_reason,
        "recalled_at": lot.recalled_at,
        "recall_reference": lot.recall_reference,
    }


# ------------------------------------------------------------------ stock
def stock_breakdown(
    db: Session,
    product: MedicineProduct,
    *,
    location_id: str | None = None,
    warning_days: int = 60,
    at: datetime | None = None,
) -> dict:
    """On hand against eligible (§7), with every excluded quantity named
    and the expiry picture the dashboard shows (§10)."""
    at = at or now_utc()
    out: dict[str, Any] = {
        "on_hand": 0.0,
        "eligible": 0.0,
        "eligible_before_reservations": 0.0,
        "reserved": 0.0,
        "quarantined": 0.0,
        "blocked": 0.0,
        "recalled": 0.0,
        "expired": 0.0,
        "storage_exception": 0.0,
        "lot_count": 0,
        "eligible_lot_count": 0,
        "earliest_expiry": None,
        "expiring_quantity": 0.0,
        "expiring_lots": [],
    }
    horizon = at + timedelta(days=warning_days)
    for lot in product.lots:
        if lot.deleted_at is not None:
            continue
        # Farm-wide: the lot's on-hand. Per location: the lot's balance
        # there, derived from its movements (transfers included).
        qty = lot.quantity_on_hand if location_id is None else lot_quantity_at(db, lot, location_id)
        if qty <= EPS:
            continue
        out["on_hand"] += qty
        out["lot_count"] += 1
        why = ineligibility(lot, at)
        if why is None:
            out["eligible"] += qty
            out["eligible_lot_count"] += 1
            exp = ensure_utc(lot.expiry_date)
            ub = ensure_utc(lot.use_by_after_opening)
            if ub is not None:
                exp = ub if exp is None else min(exp, ub)
            if exp is not None:
                if out["earliest_expiry"] is None or exp < out["earliest_expiry"]:
                    out["earliest_expiry"] = exp
                if exp <= horizon:
                    out["expiring_quantity"] += qty
                    out["expiring_lots"].append(
                        {"lot_id": str(lot.id), "lot_code": lot.lot_code, "quantity": qty, "expiry_date": exp}
                    )
        elif why == "expired":
            out["expired"] += qty
        elif why in ("storage_exception", "cold_chain_exception"):
            out["storage_exception"] += qty
        elif why in ("quarantined", "blocked", "recalled"):
            out[why] += qty
    # §7: stock reserved for another protected purpose is not eligible.
    out["eligible_before_reservations"] = out["eligible"]
    out["reserved"] = min(reserved_quantity(db, product, location_id=location_id), out["eligible"])
    out["eligible"] = out["eligible"] - out["reserved"]
    for k in (
        "on_hand",
        "eligible",
        "eligible_before_reservations",
        "reserved",
        "quarantined",
        "blocked",
        "recalled",
        "expired",
        "storage_exception",
        "expiring_quantity",
    ):
        out[k] = round(out[k], 6)
    return out


def policy_for(
    db: Session, product: MedicineProduct, location_id: str | None = None
) -> PharmacyStockPolicy | None:
    loc = (
        PharmacyStockPolicy.location_id.is_(None)
        if location_id is None
        else (PharmacyStockPolicy.location_id == location_id)
    )
    return db.scalar(
        select(PharmacyStockPolicy).where(
            PharmacyStockPolicy.inventory_item_id == product.inventory_item_id,
            loc,
            PharmacyStockPolicy.deleted_at.is_(None),
        )
    )


def policies_for(db: Session, product: MedicineProduct) -> list[PharmacyStockPolicy]:
    return list(
        db.scalars(
            select(PharmacyStockPolicy).where(
                PharmacyStockPolicy.inventory_item_id == product.inventory_item_id,
                PharmacyStockPolicy.active.is_(True),
                PharmacyStockPolicy.deleted_at.is_(None),
            )
        )
    )


def stock_status(eligible: float, policy: PharmacyStockPolicy | None) -> str:
    """OK / LOW / CRITICAL / OUT from the farm's thresholds
    (MEDICINE-NOTIFICATION-RULES.md §Severity)."""
    if policy is None:
        return "OUT" if eligible <= 0 else "OK"
    if eligible <= 0:
        if policy.essential:
            return "OUT"
        if policy.critical_stock_base is not None:
            return "CRITICAL"
        return "LOW" if policy.minimum_stock_base else "OK"
    if policy.critical_stock_base is not None and eligible <= policy.critical_stock_base:
        return "CRITICAL"
    if policy.minimum_stock_base is not None and eligible < policy.minimum_stock_base:
        return "LOW"
    return "OK"


def recommended_reorder(
    product: MedicineProduct, policy: PharmacyStockPolicy | None, eligible: float, incoming: float = 0
) -> float:
    """recommended = max(0, target − eligible − confirmed incoming), rounded
    up to the pack size (§9). Never a purchase order."""
    if policy is None or policy.target_stock_base is None:
        return 0.0
    need = max(0.0, policy.target_stock_base - eligible - incoming)
    if need > 0 and product.default_pack_size and product.default_pack_size > 0:
        need = math.ceil(need / product.default_pack_size - 1e-9) * product.default_pack_size
    return round(need, 6)


def confirmed_incoming(db: Session, product: MedicineProduct) -> float:
    """Submitted / approved requisition lines for the medicine, in its
    stock unit (PROCUREMENT §5; the §9 formula's `confirmed_incoming`)."""
    return procurement.incoming_quantity(db, product.inventory_item_id, item_of(db, product).unit)


def average_daily_consumption(db: Session, product: MedicineProduct, *, days: int = 30) -> float:
    since = now_utc() - timedelta(days=days)
    used = db.scalar(
        select(func.coalesce(func.sum(-InventoryMovement.quantity_delta), 0)).where(
            InventoryMovement.inventory_item_id == product.inventory_item_id,
            InventoryMovement.reason == "administration",
            InventoryMovement.occurred_at >= since,
        )
    )
    return round(_f(used) / days, 6)


# ----------------------------------------------------------------- policy
def upsert_policy(
    db: Session,
    access: AccessContext,
    product: MedicineProduct,
    *,
    location_id: str | None = None,
    **fields: Any,
) -> PharmacyStockPolicy:
    for key in (
        "minimum_stock_base",
        "target_stock_base",
        "critical_stock_base",
        "minimum_days_cover",
        "lead_time_days",
    ):
        v = fields.get(key)
        if v is not None and v < 0:
            raise PharmacyError(f"{key} must be zero or more")
    mn, cr, tg = (
        fields.get("minimum_stock_base"),
        fields.get("critical_stock_base"),
        fields.get("target_stock_base"),
    )
    if cr is not None and mn is not None and cr > mn:
        raise PharmacyError("The critical level cannot be above the minimum.")
    if tg is not None and mn is not None and tg < mn:
        raise PharmacyError("The target level cannot be below the minimum.")
    if fields.get("expiry_warning_days") is not None and fields["expiry_warning_days"] < 0:
        raise PharmacyError("expiry_warning_days must be zero or more")
    policy = policy_for(db, product, location_id)
    if policy is None:
        policy = PharmacyStockPolicy(
            tenant_id=access.tenant_id,
            inventory_item_id=product.inventory_item_id,
            location_id=location_id,
            policy_version=0,
        )
        db.add(policy)
    for k in (
        "essential",
        "minimum_stock_base",
        "target_stock_base",
        "critical_stock_base",
        "minimum_days_cover",
        "lead_time_days",
        "preferred_supplier_id",
        "alert_enabled",
        "expiry_warning_days",
        "auto_draft_requisition",
        "active",
    ):
        if k in fields:
            setattr(policy, k, fields[k])
    policy.policy_version = (policy.policy_version or 0) + 1
    policy.updated_by = access.user_id
    policy.last_modified_by = access.membership_id
    db.flush()
    evaluate_item(db, access, product)
    return policy


def policy_dict(p: PharmacyStockPolicy | None) -> dict | None:
    if p is None:
        return None
    return {
        "id": str(p.id),
        "inventory_item_id": str(p.inventory_item_id),
        "location_id": p.location_id,
        "essential": p.essential,
        "minimum_stock_base": p.minimum_stock_base,
        "target_stock_base": p.target_stock_base,
        "critical_stock_base": p.critical_stock_base,
        "minimum_days_cover": p.minimum_days_cover,
        "lead_time_days": p.lead_time_days,
        "preferred_supplier_id": p.preferred_supplier_id,
        "alert_enabled": p.alert_enabled,
        "expiry_warning_days": p.expiry_warning_days,
        "auto_draft_requisition": p.auto_draft_requisition,
        "active": p.active,
        "version": p.policy_version,
        "updated_by": _s(p.updated_by),
        "updated_at": p.updated_at,
    }


# ----------------------------------------------------------------- alerts
def _open_alert(db: Session, key: str) -> PharmacyStockAlert | None:
    return db.scalar(
        select(PharmacyStockAlert).where(
            PharmacyStockAlert.deduplication_key == key, PharmacyStockAlert.status != "resolved"
        )
    )


def _location_filter(location_id: str | None):
    if location_id is None:
        return PharmacyStockAlert.location_id.is_(None)
    return PharmacyStockAlert.location_id == location_id


def _bell_for_alert(
    db: Session, access: AccessContext, product: MedicineProduct, row: PharmacyStockAlert
) -> None:
    item = item_of(db, product)
    routing.notify_bell(
        db,
        access.tenant_id,
        module_code="inventory",
        notification_type=f"pharmacy_{row.alert_type.lower()}",
        title=f"{item.name}: {row.alert_type.replace('_', ' ').lower()}",
        description=row.explanation[:300],
        priority=row.severity,
        entity_type="medicine_product",
        entity_id=str(product.inventory_item_id),
        source_type="pharmacy_stock_alert",
        source_id=str(row.id),
        actor=access.membership_id,
    )


def _raise_alert(
    db: Session,
    access: AccessContext,
    product: MedicineProduct,
    *,
    alert_type: str,
    location_id: str | None,
    policy: PharmacyStockPolicy | None,
    breakdown: dict,
    explanation: str,
    reorder: float = 0.0,
) -> tuple[PharmacyStockAlert, bool]:
    """One open alert per condition (§8): a repeat scan refreshes the
    figures on the existing row instead of raising a twin."""
    pv = policy.policy_version if policy else 0
    key = f"{product.inventory_item_id}|{location_id or '*'}|{alert_type}|v{pv}"
    existing = _open_alert(db, key)
    at = now_utc()
    if existing is None:
        # The same condition under an older policy version is superseded.
        for stale in db.scalars(
            select(PharmacyStockAlert).where(
                PharmacyStockAlert.inventory_item_id == product.inventory_item_id,
                PharmacyStockAlert.alert_type == alert_type,
                PharmacyStockAlert.status != "resolved",
                _location_filter(location_id),
            )
        ):
            stale.status = "resolved"
            stale.resolved_at = at
            stale.resolution_note = "policy changed"
            routing.clear_bell(db, source_type="pharmacy_stock_alert", source_id=str(stale.id), at=at)
    row = existing or PharmacyStockAlert(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        inventory_item_id=product.inventory_item_id,
        location_id=location_id,
        alert_type=alert_type,
        severity=pc.SEVERITY[alert_type],
        detected_at=at,
        status="open",
        deduplication_key=key,
    )
    row.last_seen_at = at
    row.eligible_available_base = breakdown["eligible"]
    row.minimum_stock_base = policy.minimum_stock_base if policy else None
    row.target_stock_base = policy.target_stock_base if policy else None
    row.earliest_expiry_date = breakdown["earliest_expiry"]
    if alert_type == "EXPIRING_SOON":
        row.expiring_quantity = breakdown["expiring_quantity"]
    elif alert_type == "EXPIRED_STOCK":
        row.expiring_quantity = breakdown["expired"]
    else:
        row.expiring_quantity = None
    row.recommended_reorder_base = reorder or None
    row.explanation = explanation
    if existing is None:
        db.add(row)
        db.flush()
    if row.status == "open":
        _bell_for_alert(db, access, product, row)
    return row, existing is None


def _resolve_alerts(
    db: Session,
    access: AccessContext,
    product: MedicineProduct,
    *,
    alert_types: tuple[str, ...],
    location_id: str | None,
    reason: str,
) -> list[PharmacyStockAlert]:
    rows = db.scalars(
        select(PharmacyStockAlert).where(
            PharmacyStockAlert.inventory_item_id == product.inventory_item_id,
            PharmacyStockAlert.alert_type.in_(alert_types),
            PharmacyStockAlert.status != "resolved",
            _location_filter(location_id),
        )
    ).all()
    at = now_utc()
    for a in rows:
        a.status = "resolved"
        a.resolved_at = at
        a.resolution_note = reason
        routing.clear_bell(db, source_type="pharmacy_stock_alert", source_id=str(a.id), at=at)
        for t in db.scalars(
            select(Task).where(
                Task.source_type == "pharmacy_requisition", Task.source_id == str(a.id), Task.status == "open"
            )
        ):
            # The shortage is gone; the draft requisition is closed, not deleted.
            t.status = "done"
        # A draft nobody has acted on is withdrawn with the shortage; a
        # submitted or approved requisition is a person's decision and stays.
        if a.requisition_id and not reason.startswith("superseded"):
            req = db.get(procurement.PurchaseRequisition, a.requisition_id)
            if req is not None and req.status == "draft":
                procurement.cancel(db, access, req, reason=f"pharmacy shortage resolved: {reason}")
    return list(rows)


def evaluate_item(
    db: Session, access: AccessContext, product: MedicineProduct, *, at: datetime | None = None
) -> list[PharmacyStockAlert]:
    """Re-evaluates one medicine against its policies and its lots
    (MEDICINE-NOTIFICATION-RULES.md): raises or refreshes the alerts whose
    condition holds, resolves the ones whose condition cleared."""
    at = at or now_utc()
    item = item_of(db, product)
    farm_policy = policy_for(db, product, None)
    warning_days = farm_policy.expiry_warning_days if farm_policy else 60
    alerts_enabled = farm_policy.alert_enabled if farm_policy else True
    whole = stock_breakdown(db, product, warning_days=warning_days, at=at)
    name = item.name
    out: list[PharmacyStockAlert] = []

    # Conditions of the lots themselves, whatever the thresholds say.
    earliest = f" (earliest {whole['earliest_expiry']:%Y-%m-%d})" if whole["earliest_expiry"] else ""
    conditions = {
        "EXPIRED_STOCK": (
            whole["expired"] > 0,
            f"{name}: {whole['expired']:g} {item.unit} on hand is expired or past its after-opening use-by "
            "and does not count toward the minimum. Remove it from the shelf and record the disposal.",
        ),
        "EXPIRING_SOON": (
            whole["expiring_quantity"] > 0,
            f"{name}: {whole['expiring_quantity']:g} {item.unit} expires within "
            f"{warning_days} days{earliest}. "
            "Use it first or plan its replacement.",
        ),
        "RECALL_AFFECTED": (
            whole["recalled"] > 0,
            f"{name}: {whole['recalled']:g} {item.unit} on hand belongs to a recalled lot. "
            "It is blocked from "
            "use; return or dispose of it per the recall.",
        ),
        "STORAGE_EXCEPTION": (
            whole["storage_exception"] > 0,
            f"{name}: {whole['storage_exception']:g} {item.unit} is held under a storage or cold-chain "
            "exception and is not eligible until a person clears it.",
        ),
    }
    for alert_type, (holds, text) in conditions.items():
        if holds and alerts_enabled:
            row, _ = _raise_alert(
                db,
                access,
                product,
                alert_type=alert_type,
                location_id=None,
                policy=farm_policy,
                breakdown=whole,
                explanation=text,
            )
            out.append(row)
        else:
            _resolve_alerts(
                db, access, product, alert_types=(alert_type,), location_id=None, reason="condition cleared"
            )

    # Threshold conditions, per policy (farm-wide or per location).
    for policy in policies_for(db, product):
        loc = policy.location_id
        bd = (
            whole
            if loc is None
            else stock_breakdown(db, product, location_id=loc, warning_days=policy.expiry_warning_days, at=at)
        )
        if not policy.alert_enabled:
            _resolve_alerts(
                db,
                access,
                product,
                alert_types=pc.STOCK_ALERT_TYPES + ("LOW_DAYS_COVER",),
                location_id=loc,
                reason="alerts disabled by policy",
            )
            continue
        status = stock_status(bd["eligible"], policy)
        incoming = confirmed_incoming(db, product)
        reorder = recommended_reorder(product, policy, bd["eligible"], incoming)
        raise_type = {"OUT": "OUT_OF_STOCK", "CRITICAL": "CRITICAL_STOCK", "LOW": "BELOW_MINIMUM_STOCK"}.get(
            status
        )
        figures = (
            f"eligible {bd['eligible']:g} {item.unit} (on hand {bd['on_hand']:g}; excluded: expired "
            f"{bd['expired']:g}, quarantined {bd['quarantined']:g}, blocked {bd['blocked']:g}, recalled "
            f"{bd['recalled']:g}, storage exception {bd['storage_exception']:g}"
            + (f", reserved {bd['reserved']:g}" if bd["reserved"] else "")
            + ")"
        )
        thresholds_set = (
            policy.essential
            or policy.minimum_stock_base is not None
            or (policy.critical_stock_base is not None)
        )
        if raise_type and thresholds_set:
            if status == "OUT":
                head = "is out of eligible stock"
            elif status == "CRITICAL":
                head = f"is at a critical level (≤ {policy.critical_stock_base or 0:g} {item.unit})"
            else:
                head = f"is below the farm's minimum of {policy.minimum_stock_base or 0:g} {item.unit}"
            text = f"{name} {head}: {figures}."
            if reorder > 0:
                text += (
                    f" Recommended reorder {reorder:g} {item.unit} to reach the target of "
                    f"{policy.target_stock_base:g}"
                )
                text += f" ({incoming:g} {item.unit} already on requisition)." if incoming > 0 else "."
            elif incoming > 0:
                text += f" {incoming:g} {item.unit} is already on an approved or submitted requisition."
            text += f" {pc.SAFETY}"
            row, created = _raise_alert(
                db,
                access,
                product,
                alert_type=raise_type,
                location_id=loc,
                policy=policy,
                breakdown=bd,
                explanation=text,
                reorder=reorder,
            )
            out.append(row)
            _resolve_alerts(
                db,
                access,
                product,
                alert_types=tuple(t for t in pc.STOCK_ALERT_TYPES if t != raise_type),
                location_id=loc,
                reason=f"superseded by {raise_type}",
            )
            if created and policy.auto_draft_requisition and reorder > 0:
                create_requisition(db, access, row)
        else:
            _resolve_alerts(
                db,
                access,
                product,
                alert_types=pc.STOCK_ALERT_TYPES,
                location_id=loc,
                reason="eligible stock back above the threshold",
            )
        daily = average_daily_consumption(db, product)
        if policy.minimum_days_cover and daily > 0 and bd["eligible"] / daily < policy.minimum_days_cover:
            text = (
                f"{name}: eligible stock covers {bd['eligible'] / daily:.1f} day(s) at the last 30 days' use "
                f"({daily:g} {item.unit}/day), below the farm's minimum of "
                f"{policy.minimum_days_cover:g} days. "
                f"{pc.SAFETY}"
            )
            row, _ = _raise_alert(
                db,
                access,
                product,
                alert_type="LOW_DAYS_COVER",
                location_id=loc,
                policy=policy,
                breakdown=bd,
                explanation=text,
                reorder=reorder,
            )
            out.append(row)
        else:
            _resolve_alerts(
                db,
                access,
                product,
                alert_types=("LOW_DAYS_COVER",),
                location_id=loc,
                reason="days of cover back above the minimum",
            )
    return out


def evaluate_farm(db: Session, access: AccessContext, *, at: datetime | None = None) -> dict:
    """The scheduled pharmacy scan: every medicine, every lot's expiry
    boundary, every policy. Idempotent."""
    products = db.scalars(
        select(MedicineProduct).where(MedicineProduct.active.is_(True), MedicineProduct.deleted_at.is_(None))
    ).all()
    raised = 0
    for p in products:
        raised += len(evaluate_item(db, access, p, at=at))
    open_count = (
        db.scalar(select(func.count(PharmacyStockAlert.id)).where(PharmacyStockAlert.status != "resolved"))
        or 0
    )
    return {
        "evaluated_at": at or now_utc(),
        "medicines": len(products),
        "conditions": raised,
        "open_alerts": open_count,
    }


def get_alert(db: Session, alert_id: str | uuid.UUID | None) -> PharmacyStockAlert:
    pk = parse_uuid(alert_id)
    a = db.get(PharmacyStockAlert, pk) if pk else None
    if a is None or a.deleted_at is not None:
        raise PharmacyError("Alert not found", 404)
    return a


def acknowledge_alert(db: Session, access: AccessContext, alert: PharmacyStockAlert) -> PharmacyStockAlert:
    """Records that a person saw it; the stock condition is untouched."""
    if alert.status == "resolved":
        raise PharmacyError("This alert is already resolved.")
    at = now_utc()
    alert.status = "acknowledged"
    alert.acknowledged_by = access.user_id
    alert.acknowledged_at = at
    alert.last_modified_by = access.membership_id
    routing.clear_bell(db, source_type="pharmacy_stock_alert", source_id=str(alert.id), at=at)
    return alert


def resolve_alert_manually(
    db: Session, access: AccessContext, alert: PharmacyStockAlert, *, note: str | None
) -> PharmacyStockAlert:
    if alert.status == "resolved":
        raise PharmacyError("This alert is already resolved.")
    if not note or not note.strip():
        raise PharmacyError("Say why this alert is being closed — the stock condition has not changed.")
    at = now_utc()
    alert.status = "resolved"
    alert.resolved_at = at
    alert.resolution_note = note.strip()
    alert.last_modified_by = access.membership_id
    routing.clear_bell(db, source_type="pharmacy_stock_alert", source_id=str(alert.id), at=at)
    return alert


def create_requisition(
    db: Session, access: AccessContext, alert: PharmacyStockAlert
) -> tuple[procurement.PurchaseRequisition, bool]:
    """One DRAFT purchase requisition per unresolved shortage (§9,
    PROCUREMENT §5): a document the buyer submits and a manager approves,
    never a purchase order and never approved here."""
    if alert.alert_type not in pc.STOCK_ALERT_TYPES and alert.alert_type != "LOW_DAYS_COVER":
        raise PharmacyError("Only a stock shortage can open a requisition.")
    if alert.status == "resolved":
        raise PharmacyError("This alert is resolved; the shortage is gone.")
    existing = procurement.open_for_source(db, "pharmacy_stock_alert", str(alert.id))
    if existing is None and alert.requisition_id:
        existing = db.get(procurement.PurchaseRequisition, alert.requisition_id)
        if existing is not None and existing.status in ("cancelled", "rejected", "received"):
            existing = None
    if existing is not None:
        alert.requisition_id = existing.id
        alert.requisition_task_id = existing.task_id
        return existing, False
    product = get_product(db, alert.inventory_item_id)
    item = item_of(db, product)
    policy = policy_for(db, product, alert.location_id)
    qty = alert.recommended_reorder_base or recommended_reorder(
        product, policy, alert.eligible_available_base or 0, confirmed_incoming(db, product)
    )
    if qty <= 0:
        raise PharmacyError("No reorder quantity: set a target stock level on the policy first.")
    lead_days = max(1, int(policy.lead_time_days or 2)) if policy else 2
    req = procurement.create_requisition(
        db,
        access,
        purpose_code="pharmacy_replenishment",
        needed_by=now_utc() + timedelta(days=lead_days),
        justification=(
            f"Pharmacy alert {alert.alert_type}: "
            f"eligible {alert.eligible_available_base or 0:g} {item.unit}, "
            f"minimum {alert.minimum_stock_base or 0:g}, target {alert.target_stock_base or 0:g}. {pc.SAFETY}"
        ),
        source_type="pharmacy_stock_alert",
        source_reference_id=str(alert.id),
        lines=[
            {
                "inventory_item_id": str(item.id),
                "requested_quantity": qty,
                "uom_code": item.unit,
                "estimated_unit_price": float(item.unit_cost) if item.unit_cost is not None else None,
                "preferred_supplier_id": policy.preferred_supplier_id if policy else None,
                "specification": f"{product.brand_name or ''} {product.generic_name or ''}".strip() or None,
            }
        ],
        task_priority="high" if alert.severity in ("critical", "high") else "medium",
        task_source_type="pharmacy_requisition",
        task_source_id=str(alert.id),
    )
    alert.requisition_id = req.id
    alert.requisition_task_id = req.task_id
    alert.last_modified_by = access.membership_id
    return req, True


def alert_dict(db: Session, a: PharmacyStockAlert) -> dict:
    item = db.get(InventoryItem, a.inventory_item_id)
    return {
        "id": str(a.id),
        "inventory_item_id": str(a.inventory_item_id),
        "medicine_name": item.name if item else None,
        "unit": item.unit if item else None,
        "location_id": a.location_id,
        "alert_type": a.alert_type,
        "severity": a.severity,
        "detected_at": a.detected_at,
        "last_seen_at": a.last_seen_at,
        "eligible_available_base": a.eligible_available_base,
        "minimum_stock_base": a.minimum_stock_base,
        "target_stock_base": a.target_stock_base,
        "earliest_expiry_date": a.earliest_expiry_date,
        "expiring_quantity": a.expiring_quantity,
        "recommended_reorder_base": a.recommended_reorder_base,
        "explanation": a.explanation,
        "status": a.status,
        "acknowledged_by": _s(a.acknowledged_by),
        "acknowledged_at": a.acknowledged_at,
        "resolved_at": a.resolved_at,
        "resolution_note": a.resolution_note,
        "deduplication_key": a.deduplication_key,
        "requisition_task_id": _s(a.requisition_task_id),
        "requisition_id": _s(a.requisition_id),
        "route_code": pc.ROUTE_FOR_ALERT.get(a.alert_type, "pharmacy_stock"),
    }


# --------------------------------------------------------- administration
def subject_or_404(db: Session, subject_type: str, subject_id: str | uuid.UUID | None) -> Any:
    if subject_type not in ("animal", "group"):
        raise PharmacyError("subject_type must be animal or group")
    pk = parse_uuid(subject_id)
    subject: Any = None
    if pk is not None:
        subject = db.get(Animal, pk) if subject_type == "animal" else db.get(AnimalGroup, pk)
    if subject is None or subject.deleted_at is not None:
        raise PharmacyError("Subject not found", 404)
    return subject


def lot_free_quantity(
    db: Session,
    product: MedicineProduct,
    lot: InventoryLot,
    *,
    reservation: InventoryReservation | None = None,
) -> float:
    """What the lot can give to a purpose other than the reservations on
    it — the dose for a reserved purpose may draw on its own reservation."""
    return round(lot.quantity_on_hand - reserved_quantity(db, product, lot=lot, exclude=reservation), 6)


def eligible_lots(
    db: Session,
    product: MedicineProduct,
    *,
    location_id: str | None = None,
    at: datetime | None = None,
    reservation: InventoryReservation | None = None,
) -> list[InventoryLot]:
    """First-expiry-first-out order among lots that count (§7). A lot
    held for a purpose is only offered to that purpose."""
    at = at or now_utc()
    rows = []
    for lot in product.lots:
        if lot.deleted_at is not None or ineligibility(lot, at) is not None:
            continue
        if location_id is not None and lot_quantity_at(db, lot, location_id) <= EPS:
            continue
        if reservation is not None and reservation.inventory_lot_id not in (None, lot.id):
            continue
        if lot_free_quantity(db, product, lot, reservation=reservation) <= EPS:
            continue
        rows.append(lot)
    far = datetime.max.replace(tzinfo=at.tzinfo)
    rows.sort(key=lambda lot: (ensure_utc(lot.expiry_date) or far, ensure_utc(lot.received_at) or far))
    return rows


def administer(
    db: Session,
    access: AccessContext,
    *,
    subject_type: str,
    subject_id: str | uuid.UUID,
    inventory_item_id: str | uuid.UUID,
    lot_id: str | uuid.UUID | None = None,
    dose_quantity: float,
    dose_unit: str,
    route_code: str,
    head_count: int | None = None,
    quantity_consumed: float | None = None,
    treatment_id: str | uuid.UUID | None = None,
    protocol_run_step_id: uuid.UUID | None = None,
    administered_at: datetime | None = None,
    reason: str | None = None,
    notes: str | None = None,
    reservation_id: str | None = None,
    location_id: str | None = None,
) -> MedicationAdministration:
    """A dose actually given (§11), atomically: authorisation and
    treatment context, lot eligibility, the record, the exact-lot
    consumption, the product's authorised withdrawal and the threshold
    re-evaluation."""
    product = get_product(db, inventory_item_id)
    item = item_of(db, product)
    if not product.active:
        raise PharmacyError(f"{item.name} is retired and cannot be administered.")
    subject = subject_or_404(db, subject_type, subject_id)
    at = ensure_utc(administered_at) or now_utc()
    if dose_quantity <= 0:
        raise PharmacyError("Dose must be greater than zero.")
    dose_unit = uom_service.require(db, dose_unit, what="dose unit")
    if route_code not in pc.ROUTES:
        raise PharmacyError(f"route_code must be one of {list(pc.ROUTES)}")
    if product.administration_routes and route_code not in product.administration_routes:
        raise PharmacyError(
            f"{item.name} is authorised for {', '.join(product.administration_routes)} only, "
            f"not {route_code}."
        )
    if product.species_codes and subject.species not in product.species_codes:
        raise PharmacyError(
            f"{item.name} is not authorised for {subject.species}; it is configured for "
            f"{', '.join(product.species_codes)}."
        )
    treatment: Treatment | None = None
    treatment_pk = parse_uuid(treatment_id) if treatment_id else None
    if treatment_id:
        treatment = db.get(Treatment, treatment_pk) if treatment_pk else None
        expected = ("animal",) if subject_type == "animal" else GROUP_ENTITY_TYPES
        if (
            treatment is None
            or treatment.entity_type not in expected
            or treatment.entity_id != str(subject.id)
        ):
            raise PharmacyError(
                "The treatment does not belong to this subject.", 404 if treatment is None else 422
            )
        # The prescription names the product (§11): the dose must be of it.
        if treatment.medicine_product_id and treatment.medicine_product_id != product.inventory_item_id:
            prescribed = db.get(InventoryItem, treatment.medicine_product_id)
            raise PharmacyError(
                f"The treatment prescribes {prescribed.name if prescribed else treatment.medication}, not "
                f"{item.name}. Record a treatment for this medicine first."
            )
        if treatment.status != "active":
            raise PharmacyError(
                f"The treatment is {treatment.status}; an administration needs an active prescription."
            )
    if product.prescription_required and treatment is None and not protocol_run_step_id:
        raise PharmacyError(
            f"{item.name} needs a prescription: record the treatment (veterinarian or manager) or run an "
            "approved protocol step first. Stock on the shelf never authorises its use."
        )
    if head_count is not None:
        heads = head_count
    else:
        heads = 1 if subject_type == "animal" else max(1, int(getattr(subject, "count", 1) or 1))
    if heads <= 0:
        raise PharmacyError("head_count must be at least one.")
    if quantity_consumed is None:
        if not uom_service.compatible(db, dose_unit, item.unit):
            raise PharmacyError(
                f"The dose is in {dose_unit} and {item.name} is stocked in {item.unit}; "
                "give quantity_consumed "
                f"in {item.unit}."
            )
        quantity_consumed = round(uom_service.convert(db, dose_quantity * heads, dose_unit, item.unit), 6)
    if quantity_consumed <= 0:
        raise PharmacyError("Quantity consumed must be greater than zero.")
    reservation = None
    if reservation_id:
        reservation = get_reservation(db, reservation_id)
        if reservation.inventory_item_id != product.inventory_item_id or reservation.status != "active":
            raise PharmacyError("The reservation is not an active hold on this medicine.")

    # The exact lot: the one named, if it counts; else first-expiry-first-out.
    if lot_id:
        lot = get_lot(db, lot_id)
        if lot.inventory_item_id != product.inventory_item_id:
            raise PharmacyError("That lot belongs to another medicine.")
        why = ineligibility(lot, at)
        if why is not None:
            raise PharmacyError(
                f"Lot {lot.lot_code} is not eligible ({why.replace('_', ' ')}). Choose an eligible lot."
            )
        if reservation is not None and reservation.inventory_lot_id not in (None, lot.id):
            held_lot = db.get(InventoryLot, reservation.inventory_lot_id)
            raise PharmacyError(
                f"The reservation holds lot {held_lot.lot_code if held_lot else '?'}, not {lot.lot_code}."
            )
        free = lot_free_quantity(db, product, lot, reservation=reservation)
        if free + EPS < quantity_consumed:
            held = lot.quantity_on_hand - free
            raise PharmacyError(
                f"Lot {lot.lot_code} has only {max(free, 0):g} {item.unit} free of "
                f"{lot.quantity_on_hand:g} on hand"
                + (f" ({held:g} is reserved for another purpose)" if held > EPS else "")
                + f"; {quantity_consumed:g} is needed."
            )
    else:
        candidates = eligible_lots(db, product, at=at, location_id=location_id, reservation=reservation)
        chosen = next(
            (
                c
                for c in candidates
                if lot_free_quantity(db, product, c, reservation=reservation) + EPS >= quantity_consumed
            ),
            None,
        )
        if chosen is None:
            total = round(
                sum(lot_free_quantity(db, product, c, reservation=reservation) for c in candidates), 6
            )
            bd = stock_breakdown(db, product, at=at)
            raise PharmacyError(
                f"No eligible lot of {item.name} holds {quantity_consumed:g} {item.unit} "
                f"(eligible {total:g}; on hand "
                f"{bd['on_hand']:g}, of which expired {bd['expired']:g}, "
                f"quarantined {bd['quarantined']:g}, storage "
                f"exception {bd['storage_exception']:g}"
                + (f", reserved for other purposes {bd['reserved']:g}" if bd["reserved"] else "")
                + "). Only eligible stock can be administered."
            )
        lot = chosen
    issue_location = location_id or _default_issue_location(db, lot)

    adm = MedicationAdministration(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        subject_type=subject_type,
        subject_id=subject.id,
        treatment_id=treatment.id if treatment else None,
        protocol_run_step_id=protocol_run_step_id,
        inventory_item_id=product.inventory_item_id,
        inventory_lot_id=lot.id,
        reservation_id=reservation.id if reservation else None,
        location_id=issue_location,
        dose_quantity=dose_quantity,
        dose_unit=dose_unit,
        route_code=route_code,
        head_count=heads,
        quantity_consumed=quantity_consumed,
        unit=item.unit,
        administered_at=at,
        administered_by=access.user_id,
        reason=reason,
        notes=notes,
        status="recorded",
    )
    db.add(adm)
    db.flush()
    mv = _movement(
        db,
        access,
        item,
        delta=-quantity_consumed,
        unit_cost=lot.unit_cost,
        reason="administration",
        linked_entity_type="medication_administration",
        linked_entity_id=str(adm.id),
        lot_id=lot.id,
        occurred_at=at,
        location_id=issue_location,
    )
    adm.inventory_movement_id = mv.id
    lot.quantity_on_hand = round(lot.quantity_on_hand - quantity_consumed, 6)
    lot.version += 1
    refresh_lot_status(lot)
    if reservation is not None:
        # Issue for the purpose consumes the reservation (INVENTORY §5).
        reservation.consumed_quantity = round(
            (reservation.consumed_quantity or 0) + min(quantity_consumed, reservation_remaining(reservation)),
            6,
        )
        if reservation_remaining(reservation) <= EPS:
            reservation.status = "consumed"
            reservation.released_at = at
    if lot.opened_at is None and product.opened_shelf_life_days:
        lot.opened_at = at
        lot.use_by_after_opening = at + timedelta(days=product.opened_shelf_life_days)

    # Withdrawal from the product's authorised rule — never invented here.
    rules = product.withdrawal_rules_json or {}
    milk = at + timedelta(days=float(rules["milk_days"])) if rules.get("milk_days") is not None else None
    meat = at + timedelta(days=float(rules["meat_days"])) if rules.get("meat_days") is not None else None
    adm.withdrawal_milk_until, adm.withdrawal_meat_until = milk, meat
    until = max([x for x in (milk, meat) if x is not None], default=None)
    animals = [subject] if subject_type == "animal" else programs.animals_in_group(db, subject)
    if until is not None:
        for a in animals:
            current = ensure_utc(a.withdrawal_until)
            if current is None or until > current:
                a.withdrawal_until = until
                a.withdrawal_reason = f"Medication: {item.name}"
            if a.status == "healthy":
                a.status = "under_treatment"
            a.version += 1
            a.last_modified_by = access.membership_id
    evaluate_item(db, access, product)
    return adm


def get_administration(db: Session, administration_id: str | uuid.UUID | None) -> MedicationAdministration:
    pk = parse_uuid(administration_id)
    adm = db.get(MedicationAdministration, pk) if pk else None
    if adm is None or adm.deleted_at is not None:
        raise PharmacyError("Administration not found", 404)
    return adm


def reverse_administration(
    db: Session, access: AccessContext, adm: MedicationAdministration, *, reason: str
) -> MedicationAdministration:
    """Puts the quantity back on the exact lot and keeps the original
    record. The withdrawal is not shortened: a mistaken entry does not
    prove the animal was not dosed — the veterinarian corrects that
    explicitly."""
    if adm.status == "reversed":
        raise PharmacyError("This administration is already reversed.")
    if not reason or not reason.strip():
        raise PharmacyError("A reversal needs a reason.")
    product = get_product(db, adm.inventory_item_id)
    item = item_of(db, product)
    lot = get_lot(db, adm.inventory_lot_id)
    at = now_utc()
    lot.quantity_on_hand = round(lot.quantity_on_hand + adm.quantity_consumed, 6)
    lot.version += 1
    refresh_lot_status(lot)
    _movement(
        db,
        access,
        item,
        delta=adm.quantity_consumed,
        unit_cost=lot.unit_cost,
        reason="administration_reversal",
        linked_entity_type="medication_administration",
        linked_entity_id=str(adm.id),
        lot_id=lot.id,
        occurred_at=at,
        location_id=adm.location_id,
    )
    adm.status = "reversed"
    adm.reversed_at = at
    adm.reversal_reason = reason.strip()
    adm.last_modified_by = access.membership_id
    adm.version += 1
    evaluate_item(db, access, product)
    return adm


def administration_dict(db: Session, adm: MedicationAdministration, names: NameCache) -> dict:
    item = db.get(InventoryItem, adm.inventory_item_id)
    lot = db.get(InventoryLot, adm.inventory_lot_id)
    subject: Any = (
        db.get(Animal, adm.subject_id)
        if adm.subject_type == "animal"
        else db.get(AnimalGroup, adm.subject_id)
    )
    treatment = db.get(Treatment, adm.treatment_id) if adm.treatment_id else None
    return {
        "id": str(adm.id),
        "subject_type": adm.subject_type,
        "subject_id": str(adm.subject_id),
        "subject_name": getattr(subject, "name", None),
        "species": getattr(subject, "species", None),
        "treatment_id": _s(adm.treatment_id),
        "protocol_run_step_id": _s(adm.protocol_run_step_id),
        "health_case_id": _s(treatment.health_case_id) if treatment else None,
        "reservation_id": _s(adm.reservation_id),
        "location_id": adm.location_id,
        "inventory_item_id": str(adm.inventory_item_id),
        "medicine_name": item.name if item else None,
        "inventory_lot_id": str(adm.inventory_lot_id),
        "lot_code": lot.lot_code if lot else None,
        "dose_quantity": adm.dose_quantity,
        "dose_unit": adm.dose_unit,
        "route_code": adm.route_code,
        "head_count": adm.head_count,
        "quantity_consumed": adm.quantity_consumed,
        "unit": adm.unit,
        "administered_at": adm.administered_at,
        "administered_by": _s(adm.administered_by),
        "administered_by_name": names.get(adm.administered_by),
        "inventory_transaction_id": _s(adm.inventory_movement_id),
        "withdrawal_milk_until": adm.withdrawal_milk_until,
        "withdrawal_meat_until": adm.withdrawal_meat_until,
        "reason": adm.reason,
        "notes": adm.notes,
        "status": adm.status,
        "reversed_at": adm.reversed_at,
        "reversal_reason": adm.reversal_reason,
    }


# ------------------------------------------------------------- dashboard
def product_dict(db: Session, product: MedicineProduct, names: NameCache, *, with_lots: bool = False) -> dict:
    item = item_of(db, product)
    policy = policy_for(db, product, None)
    bd = stock_breakdown(db, product, warning_days=policy.expiry_warning_days if policy else 60)
    status = stock_status(bd["eligible"], policy)
    incoming = confirmed_incoming(db, product)
    open_alerts = db.scalars(
        select(PharmacyStockAlert)
        .where(
            PharmacyStockAlert.inventory_item_id == product.inventory_item_id,
            PharmacyStockAlert.status != "resolved",
        )
        .order_by(PharmacyStockAlert.detected_at)
    ).all()
    out: dict[str, Any] = {
        "inventory_item_id": str(product.inventory_item_id),
        "name": item.name,
        "unit": item.unit,
        "category": item.category,
        "current_qty": _f(item.current_qty),
        "supplier_label": item.supplier_label,
        "unit_cost": float(item.unit_cost) if item.unit_cost is not None else None,
        "generic_name": product.generic_name,
        "brand_name": product.brand_name,
        "dosage_form": product.dosage_form,
        "strength_value": product.strength_value,
        "strength_uom": product.strength_uom,
        "strength_basis": product.strength_basis,
        "administration_routes": product.administration_routes or [],
        "prescription_required": product.prescription_required,
        "antimicrobial": product.antimicrobial,
        "controlled_medicine": product.controlled_medicine,
        "cold_chain_required": product.cold_chain_required,
        "storage_min_c": product.storage_min_c,
        "storage_max_c": product.storage_max_c,
        "opened_shelf_life_days": product.opened_shelf_life_days,
        "default_pack_size": product.default_pack_size,
        "pack_uom": product.pack_uom,
        "manufacturer_name": product.manufacturer_name,
        "authorization_reference": product.authorization_reference,
        "species_codes": product.species_codes or [],
        "withdrawal_rules": product.withdrawal_rules_json or {},
        "lot_tracking_exception": product.lot_tracking_exception,
        "active": product.active,
        "categories": [
            {"code": c["code"], "name": c["name"], "name_ar": c["name_ar"]}
            for c in (pc.CATEGORY_BY_CODE.get(code) for code in product.category_codes or [])
            if c is not None
        ],
        "ingredients": [
            {
                "code": i.ingredient_code,
                "name": i.ingredient_name,
                "concentration_value": i.concentration_value,
                "concentration_uom": i.concentration_uom,
                "concentration_basis": i.concentration_basis,
            }
            for i in product.ingredients
        ],
        "stock": {k: v for k, v in bd.items() if k != "expiring_lots"},
        "eligible_available": bd["eligible"],
        "on_hand": bd["on_hand"],
        "lot_count": bd["lot_count"],
        "reserved": bd["reserved"],
        "confirmed_incoming": incoming,
        "earliest_expiry": bd["earliest_expiry"],
        "expiring_quantity": bd["expiring_quantity"],
        "storage_exception": bd["storage_exception"] > 0,
        "status": status,
        "policy": policy_dict(policy),
        "recommended_reorder": recommended_reorder(product, policy, bd["eligible"], incoming),
        "open_alerts": [
            {
                "id": str(a.id),
                "alert_type": a.alert_type,
                "severity": a.severity,
                "status": a.status,
                "requisition_task_id": _s(a.requisition_task_id),
                "requisition_id": _s(a.requisition_id),
            }
            for a in open_alerts
        ],
        "open_requisition": any(a.requisition_id or a.requisition_task_id for a in open_alerts),
        "average_daily_use": average_daily_consumption(db, product),
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }
    if with_lots:
        far = datetime.max.replace(tzinfo=now_utc().tzinfo)
        out["lots"] = [
            lot_dict(db, lot)
            for lot in sorted(
                (lot for lot in product.lots if lot.deleted_at is None),
                key=lambda lot: ensure_utc(lot.expiry_date) or far,
            )
        ]
        out["policies"] = [policy_dict(p) for p in policies_for(db, product)]
        out["alerts"] = [alert_dict(db, a) for a in open_alerts]
        out["reservations"] = [reservation_dict(db, r) for r in active_reservations(db, product)]
        recent = db.scalars(
            select(MedicationAdministration)
            .where(MedicationAdministration.inventory_item_id == product.inventory_item_id)
            .order_by(MedicationAdministration.administered_at.desc())
            .limit(20)
        ).all()
        out["administrations"] = [administration_dict(db, a, names) for a in recent]
    return out


def dashboard(db: Session, names: NameCache, *, include_inactive: bool = False) -> list[dict]:
    stmt = select(MedicineProduct).where(MedicineProduct.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(MedicineProduct.active.is_(True))
    rows = [product_dict(db, p, names) for p in db.scalars(stmt)]
    order = {"OUT": 0, "CRITICAL": 1, "LOW": 2, "OK": 3}
    rows.sort(
        key=lambda r: (0 if (r["policy"] or {}).get("essential") else 1, order.get(r["status"], 9), r["name"])
    )
    return rows


def summary(db: Session, names: NameCache) -> dict:
    rows = dashboard(db, names)
    alerts = db.scalars(select(PharmacyStockAlert).where(PharmacyStockAlert.status != "resolved")).all()
    by_type: dict[str, int] = {}
    for a in alerts:
        by_type[a.alert_type] = by_type.get(a.alert_type, 0) + 1
    by_status = {s: sum(1 for r in rows if r["status"] == s) for s in ("OK", "LOW", "CRITICAL", "OUT")}
    return {
        "medicines": len(rows),
        "essential": sum(1 for r in rows if (r["policy"] or {}).get("essential")),
        "by_status": by_status,
        "open_alerts": len(alerts),
        "unseen_alerts": sum(1 for a in alerts if a.status == "open"),
        "alerts_by_type": by_type,
        "expiring_soon": sum(1 for r in rows if r["expiring_quantity"] > 0),
        "storage_exceptions": sum(1 for r in rows if r["storage_exception"]),
        "open_requisitions": sum(1 for r in rows if r["open_requisition"]),
    }


def ingredient_catalog(db: Session) -> list[dict]:
    """The built-in ingredient list plus every code this farm's products use."""
    known: dict[str, str] = dict(pc.INGREDIENTS)
    for row in db.scalars(
        select(MedicineProductIngredient).where(MedicineProductIngredient.deleted_at.is_(None))
    ):
        known.setdefault(row.ingredient_code, row.ingredient_name)
    return [
        {"id": code, "code": code, "name": name, "active": True}
        for code, name in sorted(known.items(), key=lambda kv: kv[1])
    ]
