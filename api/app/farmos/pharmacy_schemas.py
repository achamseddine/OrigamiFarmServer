"""Request bodies for the pharmacy and procurement APIs (OrigamiFarmOS
database/MEDICINE-PHARMACY-SCHEMA.md, database/PROCUREMENT-SCHEMA.md).

Responses are plain dicts built by the services, so the tablet and the
contract page read one shape — the same shapes the reference backend
returns.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MedicineIngredientIn(BaseModel):
    code: str
    name: str | None = None
    concentration_value: float | None = None
    concentration_uom: str | None = None
    concentration_basis: str | None = None


class MedicineProductIn(BaseModel):
    """`POST /pharmacy/medicines`. Either names an existing inventory item
    (`inventory_item_id`) or creates one from `name` and `unit`."""

    inventory_item_id: str | None = None
    name: str | None = None
    unit: str | None = None
    generic_name: str | None = None
    brand_name: str | None = None
    dosage_form: str = "injectable"
    strength_value: float | None = None
    strength_uom: str | None = None
    strength_basis: str | None = None
    administration_routes: list[str] = Field(default_factory=list)
    prescription_required: bool = False
    antimicrobial: bool = False
    controlled_medicine: bool = False
    cold_chain_required: bool = False
    storage_min_c: float | None = None
    storage_max_c: float | None = None
    opened_shelf_life_days: int | None = None
    default_pack_size: float | None = None
    pack_uom: str | None = None
    manufacturer_name: str | None = None
    authorization_reference: str | None = None
    species_codes: list[str] = Field(default_factory=list)
    withdrawal_rules: dict = Field(default_factory=dict)
    lot_tracking_exception: str | None = None
    category_codes: list[str] = Field(default_factory=list)
    ingredients: list[MedicineIngredientIn] = Field(default_factory=list)
    supplier_label: str | None = None
    unit_cost: float | None = None


class MedicineProductPatch(BaseModel):
    generic_name: str | None = None
    brand_name: str | None = None
    dosage_form: str | None = None
    strength_value: float | None = None
    strength_uom: str | None = None
    strength_basis: str | None = None
    administration_routes: list[str] | None = None
    prescription_required: bool | None = None
    antimicrobial: bool | None = None
    controlled_medicine: bool | None = None
    cold_chain_required: bool | None = None
    storage_min_c: float | None = None
    storage_max_c: float | None = None
    opened_shelf_life_days: int | None = None
    default_pack_size: float | None = None
    pack_uom: str | None = None
    manufacturer_name: str | None = None
    authorization_reference: str | None = None
    species_codes: list[str] | None = None
    withdrawal_rules: dict | None = None
    lot_tracking_exception: str | None = None
    category_codes: list[str] | None = None
    ingredients: list[MedicineIngredientIn] | None = None
    active: bool | None = None


class MedicineLotReceive(BaseModel):
    """`POST /pharmacy/lots/receive` — each receipt records the medicine
    lot and expiry (acceptance 2)."""

    inventory_item_id: str
    quantity: float
    unit: str | None = None
    lot_code: str | None = None
    expiry_date: datetime | None = None
    unit_cost: float | None = None
    supplier_id: str | None = None
    supplier_label: str | None = None
    location_id: str | None = None
    rejected_quantity: float = 0
    source_type: str = "purchased"
    reference: str | None = None
    notes: str | None = None
    received_at: datetime | None = None
    storage_status: str = "COMPLIANT"
    cold_chain_exception: bool = False
    # INVENTORY-SCHEMA §3 lot facts
    supplier_lot_code: str | None = None
    manufactured_at: datetime | None = None
    currency: str | None = None
    # The purchase requisition this delivery fulfils, if any — closes it.
    source_reference_type: str | None = None
    source_reference_id: str | None = None


class ReservationIn(BaseModel):
    """`POST /pharmacy/reservations` — hold eligible stock for a purpose
    (INVENTORY-SCHEMA §5). Reservation is not consumption."""

    inventory_item_id: str
    quantity: float
    unit: str | None = None
    lot_id: str | None = None
    location_id: str | None = None
    purpose_type: str = "other"
    purpose_id: str | None = None
    purpose_label: str | None = None
    valid_to: datetime | None = None


class ReservationRelease(BaseModel):
    reason: str | None = None


class TransferIn(BaseModel):
    """`POST /pharmacy/transfers` — part of a lot moves between locations
    as a paired OUT / IN (INVENTORY-SCHEMA §4)."""

    inventory_item_id: str
    lot_id: str
    quantity: float
    to_location_id: str
    from_location_id: str | None = None
    reason: str | None = None


class MedicineLotStatus(BaseModel):
    status: str
    reason: str | None = None
    recall_reference: str | None = None


class MedicineLotStorage(BaseModel):
    storage_status: str
    cold_chain_exception: bool = False
    note: str | None = None


class MedicineLotOpen(BaseModel):
    opened_at: datetime | None = None


class PharmacyAdjustment(BaseModel):
    inventory_item_id: str
    lot_id: str
    delta: float
    reason: str
    explanation: str


class PharmacyPolicyIn(BaseModel):
    """`PUT /pharmacy/policies/{item_id}` — the farm manager's own
    thresholds (acceptance 3). None leaves a threshold unset."""

    location_id: str | None = None
    essential: bool = False
    minimum_stock_base: float | None = None
    target_stock_base: float | None = None
    critical_stock_base: float | None = None
    minimum_days_cover: float | None = None
    lead_time_days: float | None = None
    preferred_supplier_id: str | None = None
    alert_enabled: bool = True
    expiry_warning_days: int = 60
    auto_draft_requisition: bool = False
    active: bool = True


class PharmacyAlertClose(BaseModel):
    note: str


class MedicationAdministrationIn(BaseModel):
    """`POST /pharmacy/administrations` — the dose actually given, bound
    to the exact lot (acceptance 7). `lot_id` empty means first-expiry-
    first-out among eligible lots. `quantity_consumed` is needed only when
    the dose unit cannot be converted to the stock unit."""

    subject_type: str
    subject_id: str
    inventory_item_id: str
    lot_id: str | None = None
    dose_quantity: float
    dose_unit: str
    route_code: str
    head_count: int | None = None
    quantity_consumed: float | None = None
    treatment_id: str | None = None
    administered_at: datetime | None = None
    reason: str | None = None
    notes: str | None = None
    # The reservation this dose draws on, and where it was issued from.
    reservation_id: str | None = None
    location_id: str | None = None


class AdministrationReverse(BaseModel):
    reason: str


# ------------------------------------------------------------- procurement
class RequisitionLineIn(BaseModel):
    inventory_item_id: str
    requested_quantity: float
    uom_code: str | None = None
    estimated_unit_price: float | None = None
    preferred_supplier_id: str | None = None
    specification: str | None = None


class RequisitionIn(BaseModel):
    """`POST /procurement/requisitions` — a manual DRAFT requisition."""

    lines: list[RequisitionLineIn]
    purpose_code: str | None = None
    needed_by: datetime | None = None
    justification: str | None = None
    currency: str | None = None


class RequisitionDecision(BaseModel):
    comments: str | None = None


class RequisitionCancel(BaseModel):
    reason: str
