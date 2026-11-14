"""Farm-data-plane (RLS-protected, tenant-scoped) tables for the generic
animal model's per-animal data: the identifiers an animal carries, the
groups a farm manages as one unit with their effective-dated memberships
and head-count ledger, the animal's effective-dated state history and
parentage (LIVESTOCK-SCHEMA §5–§10), and the care record (standing
conditions, dated weights). Field names match docs/FARMOS_API.md.

The *configuration* side — species, breeds, life stages, management
profiles, capabilities and their rules — is code-defined in
app/farmos/livestock_catalog.py, not stored here; see that module.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AnimalIdentifier(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """An identifier an individual animal carries (generic animal model
    §4). A cow may have a farm number, an ear tag and an RFID; a mare a
    name, a microchip and a passport. All are rows here, none a column on
    `animal`. Uniqueness is enforced in code per farm, type and issuing
    authority — a farm number is unique on one farm, an RFID everywhere.
    """

    __tablename__ = "animal_identifier"
    __table_args__ = (Index("ix_animal_identifier_lookup", "identifier_type", "identifier_value"),)

    animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    identifier_type: Mapped[str] = mapped_column(String(30))
    identifier_value: Mapped[str] = mapped_column(String(120))
    issuing_authority: Mapped[str | None] = mapped_column(String(120), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    # active | retired — a replaced ear tag is retired, never deleted, so
    # a record that names the old tag still resolves.
    status: Mapped[str] = mapped_column(String(20), default="active")
    notes: Mapped[str | None] = mapped_column(nullable=True)


class AnimalGroup(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A group managed as one unit — the generic model's `AnimalGroup`
    (§7). Poultry usually; sheep and goats sometimes. Production, feed and
    health can all attach here as well as to an individual animal."""

    __tablename__ = "animal_group"

    name: Mapped[str] = mapped_column(String(200))
    species: Mapped[str] = mapped_column(String(30))
    # flock | herd | batch | pen — what the farm calls it.
    group_type: Mapped[str] = mapped_column(String(20), default="flock")
    count: Mapped[int] = mapped_column(Integer, default=0)
    # female | male | mixed — a laying flock is female; the resolver will
    # not claim a mixed flock lays.
    sex_composition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    life_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    management_profile: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="healthy")
    location_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # MEMBERSHIP_DERIVED | QUANTITY_MANAGED | HYBRID (LIVESTOCK-SCHEMA §6):
    # whether `count` is the number of current members or the running total
    # of the group's quantity ledger (app/farmos/group_service.py).
    tracking_mode: Mapped[str] = mapped_column(String(30), default="QUANTITY_MANAGED")
    # PREGNANT | NOT_PREGNANT | IN_MILK | DRY — a group whose members follow
    # the herd records (a cohort filled and kept in step by the service).
    membership_rule: Mapped[str | None] = mapped_column(String(30), nullable=True)


class AnimalStateHistory(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Effective-dated master state (LIVESTOCK-SCHEMA §5, §14). The current
    value stays on `animal` for operational reads; this is the history a
    change must write first — a status, stage, profile or location is never
    changed on the animal alone. Ranges for one animal and state type never
    overlap: the service closes the open one before opening the next."""

    __tablename__ = "animal_state_history"
    __table_args__ = (
        Index("ix_animal_state_history_animal", "animal_id", "state_type", "effective_from"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="ck_animal_state_history_range"
        ),
    )

    animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    # STATUS (lifecycle) | LIFE_STAGE | MANAGEMENT_PROFILE | LOCATION
    state_type: Mapped[str] = mapped_column(String(40))
    value_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    value_reference_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class AnimalGroupMembership(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """An animal's time in a group (LIVESTOCK-SCHEMA §7). Closed, never
    deleted: leaving a herd sets `valid_to`, so a question about who was in
    it last spring still has an answer."""

    __tablename__ = "animal_group_membership"
    __table_args__ = (
        Index("ix_animal_group_membership_group", "animal_group_id", "valid_from"),
        Index("ix_animal_group_membership_animal", "animal_id", "valid_from"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name="ck_animal_group_membership_range"
        ),
    )

    animal_group_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    end_reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    ended_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class AnimalGroupQuantityEvent(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """The head-count ledger of a quantity-managed or hybrid group
    (LIVESTOCK-SCHEMA §9). `animal_group.count` is the latest
    `resulting_quantity` — a projection of these rows, never typed over."""

    __tablename__ = "animal_group_quantity_event"
    __table_args__ = (
        Index("ix_animal_group_quantity_event_group", "animal_group_id", "occurred_at"),
        CheckConstraint("resulting_quantity >= 0", name="ck_animal_group_quantity_non_negative"),
    )

    animal_group_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    # INITIAL_COUNT | ADDITION | REMOVAL | BIRTH | HATCH | DEATH | SALE |
    # PURCHASE | TRANSFER_IN | TRANSFER_OUT | CULL | SLAUGHTER | COUNT_ADJUSTMENT
    event_type: Mapped[str] = mapped_column(String(30))
    quantity_delta: Mapped[int] = mapped_column(Integer)
    resulting_quantity: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AnimalRelationship(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Parentage and other animal-to-animal links (LIVESTOCK-SCHEMA §10).
    Written by a recorded birth, or entered for an animal bought in with
    known parents; either way it names its evidence."""

    __tablename__ = "animal_relationship"
    __table_args__ = (
        Index("ix_animal_relationship_source", "source_animal_id", "relationship_type"),
        Index("ix_animal_relationship_target", "target_animal_id", "relationship_type"),
        CheckConstraint("source_animal_id <> target_animal_id", name="ck_animal_relationship_not_self"),
    )

    # DAM_OF | SIRE_OF | FOSTER_DAM_OF — source is the parent, target the offspring.
    source_animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    target_animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    relationship_type: Mapped[str] = mapped_column(String(30))
    # recorded (a birth recorded here) | stated (entered by hand)
    confidence_code: Mapped[str] = mapped_column(String(30), default="recorded")
    evidence_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    evidence_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class AnimalHealthCondition(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A standing clinical fact on the animal: an allergy to an active
    ingredient, an adverse reaction to a product, a chronic condition, a
    contraindication the veterinarian wrote down. Recorded by a diagnostic
    role; ended, never deleted."""

    __tablename__ = "animal_health_condition"
    __table_args__ = (Index("ix_animal_health_condition_animal_active", "animal_id", "active"),)

    animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    # allergy | adverse_reaction | chronic_condition | contraindication
    condition_type: Mapped[str] = mapped_column(String(40))
    # What it refers to: an active-ingredient code, a medicine product id,
    # a diagnosis code — matched by the emergency eligibility rules.
    code: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(200))
    severity: Mapped[str | None] = mapped_column(String(30), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)


class AnimalWeightRecord(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A dated weight with its method. `animal.weight_kg` is the latest of
    these, never edited on its own."""

    __tablename__ = "animal_weight_record"
    __table_args__ = (Index("ix_animal_weight_record_animal_measured", "animal_id", "measured_at"),)

    animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    weight_kg: Mapped[float] = mapped_column(Numeric(10, 2))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # scale | tape | visual_estimate | point_of_care | legacy
    method: Mapped[str] = mapped_column(String(30), default="scale")
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # manual | animal_update | emergency_run_step | migration
    source_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)
