"""Farm-data-plane (RLS-protected, tenant-scoped) tables for the generic
feed architecture (OrigamiFarmOS docs/GENERIC-FEED-ARCHITECTURE.md).

Five concepts that are never collapsed into one table (§23):

    FeedProduct          what can be fed (an ingredient, a finished feed, or both)
    FeedFormula(Version) how a farm-produced feed is intended to be made
    FeedBatch            what was actually mixed, from which lots, at what cost —
                         every one a numbered mix from one farm-wide sequence
    FeedingProgram(Version) what a subject should receive, and to whom it applies
    FeedingEvent         what was actually offered / fed

around the masters this codebase already has: stock lives on
`inventory_item` / `inventory_movement` (every feed product owns one
inventory item, and every lot movement is an inventory movement with a
`lot_id`), and the livestock subject is `(subject_type, subject_id)` over
`animal` and `animal_group` — the generic animal model's Animal | AnimalGroup.

On top of the five: lots (§3, §26), nutrient profiles (§8), usage
policies (§24), allocations (§25), reorder policies and forecasts (§28)
and reconciliations (§27). Every quantity has a unit; the balances are
derived from the ledger, never edited. Quantities and costs are stored as
double precision, as the reference backend does, so the arithmetic of the
two implementations agrees to the cent.

Child rows (components, rules, values) carry tenant_id like every other
row here — RLS does not make exceptions for children.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin


# ------------------------------------------------------------- products
class FeedProduct(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """What can be fed, and what can go into a mix. One row is both when the
    farm both feeds it straight and mixes with it (hay, barley); a premix is
    an ingredient only; a purchased complete feed is feedable only. The
    stock itself is the linked inventory item — never a second balance."""

    __tablename__ = "feed_product"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_feed_product_tenant_code"),)

    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    name_ar: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # purchased | farm_produced
    source_type: Mapped[str] = mapped_column(String(20), default="purchased")
    is_ingredient: Mapped[bool] = mapped_column(Boolean, default=True)
    is_feedable: Mapped[bool] = mapped_column(Boolean, default=False)
    # forage | concentrate | premix | mineral | complete_feed | byproduct | other
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    default_unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    notes: Mapped[str | None] = mapped_column(nullable=True)

    lots: Mapped[list["FeedLot"]] = relationship(
        back_populates="product", lazy="selectin", order_by="FeedLot.received_at"
    )


class FeedLot(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A physical quantity of one product with one origin: a supplier
    delivery, a completed mixing batch, or the opening balance a farm
    started the system with. Quantities on hand move only through
    inventory movements that name the lot."""

    __tablename__ = "feed_lot"
    __table_args__ = (UniqueConstraint("tenant_id", "lot_code", name="uq_feed_lot_tenant_code"),)

    feed_product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_product.id"), index=True
    )
    lot_code: Mapped[str] = mapped_column(String(80))
    # purchased | farm_produced | opening_balance
    source_type: Mapped[str] = mapped_column(String(20), default="purchased")
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    supplier_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    feed_batch_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Purchase-to-consumption control (§26): what was ordered, what came,
    # what was accepted. On-hand starts at the accepted quantity.
    ordered_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    received_quantity: Mapped[float] = mapped_column(Float, default=0)
    accepted_quantity: Mapped[float] = mapped_column(Float, default=0)
    rejected_quantity: Mapped[float] = mapped_column(Float, default=0)
    quantity_on_hand: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    # active | quarantined | blocked | recalled | expired | depleted
    status: Mapped[str] = mapped_column(String(20), default="active")
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    product: Mapped[FeedProduct] = relationship(back_populates="lots")


# ------------------------------------------------------------- formulas
class FeedFormula(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """The recipe family for one farm-produced product. Versions carry the
    composition; the formula is the name that stays."""

    __tablename__ = "feed_formula"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_feed_formula_tenant_code"),)

    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    feed_product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_product.id"), index=True
    )
    # The species this feed is made for, if any: what usage policies are
    # checked against when a component is added (§24). Null = general.
    species_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    description: Mapped[str | None] = mapped_column(nullable=True)

    versions: Mapped[list["FeedFormulaVersion"]] = relationship(
        back_populates="formula",
        lazy="selectin",
        order_by="FeedFormulaVersion.version_number",
        cascade="all, delete-orphan",
    )


class FeedFormulaVersion(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """One immutable composition. `locked` is set the moment a completed
    batch references it; after that the rows cannot change — a new version
    is the only way to change the recipe (§6)."""

    __tablename__ = "feed_formula_version"
    __table_args__ = (UniqueConstraint("formula_id", "version_no", name="uq_feed_formula_version"),)

    formula_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_formula.id"), index=True
    )
    version_number: Mapped[int] = mapped_column("version_no", Integer, default=1)
    # draft | active | retired
    status: Mapped[str] = mapped_column(String(20), default="draft")
    batch_size: Mapped[float] = mapped_column(Float, default=1000)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    formula: Mapped[FeedFormula] = relationship(back_populates="versions")
    components: Mapped[list["FeedFormulaComponent"]] = relationship(
        back_populates="formula_version",
        lazy="selectin",
        order_by="FeedFormulaComponent.sort_order",
        cascade="all, delete-orphan",
    )


class FeedFormulaComponent(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "feed_formula_component"

    version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_formula_version.id"), index=True
    )
    feed_product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("feed_product.id"))
    target_quantity: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    target_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    formula_version: Mapped[FeedFormulaVersion] = relationship(back_populates="components")


# -------------------------------------------------------------- batches
class FeedBatch(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A mixing occurrence. Target quantities come from the formula scaled
    to the batch size; actual quantities are what was weighed in, and they
    — not the targets — drive inventory consumption and cost (§7)."""

    __tablename__ = "feed_batch"
    __table_args__ = (
        UniqueConstraint("tenant_id", "batch_code", name="uq_feed_batch_tenant_code"),
        UniqueConstraint("tenant_id", "mix_number", name="uq_feed_batch_tenant_mix_number"),
        UniqueConstraint("tenant_id", "mix_code", name="uq_feed_batch_tenant_mix_code"),
    )

    feed_product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_product.id"), index=True
    )
    formula_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_formula_version.id"), nullable=True
    )
    batch_code: Mapped[str] = mapped_column(String(80))
    # The numbered mix (FEED-SCHEMA §20): one immutable, farm-wide sequence
    # for every locally mixed preparation, whatever it is intended for. A
    # dairy mix, a horse mix and a chicken mix take consecutive numbers from
    # the same counter; the number is assigned when the batch is opened and
    # is never reused or changed. `mix_code` is its display form.
    mix_number: Mapped[int] = mapped_column(Integer)
    mix_code: Mapped[str] = mapped_column(String(120))
    # Intended use is classification and policy context, not identity: it
    # is the target the components' usage policies are checked against.
    intended_species_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    intended_management_profile: Mapped[str | None] = mapped_column(String(40), nullable=True)
    production_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    use_by_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The person who physically mixed (§20 operator_id); may differ from
    # `mixed_by`, the account that opened the batch. Both are user ids.
    operator_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # The mixer / equipment, when recorded: a farm asset id (platform stage).
    mixer_asset_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # planned | in_progress | completed | quarantined | cancelled
    status: Mapped[str] = mapped_column(String(20), default="planned")
    target_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    planned_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The lot this batch produced. Not a foreign key: the lot points back at
    # the batch, and two tables should not point at each other.
    output_lot_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    produced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mixed_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)

    components: Mapped[list["FeedBatchComponent"]] = relationship(
        back_populates="batch", lazy="selectin", cascade="all, delete-orphan"
    )


class FeedMixSequence(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """The farm's one mix counter (§20). A row per tenant, incremented under
    the row lock, so two tablets syncing at the same moment cannot both be
    handed mix 42. `code_format` is the display rule the farm chose; the
    integer is the identity."""

    __tablename__ = "feed_mix_sequence"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_feed_mix_sequence_tenant"),)

    last_number: Mapped[int] = mapped_column(Integer, default=0)
    code_format: Mapped[str] = mapped_column(String(60), default="MIX-{n:06d}")


class FeedBatchComponent(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "feed_batch_component"

    batch_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_batch.id"), index=True
    )
    feed_product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("feed_product.id"))
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_lot.id"), nullable=True, index=True
    )
    target_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    batch: Mapped[FeedBatch] = relationship(back_populates="components")


# ------------------------------------------------------------ nutrients
class FeedNutrientProfile(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A set of nutrient values for a product, formula version, lot or batch,
    on one basis, from one source. A lab result is a new profile beside the
    declared one — it never overwrites it (§8). Nutrient codes come from
    app/farmos/feed_catalog.py."""

    __tablename__ = "feed_nutrient_profile"
    __table_args__ = (Index("ix_feed_nutrient_profile_subject", "subject_type", "subject_id"),)

    # feed_product | formula_version | feed_lot | feed_batch
    subject_type: Mapped[str] = mapped_column(String(30))
    subject_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    # as_fed | dry_matter
    basis: Mapped[str] = mapped_column(String(20), default="as_fed")
    # declared | calculated | lab
    source_type: Mapped[str] = mapped_column(String(20), default="declared")
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    values: Mapped[list["FeedNutrientValue"]] = relationship(
        back_populates="profile", lazy="selectin", cascade="all, delete-orphan"
    )


class FeedNutrientValue(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "feed_nutrient_value"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_nutrient_profile.id"), index=True
    )
    nutrient_code: Mapped[str] = mapped_column(String(30))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))

    profile: Mapped[FeedNutrientProfile] = relationship(back_populates="values")


# ------------------------------------------------------------- programs
class FeedingProgram(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A ration: what an eligible subject should receive. Applicability is
    in the version's rules, never in the program's name."""

    __tablename__ = "feeding_program"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_feeding_program_tenant_code"),)

    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    name_ar: Mapped[str | None] = mapped_column(String(200), nullable=True)
    category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    description: Mapped[str | None] = mapped_column(nullable=True)

    versions: Mapped[list["FeedingProgramVersion"]] = relationship(
        back_populates="program",
        lazy="selectin",
        order_by="FeedingProgramVersion.version_number",
        cascade="all, delete-orphan",
    )


class FeedingProgramVersion(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "feeding_program_version"
    __table_args__ = (UniqueConstraint("program_id", "version_no", name="uq_feeding_program_version"),)

    program_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feeding_program.id"), index=True
    )
    version_number: Mapped[int] = mapped_column("version_no", Integer, default=1)
    # draft | active | retired
    status: Mapped[str] = mapped_column(String(20), default="draft")
    feedings_per_day: Mapped[int] = mapped_column(Integer, default=2)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    program: Mapped[FeedingProgram] = relationship(back_populates="versions")
    components: Mapped[list["FeedingProgramComponent"]] = relationship(
        back_populates="program_version",
        lazy="selectin",
        order_by="FeedingProgramComponent.sort_order",
        cascade="all, delete-orphan",
    )
    rules: Mapped[list["FeedingProgramRule"]] = relationship(
        back_populates="program_version", lazy="selectin", cascade="all, delete-orphan"
    )


class FeedingProgramComponent(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """One line of the ration: this product, this much per head, this often."""

    __tablename__ = "feeding_program_component"

    version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feeding_program_version.id"), index=True
    )
    feed_product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("feed_product.id"))
    quantity_per_head: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    # per_day | per_feeding
    frequency: Mapped[str] = mapped_column(String(20), default="per_day")
    # morning | evening | null (every feeding)
    timing: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    program_version: Mapped[FeedingProgramVersion] = relationship(back_populates="components")


class FeedingProgramRule(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """Who this program is for (§4, §11). Every column is optional; an unset
    one matches anything. A range on a metric the subject does not have is
    a non-match with a warning, never a silent match."""

    __tablename__ = "feeding_program_rule"

    version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feeding_program_version.id"), index=True
    )
    species_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sex: Mapped[str | None] = mapped_column(String(1), nullable=True)
    life_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    management_profile: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # pregnant | open
    reproductive_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # lactating | dry
    lactation_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # milk_l_per_day | eggs_per_day …
    production_metric: Mapped[str | None] = mapped_column(String(40), nullable=True)
    production_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    production_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    age_min_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    age_max_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(String(300), nullable=True)

    program_version: Mapped[FeedingProgramVersion] = relationship(back_populates="rules")


# ----------------------------------------------------------- assignments
class FeedingAssignment(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """What a subject is actually on. `explicit` puts a subject on a program
    version; `supplement` adds a product on top of whatever program applies;
    `override` replaces one component's quantity for a while; `restriction`
    forbids a product (a vet's order). Inheritance from a group is computed
    at read time and never stored, so an individual override can never erase
    the group program underneath it (§9, §16)."""

    __tablename__ = "feeding_assignment"
    __table_args__ = (Index("ix_feeding_assignment_subject", "subject_type", "subject_id"),)

    # animal | group
    subject_type: Mapped[str] = mapped_column(String(10))
    subject_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    program_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feeding_program_version.id"), nullable=True
    )
    # explicit | supplement | override | restriction
    assignment_type: Mapped[str] = mapped_column(String(20), default="explicit")
    feed_product_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_product.id"), nullable=True
    )
    quantity_per_head: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # active | ended | superseded
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------- events
class FeedingEvent(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """What was actually put in front of a subject. Immutable once
    recorded: a mistake is reversed by a second event that points at the
    first, never by editing (§16)."""

    __tablename__ = "feeding_event"
    __table_args__ = (Index("ix_feeding_event_subject", "subject_type", "subject_id", "occurred_at"),)

    subject_type: Mapped[str] = mapped_column(String(10))
    subject_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    program_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feeding_program_version.id"), nullable=True
    )
    # The assignment in force when the feed was issued (§20: every issue of
    # a numbered mix identifies its feeding program / assignment).
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feeding_assignment.id"), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # offered | delivered | consumed_estimate | refusal
    event_type: Mapped[str] = mapped_column(String(20), default="offered")
    head_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)
    # recorded | reversed | reversal
    status: Mapped[str] = mapped_column(String(20), default="recorded")
    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    total_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    components: Mapped[list["FeedingEventComponent"]] = relationship(
        back_populates="event", lazy="selectin", cascade="all, delete-orphan"
    )


class FeedingEventComponent(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "feeding_event_component"

    event_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feeding_event.id"), index=True
    )
    feed_product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("feed_product.id"))
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_lot.id"), nullable=True, index=True
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_batch.id"), nullable=True
    )
    quantity_offered: Mapped[float] = mapped_column(Float)
    # Null means "not measured" — never zero (§16).
    quantity_consumed: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    event: Mapped[FeedingEvent] = relationship(back_populates="components")


# --------------------------------------------------------------- policy
class FeedUsagePolicy(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Who a feed may be used for (§24). Enforced when a formula component
    is added, a batch is completed, a program is activated and a feeding is
    recorded — not informational. A lot-level policy may only tighten the
    product's."""

    __tablename__ = "feed_usage_policy"

    feed_product_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_product.id"), nullable=True, index=True
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_lot.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    requires_approved_formula: Mapped[bool] = mapped_column(Boolean, default=False)
    cross_species_transfer_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)

    rules: Mapped[list["FeedUsagePolicyRule"]] = relationship(
        back_populates="policy", lazy="selectin", cascade="all, delete-orphan"
    )


class FeedUsagePolicyRule(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """`allow` lists who may use it (anyone else is blocked once any allow
    rule exists); `block` forbids explicitly; `limit` caps the inclusion
    rate. Dimensions unset on a rule match anything."""

    __tablename__ = "feed_usage_policy_rule"

    policy_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_usage_policy.id"), index=True
    )
    # allow | block | limit
    effect: Mapped[str] = mapped_column(String(10))
    species_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    management_profile: Mapped[str | None] = mapped_column(String(30), nullable=True)
    life_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reproductive_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    max_inclusion_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    policy: Mapped[FeedUsagePolicy] = relationship(back_populates="rules")


# ------------------------------------------------------------ allocation
class FeedAllocation(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Stock reserved for a purpose (§25). Reduces what is available to
    everyone else; consumed down as feedings for that purpose draw on it."""

    __tablename__ = "feed_allocation"

    feed_product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_product.id"), index=True
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_lot.id"), nullable=True
    )
    species_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    subject_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    feeding_program_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feeding_program.id"), nullable=True
    )
    location_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cost_centre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(300), nullable=True)
    allocated_quantity: Mapped[float] = mapped_column(Float)
    consumed_quantity: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    transferable: Mapped[bool] = mapped_column(Boolean, default=False)
    # active | released | exhausted
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ------------------------------------------------------- replenishment
class FeedReorderPolicy(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "feed_reorder_policy"
    __table_args__ = (
        UniqueConstraint("feed_product_id", "location_label", name="uq_feed_reorder_product_location"),
    )

    feed_product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_product.id"), index=True
    )
    location_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    minimum_stock: Mapped[float | None] = mapped_column(Float, nullable=True)
    reorder_point: Mapped[float | None] = mapped_column(Float, nullable=True)
    safety_stock: Mapped[float | None] = mapped_column(Float, nullable=True)
    preferred_reorder_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum_stock: Mapped[float | None] = mapped_column(Float, nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Days of cover below which a stockout risk is raised even when the
    # reorder point has not been crossed: lead time plus this margin.
    cover_margin_days: Mapped[int] = mapped_column(Integer, default=3)
    variance_threshold_pct: Mapped[float] = mapped_column(Float, default=5.0)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FeedDemandForecast(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """One forecast run for one product — kept so a reorder alert can show
    the demand it was raised on, and so forecasts can be compared with what
    actually happened."""

    __tablename__ = "feed_demand_forecast"

    feed_product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_product.id"), index=True
    )
    forecast_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    forecast_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    daily_demand: Mapped[float] = mapped_column(Float, default=0)
    forecast_quantity: Mapped[float] = mapped_column(Float, default=0)
    available_quantity: Mapped[float] = mapped_column(Float, default=0)
    days_of_cover: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_stockout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suggested_reorder_quantity: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    basis_json: Mapped[dict | None] = mapped_column(JSONB, default=dict, nullable=True)


# -------------------------------------------------------- reconciliation
class FeedReconciliation(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Book stock against physical stock for one product (or lot) over a
    period, every kilogram accounted for by category. An unexplained
    difference stays a variance until someone explains it (§27)."""

    __tablename__ = "feed_reconciliation"

    feed_product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_product.id"), index=True
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_lot.id"), nullable=True
    )
    period_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    opening_quantity: Mapped[float] = mapped_column(Float, default=0)
    received_quantity: Mapped[float] = mapped_column(Float, default=0)
    issued_to_batches: Mapped[float] = mapped_column(Float, default=0)
    issued_to_feeding: Mapped[float] = mapped_column(Float, default=0)
    other_issued_quantity: Mapped[float] = mapped_column(Float, default=0)
    waste_quantity: Mapped[float] = mapped_column(Float, default=0)
    returned_quantity: Mapped[float] = mapped_column(Float, default=0)
    adjustment_quantity: Mapped[float] = mapped_column(Float, default=0)
    expected_closing_quantity: Mapped[float] = mapped_column(Float, default=0)
    counted_closing_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    variance_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    variance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), default="kg")
    # open | reviewed | closed
    status: Mapped[str] = mapped_column(String(20), default="open")
    explanation: Mapped[str | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Tables in dependency order, for the migration and the test fixture's TRUNCATE.
FEED_TABLES: tuple[str, ...] = (
    "feed_product",
    "feed_lot",
    "feed_formula",
    "feed_formula_version",
    "feed_formula_component",
    "feed_batch",
    "feed_mix_sequence",
    "feed_batch_component",
    "feed_nutrient_profile",
    "feed_nutrient_value",
    "feeding_program",
    "feeding_program_version",
    "feeding_program_component",
    "feeding_program_rule",
    "feeding_assignment",
    "feeding_event",
    "feeding_event_component",
    "feed_usage_policy",
    "feed_usage_policy_rule",
    "feed_allocation",
    "feed_reorder_policy",
    "feed_demand_forecast",
    "feed_reconciliation",
)
