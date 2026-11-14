"""Reproduction and lactation (OrigamiFarmOS database/REPRODUCTION-SCHEMA.md,
PRODUCTION-SCHEMA.md §8) — farm-data-plane, RLS-protected tables.

Reproduction owns breeding, pregnancy, birth and incubation/hatch facts.
It stores no animal identity (that is `animal`), no production output
(milk is `milk_record`) and no diagnosis (that is health). The animal's
`pregnant` / `lactating` / due-date fields are a projection of the rows
here, refreshed by app/farmos/reproduction_service.py and never edited.

`lactation_episode` is a production state (PRODUCTION-SCHEMA §8). It lives
in this module because the birth that usually starts it is here, and the
two are written in one transaction; it is still read by production and
feed as their own context, never as a reproduction record.

Like the other farmos tables these carry no database foreign keys to
`animal` / `animal_group`: rows are tenant-scoped by RLS and the services
check ownership the way the rest of the module does.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin


class BreedingEvent(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A heat seen, a mating, an insemination or an embryo transfer.
    Pregnancy is never inferred from it (REPRODUCTION-SCHEMA core
    invariant): only a pregnancy check opens a pregnancy."""

    __tablename__ = "breeding_event"
    __table_args__ = (Index("ix_breeding_event_female", "female_animal_id", "occurred_at"),)

    female_animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    male_animal_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # A sire that is not on this farm: a hired ram, an AI bull's name.
    sire_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # HEAT_OBSERVED | NATURAL_MATING | ARTIFICIAL_INSEMINATION | EMBRYO_TRANSFER
    event_type: Mapped[str] = mapped_column(String(40))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    semen_batch_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    technician_user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # recorded | voided — a mistaken entry is voided with a reason, never deleted.
    status: Mapped[str] = mapped_column(String(20), default="recorded")
    void_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PregnancyEpisode(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """One pregnancy, from the check that confirmed it to its outcome. At
    most one active episode per animal, enforced by the service."""

    __tablename__ = "pregnancy_episode"
    __table_args__ = (Index("ix_pregnancy_episode_animal", "animal_id", "status"),)

    animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    originating_breeding_event_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    # active | ended
    status: Mapped[str] = mapped_column(String(20), default="active")
    estimated_conception_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # BIRTH | LOSS | ABORTION | NOT_PREGNANT | ANIMAL_EXITED
    outcome_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # assessment | registration | backfill — how the episode came to exist.
    source: Mapped[str] = mapped_column(String(20), default="assessment")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class PregnancyAssessment(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A pregnancy check. A NEGATIVE result may exist with no episode;
    POSITIVE opens one (or refines the active one); NEGATIVE on an active
    episode ends it."""

    __tablename__ = "pregnancy_assessment"
    __table_args__ = (Index("ix_pregnancy_assessment_animal", "animal_id", "assessed_at"),)

    pregnancy_episode_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # ULTRASOUND | PALPATION | BLOOD_TEST | OBSERVATION | REGISTRATION
    assessment_type: Mapped[str] = mapped_column(String(30))
    # POSITIVE | NEGATIVE | INCONCLUSIVE
    result_code: Mapped[str] = mapped_column(String(20))
    estimated_gestation_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    examiner_user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class BirthEvent(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A calving, lambing, kidding or foaling — one generic record, the
    species' own word on screen (terminology in the catalog)."""

    __tablename__ = "birth_event"
    __table_args__ = (
        Index("ix_birth_event_dam", "dam_animal_id", "occurred_at"),
        CheckConstraint(
            "offspring_count >= 0 AND live_count >= 0 AND stillborn_count >= 0",
            name="ck_birth_counts_non_negative",
        ),
        CheckConstraint("live_count + stillborn_count <= offspring_count", name="ck_birth_counts_add_up"),
    )

    dam_animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    pregnancy_episode_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    sire_animal_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    sire_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # NONE | EASY | ASSISTED | VETERINARY | CAESAREAN
    assistance_code: Mapped[str] = mapped_column(String(20), default="NONE")
    offspring_count: Mapped[int] = mapped_column(Integer)
    live_count: Mapped[int] = mapped_column(Integer)
    stillborn_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="recorded")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class BirthOffspring(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """One newborn of a birth. A live, individually tracked newborn links
    to the `animal` row registered with it (now, or later from this row);
    a stillborn one, or one counted into a flock, has none."""

    __tablename__ = "birth_offspring"
    __table_args__ = (UniqueConstraint("birth_event_id", "sequence_no", name="uq_birth_offspring_sequence"),)

    birth_event_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    offspring_animal_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    sex: Mapped[str | None] = mapped_column(String(5), nullable=True)
    birth_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    # LIVE | STILLBORN | DIED_AFTER_BIRTH
    outcome_code: Mapped[str] = mapped_column(String(20))
    destination_group_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class LactationEpisode(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A lactation (PRODUCTION-SCHEMA §8), from its start — usually a
    birth — to dry-off. `lactation_number` is the parity the feed
    baselines read."""

    __tablename__ = "lactation_episode"
    __table_args__ = (
        Index("ix_lactation_episode_animal", "animal_id", "status"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_lactation_range"),
    )

    animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    birth_event_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # active | ended
    status: Mapped[str] = mapped_column(String(20), default="active")
    lactation_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # DRY_OFF | NEW_LACTATION | ANIMAL_EXITED | PROFILE_CHANGED
    end_reason_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # birth | recorded | registration | backfill
    source: Mapped[str] = mapped_column(String(20), default="recorded")
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    ended_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class IncubationBatch(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Eggs set to hatch (REPRODUCTION-SCHEMA incubation_batch). Poultry
    reproduction runs through here — never through pregnancy."""

    __tablename__ = "incubation_batch"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_incubation_batch_code"),
        CheckConstraint("eggs_set > 0", name="ck_incubation_eggs_set_positive"),
    )

    # The species decides INCUBATION / HATCHING and the expected hatch
    # date; the source flock, when there is one, is where the eggs came from.
    species_code: Mapped[str] = mapped_column(String(30))
    source_group_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    code: Mapped[str] = mapped_column(String(60))
    eggs_set: Mapped[int] = mapped_column(Integer)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expected_hatch_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # incubating | hatched | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="incubating")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class HatchEvent(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """The hatch of one incubation batch: live chicks counted into the
    destination flock's ledger, the rest accounted for."""

    __tablename__ = "hatch_event"
    __table_args__ = (CheckConstraint("hatched_live >= 0", name="ck_hatch_live_non_negative"),)

    incubation_batch_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hatched_live: Mapped[int] = mapped_column(Integer)
    unhatched: Mapped[int | None] = mapped_column(Integer, nullable=True)
    culled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    destination_group_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
