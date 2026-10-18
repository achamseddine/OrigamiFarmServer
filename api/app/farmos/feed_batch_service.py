"""Formulas, mixing batches and nutrient profiles (feed architecture §5–§8).

A formula is a recipe family; a version is one immutable composition; a
batch is one mixing occurrence that consumed real lots and produced a new
lot at a real cost. Target quantities are scaled from the version; actual
quantities are what was weighed — and only the actuals touch inventory
and cost. The version is locked the moment a completed batch refers to
it, so history always resolves to the exact recipe used.

Every batch is a numbered mix (FEED-SCHEMA §20): one immutable, farm-wide
sequence whatever the mix is intended for. User and asset *names* are not
resolved here — the tenant session cannot see the control database; the
routers fill them in.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.farmos import feed_catalog as catalog
from app.farmos import feed_inventory_service as inv
from app.farmos import feed_models as fm
from app.farmos import feed_policy_service as policy
from app.farmos import livestock_catalog
from app.farmos.deps import AccessContext
from app.farmos.feed_catalog import FeedError
from app.farmos.livestock_service import ensure_utc, now_utc
from app.tenant_api.models import InventoryItem, InventoryMovement


# -------------------------------------------------------------- formulas
def _validate_components(
    db: Session,
    tenant_id: uuid.UUID,
    formula: fm.FeedFormula | None,
    batch_size: float,
    unit: str,
    components: list[dict],
    *,
    species_code: str | None,
) -> list[fm.FeedFormulaComponent]:
    if not components:
        raise FeedError("A formula needs at least one component.")
    if batch_size <= 0:
        raise FeedError("Batch size must be greater than zero.")
    rows = []
    total = 0.0
    for i, c in enumerate(components):
        product = inv.get_product(db, c["feed_product_id"])
        if not product.is_ingredient:
            raise FeedError(
                f"{product.name} is a finished feed, not an ingredient; it cannot go into a formula."
            )
        qty = float(c.get("target_quantity") or 0)
        c_unit = catalog.normalise(c.get("unit") or product.unit)
        if qty <= 0:
            raise FeedError(f"Target quantity for {product.name} must be greater than zero.")
        if not catalog.compatible(c_unit, unit):
            raise FeedError(f"{product.name} is measured in {c_unit}; the formula is in {unit}.")
        in_formula_unit = catalog.convert(qty, c_unit, unit)
        pct = c.get("target_percentage")
        if pct is None:
            pct = round(in_formula_unit / batch_size * 100, 3)
        # The policy check of §24 at authoring time: a cattle-only premix in
        # a horse formula fails here, before any batch exists.
        label = f"{formula.name if formula else 'this formula'} ({species_code or 'general'})"
        policy.check_or_raise(
            db,
            product,
            policy.Target(species_code=species_code, label=label, partial=True),
            inclusion_pct=pct,
            through_formula=True,
        )
        total += in_formula_unit
        rows.append(
            fm.FeedFormulaComponent(
                tenant_id=tenant_id,
                feed_product_id=product.id,
                target_quantity=qty,
                unit=c_unit,
                target_percentage=pct,
                sort_order=i,
            )
        )
    if abs(total - batch_size) > batch_size * 0.02:
        raise FeedError(
            f"The components add up to {total:.1f} {unit}, not the batch size of {batch_size:.1f} {unit}."
        )
    return rows


def _formula_code_taken(db: Session, code: str | None) -> bool:
    if not code:
        return True
    return db.scalar(select(fm.FeedFormula.id).where(fm.FeedFormula.code == code)) is not None


def _unique_formula_code(db: Session, base: str) -> str:
    code, n = base, 2
    while db.scalar(select(fm.FeedFormula.id).where(fm.FeedFormula.code == code)):
        code = f"{base}-{n}"
        n += 1
    return code


def create_formula(
    db: Session,
    access: AccessContext,
    *,
    code: str | None,
    name: str,
    feed_product_id: str,
    species_code: str | None,
    description: str | None,
    batch_size: float,
    unit: str,
    components: list[dict],
    notes: str | None = None,
    activate: bool = True,
) -> fm.FeedFormula:
    product = inv.get_product(db, feed_product_id)
    if product.source_type != "farm_produced":
        raise FeedError(
            f"{product.name} is a purchased feed; a formula describes a farm-produced one. "
            "Feed it directly (§22)."
        )
    if species_code and species_code not in livestock_catalog.SPECIES_BY_CODE:
        raise FeedError(f"Unknown species '{species_code}'.")
    unit = catalog.normalise(unit or product.unit)
    formula = fm.FeedFormula(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        code=_unique_formula_code(db, inv.slug(code or name)) if _formula_code_taken(db, code) else str(code),
        name=name,
        feed_product_id=product.id,
        species_code=species_code,
        status="active",
        description=description,
    )
    db.add(formula)
    db.flush()
    add_version(
        db,
        access,
        formula,
        batch_size=batch_size,
        unit=unit,
        components=components,
        notes=notes,
        activate=activate,
    )
    return formula


def add_version(
    db: Session,
    access: AccessContext,
    formula: fm.FeedFormula,
    *,
    batch_size: float,
    unit: str | None,
    components: list[dict],
    notes: str | None = None,
    activate: bool = True,
) -> fm.FeedFormulaVersion:
    """A new composition. The previous version is untouched — retired if
    this one is activated, still referenced by every batch it made."""
    unit = catalog.normalise(unit or (formula.versions[-1].unit if formula.versions else "kg"))
    rows = _validate_components(
        db, access.tenant_id, formula, batch_size, unit, components, species_code=formula.species_code
    )
    next_version = (max((v.version_number for v in formula.versions), default=0)) + 1
    version = fm.FeedFormulaVersion(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        formula_id=formula.id,
        version_number=next_version,
        status="draft",
        batch_size=batch_size,
        unit=unit,
        notes=notes,
        created_by=access.user_id,
    )
    version.components.extend(rows)
    db.add(version)
    formula.versions.append(version)
    db.flush()
    if activate:
        activate_version(db, access, version)
    return version


def activate_version(
    db: Session, access: AccessContext, version: fm.FeedFormulaVersion
) -> fm.FeedFormulaVersion:
    formula = version.formula
    for other in formula.versions:
        if other.id != version.id and other.status == "active":
            other.status = "retired"
            other.effective_to = now_utc()
    version.status = "active"
    version.effective_from = now_utc()
    version.effective_to = None
    version.last_modified_by = access.membership_id
    return version


def scale(version: fm.FeedFormulaVersion, batch_size: float, unit: str | None = None) -> list[dict]:
    """The version's targets at another batch size (§17 mixing screen)."""
    unit = catalog.normalise(unit or version.unit)
    factor = catalog.convert(batch_size, unit, version.unit) / version.batch_size
    return [
        {
            "feed_product_id": str(c.feed_product_id),
            "target_quantity": round(c.target_quantity * factor, 3),
            "unit": c.unit,
            "target_percentage": c.target_percentage,
        }
        for c in version.components
    ]


def planned_cost(
    db: Session, version: fm.FeedFormulaVersion, batch_size: float, unit: str | None = None
) -> float | None:
    total = 0.0
    known = False
    for line in scale(version, batch_size, unit):
        product = db.get(fm.FeedProduct, uuid.UUID(line["feed_product_id"]))
        item = db.get(InventoryItem, product.inventory_item_id) if product else None
        cost = inv.unit_cost_of(None, product, item) if product else None
        if cost is None or product is None:
            continue
        known = True
        total += catalog.convert(line["target_quantity"], line["unit"], product.unit) * cost
    return round(total, 4) if known else None


# --------------------------------------------------------------- batches
def _unique_batch_code(db: Session, base: str) -> str:
    code, n = base, 2
    while db.scalar(select(fm.FeedBatch.id).where(fm.FeedBatch.batch_code == code)):
        code = f"{base}-{n}"
        n += 1
    return code


def mix_code_for(code_format: str, number: int) -> str:
    try:
        return code_format.format(n=number)
    except (KeyError, ValueError, IndexError):
        return f"MIX-{number:06d}"


def _sequence(db: Session, tenant_id: uuid.UUID, *, lock: bool) -> fm.FeedMixSequence:
    stmt = select(fm.FeedMixSequence).where(fm.FeedMixSequence.tenant_id == tenant_id)
    if lock:
        stmt = stmt.with_for_update()
    seq = db.execute(stmt).scalar_one_or_none()
    if seq is None:
        # A farm that mixed before the sequence existed continues after its
        # highest number rather than restarting at one.
        highest = db.scalar(select(func.max(fm.FeedBatch.mix_number))) or 0
        seq = fm.FeedMixSequence(tenant_id=tenant_id, last_number=highest)
        db.add(seq)
        db.flush()
    return seq


def next_mix_number(db: Session, tenant_id: uuid.UUID) -> tuple[int, str]:
    """The next number of the farm's one mix sequence (§20). Taken under
    the sequence row's lock so concurrent starts serialise; the number is
    consumed even if the batch is later cancelled — a gap is honest, a
    reused number is not."""
    seq = _sequence(db, tenant_id, lock=True)
    seq.last_number += 1
    db.flush()
    return seq.last_number, mix_code_for(seq.code_format, seq.last_number)


def batch_by_mix_number(db: Session, mix_number: int) -> fm.FeedBatch | None:
    return db.scalar(select(fm.FeedBatch).where(fm.FeedBatch.mix_number == mix_number))


def _intended_target(
    formula: fm.FeedFormula | None, species_code: str | None, profile: str | None, label: str
) -> policy.Target:
    """What the components are checked against: the intended use when the
    mixer said one, else the formula's own species. Partial, because a mix
    knows its species and profile but not the animal's life stage."""
    return policy.Target(
        species_code=species_code or (formula.species_code if formula else None),
        management_profile=profile,
        label=label,
        partial=True,
    )


def validate_intended_use(
    *,
    species_code: str | None,
    management_profile: str | None,
    mixer_asset_id: str | None,
    operator_id: str | None,
) -> None:
    """FEED-SCHEMA §20: intended species and profile are references to the
    configured catalog, the mixer is a farm asset, the operator a user —
    classification and provenance, never free text. (The operator's
    membership on this farm is checked by the router against the control
    database.)"""
    if species_code and species_code not in livestock_catalog.SPECIES_BY_CODE:
        raise FeedError(f"Unknown species '{species_code}'.")
    if management_profile and management_profile not in livestock_catalog.MANAGEMENT_PROFILE_CODES:
        raise FeedError(
            f"Unknown management profile '{management_profile}'. "
            "See GET /species/{code}/configuration for the configured profiles."
        )
    if mixer_asset_id:
        # Farm assets arrive with the platform stage; until then no mixer
        # can be named, and a free-text name is never accepted.
        raise FeedError("Mixer asset not found; register the mixer under Assets first.", 404)
    if operator_id and inv.parse_uuid(operator_id) is None:
        raise FeedError("Operator not found on this farm.", 404)


def start_batch(
    db: Session,
    access: AccessContext,
    *,
    formula_version_id: str | None,
    formula_id: str | None,
    batch_code: str | None,
    target_quantity: float,
    unit: str | None,
    notes: str | None,
    intended_species_code: str | None = None,
    intended_management_profile: str | None = None,
    mixer_asset_id: str | None = None,
    operator_id: str | None = None,
    production_date: datetime | None = None,
) -> fm.FeedBatch:
    """Opens a mixing batch from the active formula version, with scaled
    targets, and gives it the farm's next mix number. Re-runs the
    usage-policy check on every component against the intended use (§33:
    the restriction is revalidated when the physical batch is created)."""
    validate_intended_use(
        species_code=intended_species_code,
        management_profile=intended_management_profile,
        mixer_asset_id=mixer_asset_id,
        operator_id=operator_id,
    )
    version: fm.FeedFormulaVersion | None = None
    if formula_version_id:
        pk = inv.parse_uuid(formula_version_id)
        version = db.get(fm.FeedFormulaVersion, pk) if pk else None
    elif formula_id:
        pk = inv.parse_uuid(formula_id)
        formula = db.get(fm.FeedFormula, pk) if pk else None
        if formula is None:
            raise FeedError("Formula not found", 404)
        version = next((v for v in formula.versions if v.status == "active"), None)
        if version is None:
            raise FeedError(f"{formula.name} has no active version.")
    if version is None:
        raise FeedError("Formula version not found", 404)
    if version.status != "active":
        raise FeedError(
            f"{version.formula.name} v{version.version_number} is {version.status}; "
            "mix from the active version."
        )
    if target_quantity <= 0:
        raise FeedError("Target quantity must be greater than zero.")
    unit = catalog.normalise(unit or version.unit)
    formula = version.formula
    product = db.get(fm.FeedProduct, formula.feed_product_id)
    if product is None:
        raise FeedError("The formula's product no longer exists.", 409)
    when = now_utc()
    species = intended_species_code or formula.species_code
    target = _intended_target(
        formula, intended_species_code, intended_management_profile, f"batch of {formula.name}"
    )
    # The policy check comes before the number is taken, so a refused mix
    # does not burn a number.
    lines = scale(version, target_quantity, unit)
    for line in lines:
        component = inv.get_product(db, line["feed_product_id"])
        policy.check_or_raise(
            db, component, target, inclusion_pct=line["target_percentage"], through_formula=True
        )
    number, code = next_mix_number(db, access.tenant_id)
    batch = fm.FeedBatch(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        feed_product_id=product.id,
        formula_version_id=version.id,
        batch_code=_unique_batch_code(db, batch_code or code),
        mix_number=number,
        mix_code=code,
        intended_species_code=species,
        intended_management_profile=intended_management_profile,
        production_date=ensure_utc(production_date) or when,
        mixer_asset_id=None,
        operator_id=inv.parse_uuid(operator_id) or access.user_id,
        status="in_progress",
        target_quantity=target_quantity,
        unit=unit,
        planned_cost=planned_cost(db, version, target_quantity, unit),
        started_at=when,
        mixed_by=access.user_id,
        notes=notes,
    )
    for line in lines:
        component = inv.get_product(db, line["feed_product_id"])
        batch.components.append(
            fm.FeedBatchComponent(
                tenant_id=access.tenant_id,
                feed_product_id=component.id,
                target_quantity=line["target_quantity"],
                unit=line["unit"],
                unit_cost=inv.unit_cost_of(
                    None, component, db.get(InventoryItem, component.inventory_item_id)
                ),
            )
        )
    db.add(batch)
    db.flush()
    return batch


def complete_batch(
    db: Session,
    access: AccessContext,
    batch: fm.FeedBatch,
    *,
    actuals: list[dict],
    actual_quantity: float,
    produced_at: datetime | None = None,
    lot_code: str | None = None,
    expiry_date: datetime | None = None,
    notes: str | None = None,
    allow_negative: bool = False,
    operator_id: str | None = None,
    mixer_asset_id: str | None = None,
) -> fm.FeedBatch:
    """§7: the actual weighed quantities consume inventory (from the lots
    named, else FIFO), cost the batch, and produce the output lot. Targets
    stay on the components for variance. Locks the formula version."""
    if batch.status not in ("planned", "in_progress"):
        raise FeedError(f"Batch {batch.batch_code} is {batch.status}.")
    if actual_quantity <= 0:
        raise FeedError("Actual quantity produced must be greater than zero.")
    validate_intended_use(
        species_code=None, management_profile=None, mixer_asset_id=mixer_asset_id, operator_id=operator_id
    )
    if operator_id:
        batch.operator_id = inv.parse_uuid(operator_id)
    version = db.get(fm.FeedFormulaVersion, batch.formula_version_id) if batch.formula_version_id else None
    formula = version.formula if version else None
    provided = {str(a["feed_product_id"]): a for a in actuals}
    targets = {str(c.feed_product_id): c for c in batch.components}
    missing = [p for p in targets if p not in provided]
    if missing:
        names = ", ".join(inv.get_product(db, p).name for p in missing)
        raise FeedError(
            f"Actual quantities are missing for: {names}. Record zero explicitly if none was used."
        )

    when = ensure_utc(produced_at) or now_utc()
    new_components: list[fm.FeedBatchComponent] = []
    total_cost = 0.0
    cost_known = False
    total_in = 0.0
    for product_id, a in provided.items():
        product = inv.get_product(db, product_id)
        qty = float(a.get("actual_quantity") or 0)
        unit = catalog.normalise(a.get("unit") or product.unit)
        target = targets.get(product_id)
        if qty < 0:
            raise FeedError(f"Actual quantity for {product.name} cannot be negative.")
        in_batch_unit = catalog.convert(qty, unit, batch.unit)
        pct = in_batch_unit / actual_quantity * 100 if actual_quantity else None
        policy.check_or_raise(
            db,
            product,
            _intended_target(
                formula,
                batch.intended_species_code,
                batch.intended_management_profile,
                f"mix {batch.mix_code}",
            ),
            inclusion_pct=pct,
            through_formula=True,
        )
        if qty == 0:
            new_components.append(
                fm.FeedBatchComponent(
                    tenant_id=batch.tenant_id,
                    feed_product_id=product.id,
                    lot_id=None,
                    target_quantity=target.target_quantity if target else None,
                    actual_quantity=0,
                    unit=unit,
                    unit_cost=None,
                    cost=0,
                )
            )
            continue
        draws = inv.consume(
            db,
            product,
            qty,
            unit,
            reason="mixing",
            linked_entity_type="feed_batch",
            linked_entity_id=str(batch.id),
            lot_id=a.get("lot_id"),
            allow_negative=allow_negative,
            actor=access.membership_id,
            occurred_at=when,
        )
        first = True
        for d in draws:
            target_qty = None
            if target and first:
                target_qty = catalog.convert(target.target_quantity or 0, target.unit, product.unit)
            new_components.append(
                fm.FeedBatchComponent(
                    tenant_id=batch.tenant_id,
                    feed_product_id=product.id,
                    lot_id=d.lot.id if d.lot else None,
                    target_quantity=target_qty,
                    actual_quantity=round(d.quantity, 3),
                    unit=product.unit,
                    unit_cost=d.unit_cost,
                    cost=d.cost,
                )
            )
            first = False
            if d.cost is not None:
                cost_known = True
                total_cost += d.cost
        total_in += in_batch_unit
    if abs(total_in - actual_quantity) > max(actual_quantity * 0.1, 1):
        raise FeedError(
            f"The ingredients weigh {total_in:.1f} {batch.unit} but the batch is recorded as "
            f"{actual_quantity:.1f} {batch.unit} — check the quantities."
        )
    batch.components.clear()
    db.flush()
    batch.components.extend(new_components)
    batch.actual_quantity = actual_quantity
    batch.actual_cost = round(total_cost, 4) if cost_known else None
    batch.unit_cost = round(total_cost / actual_quantity, 6) if cost_known else None
    batch.produced_at = when
    batch.production_date = when
    batch.use_by_date = ensure_utc(expiry_date)
    batch.status = "completed"
    batch.version += 1
    batch.last_modified_by = access.membership_id
    if notes:
        batch.notes = (batch.notes + "\n" if batch.notes else "") + notes
    if version is not None:
        version.locked = True
    output = db.get(fm.FeedProduct, batch.feed_product_id)
    if output is None:
        raise FeedError("The batch's product no longer exists.", 409)
    # The output lot carries the mix code, so "who ate MIX-000042" is one
    # lookup on the lot the feeding events name.
    lot = inv.receive(
        db,
        output,
        quantity=actual_quantity,
        unit=batch.unit,
        unit_cost=batch.unit_cost,
        lot_code=lot_code or batch.mix_code or batch.batch_code,
        source_type="farm_produced",
        expiry_date=expiry_date,
        feed_batch_id=batch.id,
        received_at=when,
        notes=f"Produced by mix {batch.mix_code} ({batch.batch_code})",
        actor=access.membership_id,
        actor_user=access.user_id,
    )
    batch.output_lot_id = lot.id
    db.flush()
    return batch


def quarantine_batch(db: Session, access: AccessContext, batch: fm.FeedBatch, *, reason: str) -> fm.FeedBatch:
    if batch.status != "completed":
        raise FeedError("Only a completed batch can be quarantined.")
    batch.status = "quarantined"
    batch.notes = (batch.notes + "\n" if batch.notes else "") + f"Quarantined: {reason}"
    batch.version += 1
    batch.last_modified_by = access.membership_id
    if batch.output_lot_id:
        lot = db.get(fm.FeedLot, batch.output_lot_id)
        if lot is not None:
            inv.set_lot_status(db, lot, "quarantined", actor=access.membership_id)
    return batch


# ------------------------------------------------------------ numbering
def mix_settings(db: Session, tenant_id: uuid.UUID) -> fm.FeedMixSequence:
    return _sequence(db, tenant_id, lock=False)


def set_mix_code_format(db: Session, access: AccessContext, *, code_format: str) -> fm.FeedMixSequence:
    """§20: the display format is the farm's to choose; the number is the
    identity and never changes. Existing mixes keep the code they were
    given — a historical mix is never renumbered or relabelled."""
    fmt = (code_format or "").strip()
    if "{n" not in fmt:
        raise FeedError("The mix code format must contain {n} (the number), e.g. MIX-{n:06d} or DM-{n}.")
    try:
        sample = fmt.format(n=1)
    except (KeyError, ValueError, IndexError) as e:
        raise FeedError(f"That format is not valid: {e}") from e
    if len(sample) > 120:
        raise FeedError("The mix code is limited to 120 characters.")
    seq = _sequence(db, access.tenant_id, lock=True)
    seq.code_format = fmt
    seq.version += 1
    seq.last_modified_by = access.membership_id
    return seq


def mix_settings_dict(seq: fm.FeedMixSequence) -> dict:
    return {
        "farm_id": str(seq.tenant_id),
        "code_format": seq.code_format,
        "last_number": seq.last_number,
        "next_number": seq.last_number + 1,
        "next_code": mix_code_for(seq.code_format, seq.last_number + 1),
    }


def mix_usage(db: Session, batch: fm.FeedBatch) -> dict:
    """The numbered mix's whole life (§20): what went into it, what came
    out, every dated issue to an animal or group through the output lot,
    waste and corrections on that lot, and the remaining quantity — which
    is the lot's ledger balance, never a figure kept on the batch. Answers
    "what was in MIX-N?" and "who ate MIX-N, when, how much, and what is
    left?" from one call. Names of people are filled in by the router."""
    from app.farmos import feeding_program_service as programs  # local: programs imports this module

    lot = db.get(fm.FeedLot, batch.output_lot_id) if batch.output_lot_id else None
    if lot is not None:
        inv.refresh_lot_status(lot)
    version = db.get(fm.FeedFormulaVersion, batch.formula_version_id) if batch.formula_version_id else None
    formula = version.formula if version else None
    product = db.get(fm.FeedProduct, batch.feed_product_id)

    components = []
    for c in batch.components:
        src = db.get(fm.FeedLot, c.lot_id) if c.lot_id else None
        comp_product = db.get(fm.FeedProduct, c.feed_product_id)
        components.append(
            {
                "feed_product_id": str(c.feed_product_id),
                "product_name": comp_product.name if comp_product else None,
                "lot_id": str(c.lot_id) if c.lot_id else None,
                "lot_code": src.lot_code if src else None,
                "supplier_id": str(src.supplier_id) if src and src.supplier_id else None,
                "supplier_label": src.supplier_label if src else None,
                "lot_source_type": src.source_type if src else None,
                "target_quantity": c.target_quantity,
                "actual_quantity": c.actual_quantity,
                "unit": c.unit,
                "unit_cost": c.unit_cost,
                "cost": c.cost,
            }
        )

    issues: list[dict] = []
    ledger: list[dict] = []
    issued = consumed = refused = waste = other_out = returned = 0.0
    consumed_known = False
    use_dates: set = set()
    head_days: dict[tuple, int] = {}
    first_use = last_use = None
    if lot is not None:
        comps = db.scalars(
            select(fm.FeedingEventComponent).where(fm.FeedingEventComponent.lot_id == lot.id)
        ).all()
        for fc in comps:
            e = db.get(fm.FeedingEvent, fc.event_id)
            if e is None:
                continue
            subject = programs.subject_row(db, e.subject_type, e.subject_id)
            tx = db.scalar(
                select(InventoryMovement).where(
                    InventoryMovement.linked_entity_type == "feeding_event",
                    InventoryMovement.linked_entity_id == str(e.id),
                    InventoryMovement.lot_id == lot.id,
                )
            )
            pv = db.get(fm.FeedingProgramVersion, e.program_version_id) if e.program_version_id else None
            assignment = db.get(fm.FeedingAssignment, e.assignment_id) if e.assignment_id else None
            issues.append(
                {
                    "feeding_event_id": str(e.id),
                    "occurred_at": e.occurred_at,
                    "event_type": e.event_type,
                    "status": e.status,
                    "reversal_of_id": str(e.reversal_of_id) if e.reversal_of_id else None,
                    "subject_type": e.subject_type,
                    "subject_id": str(e.subject_id),
                    "subject_name": getattr(subject, "name", None),
                    "species": getattr(subject, "species", None),
                    "head_count": e.head_count,
                    "quantity_offered": fc.quantity_offered,
                    "quantity_consumed": fc.quantity_consumed,
                    "unit": fc.unit,
                    "cost": fc.cost,
                    "recorded_by": str(e.recorded_by) if e.recorded_by else None,
                    "recorded_by_name": None,
                    "program_id": str(pv.program_id) if pv else None,
                    "program_version_id": str(e.program_version_id) if e.program_version_id else None,
                    "program_code": pv.program.code if pv else None,
                    "program_version": pv.version_number if pv else None,
                    "assignment_id": str(e.assignment_id) if e.assignment_id else None,
                    "assignment_type": assignment.assignment_type if assignment else None,
                    "assignment_subject_type": assignment.subject_type if assignment else None,
                    "inventory_transaction_id": str(tx.id) if tx else None,
                }
            )
            if e.status != "recorded":
                continue  # reversed originals and the reversals themselves net to zero
            if e.event_type in ("offered", "delivered"):
                issued += fc.quantity_offered
                if fc.quantity_consumed is not None:
                    consumed += fc.quantity_consumed
                    consumed_known = True
                day = (ensure_utc(e.occurred_at) or now_utc()).date()
                use_dates.add(day)
                key = (day, e.subject_type, str(e.subject_id))
                head_days[key] = max(head_days.get(key, 0), e.head_count or 1)
                first_use = e.occurred_at if first_use is None or e.occurred_at < first_use else first_use
                last_use = e.occurred_at if last_use is None or e.occurred_at > last_use else last_use
            elif e.event_type == "consumed_estimate":
                consumed += fc.quantity_consumed if fc.quantity_consumed is not None else fc.quantity_offered
                consumed_known = True
            elif e.event_type == "refusal":
                refused += fc.quantity_offered
        # Everything else that touched the output lot: waste, corrections,
        # reconciliation adjustments, returns.
        for tx in db.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.lot_id == lot.id)
            .order_by(InventoryMovement.occurred_at)
        ):
            if tx.reason in ("feeding", "feeding_reversal", "production", "purchase", "opening_balance"):
                continue
            delta = float(tx.quantity_delta)
            direction = "in" if delta >= 0 else "out"
            ledger.append(
                {
                    "inventory_transaction_id": str(tx.id),
                    "occurred_at": tx.occurred_at,
                    "direction": direction,
                    "quantity": abs(delta),
                    "reason": tx.reason,
                    "linked_entity_type": tx.linked_entity_type,
                    "linked_entity_id": tx.linked_entity_id,
                }
            )
            if direction == "out" and tx.reason == "waste":
                waste += abs(delta)
            elif direction == "out":
                other_out += abs(delta)
            else:
                returned += delta
    issues.sort(key=lambda i: ensure_utc(i["occurred_at"]) or now_utc())
    trace = inv.lot_trace(db, lot) if lot is not None else None
    produced = lot.accepted_quantity if lot is not None else (batch.actual_quantity or 0)
    remaining = lot.quantity_on_hand if lot is not None else 0.0
    variance = batch_variance(batch) if batch.status in ("completed", "quarantined") else []
    worst = max((abs(v["variance_pct"]) for v in variance if v.get("variance_pct") is not None), default=None)
    return {
        "batch_id": str(batch.id),
        "mix_number": batch.mix_number,
        "mix_code": batch.mix_code,
        "batch_code": batch.batch_code,
        "status": batch.status,
        "feed_product_id": str(batch.feed_product_id),
        "product_name": product.name if product else None,
        "formula_id": str(formula.id) if formula else None,
        "formula_code": formula.code if formula else None,
        "formula_name": formula.name if formula else None,
        "formula_version": version.version_number if version else None,
        "formula_version_id": str(batch.formula_version_id) if batch.formula_version_id else None,
        "intended_species_code": batch.intended_species_code,
        "intended_management_profile": batch.intended_management_profile,
        "production_date": batch.production_date,
        "started_at": batch.started_at,
        "produced_at": batch.produced_at,
        "use_by_date": batch.use_by_date,
        "operator_id": str(batch.operator_id or batch.mixed_by)
        if (batch.operator_id or batch.mixed_by)
        else None,
        "operator_name": None,
        "mixed_by": str(batch.mixed_by) if batch.mixed_by else None,
        "mixer_asset_id": str(batch.mixer_asset_id) if batch.mixer_asset_id else None,
        "mixer_asset_name": None,
        "notes": batch.notes,
        "target_quantity": batch.target_quantity,
        "actual_quantity": batch.actual_quantity,
        "unit": batch.unit,
        "planned_cost": batch.planned_cost,
        "actual_cost": batch.actual_cost,
        "cost_per_unit": batch.unit_cost,
        "output_lot": None
        if lot is None
        else {
            "id": str(lot.id),
            "lot_code": lot.lot_code,
            "status": lot.status,
            "quantity_on_hand": round(lot.quantity_on_hand, 3),
            "expiry_date": lot.expiry_date,
            "unit": lot.unit,
        },
        "components": components,
        "variance": variance,
        "worst_variance_pct": worst,
        "produced_quantity": round(produced, 3),
        "issued_quantity": round(issued, 3),
        "consumed_estimate": round(consumed, 3) if consumed_known else None,
        "refused_quantity": round(refused, 3),
        "waste_quantity": round(waste, 3),
        "other_issued_quantity": round(other_out, 3),
        "returned_quantity": round(returned, 3),
        "remaining_quantity": round(remaining, 3),
        "eligible_remaining_quantity": round(remaining, 3)
        if lot is not None and inv.lot_usable(lot)
        else 0.0,
        "first_use_at": first_use,
        "last_use_at": last_use,
        "days_used": len(use_dates),
        "head_days": sum(head_days.values()),
        "exposed_subjects": trace["exposed_subjects"] if trace else [],
        "issues": issues,
        "ledger_adjustments": ledger,
        # Feed-performance scoring arrives with a later stage.
        "performance": None,
    }


def batch_variance(batch: fm.FeedBatch) -> list[dict]:
    by_product: dict[str, dict] = {}
    for c in batch.components:
        pid = str(c.feed_product_id)
        e = by_product.setdefault(
            pid, {"feed_product_id": pid, "target": 0.0, "actual": 0.0, "unit": c.unit, "lots": []}
        )
        e["target"] += c.target_quantity or 0
        e["actual"] += c.actual_quantity or 0
        if c.lot_id:
            e["lots"].append(str(c.lot_id))
    for e in by_product.values():
        e["variance"] = round(e["actual"] - e["target"], 3)
        e["variance_pct"] = round((e["actual"] - e["target"]) / e["target"] * 100, 2) if e["target"] else None
    return list(by_product.values())


# ------------------------------------------------------------- nutrients
def record_profile(
    db: Session,
    access: AccessContext,
    *,
    subject_type: str,
    subject_id: str,
    basis: str,
    source_type: str,
    values: list[dict],
    reference: str | None = None,
    effective_at: datetime | None = None,
) -> fm.FeedNutrientProfile:
    if subject_type not in catalog.PROFILE_SUBJECTS:
        raise FeedError(f"subject_type must be one of {list(catalog.PROFILE_SUBJECTS)}")
    if basis not in catalog.BASES:
        raise FeedError(f"basis must be one of {list(catalog.BASES)}")
    if source_type not in catalog.PROFILE_SOURCES:
        raise FeedError(f"source_type must be one of {list(catalog.PROFILE_SOURCES)}")
    if not values:
        raise FeedError("A nutrient profile needs at least one value.")
    subject_pk = inv.parse_uuid(subject_id)
    if subject_pk is None:
        raise FeedError("subject_id must be an id.", 404)
    model = {
        "feed_product": fm.FeedProduct,
        "formula_version": fm.FeedFormulaVersion,
        "feed_lot": fm.FeedLot,
        "feed_batch": fm.FeedBatch,
    }[subject_type]
    if db.get(model, subject_pk) is None:
        raise FeedError(f"{subject_type.replace('_', ' ').capitalize()} not found", 404)
    profile = fm.FeedNutrientProfile(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        subject_type=subject_type,
        subject_id=subject_pk,
        basis=basis,
        source_type=source_type,
        reference=reference,
        effective_at=ensure_utc(effective_at) or now_utc(),
        created_by=access.user_id,
    )
    for v in values:
        nutrient = catalog.NUTRIENT_BY_CODE.get(str(v["nutrient_code"]).upper())
        if nutrient is None:
            raise FeedError(f"Unknown nutrient {v['nutrient_code']}; it is not in the nutrient catalog.")
        profile.values.append(
            fm.FeedNutrientValue(
                tenant_id=access.tenant_id,
                nutrient_code=nutrient["code"],
                value=float(v["value"]),
                unit=v.get("unit") or nutrient["unit"],
            )
        )
    db.add(profile)
    db.flush()
    return profile


_SOURCE_RANK = {"lab": 0, "declared": 1, "calculated": 2}


def latest_profile(
    db: Session, subject_type: str, subject_id: uuid.UUID, *, basis: str | None = None
) -> fm.FeedNutrientProfile | None:
    """The profile to trust: the newest lab result, else the declared one,
    else a calculation — never a lab result overwritten by a declaration."""
    rows = db.scalars(
        select(fm.FeedNutrientProfile).where(
            fm.FeedNutrientProfile.subject_type == subject_type,
            fm.FeedNutrientProfile.subject_id == subject_id,
        )
    ).all()
    rows = list(rows)
    if basis:
        rows = [r for r in rows if r.basis == basis]
    if not rows:
        return None
    rows.sort(
        key=lambda r: (
            _SOURCE_RANK.get(r.source_type, 9),
            -(ensure_utc(r.effective_at) or now_utc()).timestamp(),
        )
    )
    return rows[0]


def profile_to_dict(p: fm.FeedNutrientProfile) -> dict:
    return {
        "id": str(p.id),
        "subject_type": p.subject_type,
        "subject_id": str(p.subject_id),
        "basis": p.basis,
        "source_type": p.source_type,
        "reference": p.reference,
        "effective_at": p.effective_at,
        "values": [{"nutrient_code": v.nutrient_code, "value": v.value, "unit": v.unit} for v in p.values],
    }


def formula_nutrients(db: Session, version: fm.FeedFormulaVersion, *, basis: str = "as_fed") -> dict:
    """Weighted average of the components' profiles (§8) — a calculated
    profile, reported with its coverage so a missing ingredient analysis
    is visible rather than silently treated as zero."""
    total = 0.0
    covered = 0.0
    sums: dict[str, dict] = {}
    missing: list[str] = []
    for c in version.components:
        product = db.get(fm.FeedProduct, c.feed_product_id)
        weight = catalog.convert(c.target_quantity, c.unit, version.unit)
        total += weight
        profile = latest_profile(db, "feed_product", c.feed_product_id, basis=basis)
        if profile is None:
            missing.append(product.name if product else str(c.feed_product_id))
            continue
        covered += weight
        for v in profile.values:
            entry = sums.setdefault(
                v.nutrient_code,
                {"nutrient_code": v.nutrient_code, "weighted": 0.0, "weight": 0.0, "unit": v.unit},
            )
            entry["weighted"] += v.value * weight
            entry["weight"] += weight
    values = [
        {
            "nutrient_code": code,
            "value": round(e["weighted"] / e["weight"], 3),
            "unit": e["unit"],
            "coverage_pct": round(e["weight"] / total * 100, 1) if total else 0,
        }
        for code, e in sums.items()
    ]
    return {
        "formula_version_id": str(version.id),
        "basis": basis,
        "source_type": "calculated",
        "coverage_pct": round(covered / total * 100, 1) if total else 0,
        "missing_profiles": missing,
        "values": values,
    }
