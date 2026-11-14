"""The farm's own settings (reference `farms.dry_off_days_before_birth`
and the calendar columns of migration c3e5a7b9d1f4): how many days before
an expected birth a lactating animal is dried off, the milk-peak window,
the voluntary waiting period before the next insemination and the heat
cycle length. One `farm_settings` row per tenant, created with the
defaults the first time it is read; `PATCH /farms/me` changes it in place
with an audit entry carrying the old values.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos.farm_models import FarmSettings

# The reference defaults (migrations a8d0c2e4f6b1 and c3e5a7b9d1f4).
DEFAULTS: dict[str, int] = {
    "dry_off_days_before_birth": 60,
    "milk_peak_start_days": 40,
    "milk_peak_end_days": 70,
    "breeding_wait_days": 50,
    "heat_cycle_days": 21,
}
FIELDS: tuple[str, ...] = tuple(DEFAULTS)


def get_or_create(db: Session, tenant_id: uuid.UUID) -> FarmSettings:
    row = db.scalar(select(FarmSettings).where(FarmSettings.deleted_at.is_(None)).limit(1))
    if row is None:
        row = FarmSettings(tenant_id=tenant_id, **DEFAULTS)
        db.add(row)
        db.flush()
    return row


def calendar(db: Session, tenant_id: uuid.UUID) -> dict[str, int]:
    """The five settings as a plain dict — what the reproduction plans are
    drawn from (dry-off, milk peak, breeding wait, heat watch)."""
    row = get_or_create(db, tenant_id)
    return {k: int(getattr(row, k)) for k in FIELDS}


def as_dict(row: FarmSettings) -> dict[str, Any]:
    return {k: int(getattr(row, k)) for k in FIELDS}
