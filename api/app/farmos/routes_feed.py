"""Feed & general inventory. Validation rule (tech spec §14): "Inventory
should not go negative without explicit override."
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos.deps import AccessContext, check_farm_id, get_farmos_tenant_db, require_permission
from app.farmos.schemas import FeedTransactionCreate, InventoryItemOut
from app.tenant_api.models import InventoryItem, InventoryMovement

router = APIRouter()


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
    rows = db.execute(
        select(InventoryItem).where(InventoryItem.deleted_at.is_(None)).order_by(InventoryItem.name)
    ).scalars().all()
    return [_to_item_out(row, access.tenant_id) for row in rows]


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
