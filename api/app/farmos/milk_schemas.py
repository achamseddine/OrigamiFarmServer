"""Requests for selling milk from the tank (reference v0.7.9
`schemas/production.py`) — the milk-sale handlers of
app/farmos/routes_production.py."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MilkSaleCreate(BaseModel):
    """POST /production/milk-sales — milk sold from the tank."""

    liters: float
    price_per_liter: float
    sold_at: datetime | None = None
    customer_id: str | None = None
    buyer_name: str | None = None
    # paid | pending (to be paid later)
    payment_status: str = "paid"
    reference: str | None = None
    notes: str | None = None
    # More than the tank holds on record: Milk production approval.
    allow_negative: bool = False
    confirm_duplicate: bool = False


class MilkSaleCancel(BaseModel):
    reason: str = ""


class MilkTankAdjust(BaseModel):
    """POST /production/milk-tank/adjustments — milk out of the tank outside
    a sale (`liters`, reason spoiled | calves | home_use | other) or a count
    of the tank (`counted_level`, reason count)."""

    reason: str
    liters: float | None = None
    counted_level: float | None = None
    occurred_at: datetime | None = None
    notes: str | None = None
