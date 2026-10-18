"""Farm assets (OrigamiFarmOS database/ASSET-MAINTENANCE-SCHEMA.md §2): the
durable resources a record can name — a numbered mix names the mixer it
was made on. Spare parts and consumables stay inventory; the machine is
an asset.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos.deps import AccessContext
from app.farmos.feed_catalog import FeedError
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.livestock_service import ensure_utc, now_utc
from app.farmos.platform_models import ASSET_STATUSES, ASSET_TYPES, FarmAsset


class AssetError(FeedError):
    pass


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "asset"


def get_asset(db: Session, asset_id: str | uuid.UUID | None) -> FarmAsset:
    pk = parse_uuid(asset_id)
    a = db.get(FarmAsset, pk) if pk else None
    if a is None or a.deleted_at is not None:
        raise AssetError("Asset not found", 404)
    return a


def find_by_code(db: Session, code: str) -> FarmAsset | None:
    return db.scalar(select(FarmAsset).where(FarmAsset.code == code, FarmAsset.deleted_at.is_(None)))


def asset_name(db: Session, asset_id: uuid.UUID | str | None) -> str | None:
    pk = parse_uuid(asset_id)
    a = db.get(FarmAsset, pk) if pk else None
    return a.name if a is not None else None


def require_usable(db: Session, asset_id: str | None, *, asset_type: str, purpose: str) -> FarmAsset | None:
    """The asset a record names must exist on this farm, be of the kind
    the record needs and be in service — an out-of-service mixer cannot
    make a mix."""
    if not asset_id:
        return None
    pk = parse_uuid(asset_id)
    a = db.get(FarmAsset, pk) if pk else None
    if a is None or a.deleted_at is not None:
        raise AssetError(
            f"{asset_type.replace('_', ' ').capitalize()} asset not found; "
            f"register the {asset_type.replace('_', ' ')} under Assets first.",
            404,
        )
    if a.asset_type != asset_type:
        raise AssetError(
            f"{a.name} is a {a.asset_type.replace('_', ' ')}, not a {asset_type.replace('_', ' ')}."
        )
    if a.status != "active":
        raise AssetError(f"{a.name} is {a.status.replace('_', ' ')} and cannot be used for {purpose}.")
    return a


def create_asset(
    db: Session,
    access: AccessContext,
    *,
    name: str,
    asset_type: str = "other",
    code: str | None = None,
    manufacturer: str | None = None,
    model: str | None = None,
    serial_number: str | None = None,
    acquisition_date: datetime | None = None,
    acquisition_cost: float | None = None,
    currency: str | None = None,
    current_location_id: str | None = None,
    parent_asset_id: str | None = None,
    notes: str | None = None,
) -> FarmAsset:
    if not name or not name.strip():
        raise AssetError("An asset needs a name.")
    if asset_type not in ASSET_TYPES:
        raise AssetError(f"asset_type must be one of {list(ASSET_TYPES)}")
    base = slug(code or name)
    final, n = base, 2
    while find_by_code(db, final) is not None:
        final = f"{base}-{n}"
        n += 1
    parent = get_asset(db, parent_asset_id) if parent_asset_id else None
    row = FarmAsset(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        code=final,
        name=name.strip(),
        asset_type=asset_type,
        manufacturer=manufacturer,
        model=model,
        serial_number=serial_number,
        acquisition_date=ensure_utc(acquisition_date) if acquisition_date else None,
        acquisition_cost=acquisition_cost,
        currency=currency,
        current_location_id=current_location_id,
        status="active",
        parent_asset_id=parent.id if parent else None,
        notes=notes,
        created_by=access.user_id,
    )
    db.add(row)
    db.flush()
    return row


_PATCHABLE = {
    "name",
    "asset_type",
    "manufacturer",
    "model",
    "serial_number",
    "acquisition_date",
    "acquisition_cost",
    "currency",
    "current_location_id",
    "status",
    "notes",
}


def update_asset(db: Session, access: AccessContext, asset: FarmAsset, changes: dict) -> FarmAsset:
    for k, v in changes.items():
        if k in _PATCHABLE:
            setattr(asset, k, ensure_utc(v) if k == "acquisition_date" and v else v)
    if not asset.name or not asset.name.strip():
        raise AssetError("An asset needs a name.")
    if asset.asset_type not in ASSET_TYPES:
        raise AssetError(f"asset_type must be one of {list(ASSET_TYPES)}")
    if asset.status not in ASSET_STATUSES:
        raise AssetError(f"status must be one of {list(ASSET_STATUSES)}")
    asset.version += 1
    asset.last_modified_by = access.membership_id
    asset.updated_at = now_utc()
    return asset


def asset_dict(a: FarmAsset) -> dict:
    return {
        "id": str(a.id),
        "farm_id": str(a.tenant_id),
        "code": a.code,
        "name": a.name,
        "asset_type": a.asset_type,
        "manufacturer": a.manufacturer,
        "model": a.model,
        "serial_number": a.serial_number,
        "acquisition_date": a.acquisition_date,
        "acquisition_cost": a.acquisition_cost,
        "currency": a.currency,
        "current_location_id": a.current_location_id,
        # No locations master yet — the code is the label.
        "current_location_name": a.current_location_id,
        "status": a.status,
        "parent_asset_id": str(a.parent_asset_id) if a.parent_asset_id else None,
        "notes": a.notes,
        "created_at": a.created_at,
        "updated_at": a.updated_at,
    }
