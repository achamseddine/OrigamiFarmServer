"""Request bodies for the livestock history, group membership and
reproduction endpoints (OrigamiFarmOS database/LIVESTOCK-SCHEMA.md,
REPRODUCTION-SCHEMA.md, PRODUCTION-SCHEMA.md §8). Codes are upper-cased on
the way in so a tablet or a script may send `heat_observed` or
`HEAT_OBSERVED` alike. Response shapes are plain dicts built by the
services, as in the reference backend."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.farmos.schemas import IdentifierIn


def _upper(v: str | None) -> str | None:
    return v.strip().upper() if isinstance(v, str) and v.strip() else v


class AnimalExitIn(BaseModel):
    # sold | dead | culled | slaughtered | transferred
    exit_type: str
    occurred_at: datetime | None = None
    reason: str | None = Field(None, max_length=300)
    notes: str | None = None

    @field_validator("exit_type")
    @classmethod
    def lower(cls, v: str) -> str:
        return v.strip().lower()


class AnimalRestoreIn(BaseModel):
    reason: str | None = Field(None, max_length=200)


class ParentIn(BaseModel):
    parent_animal_id: str
    # DAM_OF | SIRE_OF — the parent's role
    relationship_type: str
    evidence: str | None = Field(None, max_length=200)

    @field_validator("relationship_type")
    @classmethod
    def _codes_upper(cls, v: str) -> str:
        return _upper(v) or v


class MembershipIn(BaseModel):
    animal_id: str
    valid_from: datetime | None = None
    reason: str | None = Field(None, max_length=80)


class MembershipEndIn(BaseModel):
    valid_to: datetime | None = None
    reason: str | None = Field(None, max_length=80)


class QuantityEventIn(BaseModel):
    event_type: str
    quantity_delta: int
    occurred_at: datetime | None = None
    reason: str | None = Field(None, max_length=200)
    # The same change to the same group within half an hour is refused as a
    # possible duplicate unless confirmed (two people counting the same loss).
    confirm_duplicate: bool = False

    @field_validator("event_type")
    @classmethod
    def _codes_upper(cls, v: str) -> str:
        return _upper(v) or v


class BreedingIn(BaseModel):
    event_type: str
    occurred_at: datetime | None = None
    male_animal_id: str | None = None
    sire_reference: str | None = Field(None, max_length=120)
    semen_batch_reference: str | None = Field(None, max_length=120)
    technician_user_id: str | None = None
    notes: str | None = None
    # The same service of the same female on the same day is refused as a
    # possible duplicate unless confirmed (a second insemination in one heat).
    confirm_duplicate: bool = False

    @field_validator("event_type")
    @classmethod
    def _codes_upper(cls, v: str) -> str:
        return _upper(v) or v


class BreedingVoidIn(BaseModel):
    reason: str = Field(..., max_length=200)


class PregnancyCheckIn(BaseModel):
    assessment_type: str = "ULTRASOUND"
    result_code: str
    assessed_at: datetime | None = None
    estimated_gestation_days: int | None = Field(None, ge=0)
    expected_due_date: datetime | None = None
    examiner_user_id: str | None = None
    notes: str | None = None
    confirm_duplicate: bool = False

    @field_validator("assessment_type", "result_code")
    @classmethod
    def _codes_upper(cls, v: str) -> str:
        return _upper(v) or v


class PregnancyLossIn(BaseModel):
    outcome_code: str = "LOSS"
    occurred_at: datetime | None = None
    notes: str | None = None

    @field_validator("outcome_code")
    @classmethod
    def _codes_upper(cls, v: str) -> str:
        return _upper(v) or v


class NewbornIn(BaseModel):
    """One newborn. `register_now: true` makes it an animal now (name and the
    identifiers its species requires); a live newborn not registered can be
    counted into a group, or registered later from the birth."""

    outcome_code: str = "LIVE"
    sex: str | None = None
    birth_weight_kg: float | None = Field(None, ge=0)
    register_now: bool = False
    name: str | None = Field(None, max_length=200)
    tag: str | None = None
    identifiers: list[IdentifierIn] = []
    management_profile: str | None = None
    group_name: str | None = None
    destination_group_id: str | None = None
    notes: str | None = None

    @field_validator("outcome_code")
    @classmethod
    def _codes_upper(cls, v: str) -> str:
        return _upper(v) or v


class BirthIn(BaseModel):
    occurred_at: datetime | None = None
    assistance_code: str = "NONE"
    sire_animal_id: str | None = None
    sire_reference: str | None = Field(None, max_length=120)
    start_lactation: bool = True
    notes: str | None = None
    offspring: list[NewbornIn]
    # A second birth of the same dam within 30 days is refused unless
    # confirmed — twins are one birth with two newborns.
    confirm_duplicate: bool = False

    @field_validator("assistance_code")
    @classmethod
    def _codes_upper(cls, v: str) -> str:
        return _upper(v) or v


class OffspringRegisterIn(BaseModel):
    name: str | None = Field(None, max_length=200)
    sex: str | None = None
    tag: str | None = None
    identifiers: list[IdentifierIn] = []
    management_profile: str | None = None
    group_name: str | None = None
    notes: str | None = None


class LactationStartIn(BaseModel):
    started_at: datetime | None = None
    lactation_number: int | None = Field(None, ge=1)


class DryOffIn(BaseModel):
    ended_at: datetime | None = None


class LactationCorrectIn(BaseModel):
    """The current lactation's calving / start-of-milking date, corrected."""

    started_at: datetime
    reason: str | None = Field(None, max_length=300)


class IncubationIn(BaseModel):
    species_code: str
    eggs_set: int = Field(..., gt=0)
    set_at: datetime | None = None
    source_group_id: str | None = None
    expected_hatch_date: datetime | None = None
    code: str | None = Field(None, max_length=60)
    notes: str | None = None


class HatchIn(BaseModel):
    hatched_live: int = Field(..., ge=0)
    unhatched: int | None = Field(None, ge=0)
    culled: int | None = Field(None, ge=0)
    occurred_at: datetime | None = None
    destination_group_id: str | None = None
    notes: str | None = None


class IncubationCancelIn(BaseModel):
    reason: str = Field(..., max_length=300)
