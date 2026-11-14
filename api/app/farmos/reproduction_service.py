"""Reproduction and lactation (OrigamiFarmOS database/REPRODUCTION-SCHEMA.md,
PRODUCTION-SCHEMA.md §8, behavioural model §3).

Mammals:  breeding → pregnancy check → pregnancy → birth → offspring
Poultry:  incubation → hatch → chicks into a flock

Invariants this module owns:
- every action is checked against the animal's resolved capabilities
  (BREEDING, PREGNANCY, LIVE_BIRTH, LACTATION, INCUBATION, HATCHING); a hen
  is never offered a pregnancy, a bull never a birth;
- pregnancy is never inferred from a mating — only a positive check opens
  a pregnancy episode, and at most one is active per animal;
- a birth closes the pregnancy, registers individually tracked newborns
  through the normal registration path (identifiers, capabilities) or
  counts them into a flock, links parentage, and starts the dam's next
  lactation where she has one;
- `animal.pregnant`, `pregnancy_days`, `expected_due_date`, `lactating`,
  `lactation_cycle`, `lactation_started_at` and `dried_off_at` are a
  projection refreshed here from the episodes ([refresh_projection]); no
  edit sets them.

Every change that moves what the feeding resolver reads raises a feeding
review (feed architecture §10) and re-reads feed performance (§12) — a
review, never a silent ration change. Audit entries are the routers'.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.farmos import capability_service, farm_settings_service
from app.farmos import livestock_catalog as catalog
from app.farmos import reproduction_models as rm
from app.farmos.capability_service import CapabilitySet
from app.farmos.deps import AccessContext
from app.farmos.livestock_catalog import Cap
from app.farmos.livestock_models import AnimalGroup, AnimalRelationship
from app.farmos.livestock_service import ensure_utc, now_utc
from app.farmos.livestock_state_service import LivestockError, ensure_on_farm
from app.farmos.schemas import IdentifierIn
from app.tenant_api.models import Animal
from app.tenants.models import TenantMembership

BREEDING_TYPES = ("HEAT_OBSERVED", "NATURAL_MATING", "ARTIFICIAL_INSEMINATION", "EMBRYO_TRANSFER")
# The breedings that can start a pregnancy (a heat is a sign, not a service).
SERVICE_TYPES = ("NATURAL_MATING", "ARTIFICIAL_INSEMINATION", "EMBRYO_TRANSFER")
ASSESSMENT_TYPES = ("ULTRASOUND", "PALPATION", "BLOOD_TEST", "OBSERVATION")
RESULTS = ("POSITIVE", "NEGATIVE", "INCONCLUSIVE")
LOSS_OUTCOMES = ("LOSS", "ABORTION")
ASSISTANCE_CODES = ("NONE", "EASY", "ASSISTED", "VETERINARY", "CAESAREAN")
OFFSPRING_OUTCOMES = ("LIVE", "STILLBORN", "DIED_AFTER_BIRTH")
MAX_OFFSPRING = 12
# A served female with no check this long after is "awaiting a pregnancy
# check" on the overview — early enough for an ultrasound in every species
# the farm keeps.
CHECK_DUE_AFTER_DAYS = 30
DUE_SOON_DAYS = 21
# Fallback for a pregnancy window when a species has no gestation length.
_DEFAULT_WINDOW_DAYS = 400
# The farm setting `dry_off_days_before_birth` when a farm has none.
DEFAULT_DRY_OFF_DAYS = farm_settings_service.DEFAULTS["dry_off_days_before_birth"]
# Defaults of the farm's calendar settings.
DEFAULT_CALENDAR = {
    k: v for k, v in farm_settings_service.DEFAULTS.items() if k != "dry_off_days_before_birth"
}
# A return to heat is watched for this many days either side of one cycle.
HEAT_WATCH_MARGIN_DAYS = 3


# ----------------------------------------------------------------- helpers
def _caps(animal: Animal) -> CapabilitySet:
    return capability_service.resolve_for_animal(animal)


def _require(animal: Animal, cap: str) -> CapabilitySet:
    cap_set = _caps(animal)
    if not cap_set.has(cap):
        raise LivestockError(capability_service.describe_missing(cap_set, cap, animal.name))
    return cap_set


def _female(animal: Animal, what: str) -> None:
    if animal.sex != "F":
        raise LivestockError(f"{animal.name} is not recorded as female; {what} is recorded on the female.")


def _utc(value: datetime | None) -> datetime:
    at = ensure_utc(value)
    return at if at is not None else now_utc()


def _dt(value: datetime | None) -> datetime | None:
    return ensure_utc(value) if value is not None else None


def _when(at: datetime | None, what: str) -> datetime:
    at = _utc(at)
    if at > now_utc() + timedelta(minutes=5):
        raise LivestockError(f"{what} cannot be dated in the future.")
    return at


def _species(code: str) -> dict[str, Any]:
    return capability_service.get_species(code)


def _species_label(code: str) -> str:
    return code.replace("_", " ")


def _gestation_days(animal: Animal) -> int | None:
    days = _species(animal.species).get("gestation_days")
    return int(days) if days else None


def _due(animal: Animal, conceived: datetime | None) -> datetime | None:
    days = _gestation_days(animal)
    return conceived + timedelta(days=days) if conceived is not None and days else None


def _terminology(species_code: str) -> dict[str, Any]:
    return dict(_species(species_code).get("terminology_json") or {})


def _parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def active_pregnancy(db: Session, animal_id: uuid.UUID) -> rm.PregnancyEpisode | None:
    db.flush()
    return db.scalar(
        select(rm.PregnancyEpisode)
        .where(
            rm.PregnancyEpisode.animal_id == animal_id,
            rm.PregnancyEpisode.status == "active",
            rm.PregnancyEpisode.deleted_at.is_(None),
        )
        .order_by(rm.PregnancyEpisode.created_at.desc())
        .limit(1)
    )


def active_lactation(db: Session, animal_id: uuid.UUID) -> rm.LactationEpisode | None:
    db.flush()
    return db.scalar(
        select(rm.LactationEpisode)
        .where(
            rm.LactationEpisode.animal_id == animal_id,
            rm.LactationEpisode.status == "active",
            rm.LactationEpisode.deleted_at.is_(None),
        )
        .order_by(rm.LactationEpisode.started_at.desc())
        .limit(1)
    )


def _latest_lactation(db: Session, animal_id: uuid.UUID) -> rm.LactationEpisode | None:
    db.flush()
    return db.scalar(
        select(rm.LactationEpisode)
        .where(rm.LactationEpisode.animal_id == animal_id, rm.LactationEpisode.deleted_at.is_(None))
        .order_by(rm.LactationEpisode.started_at.desc())
        .limit(1)
    )


def _same_farm_animal(db: Session, animal_id: str | uuid.UUID | None, label: str) -> Animal | None:
    """Another animal of this farm named in a request. RLS already hides
    other tenants' rows, so a stranger's id and a missing one read alike."""
    if not animal_id:
        return None
    pk = _parse_uuid(animal_id)
    other = db.get(Animal, pk) if pk is not None else None
    if other is None or other.deleted_at is not None:
        raise LivestockError(f"{label} not found", 404)
    return other


def _same_farm_group(db: Session, group_id: str | uuid.UUID | None, label: str) -> AnimalGroup | None:
    if not group_id:
        return None
    pk = _parse_uuid(group_id)
    group = db.get(AnimalGroup, pk) if pk is not None else None
    if group is None or group.deleted_at is not None:
        raise LivestockError(f"{label} not found", 404)
    return group


def ensure_farm_user(control_db: Session, tenant_id: uuid.UUID, user_id: str | None, label: str) -> None:
    """A technician or examiner named on a record must be someone on this
    farm — a member of the tenant (control plane)."""
    if not user_id:
        return
    pk = _parse_uuid(user_id)
    member = (
        control_db.scalar(
            select(TenantMembership.id).where(
                TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == pk
            )
        )
        if pk is not None
        else None
    )
    if member is None:
        raise LivestockError(f"The {label} must be someone on this farm.")


def _validate_sire(female: Animal, sire: Animal) -> None:
    if sire.id == female.id:
        raise LivestockError("An animal cannot be its own sire.")
    if sire.species != female.species:
        raise LivestockError(
            f"{sire.name} is a {_species_label(sire.species)}, "
            f"{female.name} a {_species_label(female.species)}."
        )
    if sire.sex != "M":
        raise LivestockError(f"{sire.name} is not recorded as male.")
    if not _caps(sire).has(Cap.BREEDING):
        raise LivestockError(capability_service.describe_missing(_caps(sire), Cap.BREEDING, sire.name))


def days_between(start: datetime, end: datetime) -> int:
    """Calendar days, so "due in 9 days" does not read 8 by the afternoon."""
    return (_utc(end).date() - _utc(start).date()).days


def days_pregnant(episode: rm.PregnancyEpisode | None, at: datetime | None = None) -> int | None:
    if episode is None or episode.estimated_conception_at is None:
        return None
    return max(0, days_between(episode.estimated_conception_at, at or now_utc()))


def _hooks(db: Session, access: AccessContext, animal: Animal, changed: set[str], *, trigger: str) -> None:
    from app.farmos import feed_performance_service, feeding_program_service, group_service

    if changed:
        feeding_program_service.review_animal_if_relevant(db, access, animal, changed)
        # Pregnant / open / in-milk / dry groups follow the records.
        group_service.sync_rule_groups(db, access, animal)
    feed_performance_service.on_context_changed(db, access, "animal", animal, trigger=trigger)


def _touch(animal: Animal, access: AccessContext) -> None:
    animal.version += 1
    animal.last_modified_by = access.membership_id


# ----------------------------------------------------------------- dry-off
def dry_off_days(db: Session, tenant_id: uuid.UUID) -> int:
    """The farm's setting: how many days before an expected birth a
    lactating animal stops being milked."""
    return farm_settings_service.calendar(db, tenant_id)["dry_off_days_before_birth"]


def dry_off_plan(
    expected_due_date: datetime | None, lactating: bool, days_before: int, at: datetime | None = None
) -> dict[str, Any]:
    """When milking should stop ahead of the expected birth. A plan, never
    a record: the dry-off itself is recorded when it happens."""
    if expected_due_date is None:
        return {"dry_off_from": None, "days_to_dry_off": None, "dry_off_due": False}
    start = _utc(expected_due_date) - timedelta(days=days_before)
    days = days_between(at or now_utc(), start)
    return {"dry_off_from": start, "days_to_dry_off": days, "dry_off_due": bool(lactating) and days <= 0}


# ----------------------------------------------------------------- calendar
def farm_calendar(db: Session, tenant_id: uuid.UUID) -> dict[str, int]:
    return farm_settings_service.calendar(db, tenant_id)


def calendar_plan(
    *,
    calving_at: datetime | None,
    lactating: bool,
    pregnant: bool,
    can_breed: bool,
    last_service_at: datetime | None,
    last_check_at: datetime | None,
    calendar: dict[str, int],
    at: datetime | None = None,
) -> dict[str, Any]:
    """The working calendar after a birth (Vision: "when should I watch her
    peak, when should she be inseminated"): days in milk, the milk peak
    window, the end of the voluntary waiting period, and — after a service
    not yet confirmed — the days to watch for a return to heat. A plan from
    the farm's settings and the records; it records nothing."""
    at = at or now_utc()
    out: dict[str, Any] = {
        "calving_at": None,
        "days_in_milk": None,
        "milk_peak_from": None,
        "milk_peak_to": None,
        "in_milk_peak": False,
        "breeding_from": None,
        "ready_for_breeding": False,
        "heat_watch_from": None,
        "heat_watch_to": None,
        "heat_watch_now": False,
    }
    if calving_at is not None:
        calved = _utc(calving_at)
        dim = max(0, days_between(calved, at))
        out.update(
            calving_at=calved,
            days_in_milk=dim if lactating else None,
            milk_peak_from=calved + timedelta(days=calendar["milk_peak_start_days"]),
            milk_peak_to=calved + timedelta(days=calendar["milk_peak_end_days"]),
            in_milk_peak=lactating
            and calendar["milk_peak_start_days"] <= dim <= calendar["milk_peak_end_days"],
        )
        if can_breed and not pregnant:
            breeding_from = calved + timedelta(days=calendar["breeding_wait_days"])
            served_since = last_service_at is not None and _utc(last_service_at) >= calved
            out.update(
                breeding_from=breeding_from,
                ready_for_breeding=dim >= calendar["breeding_wait_days"] and not served_since,
            )
    if can_breed and not pregnant and last_service_at is not None:
        served = _utc(last_service_at)
        confirmed_after = last_check_at is not None and _utc(last_check_at) >= served
        if not confirmed_after:
            cycle = calendar["heat_cycle_days"]
            start = served + timedelta(days=cycle - HEAT_WATCH_MARGIN_DAYS)
            end = served + timedelta(days=cycle + HEAT_WATCH_MARGIN_DAYS)
            out.update(
                heat_watch_from=start,
                heat_watch_to=end,
                heat_watch_now=start.date() <= at.date() <= end.date(),
            )
    return out


def correct_lactation_start(
    db: Session, access: AccessContext, animal: Animal, *, started_at: datetime
) -> tuple[rm.LactationEpisode, datetime | None]:
    """The calving / start-of-milking date of the current lactation, corrected
    (it was entered when milking was recorded, before the date was known).
    Returns the row and the old date for the router's audit entry; a
    lactation that a recorded birth started is dated by that birth."""
    ensure_on_farm(animal, "a lactation correction")
    row = active_lactation(db, animal.id)
    if row is None:
        raise LivestockError(f"{animal.name} is not in milk.", 404)
    if row.birth_event_id:
        raise LivestockError(
            f"{animal.name}'s lactation started with a recorded birth; correct that birth's date instead.",
            409,
        )
    new_at = _when(started_at, "A calving")
    previous_end = db.scalar(
        select(func.max(rm.LactationEpisode.ended_at)).where(
            rm.LactationEpisode.animal_id == animal.id, rm.LactationEpisode.id != row.id
        )
    )
    if previous_end is not None and new_at < _utc(previous_end):
        raise LivestockError(
            f"{animal.name} was dried off on {_utc(previous_end).date().isoformat()}; "
            "this lactation cannot start before that."
        )
    before = _dt(row.started_at)
    row.started_at = new_at
    row.version += 1
    row.last_modified_by = access.membership_id
    refresh_projection(db, animal)
    _touch(animal, access)
    _hooks(db, access, animal, set(), trigger="lifecycle_changed")
    return row, before


# ----------------------------------------------------------------- projection
def refresh_projection(db: Session, animal: Animal) -> set[str]:
    """Re-derives the animal's reproduction and lactation fields from the
    episodes. Returns the names of the fields whose value changed."""
    before = {
        k: getattr(animal, k) for k in ("pregnant", "lactating", "expected_due_date", "lactation_cycle")
    }
    pregnancy = active_pregnancy(db, animal.id)
    animal.pregnant = pregnancy is not None
    animal.pregnancy_started_at = pregnancy.estimated_conception_at if pregnancy else None
    animal.expected_due_date = pregnancy.expected_due_date if pregnancy else None
    animal.pregnancy_days = days_pregnant(pregnancy)
    lactation = active_lactation(db, animal.id)
    latest = lactation or _latest_lactation(db, animal.id)
    animal.lactating = lactation is not None
    if latest is not None:
        animal.lactation_started_at = latest.started_at
        if latest.lactation_number is not None:
            animal.lactation_cycle = latest.lactation_number
        if latest.ended_at is not None:
            animal.dried_off_at = latest.ended_at
    return {k for k, v in before.items() if getattr(animal, k) != v}


def open_registration_state(
    db: Session,
    animal: Animal,
    *,
    pregnant: bool,
    pregnancy_days: int | None,
    lactating: bool,
    lactation_cycle: int | None,
    user_id: uuid.UUID,
    at: datetime | None = None,
    breeding: rm.BreedingEvent | None = None,
    lactation_started_at: datetime | None = None,
) -> None:
    """An animal registered pregnant or in milk — bought in, or already on
    the farm before the app — arrives with the episode that says so, sourced
    "registration", rather than with a bare flag. With the insemination /
    mating it came from ([breeding], recorded first), the pregnancy is dated
    from that service rather than from an estimate."""
    at = _utc(at)
    if lactation_cycle is not None and lactation_cycle < 0:
        raise LivestockError("The lactation number cannot be negative.")
    animal.lactation_cycle = lactation_cycle
    if pregnant:
        gestation = _gestation_days(animal)
        if breeding is not None:
            pregnancy_days = days_between(breeding.occurred_at, at)
        if pregnancy_days is not None and (
            pregnancy_days < 0 or (gestation and pregnancy_days > gestation + 30)
        ):
            raise LivestockError(
                f"{pregnancy_days} days is not a {_species_label(animal.species)} pregnancy"
                + (f" (about {gestation} days)." if gestation else ".")
                + (" Check the insemination date." if breeding is not None else "")
            )
        if breeding is not None:
            conceived: datetime | None = _utc(breeding.occurred_at)
        else:
            conceived = at - timedelta(days=pregnancy_days) if pregnancy_days is not None else None
        episode = rm.PregnancyEpisode(
            tenant_id=animal.tenant_id,
            animal_id=animal.id,
            status="active",
            estimated_conception_at=conceived,
            originating_breeding_event_id=breeding.id if breeding is not None else None,
            expected_due_date=_due(animal, conceived),
            source="registration",
            created_by=user_id,
        )
        db.add(episode)
        db.flush()
        db.add(
            rm.PregnancyAssessment(
                tenant_id=animal.tenant_id,
                pregnancy_episode_id=episode.id,
                animal_id=animal.id,
                assessed_at=at,
                assessment_type="REGISTRATION",
                result_code="POSITIVE",
                estimated_gestation_days=pregnancy_days,
                expected_due_date=episode.expected_due_date,
                recorded_at=now_utc(),
                recorded_by=user_id,
                notes="Registered pregnant",
            )
        )
    if lactating:
        # The calving that started this lactation, when the farmer knows it —
        # days in milk, the peak and the breeding wait are reckoned from it.
        started = _when(lactation_started_at, "A calving") if lactation_started_at else at
        db.add(
            rm.LactationEpisode(
                tenant_id=animal.tenant_id,
                animal_id=animal.id,
                started_at=started,
                status="active",
                lactation_number=lactation_cycle,
                source="registration",
                created_by=user_id,
            )
        )
    refresh_projection(db, animal)


# ----------------------------------------------------------------- breeding
def record_breeding(
    db: Session,
    access: AccessContext,
    female: Animal,
    *,
    event_type: str,
    at: datetime | None = None,
    male_animal_id: str | None = None,
    sire_reference: str | None = None,
    semen_batch_reference: str | None = None,
    technician_user_id: str | None = None,
    notes: str | None = None,
) -> rm.BreedingEvent:
    if event_type not in BREEDING_TYPES:
        raise LivestockError(f"event_type must be one of {list(BREEDING_TYPES)}")
    ensure_on_farm(female, "breeding")
    _female(female, "a breeding")
    _require(female, Cap.BREEDING)
    at = _when(at, "A breeding")
    male = _same_farm_animal(db, male_animal_id, "Sire")
    if event_type == "HEAT_OBSERVED" and (male or sire_reference or semen_batch_reference):
        raise LivestockError(
            "A heat is a sign seen on the female; "
            "record the mating or insemination that follows it separately."
        )
    if male is not None:
        ensure_on_farm(male, "a mating")
        _validate_sire(female, male)
    pregnancy = active_pregnancy(db, female.id)
    if pregnancy is not None:
        due = _dt(pregnancy.expected_due_date)
        raise LivestockError(
            f"{female.name} is recorded pregnant"
            + (f" (due {due.date().isoformat()})" if due else "")
            + ". Record a negative pregnancy check or a pregnancy loss first.",
            409,
        )
    row = rm.BreedingEvent(
        tenant_id=female.tenant_id,
        last_modified_by=access.membership_id,
        female_animal_id=female.id,
        male_animal_id=male.id if male else None,
        sire_reference=(sire_reference or "").strip() or None,
        event_type=event_type,
        occurred_at=at,
        semen_batch_reference=(semen_batch_reference or "").strip() or None,
        technician_user_id=_parse_uuid(technician_user_id),
        status="recorded",
        notes=notes,
        recorded_at=now_utc(),
        recorded_by=access.user_id,
    )
    db.add(row)
    db.flush()
    return row


def void_breeding(
    db: Session, access: AccessContext, breeding: rm.BreedingEvent, *, reason: str
) -> rm.BreedingEvent:
    """A mistaken entry is voided with its reason, never deleted. A
    breeding a pregnancy already rests on stays."""
    if breeding.status == "voided":
        return breeding
    if not reason or not reason.strip():
        raise LivestockError("Say why the breeding record is being voided.")
    used = db.scalar(
        select(rm.PregnancyEpisode.id).where(rm.PregnancyEpisode.originating_breeding_event_id == breeding.id)
    )
    if used:
        raise LivestockError(
            "A pregnancy is dated from this breeding; correct the pregnancy check instead.", 409
        )
    breeding.status = "voided"
    breeding.void_reason = reason.strip()
    breeding.version += 1
    breeding.last_modified_by = access.membership_id
    return breeding


def _likely_breeding(db: Session, animal: Animal, before: datetime) -> rm.BreedingEvent | None:
    """The service a confirmed pregnancy most plausibly comes from: the
    latest mating / insemination before the check, inside one gestation."""
    window = _gestation_days(animal) or _DEFAULT_WINDOW_DAYS
    return db.scalar(
        select(rm.BreedingEvent)
        .where(
            rm.BreedingEvent.female_animal_id == animal.id,
            rm.BreedingEvent.status == "recorded",
            rm.BreedingEvent.event_type.in_(SERVICE_TYPES),
            rm.BreedingEvent.occurred_at <= before,
            rm.BreedingEvent.occurred_at >= before - timedelta(days=window + 30),
        )
        .order_by(rm.BreedingEvent.occurred_at.desc())
        .limit(1)
    )


# ----------------------------------------------------------------- pregnancy
def _end_episode(episode: rm.PregnancyEpisode, at: datetime, outcome: str) -> None:
    episode.status = "ended"
    episode.ended_at = at
    episode.outcome_code = outcome
    episode.version += 1


def record_assessment(
    db: Session,
    access: AccessContext,
    animal: Animal,
    *,
    assessment_type: str,
    result_code: str,
    at: datetime | None = None,
    estimated_gestation_days: int | None = None,
    expected_due_date: datetime | None = None,
    examiner_user_id: str | None = None,
    notes: str | None = None,
) -> tuple[rm.PregnancyAssessment, rm.PregnancyEpisode | None]:
    """A pregnancy check. POSITIVE opens the pregnancy (dated from the
    gestation the examiner gives, else from the likely service) or refines
    the open one; NEGATIVE ends an open one; INCONCLUSIVE changes nothing."""
    if assessment_type not in ASSESSMENT_TYPES:
        raise LivestockError(f"assessment_type must be one of {list(ASSESSMENT_TYPES)}")
    if result_code not in RESULTS:
        raise LivestockError(f"result_code must be one of {list(RESULTS)}")
    ensure_on_farm(animal, "a pregnancy check")
    _female(animal, "a pregnancy check")
    _require(animal, Cap.PREGNANCY)
    at = _when(at, "A pregnancy check")
    gestation = _gestation_days(animal)
    if estimated_gestation_days is not None and (
        estimated_gestation_days < 0 or (gestation and estimated_gestation_days > gestation + 30)
    ):
        raise LivestockError(
            f"{estimated_gestation_days} days is not a {_species_label(animal.species)} pregnancy"
            + (f" (about {gestation} days)." if gestation else ".")
        )
    episode = active_pregnancy(db, animal.id)
    if result_code == "POSITIVE":
        breeding = _likely_breeding(db, animal, at)
        if estimated_gestation_days is not None:
            conceived: datetime | None = at - timedelta(days=estimated_gestation_days)
        else:
            conceived = _dt(breeding.occurred_at) if breeding else None
        due = _dt(expected_due_date) if expected_due_date else _due(animal, conceived)
        if episode is None:
            episode = rm.PregnancyEpisode(
                tenant_id=animal.tenant_id,
                animal_id=animal.id,
                status="active",
                originating_breeding_event_id=breeding.id if breeding else None,
                estimated_conception_at=conceived,
                expected_due_date=due,
                source="assessment",
                created_by=access.user_id,
            )
            db.add(episode)
            db.flush()
        else:
            if estimated_gestation_days is not None or episode.estimated_conception_at is None:
                episode.estimated_conception_at = conceived or episode.estimated_conception_at
            if expected_due_date is not None or episode.expected_due_date is None:
                episode.expected_due_date = due or episode.expected_due_date
            if episode.originating_breeding_event_id is None and breeding is not None:
                episode.originating_breeding_event_id = breeding.id
            episode.version += 1
    elif result_code == "NEGATIVE" and episode is not None:
        _end_episode(episode, at, "NOT_PREGNANT")
    if episode is not None and result_code == "POSITIVE":
        recorded_due = episode.expected_due_date
    else:
        recorded_due = _dt(expected_due_date) if expected_due_date else None
    assessment = rm.PregnancyAssessment(
        tenant_id=animal.tenant_id,
        last_modified_by=access.membership_id,
        pregnancy_episode_id=episode.id if episode else None,
        animal_id=animal.id,
        assessed_at=at,
        assessment_type=assessment_type,
        result_code=result_code,
        estimated_gestation_days=estimated_gestation_days,
        expected_due_date=recorded_due,
        examiner_user_id=_parse_uuid(examiner_user_id),
        notes=notes,
        recorded_at=now_utc(),
        recorded_by=access.user_id,
    )
    db.add(assessment)
    db.flush()
    changed = refresh_projection(db, animal)
    _touch(animal, access)
    _hooks(db, access, animal, changed & {"pregnant"}, trigger="lifecycle_changed")
    return assessment, (episode if episode is not None and episode.status == "active" else None)


def end_pregnancy(
    db: Session,
    access: AccessContext,
    animal: Animal,
    *,
    outcome_code: str,
    at: datetime | None = None,
    notes: str | None = None,
) -> rm.PregnancyEpisode:
    """A pregnancy lost before term. A birth ends a pregnancy through
    [record_birth]; a mistaken confirmation through a negative check."""
    if outcome_code not in LOSS_OUTCOMES:
        raise LivestockError(f"outcome_code must be one of {list(LOSS_OUTCOMES)}")
    ensure_on_farm(animal, "a pregnancy loss")
    episode = active_pregnancy(db, animal.id)
    if episode is None:
        raise LivestockError(f"{animal.name} has no open pregnancy.", 404)
    at = _when(at, "A pregnancy loss")
    _end_episode(episode, at, outcome_code)
    if notes:
        episode.notes = ((episode.notes + "\n") if episode.notes else "") + notes
    changed = refresh_projection(db, animal)
    _touch(animal, access)
    _hooks(db, access, animal, changed & {"pregnant"}, trigger="lifecycle_changed")
    return episode


# ----------------------------------------------------------------- birth
def record_birth(
    db: Session,
    access: AccessContext,
    dam: Animal,
    *,
    offspring: list[dict[str, Any]],
    at: datetime | None = None,
    assistance_code: str = "NONE",
    sire_animal_id: str | None = None,
    sire_reference: str | None = None,
    start_lactation: bool = True,
    notes: str | None = None,
) -> tuple[rm.BirthEvent, dict[str, Any], list[Animal]]:
    """A calving / lambing / kidding / foaling. One transaction: the birth,
    each newborn (registered, counted into a flock, or recorded stillborn),
    parentage, the end of the pregnancy and the start of the next lactation.
    Any newborn the registration rules refuse refuses the whole birth, with
    the reason — nothing half-recorded. Returns the birth, the wire extras
    and the newborns registered (for the router's audit entries)."""
    from app.farmos import group_service

    ensure_on_farm(dam, "a birth")
    _female(dam, "a birth")
    cap_set = _require(dam, Cap.LIVE_BIRTH)
    at = _when(at, "A birth")
    if assistance_code not in ASSISTANCE_CODES:
        raise LivestockError(f"assistance_code must be one of {list(ASSISTANCE_CODES)}")
    if not offspring:
        raise LivestockError("A birth records at least one newborn, live or stillborn.")
    if len(offspring) > MAX_OFFSPRING:
        raise LivestockError(f"At most {MAX_OFFSPRING} newborns in one birth.")
    for i, o in enumerate(offspring, start=1):
        if o.get("outcome_code") not in OFFSPRING_OUTCOMES:
            raise LivestockError(f"Newborn {i}: outcome_code must be one of {list(OFFSPRING_OUTCOMES)}")
        if o.get("register_now") and o["outcome_code"] != "LIVE":
            raise LivestockError(f"Newborn {i} is not recorded live, so it cannot be registered.")
        if o.get("register_now") and o.get("destination_group_id"):
            raise LivestockError(
                f"Newborn {i}: register it as an animal, or count it into a group — not both."
            )
        if o.get("birth_weight_kg") is not None and o["birth_weight_kg"] < 0:
            raise LivestockError(f"Newborn {i}: a weight cannot be negative.")

    pregnancy = active_pregnancy(db, dam.id)
    breeding = (
        db.get(rm.BreedingEvent, pregnancy.originating_breeding_event_id)
        if pregnancy and pregnancy.originating_breeding_event_id
        else None
    )
    sire = _same_farm_animal(db, sire_animal_id, "Sire")
    if sire is not None:
        _validate_sire(dam, sire)
    elif breeding is not None and breeding.male_animal_id:
        sire = db.get(Animal, breeding.male_animal_id)
    sire_ref = (sire_reference or "").strip() or (breeding.sire_reference if breeding else None)

    live = sum(1 for o in offspring if o["outcome_code"] in ("LIVE", "DIED_AFTER_BIRTH"))
    stillborn = sum(1 for o in offspring if o["outcome_code"] == "STILLBORN")
    birth = rm.BirthEvent(
        tenant_id=dam.tenant_id,
        last_modified_by=access.membership_id,
        dam_animal_id=dam.id,
        pregnancy_episode_id=pregnancy.id if pregnancy else None,
        sire_animal_id=sire.id if sire else None,
        sire_reference=sire_ref,
        occurred_at=at,
        assistance_code=assistance_code,
        offspring_count=len(offspring),
        live_count=live,
        stillborn_count=stillborn,
        status="recorded",
        notes=notes,
        recorded_at=now_utc(),
        recorded_by=access.user_id,
    )
    db.add(birth)
    db.flush()
    if pregnancy is not None:
        _end_episode(pregnancy, at, "BIRTH")

    term = _terminology(dam.species)
    registered: list[Animal] = []
    counted = 0
    for i, o in enumerate(offspring, start=1):
        child = None
        group = None
        if o["outcome_code"] == "LIVE" and o.get("register_now"):
            child = _register_newborn(db, access, dam, sire, birth, o, sequence_no=i, term=term)
            registered.append(child)
        elif o["outcome_code"] == "LIVE" and o.get("destination_group_id"):
            group = _same_farm_group(db, o["destination_group_id"], f"Newborn {i}: group")
            assert group is not None
            if group.species != dam.species:
                raise LivestockError(
                    f"Newborn {i}: {group.name} is not a group of {_species_label(dam.species)}."
                )
            group_service.record_quantity(
                db,
                access,
                group,
                "BIRTH",
                1,
                at=at,
                reason=f"Born to {dam.name}",
                source_type="birth_event",
                source_id=str(birth.id),
                correlation_id=str(birth.id),
            )
            counted += 1
        db.add(
            rm.BirthOffspring(
                tenant_id=dam.tenant_id,
                last_modified_by=access.membership_id,
                birth_event_id=birth.id,
                offspring_animal_id=child.id if child else None,
                sequence_no=i,
                sex=animal_sex(o.get("sex")),
                birth_weight_kg=o.get("birth_weight_kg"),
                outcome_code=o["outcome_code"],
                destination_group_id=group.id if group else None,
                notes=o.get("notes"),
            )
        )

    lactation = None
    lactation_note = None
    if start_lactation:
        if cap_set.has(Cap.LACTATION):
            previous = active_lactation(db, dam.id)
            if previous is not None:
                previous.status = "ended"
                previous.ended_at = max(at, _utc(previous.started_at))
                previous.end_reason_code = "NEW_LACTATION"
                previous.ended_by = access.user_id
                previous.version += 1
            lactation = _start_lactation_row(
                db, dam, at=at, number=None, birth_event_id=birth.id, user_id=access.user_id, source="birth"
            )
        else:
            lactation_note = capability_service.describe_missing(cap_set, Cap.LACTATION, dam.name)

    db.flush()
    changed = refresh_projection(db, dam)
    _touch(dam, access)
    _hooks(db, access, dam, changed & {"pregnant", "lactating"}, trigger="lifecycle_changed")
    extra = {
        "registered_offspring_ids": [str(c.id) for c in registered],
        "counted_into_groups": counted,
        "lactation_id": str(lactation.id) if lactation else None,
        "lactation_not_started": lactation_note,
    }
    return birth, extra, registered


def animal_sex(value: str | None) -> str | None:
    try:
        return catalog.normalise_sex(value)
    except ValueError as exc:
        raise LivestockError(str(exc)) from None


def _register_newborn(
    db: Session,
    access: AccessContext,
    dam: Animal,
    sire: Animal | None,
    birth: rm.BirthEvent,
    o: dict[str, Any],
    *,
    sequence_no: int,
    term: dict[str, Any],
) -> Animal:
    from app.farmos import animal_service

    name = (o.get("name") or "").strip() or (
        f"{term.get('offspring_en', 'Offspring')} of {dam.name}"
        + (f" #{sequence_no}" if birth.offspring_count > 1 else "")
    )
    data = {
        "name": name,
        "species": dam.species,
        "sex": animal_sex(o.get("sex")),
        "breed": dam.breed,
        "breed_id": dam.breed_id,
        "birth_date": birth.occurred_at,
        "birth_date_estimated": False,
        "life_stage": o.get("life_stage") or "newborn",
        "management_profile": o.get("management_profile"),
        "location_label": dam.location_label,
        "weight_kg": o.get("birth_weight_kg"),
        "group_name": o.get("group_name"),
        "status": "healthy",
        "health_score": 100,
        "acquisition_source": "born on farm",
        "dam_tag": dam.tag,
        "sire_tag": sire.tag if sire else (birth.sire_reference or None),
        "notes": o.get("notes"),
    }
    identifiers = [
        i if isinstance(i, IdentifierIn) else IdentifierIn(**i) for i in (o.get("identifiers") or [])
    ]
    try:
        child = animal_service.create_animal(
            db, access, data, identifiers_in=identifiers, tag=o.get("tag"), registered_at=birth.occurred_at
        )
    except LivestockError as exc:
        raise LivestockError(f"Newborn {sequence_no}: {exc.message}", exc.status_code) from None
    except Exception as exc:  # noqa: BLE001 - registration's own HTTP refusal, re-worded for the birth
        detail = getattr(exc, "detail", None)
        if detail is None:
            raise
        raise LivestockError(f"Newborn {sequence_no}: {detail}", getattr(exc, "status_code", 422)) from None
    _link_parent(db, dam, child, "DAM_OF", evidence=("birth_event", str(birth.id)), user_id=access.user_id)
    if sire is not None:
        _link_parent(
            db, sire, child, "SIRE_OF", evidence=("birth_event", str(birth.id)), user_id=access.user_id
        )
    return child


def register_offspring(
    db: Session, access: AccessContext, birth: rm.BirthEvent, sequence_no: int, data: dict[str, Any]
) -> Animal:
    """A newborn recorded live but not registered at the birth (no tag at
    hand) is registered later from its birth row — same parentage, same
    birth date."""
    row = db.scalar(
        select(rm.BirthOffspring).where(
            rm.BirthOffspring.birth_event_id == birth.id, rm.BirthOffspring.sequence_no == sequence_no
        )
    )
    if row is None:
        raise LivestockError("Newborn not found on this birth", 404)
    if row.outcome_code != "LIVE":
        raise LivestockError("Only a newborn recorded live can be registered.")
    if row.offspring_animal_id:
        raise LivestockError("This newborn is already registered.", 409)
    if row.destination_group_id:
        raise LivestockError(
            "This newborn was counted into a group; register it from the group instead.", 409
        )
    dam = db.get(Animal, birth.dam_animal_id)
    if dam is None:
        raise LivestockError("Dam not found", 404)
    sire = db.get(Animal, birth.sire_animal_id) if birth.sire_animal_id else None
    term = _terminology(dam.species)
    child = _register_newborn(
        db,
        access,
        dam,
        sire,
        birth,
        {**data, "sex": data.get("sex") or row.sex, "birth_weight_kg": row.birth_weight_kg},
        sequence_no=sequence_no,
        term=term,
    )
    row.offspring_animal_id = child.id
    if data.get("sex"):
        row.sex = animal_sex(data["sex"])
    row.version += 1
    row.last_modified_by = access.membership_id
    return child


def _link_parent(
    db: Session,
    parent: Animal,
    child: Animal,
    relationship_type: str,
    *,
    evidence: tuple[str, str] | None,
    user_id: uuid.UUID,
    confidence: str = "recorded",
) -> AnimalRelationship:
    rel = AnimalRelationship(
        tenant_id=child.tenant_id,
        source_animal_id=parent.id,
        target_animal_id=child.id,
        relationship_type=relationship_type,
        confidence_code=confidence,
        evidence_type=evidence[0] if evidence else None,
        evidence_id=evidence[1] if evidence else None,
        status="active",
        created_by=user_id,
    )
    db.add(rel)
    db.flush()
    return rel


def record_parent(
    db: Session,
    access: AccessContext,
    child: Animal,
    parent: Animal,
    relationship_type: str,
    *,
    evidence: str | None,
) -> AnimalRelationship:
    """Parentage entered by hand — an animal bought in with known parents
    on the farm. A birth recorded here links parentage itself."""
    if relationship_type not in ("DAM_OF", "SIRE_OF"):
        raise LivestockError("relationship_type must be DAM_OF or SIRE_OF")
    if parent.id == child.id:
        raise LivestockError("An animal cannot be its own parent.")
    if parent.species != child.species:
        raise LivestockError(f"{parent.name} and {child.name} are not the same species.")
    if (relationship_type == "DAM_OF" and parent.sex != "F") or (
        relationship_type == "SIRE_OF" and parent.sex != "M"
    ):
        raise LivestockError(
            f"{parent.name}'s sex does not match {relationship_type.replace('_OF', '').lower()}."
        )
    if parent.birth_date and child.birth_date and _utc(parent.birth_date) >= _utc(child.birth_date):
        raise LivestockError(f"{parent.name} was not born before {child.name}.")
    existing = db.scalar(
        select(AnimalRelationship).where(
            AnimalRelationship.target_animal_id == child.id,
            AnimalRelationship.relationship_type == relationship_type,
            AnimalRelationship.status == "active",
        )
    )
    if existing is not None:
        raise LivestockError(
            f"{child.name} already has a recorded {'dam' if relationship_type == 'DAM_OF' else 'sire'}.", 409
        )
    return _link_parent(
        db,
        parent,
        child,
        relationship_type,
        evidence=("manual", evidence) if evidence else None,
        user_id=access.user_id,
        confidence="stated",
    )


def relationships(db: Session, animal: Animal) -> dict[str, Any]:
    db.flush()
    parents = db.execute(
        select(AnimalRelationship, Animal)
        .join(Animal, Animal.id == AnimalRelationship.source_animal_id)
        .where(AnimalRelationship.target_animal_id == animal.id, AnimalRelationship.status == "active")
    ).all()
    children = db.execute(
        select(AnimalRelationship, Animal)
        .join(Animal, Animal.id == AnimalRelationship.target_animal_id)
        .where(AnimalRelationship.source_animal_id == animal.id, AnimalRelationship.status == "active")
        .order_by(Animal.birth_date.desc())
    ).all()

    def _a(rel: AnimalRelationship, other: Animal) -> dict[str, Any]:
        return {
            "relationship_id": str(rel.id),
            "relationship_type": rel.relationship_type,
            "animal_id": str(other.id),
            "name": other.name,
            "tag": other.tag,
            "sex": other.sex,
            "birth_date": other.birth_date,
            "active": other.active,
            "confidence": rel.confidence_code,
            "evidence_type": rel.evidence_type,
            "evidence_id": rel.evidence_id,
        }

    return {
        "dam": next((_a(r, a) for r, a in parents if r.relationship_type == "DAM_OF"), None),
        "sire": next((_a(r, a) for r, a in parents if r.relationship_type == "SIRE_OF"), None),
        "offspring": [_a(r, a) for r, a in children if r.relationship_type in ("DAM_OF", "SIRE_OF")],
    }


# ----------------------------------------------------------------- lactation
def _start_lactation_row(
    db: Session,
    animal: Animal,
    *,
    at: datetime,
    number: int | None,
    birth_event_id: uuid.UUID | None,
    user_id: uuid.UUID,
    source: str,
) -> rm.LactationEpisode:
    if number is None:
        previous_max = db.scalar(
            select(func.max(rm.LactationEpisode.lactation_number)).where(
                rm.LactationEpisode.animal_id == animal.id
            )
        )
        number = (previous_max or animal.lactation_cycle or 0) + 1
    row = rm.LactationEpisode(
        tenant_id=animal.tenant_id,
        animal_id=animal.id,
        birth_event_id=birth_event_id,
        started_at=at,
        status="active",
        lactation_number=number,
        source=source,
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    return row


def start_lactation(
    db: Session,
    access: AccessContext,
    animal: Animal,
    *,
    at: datetime | None = None,
    lactation_number: int | None = None,
) -> rm.LactationEpisode:
    """A lactation started without a recorded birth (an animal bought in
    fresh, a birth before the app). After a recorded birth it starts by
    itself."""
    ensure_on_farm(animal, "a lactation")
    _female(animal, "a lactation")
    _require(animal, Cap.LACTATION)
    at = _when(at, "A lactation")
    if active_lactation(db, animal.id) is not None:
        raise LivestockError(f"{animal.name} is already in milk; record the dry-off first.", 409)
    if lactation_number is not None and lactation_number < 1:
        raise LivestockError("The lactation number starts at 1.")
    row = _start_lactation_row(
        db,
        animal,
        at=at,
        number=lactation_number,
        birth_event_id=None,
        user_id=access.user_id,
        source="recorded",
    )
    changed = refresh_projection(db, animal)
    _touch(animal, access)
    _hooks(db, access, animal, changed & {"lactating"}, trigger="lifecycle_changed")
    return row


def end_lactation(
    db: Session, access: AccessContext, animal: Animal, *, at: datetime | None = None, reason: str = "DRY_OFF"
) -> rm.LactationEpisode:
    """Dry-off (PRODUCTION-SCHEMA §8 LactationEnded)."""
    ensure_on_farm(animal, "a dry-off")
    row = active_lactation(db, animal.id)
    if row is None:
        raise LivestockError(f"{animal.name} is not in milk.", 404)
    at = _when(at, "A dry-off")
    if at < _utc(row.started_at):
        raise LivestockError(
            f"This lactation started on {_utc(row.started_at).date().isoformat()}; "
            "a dry-off cannot be dated before it."
        )
    row.status, row.ended_at, row.end_reason_code, row.ended_by = "ended", at, reason, access.user_id
    row.version += 1
    changed = refresh_projection(db, animal)
    _touch(animal, access)
    _hooks(db, access, animal, changed & {"lactating"}, trigger="lifecycle_changed")
    return row


def on_exit(db: Session, animal: Animal, *, at: datetime, user_id: uuid.UUID) -> None:
    pregnancy = active_pregnancy(db, animal.id)
    if pregnancy is not None:
        _end_episode(pregnancy, max(at, _utc(pregnancy.created_at)), "ANIMAL_EXITED")
    lactation = active_lactation(db, animal.id)
    if lactation is not None:
        lactation.status = "ended"
        lactation.ended_at = max(at, _utc(lactation.started_at))
        lactation.end_reason_code = "ANIMAL_EXITED"
        lactation.ended_by = user_id
        lactation.version += 1
    refresh_projection(db, animal)


# ----------------------------------------------------------------- incubation
def _species_supports(species_code: str, cap: str) -> bool:
    return any(
        r["capability_code"] == cap and r["enabled"] for r in catalog.RULES_BY_SPECIES.get(species_code, ())
    )


def start_incubation(
    db: Session,
    access: AccessContext,
    *,
    species_code: str,
    eggs_set: int,
    at: datetime | None = None,
    source_group_id: str | None = None,
    expected_hatch_date: datetime | None = None,
    code: str | None = None,
    notes: str | None = None,
) -> rm.IncubationBatch:
    try:
        species = _species(species_code)
    except capability_service.UnknownSpecies:
        raise LivestockError(f"'{species_code}' is not a species this farm keeps.") from None
    if eggs_set is None or eggs_set <= 0:
        raise LivestockError("Set at least one egg.")
    at = _when(at, "An incubation")
    group = None
    if source_group_id:
        group = _same_farm_group(db, source_group_id, "Source flock")
        assert group is not None
        if group.species != species_code:
            raise LivestockError(
                f"{group.name} is a flock of {_species_label(group.species)}, "
                f"not {_species_label(species_code)}."
            )
        cap_set = capability_service.resolve_for_group(group)
        if not cap_set.has(Cap.INCUBATION):
            raise LivestockError(capability_service.describe_missing(cap_set, Cap.INCUBATION, group.name))
    elif not _species_supports(species_code, Cap.INCUBATION):
        raise LivestockError(f"{species['name_en']} are not configured for incubation.")
    code = (code or "").strip() or _next_incubation_code(db, at)
    if db.scalar(select(rm.IncubationBatch.id).where(rm.IncubationBatch.code == code)):
        raise LivestockError(f"An incubation batch {code} already exists.", 409)
    incubation_days = species.get("incubation_days")
    if expected_hatch_date:
        due: datetime | None = _dt(expected_hatch_date)
    else:
        due = at + timedelta(days=int(incubation_days)) if incubation_days else None
    batch = rm.IncubationBatch(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        species_code=species_code,
        source_group_id=group.id if group else None,
        code=code,
        eggs_set=eggs_set,
        set_at=at,
        expected_hatch_date=due,
        status="incubating",
        notes=notes,
        created_by=access.user_id,
    )
    db.add(batch)
    db.flush()
    return batch


def _next_incubation_code(db: Session, at: datetime) -> str:
    base = f"INC-{at:%Y%m%d}"
    n = 1
    while db.scalar(select(rm.IncubationBatch.id).where(rm.IncubationBatch.code == f"{base}-{n}")):
        n += 1
    return f"{base}-{n}"


def record_hatch(
    db: Session,
    access: AccessContext,
    batch: rm.IncubationBatch,
    *,
    hatched_live: int,
    at: datetime | None = None,
    unhatched: int | None = None,
    culled: int | None = None,
    destination_group_id: str | None = None,
    notes: str | None = None,
) -> rm.HatchEvent:
    from app.farmos import group_service

    if batch.status != "incubating":
        raise LivestockError(f"Batch {batch.code} is {batch.status}.", 409)
    if not _species_supports(batch.species_code, Cap.HATCHING):
        raise LivestockError(f"{_species(batch.species_code)['name_en']} are not configured for hatching.")
    at = _when(at, "A hatch")
    if at < _utc(batch.set_at):
        raise LivestockError(
            f"Batch {batch.code} was set on {_utc(batch.set_at).date().isoformat()}; "
            "a hatch cannot be dated before it."
        )
    counts = [hatched_live, unhatched or 0, culled or 0]
    if any(c < 0 for c in counts):
        raise LivestockError("Counts cannot be negative.")
    if sum(counts) > batch.eggs_set:
        raise LivestockError(f"{sum(counts)} accounted for, but {batch.eggs_set} eggs were set.")
    group = None
    if destination_group_id:
        group = _same_farm_group(db, destination_group_id, "Destination flock")
        assert group is not None
        if group.species != batch.species_code:
            raise LivestockError(f"{group.name} is not a flock of {_species_label(batch.species_code)}.")
    hatch = rm.HatchEvent(
        tenant_id=batch.tenant_id,
        last_modified_by=access.membership_id,
        incubation_batch_id=batch.id,
        occurred_at=at,
        hatched_live=hatched_live,
        unhatched=unhatched,
        culled=culled,
        destination_group_id=group.id if group else None,
        notes=notes,
        recorded_at=now_utc(),
        recorded_by=access.user_id,
    )
    db.add(hatch)
    db.flush()
    batch.status = "hatched"
    batch.version += 1
    batch.last_modified_by = access.membership_id
    if group is not None and hatched_live > 0:
        group_service.record_quantity(
            db,
            access,
            group,
            "HATCH",
            hatched_live,
            at=at,
            reason=f"Hatched from {batch.code}",
            source_type="hatch_event",
            source_id=str(hatch.id),
            correlation_id=str(batch.id),
        )
    return hatch


def cancel_incubation(
    db: Session, access: AccessContext, batch: rm.IncubationBatch, *, reason: str
) -> rm.IncubationBatch:
    if batch.status != "incubating":
        raise LivestockError(f"Batch {batch.code} is {batch.status}.", 409)
    if not reason or not reason.strip():
        raise LivestockError("Say why the incubation ended without a hatch.")
    batch.status = "failed"
    batch.notes = ((batch.notes + "\n") if batch.notes else "") + reason.strip()
    batch.version += 1
    batch.last_modified_by = access.membership_id
    return batch


# ----------------------------------------------------------------- reads
def _animal_name(db: Session, animal_id: uuid.UUID | None) -> str | None:
    if animal_id is None:
        return None
    animal = db.get(Animal, animal_id)
    return animal.name if animal else None


def _group_name(db: Session, group_id: uuid.UUID | None) -> str | None:
    if group_id is None:
        return None
    group = db.get(AnimalGroup, group_id)
    return group.name if group else None


def _id(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def breeding_dict(db: Session, b: rm.BreedingEvent) -> dict[str, Any]:
    return {
        "id": str(b.id),
        "event_type": b.event_type,
        "occurred_at": _dt(b.occurred_at),
        "male_animal_id": _id(b.male_animal_id),
        "male_name": _animal_name(db, b.male_animal_id),
        "sire_reference": b.sire_reference,
        "semen_batch_reference": b.semen_batch_reference,
        "technician_user_id": _id(b.technician_user_id),
        "status": b.status,
        "void_reason": b.void_reason,
        "notes": b.notes,
        "recorded_at": _dt(b.recorded_at),
        "recorded_by": _id(b.recorded_by),
    }


def assessment_dict(a: rm.PregnancyAssessment) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "pregnancy_episode_id": _id(a.pregnancy_episode_id),
        "assessed_at": _dt(a.assessed_at),
        "assessment_type": a.assessment_type,
        "result_code": a.result_code,
        "estimated_gestation_days": a.estimated_gestation_days,
        "expected_due_date": _dt(a.expected_due_date),
        "examiner_user_id": _id(a.examiner_user_id),
        "notes": a.notes,
        "recorded_by": _id(a.recorded_by),
    }


def pregnancy_dict(p: rm.PregnancyEpisode) -> dict[str, Any]:
    due = _dt(p.expected_due_date)
    return {
        "id": str(p.id),
        "status": p.status,
        "originating_breeding_event_id": _id(p.originating_breeding_event_id),
        "estimated_conception_at": _dt(p.estimated_conception_at),
        "expected_due_date": due,
        "days_pregnant": days_pregnant(p) if p.status == "active" else None,
        "days_to_due": days_between(now_utc(), due) if due and p.status == "active" else None,
        "ended_at": _dt(p.ended_at),
        "outcome_code": p.outcome_code,
        "source": p.source,
        "notes": p.notes,
        "created_at": _dt(p.created_at),
    }


def birth_dict(db: Session, b: rm.BirthEvent) -> dict[str, Any]:
    db.flush()
    rows = db.scalars(
        select(rm.BirthOffspring)
        .where(rm.BirthOffspring.birth_event_id == b.id)
        .order_by(rm.BirthOffspring.sequence_no)
    ).all()
    out = []
    for o in rows:
        child = db.get(Animal, o.offspring_animal_id) if o.offspring_animal_id else None
        out.append(
            {
                "id": str(o.id),
                "sequence_no": o.sequence_no,
                "sex": o.sex,
                "birth_weight_kg": o.birth_weight_kg,
                "outcome_code": o.outcome_code,
                "offspring_animal_id": _id(o.offspring_animal_id),
                "offspring_name": child.name if child else None,
                "offspring_tag": child.tag if child else None,
                "destination_group_id": _id(o.destination_group_id),
                "destination_group_name": _group_name(db, o.destination_group_id),
                "registrable": o.outcome_code == "LIVE"
                and not o.offspring_animal_id
                and not o.destination_group_id,
                "notes": o.notes,
            }
        )
    return {
        "id": str(b.id),
        "dam_animal_id": str(b.dam_animal_id),
        "pregnancy_episode_id": _id(b.pregnancy_episode_id),
        "occurred_at": _dt(b.occurred_at),
        "assistance_code": b.assistance_code,
        "offspring_count": b.offspring_count,
        "live_count": b.live_count,
        "stillborn_count": b.stillborn_count,
        "sire_animal_id": _id(b.sire_animal_id),
        "sire_name": _animal_name(db, b.sire_animal_id),
        "sire_reference": b.sire_reference,
        "notes": b.notes,
        "offspring": out,
        "recorded_by": _id(b.recorded_by),
    }


def lactation_dict(lac: rm.LactationEpisode) -> dict[str, Any]:
    end = _dt(lac.ended_at) or now_utc()
    return {
        "id": str(lac.id),
        "lactation_number": lac.lactation_number,
        "started_at": _dt(lac.started_at),
        "ended_at": _dt(lac.ended_at),
        "status": lac.status,
        "days_in_milk": days_between(lac.started_at, end),
        "birth_event_id": _id(lac.birth_event_id),
        "end_reason_code": lac.end_reason_code,
        "source": lac.source,
    }


def incubation_dict(db: Session, batch: rm.IncubationBatch) -> dict[str, Any]:
    db.flush()
    hatch = db.scalar(select(rm.HatchEvent).where(rm.HatchEvent.incubation_batch_id == batch.id).limit(1))
    due = _dt(batch.expected_hatch_date)
    return {
        "id": str(batch.id),
        "code": batch.code,
        "species_code": batch.species_code,
        "source_group_id": _id(batch.source_group_id),
        "source_group_name": _group_name(db, batch.source_group_id),
        "eggs_set": batch.eggs_set,
        "set_at": _dt(batch.set_at),
        "expected_hatch_date": due,
        "days_to_hatch": days_between(now_utc(), due) if due and batch.status == "incubating" else None,
        "status": batch.status,
        "notes": batch.notes,
        "hatch": (
            {
                "id": str(hatch.id),
                "occurred_at": _dt(hatch.occurred_at),
                "hatched_live": hatch.hatched_live,
                "unhatched": hatch.unhatched,
                "culled": hatch.culled,
                "destination_group_id": _id(hatch.destination_group_id),
                "hatch_rate_percent": round(100 * hatch.hatched_live / batch.eggs_set, 1),
            }
            if hatch
            else None
        ),
    }


def summary(db: Session, animal: Animal) -> dict[str, Any]:
    """What the Digital Twin's reproduction card shows, and what the
    tablet needs to decide which actions to offer."""
    cap_set = _caps(animal)
    pregnancy = active_pregnancy(db, animal.id)
    lactation = active_lactation(db, animal.id)
    last_breeding = db.scalar(
        select(rm.BreedingEvent)
        .where(rm.BreedingEvent.female_animal_id == animal.id, rm.BreedingEvent.status == "recorded")
        .order_by(rm.BreedingEvent.occurred_at.desc())
        .limit(1)
    )
    last_check = db.scalar(
        select(rm.PregnancyAssessment)
        .where(rm.PregnancyAssessment.animal_id == animal.id)
        .order_by(rm.PregnancyAssessment.assessed_at.desc())
        .limit(1)
    )
    last_birth = db.scalar(
        select(rm.BirthEvent)
        .where(rm.BirthEvent.dam_animal_id == animal.id)
        .order_by(rm.BirthEvent.occurred_at.desc())
        .limit(1)
    )
    births = (
        db.scalar(select(func.count(rm.BirthEvent.id)).where(rm.BirthEvent.dam_animal_id == animal.id)) or 0
    )
    awaiting = (
        pregnancy is None
        and last_breeding is not None
        and last_breeding.event_type in SERVICE_TYPES
        and days_between(last_breeding.occurred_at, now_utc()) >= CHECK_DUE_AFTER_DAYS
        and (last_check is None or _utc(last_check.assessed_at) < _utc(last_breeding.occurred_at))
    )
    calendar = farm_calendar(db, animal.tenant_id)
    days_before = calendar["dry_off_days_before_birth"]
    calving_at = lactation.started_at if lactation else (last_birth.occurred_at if last_birth else None)
    can_breed = cap_set.has(Cap.BREEDING) and animal.sex == "F"
    plan = calendar_plan(
        calving_at=calving_at,
        lactating=lactation is not None,
        pregnant=pregnancy is not None,
        can_breed=can_breed,
        last_service_at=(
            last_breeding.occurred_at if last_breeding and last_breeding.event_type in SERVICE_TYPES else None
        ),
        last_check_at=last_check.assessed_at if last_check else None,
        calendar=calendar,
    )
    return {
        "calendar": calendar,
        **plan,
        "applies": {
            "breeding": can_breed,
            "pregnancy": cap_set.has(Cap.PREGNANCY),
            "live_birth": cap_set.has(Cap.LIVE_BIRTH),
            "lactation": cap_set.has(Cap.LACTATION),
        },
        "terminology": _terminology(animal.species),
        "pregnant": pregnancy is not None,
        "pregnancy": pregnancy_dict(pregnancy) if pregnancy else None,
        "lactation": lactation_dict(lactation) if lactation else None,
        "last_breeding": breeding_dict(db, last_breeding) if last_breeding else None,
        "last_check": assessment_dict(last_check) if last_check else None,
        "last_birth": (
            {
                "id": str(last_birth.id),
                "occurred_at": _dt(last_birth.occurred_at),
                "live_count": last_birth.live_count,
                "offspring_count": last_birth.offspring_count,
            }
            if last_birth
            else None
        ),
        "births_count": births,
        "awaiting_pregnancy_check": awaiting,
        "lactation_number": animal.lactation_cycle,
        "dry_off_days_before_birth": days_before,
        **dry_off_plan(
            pregnancy.expected_due_date if pregnancy else None, lactation is not None, days_before
        ),
    }


def timeline(db: Session, animal: Animal) -> dict[str, Any]:
    db.flush()
    return {
        "animal_id": str(animal.id),
        "summary": summary(db, animal),
        "breedings": [
            breeding_dict(db, b)
            for b in db.scalars(
                select(rm.BreedingEvent)
                .where(rm.BreedingEvent.female_animal_id == animal.id)
                .order_by(rm.BreedingEvent.occurred_at.desc())
            )
        ],
        "assessments": [
            assessment_dict(a)
            for a in db.scalars(
                select(rm.PregnancyAssessment)
                .where(rm.PregnancyAssessment.animal_id == animal.id)
                .order_by(rm.PregnancyAssessment.assessed_at.desc())
            )
        ],
        "pregnancies": [
            pregnancy_dict(p)
            for p in db.scalars(
                select(rm.PregnancyEpisode)
                .where(rm.PregnancyEpisode.animal_id == animal.id)
                .order_by(rm.PregnancyEpisode.created_at.desc())
            )
        ],
        "births": [
            birth_dict(db, b)
            for b in db.scalars(
                select(rm.BirthEvent)
                .where(rm.BirthEvent.dam_animal_id == animal.id)
                .order_by(rm.BirthEvent.occurred_at.desc())
            )
        ],
        "lactations": [
            lactation_dict(lac)
            for lac in db.scalars(
                select(rm.LactationEpisode)
                .where(rm.LactationEpisode.animal_id == animal.id)
                .order_by(rm.LactationEpisode.started_at.desc())
            )
        ],
        "relationships": relationships(db, animal),
    }


def overview(db: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    """The farm's reproduction day (Vision: "Which cows, goats or sheep
    are ready for breeding?"): who is due, who is waiting for a check, who
    is in milk and at their peak, who is past the waiting period and ready
    to inseminate, who to watch for a return to heat, what is in the
    incubator.

    Read in a handful of farm-wide queries — the episodes, the latest
    service, check and birth per animal — never one summary per animal."""
    db.flush()
    at = now_utc()
    animals = {
        a.id: a
        for a in db.scalars(select(Animal).where(Animal.active.is_(True), Animal.deleted_at.is_(None)))
    }

    def _brief(a: Animal, **extra: Any) -> dict[str, Any]:
        return {"animal_id": str(a.id), "name": a.name, "tag": a.tag, "species": a.species, **extra}

    calendar = farm_calendar(db, tenant_id)
    days_before = calendar["dry_off_days_before_birth"]
    pregnancies = {
        p.animal_id: p
        for p in db.scalars(
            select(rm.PregnancyEpisode)
            .where(rm.PregnancyEpisode.status == "active")
            .order_by(rm.PregnancyEpisode.created_at)
        )
    }
    lactations = {
        lac.animal_id: lac
        for lac in db.scalars(
            select(rm.LactationEpisode)
            .where(rm.LactationEpisode.status == "active")
            .order_by(rm.LactationEpisode.started_at)
        )
    }
    last_breeding: dict[uuid.UUID, rm.BreedingEvent] = {}
    for breeding_row in db.scalars(
        select(rm.BreedingEvent)
        .where(rm.BreedingEvent.status == "recorded")
        .order_by(rm.BreedingEvent.occurred_at)
    ):
        last_breeding[breeding_row.female_animal_id] = breeding_row
    last_check: dict[uuid.UUID, rm.PregnancyAssessment] = {}
    for check_row in db.scalars(select(rm.PregnancyAssessment).order_by(rm.PregnancyAssessment.assessed_at)):
        last_check[check_row.animal_id] = check_row
    last_birth: dict[uuid.UUID, rm.BirthEvent] = {}
    for birth_row in db.scalars(select(rm.BirthEvent).order_by(rm.BirthEvent.occurred_at)):
        last_birth[birth_row.dam_animal_id] = birth_row

    # Breeding capability per resolver input, not per animal.
    can_breed_cache: dict[tuple[str, str | None, str | None, str | None], bool] = {}

    def can_breed(a: Animal) -> bool:
        key = (a.species, a.sex, a.life_stage, a.management_profile)
        if key not in can_breed_cache:
            try:
                can_breed_cache[key] = a.sex == "F" and capability_service.resolve(*key).has(Cap.BREEDING)
            except capability_service.UnknownSpecies:
                can_breed_cache[key] = False
        return can_breed_cache[key]

    due = []
    for animal_id, p in pregnancies.items():
        a = animals.get(animal_id)
        if a is None:
            continue
        due_date = _dt(p.expected_due_date)
        days = days_between(at, due_date) if due_date else None
        due.append(
            _brief(
                a,
                expected_due_date=due_date,
                days_to_due=days,
                days_pregnant=days_pregnant(p),
                due_soon=days is not None and days <= DUE_SOON_DAYS,
                overdue=days is not None and days < 0,
                lactating=bool(a.lactating),
                **dry_off_plan(due_date, bool(a.lactating), days_before, at),
            )
        )
    due.sort(
        key=lambda r: (r["days_to_due"] is None, r["days_to_due"] if r["days_to_due"] is not None else 0)
    )

    awaiting, peak, ready, heat = [], [], [], []
    for a in animals.values():
        if a.sex != "F":
            continue
        b = last_breeding.get(a.id)
        c = last_check.get(a.id)
        lac = lactations.get(a.id)
        pregnant = a.id in pregnancies
        service = b if b is not None and b.event_type in SERVICE_TYPES else None
        if (
            not pregnant
            and service is not None
            and days_between(service.occurred_at, at) >= CHECK_DUE_AFTER_DAYS
            and (c is None or _utc(c.assessed_at) < _utc(service.occurred_at))
        ):
            awaiting.append(
                _brief(
                    a,
                    last_breeding_at=_dt(service.occurred_at),
                    days_since_breeding=days_between(service.occurred_at, at),
                )
            )
        birth = last_birth.get(a.id)
        calving = lac.started_at if lac else (birth.occurred_at if birth else None)
        if calving is None and service is None:
            continue
        plan = calendar_plan(
            calving_at=calving,
            lactating=lac is not None,
            pregnant=pregnant,
            can_breed=can_breed(a),
            last_service_at=service.occurred_at if service else None,
            last_check_at=c.assessed_at if c else None,
            calendar=calendar,
            at=at,
        )
        if plan["in_milk_peak"]:
            peak.append(_brief(a, days_in_milk=plan["days_in_milk"], milk_peak_to=plan["milk_peak_to"]))
        if plan["ready_for_breeding"]:
            ready.append(
                _brief(
                    a,
                    days_since_calving=days_between(plan["calving_at"], at),
                    breeding_from=plan["breeding_from"],
                )
            )
        if plan["heat_watch_now"] and service is not None:
            heat.append(
                _brief(a, last_service_at=_dt(service.occurred_at), heat_watch_to=plan["heat_watch_to"])
            )

    recent = []
    for birth_row in db.scalars(
        select(rm.BirthEvent)
        .where(rm.BirthEvent.occurred_at >= at - timedelta(days=30))
        .order_by(rm.BirthEvent.occurred_at.desc())
    ):
        dam = animals.get(birth_row.dam_animal_id) or db.get(Animal, birth_row.dam_animal_id)
        recent.append(
            {
                "birth_id": str(birth_row.id),
                "dam_animal_id": str(birth_row.dam_animal_id),
                "dam_name": dam.name if dam else None,
                "occurred_at": _dt(birth_row.occurred_at),
                "live_count": birth_row.live_count,
                "offspring_count": birth_row.offspring_count,
            }
        )
    in_milk = [a for a in animals.values() if a.lactating]
    incubations = [
        incubation_dict(db, b)
        for b in db.scalars(
            select(rm.IncubationBatch)
            .where(rm.IncubationBatch.status == "incubating")
            .order_by(rm.IncubationBatch.expected_hatch_date)
        )
    ]
    return {
        "pregnant": due,
        "due_soon_count": sum(1 for r in due if r["due_soon"]),
        "awaiting_pregnancy_check": awaiting,
        "recent_births": recent,
        "in_milk_count": len(in_milk),
        "incubations": incubations,
        "check_due_after_days": CHECK_DUE_AFTER_DAYS,
        "due_soon_days": DUE_SOON_DAYS,
        "dry_off_days_before_birth": days_before,
        "dry_off_due_count": sum(1 for r in due if r["dry_off_due"]),
        "calendar": calendar,
        "in_milk_peak": sorted(peak, key=lambda r: r["days_in_milk"] or 0),
        "ready_for_breeding": sorted(ready, key=lambda r: -(r["days_since_calving"] or 0)),
        "heat_watch": heat,
    }
