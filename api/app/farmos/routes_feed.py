"""Feed & general inventory — the tablet's original, item-level feed
screen. Validation rule (tech spec §14): "Inventory should not go negative
without explicit override."

Feed items are lot-tracked (feed architecture §26): an `in` becomes a
received lot, an `out` is issued FIFO from usable, unreserved lots, and
either way the movement names its lot. Non-feed stock (medicine, produce)
keeps the plain item ledger. The lot-level API lives in
app/farmos/routes_feeding.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos import feed_inventory_service as inv
from app.farmos import feed_models as fm
from app.farmos import feeding_program_service as programs
from app.farmos.deps import AccessContext, check_farm_id, get_farmos_tenant_db, require_permission
from app.farmos.schemas import (
    FeedTransactionCreate,
    InventoryItemCreate,
    InventoryItemOut,
    InventoryMovementOut,
)
from app.tenant_api.models import InventoryItem, InventoryMovement

router = APIRouter()


def _to_movement_out(m: InventoryMovement, product_ids: dict[uuid.UUID, uuid.UUID]) -> InventoryMovementOut:
    delta = float(m.quantity_delta)
    product_id = product_ids.get(m.inventory_item_id)
    return InventoryMovementOut(
        id=str(m.id),
        inventory_item_id=str(m.inventory_item_id),
        feed_product_id=str(product_id) if product_id else None,
        lot_id=str(m.lot_id) if m.lot_id else None,
        direction="in" if delta >= 0 else "out",
        quantity=abs(delta),
        unit_cost=float(m.unit_cost) if m.unit_cost is not None else None,
        reason=m.reason or None,
        linked_entity_type=m.linked_entity_type,
        linked_entity_id=m.linked_entity_id,
        occurred_at=m.occurred_at,
    )


def _to_item_out(item: InventoryItem, tenant_id: uuid.UUID) -> InventoryItemOut:
    return InventoryItemOut(
        id=str(item.id),
        farm_id=str(tenant_id),
        name=item.name,
        category=item.category,
        unit=item.unit,
        current_qty=float(item.current_qty),
        reorder_level=float(item.reorder_level),
        supplier_label=item.supplier_label,
        unit_cost=float(item.unit_cost) if item.unit_cost is not None else None,
        last_purchase=item.last_purchase,
    )


@router.get("/feed/items", response_model=list[InventoryItemOut])
def list_inventory_items(
    farm_id: str = Query(...),
    access: AccessContext = Depends(require_permission("feed_nutrition", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[InventoryItemOut]:
    check_farm_id(farm_id, access)
    rows = (
        db.execute(
            select(InventoryItem).where(InventoryItem.deleted_at.is_(None)).order_by(InventoryItem.name)
        )
        .scalars()
        .all()
    )
    return [_to_item_out(row, access.tenant_id) for row in rows]


@router.post("/feed/items", response_model=InventoryItemOut, status_code=201)
def create_inventory_item(
    payload: InventoryItemCreate,
    access: AccessContext = Depends(require_permission("feed_nutrition", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> InventoryItemOut:
    """Register a new feed/inventory item — a farm's catalog has to start
    somewhere before POST /feed/transactions (which only moves stock on an
    item that already exists) can be used against it. An initial_qty, if
    given, is applied as a real InventoryMovement rather than set directly,
    matching every other stock change in this module; for a feed item it
    becomes the product's opening lot.
    """
    item = InventoryItem(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        name=payload.name,
        category=payload.category,
        unit=payload.unit,
        current_qty=0,
        reorder_level=payload.reorder_level,
        supplier_label=payload.supplier_label,
        unit_cost=payload.unit_cost,
        last_purchase=payload.last_purchase,
    )
    db.add(item)
    db.flush()

    if payload.initial_qty:
        if inv.is_feed_item(item):
            product = inv.ensure_product_for_item(db, item, actor=access.membership_id)
            inv.receive(
                db,
                product,
                quantity=payload.initial_qty,
                unit=item.unit,
                unit_cost=payload.unit_cost,
                lot_code=f"{product.code}-OPENING",
                source_type="opening_balance",
                notes="Initial stock",
                actor=access.membership_id,
                actor_user=access.user_id,
            )
        else:
            db.add(
                InventoryMovement(
                    tenant_id=access.tenant_id,
                    inventory_item_id=item.id,
                    quantity_delta=payload.initial_qty,
                    reason="Initial stock",
                    occurred_at=datetime.now(timezone.utc),
                    last_modified_by=access.membership_id,
                )
            )
            item.current_qty = payload.initial_qty
            item.version += 1
        db.flush()

    return _to_item_out(item, access.tenant_id)


@router.get("/feed/transactions", response_model=list[InventoryMovementOut])
def list_feed_transactions(
    farm_id: str = Query(...),
    item_id: str | None = Query(default=None),
    days: int = Query(default=90),
    access: AccessContext = Depends(require_permission("feed_nutrition", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[InventoryMovementOut]:
    """The movement history behind the feed screen's "recent movements":
    newest first, each with the lot it belongs to."""
    check_farm_id(farm_id, access)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = select(InventoryMovement).where(
        InventoryMovement.deleted_at.is_(None), InventoryMovement.occurred_at >= since
    )
    if item_id:
        try:
            stmt = stmt.where(InventoryMovement.inventory_item_id == uuid.UUID(item_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Inventory item not found.") from exc
    rows = db.execute(stmt.order_by(InventoryMovement.occurred_at.desc())).scalars().all()
    product_ids = {
        p.inventory_item_id: p.id
        for p in db.scalars(select(fm.FeedProduct).where(fm.FeedProduct.deleted_at.is_(None)))
    }
    return [_to_movement_out(row, product_ids) for row in rows]


@router.post("/feed/transactions", response_model=InventoryItemOut, status_code=201)
def create_feed_transaction(
    payload: FeedTransactionCreate,
    access: AccessContext = Depends(require_permission("feed_nutrition", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> InventoryItemOut:
    try:
        item_id = uuid.UUID(payload.item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Inventory item not found.") from exc
    item = db.get(InventoryItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Inventory item not found.")

    if inv.is_feed_item(item):
        product = inv.ensure_product_for_item(db, item, actor=access.membership_id)
        if payload.direction == "in":
            inv.receive(
                db,
                product,
                quantity=payload.quantity,
                unit=payload.unit,
                reference=payload.reason,
                notes=payload.reason,
                actor=access.membership_id,
                actor_user=access.user_id,
            )
        else:
            inv.consume(
                db,
                product,
                payload.quantity,
                payload.unit,
                reason=payload.reason or "feeding",
                linked_entity_type=payload.linked_entity_type or "inventory_item",
                linked_entity_id=payload.linked_entity_id or str(item.id),
                purpose=_purpose_for(db, payload),
                allow_negative=payload.allow_negative,
                actor=access.membership_id,
            )
        db.flush()
        db.refresh(item)
        return _to_item_out(item, access.tenant_id)

    signed_quantity = payload.quantity if payload.direction == "in" else -payload.quantity
    new_qty = float(item.current_qty) + signed_quantity
    if new_qty < 0 and not payload.allow_negative:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This would take {item.name} below zero "
                f"({new_qty:.2f} {item.unit}). Check the quantity, or confirm the override."
            ),
        )

    db.add(
        InventoryMovement(
            tenant_id=access.tenant_id,
            inventory_item_id=item.id,
            quantity_delta=signed_quantity,
            reason=payload.reason or "",
            linked_entity_type=payload.linked_entity_type,
            linked_entity_id=payload.linked_entity_id,
            occurred_at=datetime.now(timezone.utc),
            last_modified_by=access.membership_id,
        )
    )
    item.current_qty = new_qty
    item.version += 1
    item.last_modified_by = access.membership_id
    db.flush()
    return _to_item_out(item, access.tenant_id)


def _purpose_for(db: Session, payload: FeedTransactionCreate) -> inv.Purpose | None:
    """A distribution linked to an animal is issued *for* that animal, so
    stock reserved for its species or group can be drawn on."""
    if payload.linked_entity_type in ("animal", "group", "flock") and payload.linked_entity_id:
        subject = programs.subject_row(db, payload.linked_entity_type, payload.linked_entity_id)
        if subject is not None:
            subject_type = "animal" if payload.linked_entity_type == "animal" else "group"
            return inv.Purpose(
                species_code=subject.species,
                subject_type=subject_type,
                subject_id=subject.id,
                label=subject.name,
            )
    return None
