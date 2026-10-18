"""Clinical decision support & emergency protocol engine (OrigamiFarmOS
database/CLINICAL-DECISION-SUPPORT-EMERGENCY-PROTOCOLS.md) — the
deterministic first increment, ported one-to-one from the reference.

What it does: scores the worker's signs against the trigger rules of
every current, veterinarian-approved protocol version; checks the
animal's authoritative facts against the version's eligibility rules;
ranks urgency; explains which facts supported a match and what is
missing; calculates a dose only from the approved parameters and only
within their limits; checks exact eligible pharmacy stock before a
medication step; binds the administration to the lot; schedules the
reassessment; and notifies the manager and veterinarian as the version
requires.

What it never does: display a drug, route or dose that is not in a
current approved version; treat its own guess as a prescription; skip
escalation when a danger sign fires, a required observation is missing,
no approved protocol matches, the animal fails a BLOCK / REQUIRE_VET
rule, the medicine is unavailable or only ineligible stock exists, the
dose falls outside the approved range, or the response at reassessment
is not an improvement.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.farmos import capability_service, livestock_service, uom_service
from app.farmos import emergency_models as em
from app.farmos import health_case_service as cases
from app.farmos import pharmacy_service as ph
from app.farmos import routing_service as routing
from app.farmos.deps import AccessContext
from app.farmos.feed_catalog import FeedError
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.health_case_models import HealthCase
from app.farmos.livestock_models import AnimalGroup
from app.farmos.livestock_service import ensure_utc, now_utc
from app.farmos.pharmacy_catalog import ROUTES
from app.farmos.pharmacy_models import MedicationAdministration, MedicineProduct
from app.farmos.production_models import Observation, Treatment
from app.farmos.routing_service import NameCache
from app.tenant_api.models import Animal, InventoryItem, Task


class EmergencyError(FeedError):
    """A rule of the protocol engine refused the operation."""


MODEL_REFERENCE = "origami.emergency.deterministic"
MODEL_VERSION = "1.1"
OBSERVATION_LOOKBACK_HOURS = 24
DUPLICATE_WINDOW_HOURS = 6
ESCALATION_TASK = "emergency_escalation"
NOTIFY_TASK = "emergency_notification"
REASSESS_TASK = "emergency_reassessment"
CASE_SEVERITY = {"CRITICAL": "critical", "HIGH": "high", "MODERATE": "moderate", "LOW": "low"}
ALLERGY_TYPES = ("allergy", "adverse_reaction", "contraindication")
GROUP_ENTITY_TYPES = ("flock", "group")

# Standard sign vocabulary the tablet offers; protocols may add their own.
STANDARD_SIGNS: tuple[dict[str, Any], ...] = (
    {"code": "temperature_c", "kind": "numeric", "unit": "°C", "label": "Rectal temperature"},
    {"code": "heart_rate", "kind": "numeric", "unit": "bpm", "label": "Heart rate"},
    {"code": "respiratory_rate", "kind": "numeric", "unit": "/min", "label": "Respiratory rate"},
    {
        "code": "appetite",
        "kind": "categorical",
        "options": ["normal", "reduced", "absent"],
        "label": "Appetite",
    },
    {
        "code": "rumination",
        "kind": "categorical",
        "options": ["normal", "reduced", "absent"],
        "label": "Rumination",
    },
    {
        "code": "water_intake",
        "kind": "categorical",
        "options": ["normal", "reduced", "absent"],
        "label": "Water intake",
    },
    {"code": "fever", "kind": "boolean", "label": "Fever (felt / suspected)"},
    {"code": "recumbent", "kind": "boolean", "label": "Down, cannot rise"},
    {"code": "bloat", "kind": "boolean", "label": "Bloat"},
    {"code": "diarrhoea", "kind": "boolean", "label": "Diarrhoea"},
    {"code": "nasal_discharge", "kind": "boolean", "label": "Nasal discharge"},
    {"code": "coughing", "kind": "boolean", "label": "Coughing"},
    {"code": "lameness", "kind": "boolean", "label": "Lameness"},
    {"code": "colic_signs", "kind": "boolean", "label": "Colic signs (pawing, rolling)"},
    {"code": "udder_swelling", "kind": "boolean", "label": "Udder swelling / heat"},
    {"code": "panting", "kind": "boolean", "label": "Open-mouth panting"},
    {"code": "mortality_count", "kind": "numeric", "unit": "head", "label": "Dead today"},
    {"code": "ambient_temperature_c", "kind": "numeric", "unit": "°C", "label": "Ambient temperature"},
    {"code": "milk_drop_pct", "kind": "numeric", "unit": "%", "label": "Milk drop"},
)
# Legacy observation types map onto the sign vocabulary.
OBSERVATION_CODE_ALIASES = {
    "temperature": "temperature_c",
    "temp": "temperature_c",
    "body_temperature": "temperature_c",
    "mortality": "mortality_count",
    "down": "recumbent",
    "off_feed": "appetite",
}
SEVERE_OBSERVATION_SEVERITIES = ("severe", "critical")


# ---------------------------------------------------------------- helpers
def _rank(result: str) -> int:
    return em.ELIGIBILITY_RESULTS.index(result)


def _worst(results: list[str]) -> str:
    return max(results, key=_rank) if results else "ELIGIBLE"


def _s(value: uuid.UUID | None) -> str | None:
    return str(value) if value else None


def _subject(db: Session, subject_type: str, subject_id: str | uuid.UUID | None) -> Any:
    try:
        return cases.subject_or_404(db, subject_type, subject_id)
    except cases.HealthError as exc:
        raise EmergencyError(exc.message, exc.status_code) from exc


def get_protocol(db: Session, protocol_id: str | uuid.UUID | None) -> em.EmergencyProtocol:
    pk = parse_uuid(protocol_id)
    p = db.get(em.EmergencyProtocol, pk) if pk else None
    if p is None or p.deleted_at is not None:
        raise EmergencyError("Protocol not found", 404)
    return p


def get_version(db: Session, version_id: str | uuid.UUID | None) -> em.EmergencyProtocolVersion:
    pk = parse_uuid(version_id)
    v = db.get(em.EmergencyProtocolVersion, pk) if pk else None
    if v is None or v.deleted_at is not None:
        raise EmergencyError("Protocol version not found", 404)
    return v


def get_assessment(db: Session, assessment_id: str | uuid.UUID | None) -> em.EmergencyAssessment:
    pk = parse_uuid(assessment_id)
    a = db.get(em.EmergencyAssessment, pk) if pk else None
    if a is None or a.deleted_at is not None:
        raise EmergencyError("Assessment not found", 404)
    return a


def get_run(db: Session, run_id: str | uuid.UUID | None) -> em.EmergencyProtocolRun:
    pk = parse_uuid(run_id)
    r = db.get(em.EmergencyProtocolRun, pk) if pk else None
    if r is None or r.deleted_at is not None:
        raise EmergencyError("Protocol run not found", 404)
    return r


def version_is_current(v: em.EmergencyProtocolVersion, at: datetime | None = None) -> bool:
    at = at or now_utc()
    if v.status != "approved" or v.protocol.status != "active" or v.protocol.current_version_id != v.id:
        return False
    frm, to = ensure_utc(v.effective_from), ensure_utc(v.effective_to)
    if frm is not None and frm > at:
        return False
    if to is not None and to <= at:
        return False
    return True


def _task(
    db: Session,
    access: AccessContext,
    *,
    title: str,
    description: str,
    assigned_to: uuid.UUID | None,
    due_at: datetime,
    source_type: str,
    source_id: str,
    priority: str = "high",
) -> Task:
    task = Task(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        title=title[:300],
        description=description[:1500],
        assigned_to=assigned_to,
        due_at=due_at,
        priority=priority,
        status="open",
        source_type=source_type,
        source_id=source_id,
    )
    db.add(task)
    db.flush()
    return task


def _close_tasks(db: Session, source_types: tuple[str, ...], source_ids: tuple[str, ...]) -> None:
    for t in db.scalars(
        select(Task).where(
            Task.source_type.in_(source_types), Task.source_id.in_(source_ids), Task.status == "open"
        )
    ):
        t.status = "done"


# -------------------------------------------------------------- authoring
def _validate_version_payload(
    db: Session, *, trigger_rules: list[dict], eligibility_rules: list[dict], steps: list[dict]
) -> None:
    if not trigger_rules:
        raise EmergencyError("A protocol version needs at least one trigger rule.")
    for r in trigger_rules:
        if r.get("operator", "present") not in em.TRIGGER_OPERATORS:
            raise EmergencyError(f"Trigger operator must be one of {list(em.TRIGGER_OPERATORS)}")
        if r.get("operator") in (">=", ">", "<=", "<") and r.get("threshold_numeric") is None:
            raise EmergencyError(
                f"Trigger '{r.get('observation_code')}' compares a number but has no threshold."
            )
        if (r.get("weight") or 0) < 0:
            raise EmergencyError("Trigger weights cannot be negative.")
    for r in eligibility_rules:
        if r.get("rule_type") not in em.ELIGIBILITY_RULE_TYPES:
            raise EmergencyError(f"Eligibility rule type must be one of {list(em.ELIGIBILITY_RULE_TYPES)}")
        if r.get("failure_action", "WARN") not in em.FAILURE_ACTIONS:
            raise EmergencyError(f"failure_action must be one of {list(em.FAILURE_ACTIONS)}")
    if not steps:
        raise EmergencyError("A protocol version needs at least one step.")
    numbers = [s["step_no"] for s in steps]
    if len(set(numbers)) != len(numbers):
        raise EmergencyError("Step numbers must be unique.")
    for s in steps:
        if s.get("step_type") not in em.STEP_TYPES:
            raise EmergencyError(f"step_type must be one of {list(em.STEP_TYPES)}")
        med = s.get("medication")
        if s["step_type"] == "MEDICATION":
            if not med:
                raise EmergencyError(
                    f"Step {s['step_no']} is a medication step without an approved medication instruction."
                )
            product_pk = parse_uuid(med.get("medicine_product_id"))
            product = db.get(MedicineProduct, product_pk) if product_pk else None
            if product is None or product.deleted_at is not None:
                raise EmergencyError(
                    f"Step {s['step_no']}: medicine product not found in this farm's pharmacy."
                )
            if med["dose_rule_type"] not in em.DOSE_RULE_TYPES:
                raise EmergencyError("dose_rule_type must be FIXED or PER_WEIGHT")
            if med["dose_rule_type"] == "FIXED" and med.get("fixed_dose_quantity") is None:
                raise EmergencyError("A FIXED dose rule needs fixed_dose_quantity.")
            if med["dose_rule_type"] == "PER_WEIGHT" and (
                med.get("dose_per_weight_quantity") is None or not med.get("weight_unit")
            ):
                raise EmergencyError("A PER_WEIGHT dose rule needs dose_per_weight_quantity and weight_unit.")
            if med["route_code"] not in ROUTES:
                raise EmergencyError(f"route_code must be one of {list(ROUTES)}")
            if product.administration_routes and med["route_code"] not in product.administration_routes:
                raise EmergencyError(
                    f"Step {s['step_no']}: {med['route_code']} is not an authorised route for this medicine "
                    f"({', '.join(product.administration_routes)})."
                )
            lo, hi = med.get("minimum_dose_quantity"), med.get("maximum_dose_quantity")
            if lo is not None and hi is not None and lo > hi:
                raise EmergencyError("minimum_dose_quantity cannot exceed maximum_dose_quantity.")
            # Units are references (FOUNDATION uom).
            med["dose_unit"] = uom_service.require(db, med.get("dose_unit"), what="dose unit")
            if med.get("weight_unit"):
                med["weight_unit"] = uom_service.require(db, med["weight_unit"], what="weight unit")
            if (med.get("weight_max_age_days") or 0) < 0:
                raise EmergencyError("weight_max_age_days cannot be negative.")
        elif med:
            raise EmergencyError(
                f"Step {s['step_no']} is not a medication step but carries a medication instruction."
            )


def add_version(
    db: Session,
    access: AccessContext,
    protocol: em.EmergencyProtocol,
    *,
    trigger_rules: list,
    eligibility_rules: list,
    steps: list,
    protocol_text: str | None = None,
    minimum_match_confidence: float = 0.6,
    requires_manager_notification: bool = True,
    requires_vet_notification: bool = True,
    requires_pre_action_confirmation: bool = True,
    offline_eligible: bool = False,
    reassessment_minutes: int | None = None,
) -> em.EmergencyProtocolVersion:
    """A new draft version (§3). Nothing here is operational until a
    veterinarian approves it."""
    trigger_rules = [r if isinstance(r, dict) else r.model_dump() for r in trigger_rules]
    eligibility_rules = [r if isinstance(r, dict) else r.model_dump() for r in eligibility_rules]
    steps = [s if isinstance(s, dict) else s.model_dump() for s in steps]
    _validate_version_payload(
        db, trigger_rules=trigger_rules, eligibility_rules=eligibility_rules, steps=steps
    )
    if not 0 < minimum_match_confidence <= 1:
        raise EmergencyError("minimum_match_confidence must be between 0 and 1.")
    last = (
        db.scalar(
            select(func.max(em.EmergencyProtocolVersion.version_no)).where(
                em.EmergencyProtocolVersion.emergency_protocol_id == protocol.id
            )
        )
        or 0
    )
    base = dict(tenant_id=access.tenant_id, last_modified_by=access.membership_id)
    v = em.EmergencyProtocolVersion(
        **base,
        emergency_protocol_id=protocol.id,
        version_no=last + 1,
        protocol_text=protocol_text,
        minimum_match_confidence=minimum_match_confidence,
        requires_manager_notification=requires_manager_notification,
        requires_vet_notification=requires_vet_notification,
        requires_pre_action_confirmation=requires_pre_action_confirmation,
        offline_eligible=offline_eligible,
        reassessment_minutes=reassessment_minutes,
        status="draft",
        created_by=access.user_id,
    )
    for r in trigger_rules:
        v.trigger_rules.append(
            em.ProtocolTriggerRule(
                **base,
                observation_code=r["observation_code"],
                operator=r.get("operator", "present"),
                threshold_numeric=r.get("threshold_numeric"),
                threshold_unit=r.get("threshold_unit"),
                expected_value_code=r.get("expected_value_code"),
                required=bool(r.get("required")),
                weight=float(r.get("weight", 1.0) or 0),
                danger_sign=bool(r.get("danger_sign")),
                label=r.get("label"),
            )
        )
    for r in eligibility_rules:
        v.eligibility_rules.append(
            em.ProtocolEligibilityRule(
                **base,
                rule_type=r["rule_type"],
                operator=r.get("operator", "=="),
                rule_payload=r.get("rule_payload") or {},
                failure_action=r.get("failure_action", "WARN"),
                message=r.get("message"),
            )
        )
    for s in sorted(steps, key=lambda s: s["step_no"]):
        step = em.ProtocolStep(
            **base,
            step_no=s["step_no"],
            step_type=s["step_type"],
            title=s["title"],
            instructions=s["instructions"],
            required=bool(s.get("required", True)),
            requires_confirmation=bool(s.get("requires_confirmation", True)),
            timing_offset_minutes=s.get("timing_offset_minutes"),
        )
        med = s.get("medication")
        if med:
            step.medication = em.ProtocolMedicationStep(
                **base,
                medicine_product_id=parse_uuid(med["medicine_product_id"]),
                route_code=med["route_code"],
                dose_rule_type=med["dose_rule_type"],
                fixed_dose_quantity=med.get("fixed_dose_quantity"),
                dose_per_weight_quantity=med.get("dose_per_weight_quantity"),
                dose_unit=med["dose_unit"],
                weight_unit=med.get("weight_unit"),
                minimum_dose_quantity=med.get("minimum_dose_quantity"),
                maximum_dose_quantity=med.get("maximum_dose_quantity"),
                repeat_interval_minutes=med.get("repeat_interval_minutes"),
                maximum_administrations=med.get("maximum_administrations"),
                weight_max_age_days=med.get("weight_max_age_days"),
                withdrawal_rule_payload=med.get("withdrawal_rule_payload") or {},
            )
        v.steps.append(step)
    db.add(v)
    db.flush()
    db.expire(protocol, ["versions"])
    return v


def create_protocol(
    db: Session,
    access: AccessContext,
    *,
    code: str,
    title: str,
    condition_family_code: str,
    species_code: str | None = None,
    subject_scope: str = "animal",
    version: Any,
) -> em.EmergencyProtocol:
    if subject_scope not in ("animal", "group", "any"):
        raise EmergencyError("subject_scope must be animal, group or any")
    if not code or not code.strip():
        raise EmergencyError("A protocol needs a code.")
    if db.scalar(
        select(em.EmergencyProtocol.id).where(
            em.EmergencyProtocol.code == code.strip(), em.EmergencyProtocol.deleted_at.is_(None)
        )
    ):
        raise EmergencyError(f"A protocol with code {code} already exists.")
    if species_code and species_code not in capability_service.known_species_codes():
        raise EmergencyError(f"Unknown species '{species_code}'")
    p = em.EmergencyProtocol(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        code=code.strip(),
        title=title.strip(),
        condition_family_code=condition_family_code,
        species_code=species_code,
        subject_scope=subject_scope,
        status="draft",
        created_by=access.user_id,
    )
    db.add(p)
    db.flush()
    add_version(db, access, p, **(version if isinstance(version, dict) else version.model_dump()))
    return p


def approve_version(
    db: Session,
    access: AccessContext,
    version: em.EmergencyProtocolVersion,
    *,
    approval_reference: str,
    effective_from: datetime | None = None,
) -> em.EmergencyProtocolVersion:
    """Veterinary approval (§3): the only way a version becomes
    operational. Written once; a later change is a new version."""
    if access.role != "veterinarian":
        raise EmergencyError(
            "Only a veterinarian can approve an emergency protocol. Managers may draft and withdraw; "
            "approval is clinical.",
            403,
        )
    if version.status != "draft":
        raise EmergencyError(
            f"Version {version.version_no} is {version.status}; only a draft can be approved."
        )
    if not approval_reference or not approval_reference.strip():
        raise EmergencyError(
            "An approval reference is required (the vet's note, licence or document number)."
        )
    protocol = version.protocol
    at = now_utc()
    for other in protocol.versions:
        if other.id != version.id and other.status == "approved" and other.effective_to is None:
            other.effective_to = at
    version.status = "approved"
    version.approved_by_veterinarian_id = access.user_id
    version.approved_at = at
    version.approval_reference = approval_reference.strip()
    version.effective_from = ensure_utc(effective_from) or at
    version.last_modified_by = access.membership_id
    version.version += 1
    protocol.current_version_id = version.id
    protocol.status = "active"
    protocol.version += 1
    return version


def withdraw_version(
    db: Session, access: AccessContext, version: em.EmergencyProtocolVersion, *, reason: str
) -> em.EmergencyProtocolVersion:
    if version.status == "withdrawn":
        return version
    if not reason or not reason.strip():
        raise EmergencyError("Say why the version is withdrawn.")
    protocol = version.protocol
    version.status = "withdrawn"
    version.effective_to = now_utc()
    version.withdrawn_reason = reason.strip()
    version.last_modified_by = access.membership_id
    version.version += 1
    if protocol.current_version_id == version.id:
        protocol.current_version_id = None
        protocol.status = "withdrawn"
        protocol.version += 1
    return version


def current_versions(db: Session, *, at: datetime | None = None) -> list[em.EmergencyProtocolVersion]:
    out = []
    for p in db.scalars(
        select(em.EmergencyProtocol).where(
            em.EmergencyProtocol.status == "active", em.EmergencyProtocol.deleted_at.is_(None)
        )
    ):
        v = db.get(em.EmergencyProtocolVersion, p.current_version_id) if p.current_version_id else None
        if v is not None and version_is_current(v, at):
            out.append(v)
    return out


# ------------------------------------------------------------------ facts
def _weight_facts(db: Session, animal: Animal) -> tuple[float | None, int | None]:
    """(weight, age in days) of the most recent dated weight, or (None, None)."""
    rec = livestock_service.latest_weight(db, animal.id)
    if rec is None:
        return None, None
    measured = ensure_utc(rec.measured_at) or now_utc()
    return float(rec.weight_kg), int((now_utc() - measured).total_seconds() // 86400)


def _animal_facts(db: Session, subject_type: str, subject: Any) -> dict:
    """The authoritative facts the eligibility rules read (§5): the
    animal record, its dated weight, open diagnoses, recorded allergies /
    adverse reactions / conditions, recent medication and withdrawal."""
    at = now_utc()
    if subject_type == "animal":
        a: Animal = subject
        birth = ensure_utc(a.birth_date)
        age_days = int((at - birth).total_seconds() // 86400) if birth else None
        recent = db.scalars(
            select(MedicationAdministration).where(
                MedicationAdministration.subject_id == a.id,
                MedicationAdministration.status == "recorded",
                MedicationAdministration.administered_at >= at - timedelta(days=7),
            )
        ).all()
        treatments = (
            db.scalar(
                select(func.count(Treatment.id)).where(
                    Treatment.entity_type == "animal",
                    Treatment.entity_id == str(a.id),
                    Treatment.status == "active",
                    Treatment.deleted_at.is_(None),
                )
            )
            or 0
        )
        weight, weight_age = _weight_facts(db, a)
        if weight is None and a.weight_kg is not None:
            weight = float(a.weight_kg)
        conditions = livestock_service.active_conditions(db, a.id)
        case = cases.current_case(db, "animal", a.id)
        withdrawal_until = ensure_utc(a.withdrawal_until)
        return {
            "subject_type": "animal",
            "species": a.species,
            "sex": a.sex,
            "life_stage": a.life_stage,
            "management_profile": a.management_profile,
            "weight_kg": weight,
            "weight_known": weight is not None,
            "weight_age_days": weight_age,
            "weight_source": "record"
            if weight_age is not None
            else ("animal_record" if weight is not None else None),
            "pregnant": bool(a.pregnant),
            "lactating": bool(a.lactating),
            "withdrawal_active": bool(withdrawal_until and withdrawal_until > at),
            "withdrawal_until": withdrawal_until.isoformat() if withdrawal_until else None,
            "status": a.status,
            "age_days": age_days,
            "health_score": a.health_score,
            "active_treatments": treatments,
            "recent_medications": [
                {
                    "inventory_item_id": str(r.inventory_item_id),
                    "administered_at": (ensure_utc(r.administered_at) or at).isoformat(),
                    "route": r.route_code,
                }
                for r in recent
            ],
            "active_diagnoses": cases.active_diagnosis_codes(db, "animal", a.id),
            "health_case_id": str(case.id) if case else None,
            "allergies": sorted({c.code for c in conditions if c.condition_type in ALLERGY_TYPES}),
            "conditions": sorted({c.code for c in conditions if c.condition_type == "chronic_condition"}),
        }
    g: AnimalGroup = subject
    case = cases.current_case(db, "group", g.id)
    return {
        "subject_type": "group",
        "species": g.species,
        "sex": g.sex_composition,
        "life_stage": g.life_stage,
        "management_profile": g.management_profile,
        "weight_kg": None,
        "weight_known": False,
        "weight_age_days": None,
        "weight_source": None,
        "pregnant": False,
        "lactating": False,
        "withdrawal_active": False,
        "status": g.status,
        "age_days": None,
        "count": g.count,
        "active_treatments": 0,
        "recent_medications": [],
        "active_diagnoses": cases.active_diagnosis_codes(db, "group", g.id),
        "health_case_id": str(case.id) if case else None,
        "allergies": [],
        "conditions": [],
    }


def _normalise_code(code: str | None) -> str:
    c = (code or "").strip().lower().replace(" ", "_")
    return OBSERVATION_CODE_ALIASES.get(c, c)


def _sign_values(db: Session, subject_type: str, subject: Any, signs: list) -> dict[str, dict]:
    """Explicit signs win; recent observations on the subject fill in the
    rest, each tagged with its source so the explanation can say so."""
    out: dict[str, dict] = {}
    since = now_utc() - timedelta(hours=OBSERVATION_LOOKBACK_HOURS)
    entity_types = ("animal",) if subject_type == "animal" else GROUP_ENTITY_TYPES
    rows = db.scalars(
        select(Observation)
        .where(
            Observation.entity_type.in_(entity_types),
            Observation.entity_id == str(subject.id),
            Observation.observed_at >= since,
            Observation.deleted_at.is_(None),
        )
        .order_by(Observation.observed_at)
    ).all()
    for o in rows:
        code = _normalise_code(o.observation_type)
        out[code] = {
            "code": code,
            "value_numeric": float(o.value_numeric) if o.value_numeric is not None else None,
            "value_text": o.value_text,
            "unit": o.unit,
            "present": True,
            "severity": o.severity,
            "source": "observation",
            "observed_at": (ensure_utc(o.observed_at) or now_utc()).isoformat(),
            "observation_id": str(o.id),
        }
    for s in signs:
        s = s if isinstance(s, dict) else s.model_dump()
        code = _normalise_code(s["code"])
        if s.get("present", True) is False:
            out.pop(code, None)
            out[code] = {"code": code, "present": False, "source": "reported"}
            continue
        out[code] = {
            "code": code,
            "value_numeric": s.get("value_numeric"),
            "value_text": (s.get("value_text") or "").strip().lower() or None,
            "unit": s.get("unit"),
            "present": True,
            "source": "reported",
            "observed_at": now_utc().isoformat(),
        }
    return out


def _eval_trigger(rule: em.ProtocolTriggerRule, sign: dict | None) -> tuple[bool, str]:
    present = sign is not None and sign.get("present", True)
    label = rule.label or rule.observation_code
    if rule.operator == "present":
        return present, f"{label}: {'reported' if present else 'not reported'}"
    if rule.operator == "absent":
        return not present, f"{label}: {'absent' if not present else 'present'}"
    if not present or sign is None:
        return False, f"{label}: not reported"
    unit = rule.threshold_unit or ""
    if rule.operator in (">=", ">", "<=", "<"):
        v = sign.get("value_numeric")
        if v is None:
            return False, f"{label}: no measurement given"
        t = rule.threshold_numeric or 0.0
        ok = {">=": v >= t, ">": v > t, "<=": v <= t, "<": v < t}[rule.operator]
        return ok, f"{label}: {v:g}{unit} {rule.operator} {t:g}{unit} → {'yes' if ok else 'no'}"
    expected = (rule.expected_value_code or "").strip().lower()
    actual = (sign.get("value_text") or "").strip().lower()
    if actual == "" and sign.get("value_numeric") is not None and rule.threshold_numeric is not None:
        same = sign["value_numeric"] == rule.threshold_numeric
        ok = same if rule.operator == "==" else not same
        return ok, (
            f"{label}: {sign['value_numeric']:g} {rule.operator} {rule.threshold_numeric:g} "
            f"→ {'yes' if ok else 'no'}"
        )
    ok = (actual == expected) if rule.operator == "==" else (actual != expected)
    return ok, f"{label}: '{actual or '—'}' {rule.operator} '{expected}' → {'yes' if ok else 'no'}"


def _score(version: em.EmergencyProtocolVersion, signs: dict[str, dict]) -> dict:
    matched: list[dict] = []
    unmatched: list[dict] = []
    required_missing: list[dict] = []
    danger: list[dict] = []
    total = sum(r.weight for r in version.trigger_rules if not r.danger_sign) or 1.0
    got = 0.0
    for r in version.trigger_rules:
        sign = signs.get(_normalise_code(r.observation_code))
        ok, detail = _eval_trigger(r, sign)
        entry = {
            "code": r.observation_code,
            "detail": detail,
            "weight": r.weight,
            "required": r.required,
            "danger_sign": r.danger_sign,
            "source": (sign or {}).get("source"),
        }
        if r.danger_sign:
            if ok:
                danger.append(entry)
            continue
        if ok:
            got += r.weight
            matched.append(entry)
        else:
            unmatched.append(entry)
            if r.required:
                required_missing.append(entry)
    return {
        "score": round(got / total, 4),
        "matched": matched,
        "unmatched": unmatched,
        "required_missing": required_missing,
        "danger_signs": danger,
    }


def _eval_eligibility(version: em.EmergencyProtocolVersion, facts: dict) -> tuple[str, list[dict]]:
    outcomes = []
    for r in version.eligibility_rules:
        p = r.rule_payload or {}
        value: Any = facts.get(r.rule_type)
        passed = True
        if r.rule_type in ("species", "sex", "life_stage", "management_profile", "status", "subject_type"):
            if "in" in p:
                passed = value in p["in"]
            elif "not_in" in p:
                passed = value not in p["not_in"]
            elif "value" in p:
                passed = (value == p["value"]) if r.operator in ("==", "in") else (value != p["value"])
        elif r.rule_type in ("weight_kg", "age_days"):
            if value is None:
                passed = not p.get("required", True)
            else:
                lo, hi = p.get("min"), p.get("max")
                passed = (lo is None or value >= lo) and (hi is None or value <= hi)
        elif r.rule_type in ("weight_known", "pregnant", "lactating", "withdrawal_active"):
            passed = bool(value) == bool(p.get("value", True))
        elif r.rule_type == "recent_medication":
            within = float(p.get("within_hours", 24))
            product = p.get("not")
            cutoff = now_utc() - timedelta(hours=within)
            passed = not any(
                m["inventory_item_id"] == product
                and (ensure_utc(datetime.fromisoformat(m["administered_at"])) or cutoff) >= cutoff
                for m in facts.get("recent_medications", [])
            )
        elif r.rule_type in ("active_diagnosis", "allergy", "condition"):
            # Authoritative clinical facts (HEALTH-VETERINARY-SCHEMA): the
            # codes on open diagnoses, recorded allergies / adverse reactions
            # / contraindications, and chronic conditions.
            key = {"active_diagnosis": "active_diagnoses", "allergy": "allergies", "condition": "conditions"}[
                r.rule_type
            ]
            have = {str(x).lower() for x in (facts.get(key) or [])}
            listed = {str(x).lower() for x in (p.get("not_in") or ([p["not"]] if p.get("not") else []))}
            wanted = {str(x).lower() for x in (p.get("in") or [])}
            if listed:
                passed = not (have & listed)
            elif wanted:
                passed = bool(have & wanted)
            elif "any" in p:
                passed = bool(have) == bool(p["any"])
            else:
                passed = not have
            value = sorted(have)
        elif r.rule_type == "weight_age_days":
            age = facts.get("weight_age_days")
            if age is None:
                passed = not p.get("required", True)
            else:
                passed = age <= float(p.get("max", 30))
            value = age
        if isinstance(value, list) and r.rule_type not in ("active_diagnosis", "allergy", "condition"):
            value = len(value)
        outcomes.append(
            {
                "rule_type": r.rule_type,
                "passed": passed,
                "failure_action": r.failure_action,
                "message": r.message or f"{r.rule_type} check {'passed' if passed else 'failed'}",
                "fact": value,
            }
        )
    result = _worst([o["failure_action"] for o in outcomes if not o["passed"]])
    return result, outcomes


# ------------------------------------------------------------- assessment
def _bell_assessment(db: Session, access: AccessContext, a: em.EmergencyAssessment, subject: Any) -> None:
    escalated = a.status == "escalated"
    name = getattr(subject, "name", "")
    routing.notify_bell(
        db,
        access.tenant_id,
        module_code="animal_health",
        notification_type="emergency_escalated" if escalated else "emergency_protocol_matched",
        title=(
            f"EMERGENCY: {name} — call the veterinarian"
            if escalated
            else f"{name}: protocol matched, awaiting confirmation"
        ),
        description=a.explanation[:300],
        priority="critical" if escalated else "high",
        entity_type="animal" if a.animal_id else "flock",
        entity_id=str(a.animal_id or a.animal_group_id),
        source_type="emergency_assessment",
        source_id=str(a.id),
        actor=access.membership_id,
    )


def assess(
    db: Session,
    access: AccessContext,
    *,
    subject_type: str,
    subject_id: str | uuid.UUID,
    signs: list,
    source: str = "worker",
    notes: str | None = None,
    parent_run_id: uuid.UUID | None = None,
    only_version: em.EmergencyProtocolVersion | None = None,
    started_at: datetime | None = None,
    device_id: str | None = None,
    offline_operation_id: str | None = None,
    package_signature: str | None = None,
    health_case_id: uuid.UUID | None = None,
) -> em.EmergencyAssessment:
    """Triage (§12): score every current approved protocol, check
    eligibility, rank urgency, select or escalate. Everything used is
    kept on the assessment for audit (§15). An escalation opens (or
    joins) the subject's health case."""
    subject = _subject(db, subject_type, subject_id)
    at = now_utc()
    sign_map = _sign_values(db, subject_type, subject, signs)
    facts = _animal_facts(db, subject_type, subject)
    versions = [only_version] if only_version is not None else current_versions(db, at=at)
    versions = [
        v
        for v in versions
        if v.protocol.subject_scope in ("any", subject_type)
        and v.protocol.species_code in (None, facts["species"])
    ]

    assessment = em.EmergencyAssessment(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        animal_id=subject.id if subject_type == "animal" else None,
        animal_group_id=subject.id if subject_type == "group" else None,
        parent_run_id=parent_run_id,
        started_at=ensure_utc(started_at) or at,
        source=source,
        observed_signs=list(sign_map.values()),
        animal_facts=facts,
        ai_model_reference=MODEL_REFERENCE,
        status="open",
        created_by=access.user_id,
        device_id=device_id,
        offline_operation_id=offline_operation_id,
        package_signature=package_signature,
        health_case_id=health_case_id or parse_uuid(facts.get("health_case_id")),
    )
    db.add(assessment)
    db.flush()

    candidates: list[tuple[em.EmergencyProtocolVersion, em.ProtocolMatch, dict]] = []
    any_danger: list[str] = []
    for v in versions:
        scoring = _score(v, sign_map)
        eligibility, outcomes = _eval_eligibility(v, facts)
        blocking = [
            o["message"]
            for o in outcomes
            if not o["passed"] and o["failure_action"] in ("BLOCK", "REQUIRE_VET")
        ]
        warnings = [o["message"] for o in outcomes if not o["passed"] and o["failure_action"] == "WARN"]
        m = em.ProtocolMatch(
            tenant_id=access.tenant_id,
            last_modified_by=access.membership_id,
            emergency_assessment_id=assessment.id,
            protocol_version_id=v.id,
            match_score=scoring["score"],
            match_explanation={
                "protocol_code": v.protocol.code,
                "protocol_title": v.protocol.title,
                "version_no": v.version_no,
                "minimum_match_confidence": v.minimum_match_confidence,
                "supporting": scoring["matched"],
                "missing": scoring["unmatched"],
                "required_missing": scoring["required_missing"],
                "danger_signs": scoring["danger_signs"],
                "eligibility": outcomes,
                "warnings": warnings,
                "facts_used": sorted(k for k in facts if facts[k] is not None),
            },
            eligibility_result=eligibility,
            blocking_reasons=blocking,
            selected=False,
            evaluated_at=at,
        )
        db.add(m)
        candidates.append((v, m, scoring))
        any_danger += [f"{v.protocol.code}: {d['detail']}" for d in scoring["danger_signs"]]
    db.flush()
    db.expire(assessment, ["matches"])

    reasons: list[str] = []
    severe = any((s.get("severity") in SEVERE_OBSERVATION_SEVERITIES) for s in sign_map.values())
    best: em.ProtocolMatch | None = None
    for v, m, scoring in sorted(candidates, key=lambda c: -(c[1].match_score or 0)):
        if (m.match_score or 0) < v.minimum_match_confidence:
            continue
        if scoring["required_missing"]:
            reasons.append(
                f"{v.protocol.code}: required observation missing — "
                + ", ".join(e["code"] for e in scoring["required_missing"])
            )
            continue
        if m.eligibility_result in ("BLOCK", "REQUIRE_VET"):
            what = "blocked" if m.eligibility_result == "BLOCK" else "requires the veterinarian"
            reasons.append(f"{v.protocol.code}: {what} — " + "; ".join(m.blocking_reasons))
            continue
        best = m
        break
    pattern = any((m.match_score or 0) > 0 for _, m, _ in candidates)

    if any_danger:
        triage = "CRITICAL"
        reasons = [f"danger sign — {d}" for d in any_danger] + reasons
    elif best is not None:
        triage = "HIGH" if (severe or (best.match_score or 0) >= 0.8) else "MODERATE"
    elif pattern:
        triage = "HIGH" if severe else "MODERATE"
    else:
        triage = "HIGH" if severe else "LOW"
    assessment.triage_level = triage
    assessment.ai_confidence = max((m.match_score or 0) for _, m, _ in candidates) if candidates else 0.0

    if any_danger or best is None:
        if not candidates:
            reasons.append("no current veterinarian-approved protocol exists for this species / subject")
        elif best is None and not reasons:
            reasons.append(
                "no approved protocol matched above its confidence threshold — "
                + "; ".join(
                    f"{v.protocol.code} {m.match_score or 0:.2f} < {v.minimum_match_confidence:.2f}"
                    for v, m, _ in candidates
                )
            )
        _escalate_assessment(db, access, assessment, subject_type, subject, reasons)
    else:
        best.selected = True
        assessment.status = "open"
        v = get_version(db, best.protocol_version_id)
        warn = best.match_explanation.get("warnings") or []
        approved = f"{v.approved_at:%Y-%m-%d}" if v.approved_at else "—"
        assessment.explanation = (
            f"{getattr(subject, 'name', subject_id)}: {v.protocol.title} "
            f"(v{v.version_no}, approved {approved}, "
            f"ref {v.approval_reference}) matched at {best.match_score or 0:.2f} "
            f"≥ {v.minimum_match_confidence:.2f}. "
            "Supported by: "
            + "; ".join(e["detail"] for e in best.match_explanation["supporting"])
            + (
                ". Not reported: " + "; ".join(e["code"] for e in best.match_explanation["missing"])
                if best.match_explanation["missing"]
                else ""
            )
            + (". Warnings: " + "; ".join(warn) if warn else "")
            + ". Confirm to start the protocol; a person decides."
        )
        _bell_assessment(db, access, assessment, subject)
    if notes:
        assessment.explanation = (assessment.explanation + f" Notes: {notes}").strip()
    return assessment


def _ensure_case(
    db: Session,
    access: AccessContext,
    assessment: em.EmergencyAssessment,
    subject_type: str,
    subject: Any,
    *,
    source_type: str,
    problem: str | None,
    floor: str = "low",
) -> HealthCase:
    """The clinical episode (HEALTH-VETERINARY-SCHEMA health_case) the
    emergency belongs to: the subject's open case, or one the engine
    opens — never a twin. The engine records no diagnosis on it. `floor`
    is the least severity the case opens at: an escalation ("call the
    veterinarian now") is never a low-severity episode, whatever the
    triage said about the signs."""
    severity = CASE_SEVERITY.get(assessment.triage_level, "moderate")
    if cases.severity_rank(severity) < cases.severity_rank(floor):
        severity = floor
    case = cases.open_or_current_case(
        db,
        access,
        subject_type=subject_type,
        subject_id=subject.id,
        severity_code=severity,
        primary_problem_code=problem,
        source_type=source_type,
        source_id=str(assessment.id),
    )
    assessment.health_case_id = case.id
    return case


def _escalate_assessment(
    db: Session,
    access: AccessContext,
    assessment: em.EmergencyAssessment,
    subject_type: str,
    subject: Any,
    reasons: list[str],
) -> None:
    """Escalate rather than improvise (§13): the user is told, visibly,
    to contact the veterinarian / manager; a task to the routed person
    carries it; the health case is open."""
    assessment.status = "escalated"
    assessment.escalation_reasons = list(reasons)
    name = getattr(subject, "name", str(assessment.animal_id or assessment.animal_group_id))
    assessment.explanation = (
        f"{name}: ESCALATE — contact the veterinarian / manager now. "
        + " | ".join(reasons)
        + ". No drug, dose or route is shown because no current approved protocol applies."
    )
    recipient = routing.primary_recipient(
        access.tenant_id, "emergency_escalation", prefer_role="veterinarian", db=db
    )
    _task(
        db,
        access,
        title=f"EMERGENCY: {name} — call the veterinarian",
        description=assessment.explanation,
        assigned_to=recipient.user_id if recipient else None,
        due_at=now_utc(),
        source_type=ESCALATION_TASK,
        source_id=str(assessment.id),
    )
    if assessment.source != "reassessment":
        _ensure_case(
            db,
            access,
            assessment,
            subject_type,
            subject,
            source_type="emergency_assessment",
            problem=None,
            floor="moderate",
        )
    _bell_assessment(db, access, assessment, subject)


def on_observation(
    db: Session, access: AccessContext, observation: Observation
) -> em.EmergencyAssessment | None:
    """A severe observation starts triage by itself (§12, acceptance 1),
    unless one is already open for the subject."""
    if observation.severity not in SEVERE_OBSERVATION_SEVERITIES:
        return None
    if observation.entity_type == "animal":
        subject_type = "animal"
    elif observation.entity_type in GROUP_ENTITY_TYPES:
        subject_type = "group"
    else:
        return None
    pk = parse_uuid(observation.entity_id)
    if pk is None:
        return None
    col = (
        em.EmergencyAssessment.animal_id
        if subject_type == "animal"
        else em.EmergencyAssessment.animal_group_id
    )
    # An open, running or already-escalated case in the window is the case;
    # a second severe observation adds to it rather than opening a twin.
    recent = db.scalar(
        select(em.EmergencyAssessment).where(
            col == pk,
            em.EmergencyAssessment.status.in_(("open", "protocol_started", "escalated")),
            em.EmergencyAssessment.started_at >= now_utc() - timedelta(hours=DUPLICATE_WINDOW_HOURS),
        )
    )
    if recent is not None:
        return None
    try:
        return assess(
            db, access, subject_type=subject_type, subject_id=pk, signs=[], source="observation_hook"
        )
    except FeedError:
        return None


# -------------------------------------------------------------------- run
def start_run(
    db: Session,
    access: AccessContext,
    assessment: em.EmergencyAssessment,
    *,
    match_id: str | None = None,
    confirmed: bool = True,
    device_id: str | None = None,
    started_at: datetime | None = None,
    offline_operation_id: str | None = None,
    package_signature: str | None = None,
) -> em.EmergencyProtocolRun:
    if assessment.status != "open":
        raise EmergencyError(
            f"This assessment is {assessment.status}; "
            "a protocol can only start from an open, matched assessment."
        )
    wanted = parse_uuid(match_id) if match_id else None
    match = next((m for m in assessment.matches if (m.id == wanted if wanted else m.selected)), None)
    if match is None:
        raise EmergencyError("No selected protocol match on this assessment.", 404)
    if match.eligibility_result in ("BLOCK", "REQUIRE_VET"):
        raise EmergencyError(
            "This protocol is blocked for the animal or requires the veterinarian; it cannot be started."
        )
    version = get_version(db, match.protocol_version_id)
    subject_type = "animal" if assessment.animal_id else "group"
    subject = _subject(db, subject_type, assessment.animal_id or assessment.animal_group_id)
    if not version_is_current(version):
        _escalate_assessment(
            db,
            access,
            assessment,
            subject_type,
            subject,
            [f"{version.protocol.code} v{version.version_no} is no longer current (expired or withdrawn)"],
        )
        raise EmergencyError(
            "The matched protocol version is no longer current; the case has been escalated."
        )
    if version.requires_pre_action_confirmation and not confirmed:
        raise EmergencyError("This protocol requires the person at the animal to confirm before any action.")
    at = ensure_utc(started_at) or now_utc()
    case = _ensure_case(
        db,
        access,
        assessment,
        subject_type,
        subject,
        source_type="emergency_run",
        problem=version.protocol.condition_family_code,
    )
    run = em.EmergencyProtocolRun(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        emergency_assessment_id=assessment.id,
        protocol_version_id=version.id,
        animal_id=assessment.animal_id,
        animal_group_id=assessment.animal_group_id,
        health_case_id=case.id,
        started_at=at,
        started_by=access.user_id,
        status="in_progress",
        device_id=device_id,
        offline=bool(offline_operation_id),
        offline_operation_id=offline_operation_id,
        package_signature=package_signature,
    )
    for i, s in enumerate(version.steps):
        run.steps.append(
            em.ProtocolRunStep(
                tenant_id=access.tenant_id,
                last_modified_by=access.membership_id,
                protocol_step_id=s.id,
                sequence=i,
                status="pending",
                due_at=at + timedelta(minutes=s.timing_offset_minutes) if s.timing_offset_minutes else None,
            )
        )
    reassess_steps = [s for s in version.steps if s.step_type == "REASSESS" and s.timing_offset_minutes]
    if version.reassessment_minutes:
        run.next_reassessment_at = at + timedelta(minutes=version.reassessment_minutes)
    elif reassess_steps:
        run.next_reassessment_at = at + timedelta(minutes=reassess_steps[0].timing_offset_minutes or 0)
    db.add(run)
    assessment.status = "protocol_started"
    db.flush()
    routing.clear_bell(db, source_type="emergency_assessment", source_id=str(assessment.id), at=now_utc())
    _notify(db, access, run, version, subject)
    return run


def _notify(
    db: Session,
    access: AccessContext,
    run: em.EmergencyProtocolRun,
    version: em.EmergencyProtocolVersion,
    subject: Any,
) -> None:
    """Manager / veterinarian notification as the version requires
    (acceptance 5): a task to the person each route names (managers /
    veterinarians by default) and the timestamps on the run."""
    name = getattr(subject, "name", "")
    at = now_utc()
    for flag, who, route, field in (
        (version.requires_manager_notification, "manager", "emergency_manager", "manager_notified_at"),
        (
            version.requires_vet_notification,
            "veterinarian",
            "emergency_veterinarian",
            "veterinarian_notified_at",
        ),
    ):
        if not flag:
            continue
        recipient = routing.primary_recipient(access.tenant_id, route, prefer_role=who, db=db)
        description = (
            f"{version.protocol.code} v{version.version_no} started at {at:%H:%M} "
            "by the person at the animal. "
            "Review the case."
        )
        if recipient is None:
            description += (
                f" No {who} account is routed for this farm — set a notification route under Settings."
            )
        _task(
            db,
            access,
            title=f"Emergency protocol started: {name} — {version.protocol.title} ({who})",
            description=description,
            assigned_to=recipient.user_id if recipient else None,
            due_at=at,
            source_type=NOTIFY_TASK,
            source_id=str(run.id),
        )
        setattr(run, field, at)
    db.flush()


def _run_subject(db: Session, run: em.EmergencyProtocolRun) -> tuple[str, Any]:
    subject_type = "animal" if run.animal_id else "group"
    return subject_type, _subject(db, subject_type, run.animal_id or run.animal_group_id)


def _step_of(run: em.EmergencyProtocolRun, run_step_id: str | uuid.UUID) -> em.ProtocolRunStep:
    pk = parse_uuid(run_step_id)
    s = next((s for s in run.steps if s.id == pk), None)
    if s is None:
        raise EmergencyError("Step not found on this run", 404)
    return s


def prepare_step(
    db: Session,
    access: AccessContext,
    run: em.EmergencyProtocolRun,
    run_step_id: str | uuid.UUID,
    *,
    weight_kg: float | None = None,
    head_count: int | None = None,
) -> em.ProtocolRunStep:
    """Presents a step. For a medication step: the approved dose from the
    approved rule and the animal's authoritative weight, inside the
    approved limits, with the exact eligible lot it would come from
    (acceptance 2–4). Anything else escalates."""
    if run.status not in ("in_progress", "awaiting_reassessment"):
        raise EmergencyError(f"This run is {run.status}.")
    rs = _step_of(run, run_step_id)
    if rs.status in ("completed", "skipped"):
        raise EmergencyError("This step is already done.")
    step = rs.step
    subject_type, subject = _run_subject(db, run)
    payload: dict[str, Any] = {
        "step_type": step.step_type,
        "title": step.title,
        "instructions": step.instructions,
    }
    if step.step_type == "MEDICATION":
        med = step.medication
        if med is None:
            raise EmergencyError("This medication step has no approved medication instruction.")
        product = ph.get_product(db, med.medicine_product_id)
        item = ph.item_of(db, product)
        if head_count is not None:
            heads = head_count
        else:
            heads = 1 if subject_type == "animal" else max(1, int(getattr(subject, "count", 1) or 1))
        problems: list[str] = []
        # Authoritative clinical facts first: a recorded allergy / adverse
        # reaction / contraindication to this product or its ingredients
        # blocks the step whatever the protocol says.
        if subject_type == "animal":
            allergies = {
                c.code
                for c in livestock_service.active_conditions(db, subject.id)
                if c.condition_type in ALLERGY_TYPES
            }
            ingredient_codes = {i.ingredient_code for i in product.ingredients} | {
                str(product.inventory_item_id).lower()
            }
            hit = allergies & ingredient_codes
            if hit:
                problems.append(
                    f"{subject.name} has a recorded allergy / adverse reaction to {', '.join(sorted(hit))}"
                )
        dose: float | None
        weight_used: float | None
        weight_source: str | None
        weight_age: int | None
        if med.dose_rule_type == "FIXED":
            dose = med.fixed_dose_quantity
            weight_used, weight_source, weight_age = None, None, None
        else:
            # Acceptance 3: the authoritative / recent weight according to
            # the protocol's rule. Entered now → recorded as a dated weight;
            # else the latest dated record, refused when older than the
            # protocol allows.
            weight_age = None
            if weight_kg is not None:
                if subject_type == "animal":
                    livestock_service.record_weight(
                        db,
                        subject,
                        access=access,
                        weight_kg=weight_kg,
                        method="point_of_care",
                        source_type="emergency_run_step",
                        source_id=str(rs.id),
                    )
                weight_used, weight_source, weight_age = weight_kg, "entered now", 0
            elif subject_type == "animal":
                weight_used, weight_age = _weight_facts(db, subject)
                if weight_used is None and subject.weight_kg is not None:
                    weight_used, weight_source = float(subject.weight_kg), "animal record (undated)"
                elif weight_used is not None:
                    weight_source = f"weight record {weight_age} day(s) old"
                else:
                    weight_source = None
            else:
                weight_used, weight_source = None, None
            if weight_used is None:
                problems.append("weight unknown — a per-weight dose cannot be calculated")
                dose = None
            else:
                if med.weight_max_age_days is not None and (
                    weight_age is None or weight_age > med.weight_max_age_days
                ):
                    problems.append(
                        f"the protocol requires a weight no older than {med.weight_max_age_days} day(s); the "
                        "recorded weight is "
                        + (f"{weight_age} day(s) old" if weight_age is not None else "undated")
                        + " — weigh the animal and enter the weight"
                    )
                dose = round((med.dose_per_weight_quantity or 0) * weight_used, 3)
        if dose is not None:
            if med.minimum_dose_quantity is not None and dose < med.minimum_dose_quantity:
                problems.append(
                    f"calculated dose {dose:g} {med.dose_unit} is below the approved minimum "
                    f"{med.minimum_dose_quantity:g}"
                )
            if med.maximum_dose_quantity is not None and dose > med.maximum_dose_quantity:
                problems.append(
                    f"calculated dose {dose:g} {med.dose_unit} exceeds the approved maximum "
                    f"{med.maximum_dose_quantity:g}"
                )
        prior = [
            s
            for s in run.steps
            if s.protocol_step_id == step.id and s.status == "completed" and s.medication_administration_id
        ]
        if med.maximum_administrations is not None and len(prior) >= med.maximum_administrations:
            problems.append(
                f"the approved maximum of {med.maximum_administrations} administration(s) is reached"
            )
        if prior and med.repeat_interval_minutes:
            last = max(ensure_utc(s.completed_at) or now_utc() for s in prior)
            if now_utc() < last + timedelta(minutes=med.repeat_interval_minutes):
                problems.append(f"the repeat interval of {med.repeat_interval_minutes} min has not passed")
        lot = None
        needed: float | None = None
        if dose is not None and not problems:
            if uom_service.compatible(db, med.dose_unit, item.unit):
                needed = round(uom_service.convert(db, dose * heads, med.dose_unit, item.unit), 6)
                lot = next(
                    (c for c in ph.eligible_lots(db, product) if c.quantity_on_hand + ph.EPS >= needed), None
                )
                if lot is None:
                    bd = ph.stock_breakdown(db, product)
                    problems.append(
                        f"{item.name}: no eligible lot holds {needed:g} {item.unit} "
                        f"(eligible {bd['eligible']:g}; on hand "
                        f"{bd['on_hand']:g}, expired {bd['expired']:g}, "
                        f"quarantined {bd['quarantined']:g}, storage "
                        f"exception {bd['storage_exception']:g})"
                    )
            else:
                problems.append(
                    f"dose unit {med.dose_unit} cannot be converted to the stock unit {item.unit}"
                )
        payload.update(
            {
                "medicine_product_id": str(product.inventory_item_id),
                "medicine_name": item.name,
                "route_code": med.route_code,
                "dose_rule_type": med.dose_rule_type,
                "dose_quantity": dose,
                "dose_unit": med.dose_unit,
                "weight_used_kg": weight_used,
                "weight_source": weight_source,
                "weight_age_days": weight_age,
                "head_count": heads,
                "quantity_consumed": needed,
                "stock_unit": item.unit,
                "lot_id": str(lot.id) if lot else None,
                "lot_code": lot.lot_code if lot else None,
                "lot_expiry": lot.expiry_date.isoformat() if lot and lot.expiry_date else None,
                "approved_min": med.minimum_dose_quantity,
                "approved_max": med.maximum_dose_quantity,
                "withdrawal_rule": med.withdrawal_rule_payload or product.withdrawal_rules_json,
                "problems": problems,
                "source": "approved protocol parameters",
            }
        )
        if problems:
            rs.status = "blocked"
            rs.result_payload = payload
            escalate_run(db, access, run, reason="medication step blocked: " + "; ".join(problems))
            return rs
    rs.status = "presented"
    rs.presented_at = now_utc()
    rs.result_payload = payload
    return rs


def confirm_step(
    db: Session,
    access: AccessContext,
    run: em.EmergencyProtocolRun,
    run_step_id: str | uuid.UUID,
    *,
    weight_kg: float | None = None,
    head_count: int | None = None,
    lot_id: str | None = None,
    result: dict | None = None,
    notes: str | None = None,
) -> em.ProtocolRunStep:
    """The person confirms the actual action (acceptance 6). A medication
    step records a prescription-provenance treatment and consumes the
    exact lot through the pharmacy; the other step types complete with
    their result. REASSESS schedules; ESCALATE escalates."""
    if run.status not in ("in_progress", "awaiting_reassessment"):
        raise EmergencyError(f"This run is {run.status}.")
    rs = _step_of(run, run_step_id)
    if rs.status in ("completed", "skipped"):
        raise EmergencyError("This step is already done.")
    step = rs.step
    subject_type, subject = _run_subject(db, run)
    version = get_version(db, run.protocol_version_id)
    if not version_is_current(version):
        escalate_run(
            db, access, run, reason=f"{version.protocol.code} v{version.version_no} is no longer current"
        )
        raise EmergencyError("The protocol version is no longer current; the case has been escalated.")
    at = now_utc()
    if step.step_type == "MEDICATION":
        if (
            rs.status != "presented"
            or weight_kg is not None
            or head_count is not None
            or (rs.result_payload or {}).get("lot_id") is None
        ):
            prepare_step(db, access, run, rs.id, weight_kg=weight_kg, head_count=head_count)
            if rs.status == "blocked":
                raise EmergencyError(
                    "The medication step is blocked: "
                    + "; ".join(rs.result_payload.get("problems", []))
                    + ". The case has been escalated."
                )
        p = rs.result_payload
        product = ph.get_product(db, p["medicine_product_id"])
        item = ph.item_of(db, product)
        plan = _run_plan(db, access, run, version)
        treatment_id = next(
            (
                s.result_payload.get("treatment_id")
                for s in run.steps
                if s.result_payload
                and s.result_payload.get("treatment_id")
                and s.result_payload.get("medicine_product_id") == str(product.inventory_item_id)
            ),
            None,
        )
        if treatment_id is None:
            per_head = f" per head × {p['head_count']}" if p["head_count"] > 1 else ""
            t = cases.record_treatment(
                db,
                access,
                entity_type="animal" if subject_type == "animal" else "flock",
                entity_id=str(subject.id),
                diagnosis=f"Emergency protocol {version.protocol.code} v{version.version_no} — "
                f"{version.protocol.title}",
                medication=f"{item.name} ({version.protocol.code} v{version.version_no} step {step.step_no})",
                medicine_product_id=str(product.inventory_item_id),
                health_case_id=str(run.health_case_id) if run.health_case_id else None,
                treatment_plan_id=str(plan.id) if plan else None,
                dose=f"{p['dose_quantity']:g} {p['dose_unit']}{per_head}",
                route=p["route_code"],
                start_at=at,
                vet_id=version.approved_by_veterinarian_id,
                responsible_user_id=access.user_id,
                notes=(
                    f"Prescription provenance: veterinarian-approved protocol (approval ref "
                    f"{version.approval_reference}). Dose rule {p['dose_rule_type']}, "
                    f"weight {p.get('weight_used_kg')} kg."
                ),
            )
            treatment_id = str(t.id)
        adm = ph.administer(
            db,
            access,
            subject_type=subject_type,
            subject_id=subject.id,
            inventory_item_id=product.inventory_item_id,
            lot_id=lot_id or p["lot_id"],
            dose_quantity=p["dose_quantity"],
            dose_unit=p["dose_unit"],
            route_code=p["route_code"],
            head_count=p["head_count"],
            treatment_id=treatment_id,
            protocol_run_step_id=rs.id,
            administered_at=at,
            reason=f"Protocol {version.protocol.code} v{version.version_no} "
            f"step {step.step_no}: {step.title}",
            notes=notes,
        )
        rs.medication_administration_id = adm.id
        rs.result_payload = {
            **p,
            "treatment_id": treatment_id,
            "administration_id": str(adm.id),
            "lot_id": str(adm.inventory_lot_id),
            "withdrawal_milk_until": adm.withdrawal_milk_until.isoformat()
            if adm.withdrawal_milk_until
            else None,
            "withdrawal_meat_until": adm.withdrawal_meat_until.isoformat()
            if adm.withdrawal_meat_until
            else None,
            **(result or {}),
        }
    else:
        rs.result_payload = {**(rs.result_payload or {}), **(result or {}), "step_type": step.step_type}
        if step.step_type == "REASSESS":
            minutes = step.timing_offset_minutes or version.reassessment_minutes or 60
            run.status = "awaiting_reassessment"
            run.next_reassessment_at = at + timedelta(minutes=minutes)
        elif step.step_type == "NOTIFY":
            _notify(db, access, run, version, subject)
    rs.status = "completed"
    rs.confirmed_at = at
    rs.confirmed_by = access.user_id
    rs.completed_at = at
    rs.last_modified_by = access.membership_id
    if notes:
        rs.result_payload = {**rs.result_payload, "notes": notes}
    if step.step_type == "ESCALATE":
        escalate_run(db, access, run, reason=f"protocol step {step.step_no}: {step.title}")
    else:
        _maybe_complete(db, access, run)
    return rs


def _run_plan(
    db: Session, access: AccessContext, run: em.EmergencyProtocolRun, version: em.EmergencyProtocolVersion
) -> Any:
    """The treatment plan the run's prescriptions sit under
    (HEALTH-VETERINARY-SCHEMA treatment_plan): prescribed by the
    veterinarian who approved the protocol version, on the run's case."""
    if not run.health_case_id:
        return None
    from app.farmos.health_case_models import TreatmentPlan

    plan = db.scalar(
        select(TreatmentPlan).where(
            TreatmentPlan.source_type == "emergency_run", TreatmentPlan.source_id == str(run.id)
        )
    )
    if plan is None:
        case = db.get(HealthCase, run.health_case_id)
        if case is None or case.status not in cases.OPEN_STATUSES:
            return None
        plan = cases.prescribe_plan(
            db,
            access,
            case,
            instructions=(
                f"{version.protocol.title} ({version.protocol.code} v{version.version_no}, approval "
                f"{version.approval_reference}) — the approved steps of the protocol."
            ),
            source_type="emergency_run",
            source_id=str(run.id),
            prescribed_by=version.approved_by_veterinarian_id,
            status="active",
        )
    return plan


def skip_step(
    db: Session,
    access: AccessContext,
    run: em.EmergencyProtocolRun,
    run_step_id: str | uuid.UUID,
    *,
    reason: str,
) -> em.ProtocolRunStep:
    rs = _step_of(run, run_step_id)
    if rs.step.required:
        raise EmergencyError(
            f"Step {rs.step.step_no} is required by the approved protocol and cannot be skipped. "
            "Escalate instead."
        )
    if not reason or not reason.strip():
        raise EmergencyError("Say why the step is skipped.")
    rs.status = "skipped"
    rs.skip_reason = reason.strip()
    rs.completed_at = now_utc()
    rs.last_modified_by = access.membership_id
    _maybe_complete(db, access, run)
    return rs


def _maybe_complete(db: Session, access: AccessContext, run: em.EmergencyProtocolRun) -> None:
    if run.status != "in_progress":
        return
    if all(s.status in ("completed", "skipped") for s in run.steps):
        resolve_run(db, access, run, outcome="all protocol steps completed")


def reassess_run(
    db: Session,
    access: AccessContext,
    run: em.EmergencyProtocolRun,
    *,
    signs: list,
    notes: str | None = None,
) -> tuple[em.EmergencyAssessment, em.EmergencyProtocolRun]:
    """Response at reassessment (acceptance 7): the same version re-scored
    on the new signs. A danger sign or no improvement escalates; an
    improvement continues the approved steps or resolves the case."""
    if run.status not in ("in_progress", "awaiting_reassessment"):
        raise EmergencyError(f"This run is {run.status}.")
    version = get_version(db, run.protocol_version_id)
    subject_type, subject = _run_subject(db, run)
    initial = get_assessment(db, run.emergency_assessment_id)
    before = next((m.match_score for m in initial.matches if m.protocol_version_id == version.id), None) or 0
    follow = assess(
        db,
        access,
        subject_type=subject_type,
        subject_id=subject.id,
        signs=signs,
        source="reassessment",
        notes=notes,
        parent_run_id=run.id,
        only_version=version,
    )
    after = next((m.match_score for m in follow.matches if m.protocol_version_id == version.id), None) or 0
    danger = any(m.match_explanation.get("danger_signs") for m in follow.matches)
    follow.status = "closed"
    follow.closed_at = now_utc()
    # The follow-up is evidence for the run, not a second case: close its own
    # escalation task if the triage opened one, and let the run decide.
    _close_tasks(db, (ESCALATION_TASK,), (str(follow.id),))
    routing.clear_bell(db, source_type="emergency_assessment", source_id=str(follow.id), at=now_utc())
    if danger:
        details = "; ".join(
            d["detail"] for m in follow.matches for d in m.match_explanation.get("danger_signs", [])
        )
        escalate_run(db, access, run, reason="danger sign at reassessment: " + details)
    elif after >= before and after > 0:
        escalate_run(
            db,
            access,
            run,
            reason=f"no improvement at reassessment (match score {after:.2f} vs {before:.2f} at the start)",
        )
    else:
        run.status = "in_progress"
        run.next_reassessment_at = None
        follow.explanation = f"Reassessment: match score {after:.2f} (was {before:.2f}) — improving. " + (
            follow.explanation or ""
        )
        _maybe_complete(db, access, run)
    return follow, run


def escalate_run(
    db: Session, access: AccessContext, run: em.EmergencyProtocolRun, *, reason: str
) -> em.EmergencyProtocolRun:
    if run.status in ("escalated", "completed", "cancelled"):
        return run
    subject_type, subject = _run_subject(db, run)
    at = now_utc()
    run.status = "escalated"
    run.escalated_at = at
    run.escalation_reason = reason
    run.last_modified_by = access.membership_id
    assessment = get_assessment(db, run.emergency_assessment_id)
    assessment.status = "escalated"
    assessment.escalation_reasons = [*(assessment.escalation_reasons or []), reason]
    name = getattr(subject, "name", "")
    recipient = routing.primary_recipient(
        access.tenant_id, "emergency_escalation", prefer_role="veterinarian", db=db
    )
    _task(
        db,
        access,
        title=f"EMERGENCY: {name} — call the veterinarian",
        description=f"Protocol run escalated: {reason}",
        assigned_to=recipient.user_id if recipient else None,
        due_at=at,
        source_type=ESCALATION_TASK,
        source_id=str(run.id),
    )
    _close_tasks(db, (REASSESS_TASK,), (str(run.id),))
    if run.health_case_id:
        case = db.get(HealthCase, run.health_case_id)
        if (
            case is not None
            and case.status in cases.OPEN_STATUSES
            and cases.severity_rank(case.severity_code) < cases.severity_rank("high")
        ):
            case.severity_code = "high"
    db.flush()
    _bell_assessment(db, access, assessment, subject)
    return run


def resolve_run(
    db: Session, access: AccessContext, run: em.EmergencyProtocolRun, *, outcome: str
) -> em.EmergencyProtocolRun:
    if run.status in ("completed", "cancelled"):
        return run
    if not outcome or not outcome.strip():
        raise EmergencyError("Say how the case resolved.")
    at = now_utc()
    run.status = "completed"
    run.completed_at = at
    run.outcome = outcome.strip()
    run.last_modified_by = access.membership_id
    assessment = get_assessment(db, run.emergency_assessment_id)
    assessment.status = "resolved"
    assessment.closed_at = at
    _close_tasks(db, (ESCALATION_TASK, NOTIFY_TASK, REASSESS_TASK), (str(run.id), str(assessment.id)))
    routing.clear_bell(db, source_type="emergency_assessment", source_id=str(assessment.id), at=at)
    routing.clear_bell(db, source_type="emergency_run", source_id=str(run.id), at=at)
    _close_engine_case(
        db, access, run.health_case_id, outcome=f"Emergency protocol {run.id}: {outcome.strip()}"
    )
    return run


def _close_engine_case(
    db: Session, access: AccessContext, case_id: uuid.UUID | None, *, outcome: str
) -> None:
    """A case the engine opened closes with the emergency; a case a
    person opened stays theirs to close."""
    if not case_id:
        return
    case = db.get(HealthCase, case_id)
    if (
        case is not None
        and case.status in cases.OPEN_STATUSES
        and (case.source_type or "").startswith("emergency")
    ):
        cases.close_case(db, access, case, outcome=outcome)


def close_assessment(
    db: Session, access: AccessContext, assessment: em.EmergencyAssessment, *, outcome: str
) -> em.EmergencyAssessment:
    """Closes an escalated or open assessment once the vet / manager has
    dealt with it, with the outcome on record."""
    if assessment.status in ("resolved", "closed"):
        return assessment
    if not outcome or not outcome.strip():
        raise EmergencyError("Say how the case was resolved.")
    at = now_utc()
    assessment.status = "resolved"
    assessment.closed_at = at
    assessment.explanation = (assessment.explanation + f" Outcome: {outcome.strip()}").strip()
    assessment.last_modified_by = access.membership_id
    _close_tasks(db, (ESCALATION_TASK,), (str(assessment.id),))
    routing.clear_bell(db, source_type="emergency_assessment", source_id=str(assessment.id), at=at)
    _close_engine_case(
        db,
        access,
        assessment.health_case_id,
        outcome=f"Emergency assessment {assessment.id}: {outcome.strip()}",
    )
    return assessment


def check_overdue_reassessments(db: Session, access: AccessContext) -> dict:
    """The scheduled reassessment check (§12 "schedule reassessment"):
    every run past its reassessment time gets one open task to the person
    who started it (and the bell), deduplicated per run. It never
    escalates by itself — a missed reassessment is a reminder, failure to
    improve is the escalation."""
    runs = db.scalars(
        select(em.EmergencyProtocolRun).where(em.EmergencyProtocolRun.status == "awaiting_reassessment")
    ).all()
    overdue, opened = 0, 0
    at = now_utc()
    for r in runs:
        due = ensure_utc(r.next_reassessment_at)
        if not due or due >= at:
            continue
        overdue += 1
        if db.scalar(
            select(Task.id).where(
                Task.source_type == REASSESS_TASK, Task.source_id == str(r.id), Task.status == "open"
            )
        ):
            continue
        subject_type, subject = _run_subject(db, r)
        version = db.get(em.EmergencyProtocolVersion, r.protocol_version_id)
        recipient = routing.member(access.tenant_id, r.started_by) if r.started_by else None
        if recipient is None:
            recipient = routing.primary_recipient(access.tenant_id, "emergency_reassessment", db=db)
        name = getattr(subject, "name", "")
        title = f"Reassess {name} now — {version.protocol.title if version else 'protocol'}"
        _task(
            db,
            access,
            title=title,
            description=(
                f"The reassessment was due at {due:%H:%M}. Re-take the signs; "
                "no improvement escalates to the "
                "veterinarian."
            ),
            assigned_to=recipient.user_id if recipient else None,
            due_at=due,
            source_type=REASSESS_TASK,
            source_id=str(r.id),
        )
        routing.notify_bell(
            db,
            access.tenant_id,
            module_code="animal_health",
            notification_type="emergency_reassessment_due",
            title=f"{name}: reassessment overdue",
            description="The protocol's reassessment time has passed. Re-take the signs now.",
            priority="high",
            entity_type="animal" if r.animal_id else "flock",
            entity_id=str(r.animal_id or r.animal_group_id),
            source_type="emergency_run",
            source_id=str(r.id),
            actor=access.membership_id,
        )
        opened += 1
    db.flush()
    return {"awaiting_reassessment": len(runs), "overdue": overdue, "tasks_opened": opened}


# ---------------------------------------------------------------- offline sync
def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def record_offline_run(db: Session, access: AccessContext, payload: dict, names: NameCache) -> dict:
    """Sync of an emergency executed on the tablet from the signed offline
    package (§14, acceptance 9). The server re-runs triage against the
    same facts as of now, pinned to the version the tablet used, records
    what the tablet showed and did exactly as it happened — device, actor,
    occurrence times, operation id — and lists every difference it finds
    rather than silently changing anything. A stale or withdrawn version,
    a dose the current rules refuse or a lot that is no longer eligible
    becomes a discrepancy and an escalation, never a rewritten record.
    Idempotent on the operation id."""
    op_id = (payload.get("operation_id") or "").strip()
    if not op_id:
        raise EmergencyError("An offline operation needs an operation_id.")
    existing = db.scalar(
        select(em.EmergencyAssessment).where(em.EmergencyAssessment.offline_operation_id == op_id)
    )
    if existing is not None:
        run = db.scalar(
            select(em.EmergencyProtocolRun).where(
                em.EmergencyProtocolRun.emergency_assessment_id == existing.id
            )
        )
        return {
            "assessment": assessment_dict(db, existing, names),
            "run": run_dict(db, run, names) if run else None,
            "discrepancies": existing.offline_discrepancies,
            "replayed": True,
        }
    subject_type, subject_id = payload["subject_type"], payload["subject_id"]
    subject = _subject(db, subject_type, subject_id)
    device_id = payload.get("device_id")
    signature = payload.get("package_signature")
    shown = payload.get("shown") or {}
    occurred = _iso(payload.get("occurred_at"))
    discrepancies: list[dict] = []
    current = offline_package(db, names)
    if signature and signature != current["signature"]:
        discrepancies.append(
            {
                "code": "package_stale",
                "detail": "the tablet's protocol package differs from the farm's current package; "
                "the version "
                "used is checked below",
            }
        )
    shown_version: em.EmergencyProtocolVersion | None = None
    shown_version_id = (
        parse_uuid(shown.get("protocol_version_id")) if shown.get("protocol_version_id") else None
    )
    if shown.get("protocol_version_id"):
        shown_version = db.get(em.EmergencyProtocolVersion, shown_version_id) if shown_version_id else None
        if shown_version is None:
            raise EmergencyError("The protocol version the tablet used is unknown on this farm.", 404)
    pinned = shown_version if shown_version is not None and shown_version.offline_eligible else None
    assessment = assess(
        db,
        access,
        subject_type=subject_type,
        subject_id=subject.id,
        signs=payload.get("signs") or [],
        source="offline",
        notes=payload.get("notes"),
        only_version=pinned,
        started_at=occurred,
        device_id=device_id,
        offline_operation_id=op_id,
        package_signature=signature,
    )
    server_match = next((m for m in assessment.matches if m.selected), None)
    if shown.get("escalated"):
        if assessment.status != "escalated":
            discrepancies.append(
                {
                    "code": "tablet_escalated_server_matched",
                    "detail": "the tablet escalated; the server now finds a match — "
                    "the escalation stands as recorded",
                }
            )
            _escalate_assessment(
                db,
                access,
                assessment,
                subject_type,
                subject,
                [
                    "offline execution escalated at the animal: "
                    + "; ".join(shown.get("escalation_reasons") or [])
                ],
            )
    elif shown_version is not None:
        if not shown_version.offline_eligible:
            discrepancies.append(
                {
                    "code": "version_not_offline_eligible",
                    "detail": f"{shown_version.protocol.code} v{shown_version.version_no} "
                    "was never offline-eligible",
                }
            )
        if not version_is_current(shown_version):
            discrepancies.append(
                {
                    "code": "version_not_current",
                    "detail": f"{shown_version.protocol.code} v{shown_version.version_no} "
                    f"is {shown_version.status} "
                    "/ no longer current",
                }
            )
        if server_match is None or server_match.protocol_version_id != shown_version.id:
            discrepancies.append(
                {
                    "code": "match_differs",
                    "detail": "the server's triage of the same signs does not select "
                    "the version the tablet showed",
                    "server_status": assessment.status,
                    "server_reasons": assessment.escalation_reasons,
                }
            )
    run = None
    steps_done = payload.get("steps") or []
    if shown_version is not None and not shown.get("escalated") and steps_done:
        if assessment.status != "open" or server_match is None:
            # What the tablet did is recorded against the version it used; the
            # case is escalated because the server could not confirm it.
            assessment.status = "open"
            match = next((m for m in assessment.matches if m.protocol_version_id == shown_version.id), None)
            if match is None:
                match = em.ProtocolMatch(
                    tenant_id=access.tenant_id,
                    last_modified_by=access.membership_id,
                    emergency_assessment_id=assessment.id,
                    protocol_version_id=shown_version.id,
                    match_score=shown.get("match_score"),
                    match_explanation={"source": "offline_tablet", "note": "recorded as shown on the device"},
                    eligibility_result="ELIGIBLE",
                    selected=True,
                    evaluated_at=now_utc(),
                )
                db.add(match)
                db.flush()
                db.expire(assessment, ["matches"])
            match.selected = True
            server_match = match
        try:
            run = start_run(
                db,
                access,
                assessment,
                match_id=str(server_match.id),
                confirmed=True,
                device_id=device_id,
                started_at=occurred,
                offline_operation_id=op_id,
                package_signature=signature,
            )
        except FeedError as exc:
            discrepancies.append({"code": "run_refused_at_sync", "detail": exc.detail})
        if run is not None:
            by_step = {s.protocol_step_id: s for s in run.steps}
            for done in steps_done:
                step_pk = parse_uuid(done.get("protocol_step_id"))
                rs = by_step.get(step_pk) if step_pk else None
                if rs is None:
                    discrepancies.append(
                        {
                            "code": "unknown_step",
                            "detail": f"step {done.get('protocol_step_id')} is not on the version",
                        }
                    )
                    continue
                if done.get("status") == "skipped":
                    try:
                        skip_step(db, access, run, rs.id, reason=done.get("skip_reason") or "skipped offline")
                    except FeedError as exc:
                        discrepancies.append(
                            {"code": "skip_refused_at_sync", "step_no": rs.step.step_no, "detail": exc.detail}
                        )
                    continue
                med = done.get("medication") or {}
                try:
                    confirm_step(
                        db,
                        access,
                        run,
                        rs.id,
                        weight_kg=med.get("weight_used_kg"),
                        head_count=med.get("head_count"),
                        lot_id=med.get("lot_id"),
                        result={
                            **(done.get("result") or {}),
                            "offline": True,
                            "occurred_at": done.get("occurred_at"),
                            "device_id": device_id,
                        },
                        notes=done.get("notes"),
                    )
                except FeedError as exc:
                    discrepancies.append(
                        {
                            "code": "step_refused_at_sync",
                            "step_no": rs.step.step_no,
                            "detail": exc.detail,
                            "as_shown": med or done.get("result"),
                        }
                    )
                    if run.status != "escalated":
                        escalate_run(
                            db,
                            access,
                            run,
                            reason=f"offline step {rs.step.step_no} could not be confirmed at sync: "
                            f"{exc.detail}",
                        )
            if run.status not in ("escalated", "completed", "cancelled") and any(
                d["code"] in ("version_not_current", "match_differs", "version_not_offline_eligible")
                for d in discrepancies
            ):
                escalate_run(
                    db,
                    access,
                    run,
                    reason="offline execution revalidated with differences: "
                    + "; ".join(d["code"] for d in discrepancies),
                )
    elif discrepancies and assessment.status == "open":
        _escalate_assessment(
            db,
            access,
            assessment,
            subject_type,
            subject,
            [
                "offline execution revalidated with differences: "
                + "; ".join(d["code"] for d in discrepancies)
            ],
        )
    assessment.offline_discrepancies = discrepancies
    if run is not None:
        run.offline_discrepancies = discrepancies
    db.flush()
    return {
        "assessment": assessment_dict(db, assessment, names),
        "run": run_dict(db, run, names) if run else None,
        "discrepancies": discrepancies,
        "replayed": False,
    }


# ---------------------------------------------------------------- offline
def offline_package(db: Session, names: NameCache) -> dict:
    """Only versions marked offline-eligible travel to the tablet (§14),
    with their rules, steps and medicine mapping, signed by content so a
    stale or altered package is detectable when it syncs."""
    # The protocol's species and scope travel with the version: the tablet
    # filters candidates exactly as `assess` does.
    versions = [
        {
            **version_dict(db, v, names),
            "species_code": v.protocol.species_code,
            "subject_scope": v.protocol.subject_scope,
        }
        for v in current_versions(db)
        if v.offline_eligible
    ]
    body = json.dumps(versions, sort_keys=True, default=str)
    # The medicine mapping the medication steps need offline: the product,
    # its stock unit and the eligible lots as of now (FEFO order). Stock is a
    # snapshot — the sync revalidates the lot actually used.
    product_ids = {
        s["medication"]["medicine_product_id"] for v in versions for s in v["steps"] if s.get("medication")
    }
    medicines = []
    for pid in sorted(product_ids):
        product = db.get(MedicineProduct, parse_uuid(pid))
        if product is None or product.deleted_at is not None:
            continue
        item = ph.item_of(db, product)
        medicines.append(
            {
                "medicine_product_id": pid,
                "name": item.name,
                "unit": item.unit,
                "administration_routes": product.administration_routes or [],
                "species_codes": product.species_codes or [],
                "prescription_required": product.prescription_required,
                "ingredient_codes": [i.ingredient_code for i in product.ingredients],
                "withdrawal_rules": product.withdrawal_rules_json or {},
                "eligible_lots": [
                    {
                        "lot_id": str(lot.id),
                        "lot_code": lot.lot_code,
                        "quantity_on_hand": lot.quantity_on_hand,
                        "free_quantity": ph.lot_free_quantity(db, product, lot),
                        "expiry_date": lot.expiry_date.isoformat() if lot.expiry_date else None,
                    }
                    for lot in ph.eligible_lots(db, product)
                ],
            }
        )
    at = now_utc()
    return {
        "generated_at": at,
        "model_reference": MODEL_REFERENCE,
        "model_version": MODEL_VERSION,
        "signature": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "versions": versions,
        "sign_codes": sign_codes(db),
        "medicines": medicines,
        "stock_as_of": at,
        "note": (
            "Only offline-eligible approved versions. Execution offline records version, "
            "device, actor, time and "
            "operation id; sync revalidates."
        ),
    }


def sign_codes(db: Session) -> list[dict]:
    known: dict[str, dict] = {s["code"]: dict(s) for s in STANDARD_SIGNS}
    for v in current_versions(db):
        for r in v.trigger_rules:
            code = _normalise_code(r.observation_code)
            if code not in known:
                if r.operator in (">=", ">", "<=", "<"):
                    kind = "numeric"
                else:
                    kind = "categorical" if r.expected_value_code else "boolean"
                known[code] = {
                    "code": code,
                    "kind": kind,
                    "unit": r.threshold_unit,
                    "label": r.label or code.replace("_", " "),
                }
            elif r.expected_value_code and r.expected_value_code.lower() not in known[code].get(
                "options", []
            ):
                known[code].setdefault("options", []).append(r.expected_value_code.lower())
    return list(known.values())


# ------------------------------------------------------------------ views
def step_dict(db: Session, s: em.ProtocolStep) -> dict:
    med = s.medication
    out: dict[str, Any] = {
        "id": str(s.id),
        "step_no": s.step_no,
        "step_type": s.step_type,
        "title": s.title,
        "instructions": s.instructions,
        "required": s.required,
        "requires_confirmation": s.requires_confirmation,
        "timing_offset_minutes": s.timing_offset_minutes,
        "medication": None,
    }
    if med is not None:
        item = db.get(InventoryItem, med.medicine_product_id)
        out["medication"] = {
            "medicine_product_id": str(med.medicine_product_id),
            "medicine_name": item.name if item else None,
            "route_code": med.route_code,
            "dose_rule_type": med.dose_rule_type,
            "fixed_dose_quantity": med.fixed_dose_quantity,
            "dose_per_weight_quantity": med.dose_per_weight_quantity,
            "dose_unit": med.dose_unit,
            "weight_unit": med.weight_unit,
            "minimum_dose_quantity": med.minimum_dose_quantity,
            "maximum_dose_quantity": med.maximum_dose_quantity,
            "repeat_interval_minutes": med.repeat_interval_minutes,
            "maximum_administrations": med.maximum_administrations,
            "weight_max_age_days": med.weight_max_age_days,
            "withdrawal_rule_payload": med.withdrawal_rule_payload,
        }
    return out


def version_dict(db: Session, v: em.EmergencyProtocolVersion, names: NameCache) -> dict:
    return {
        "id": str(v.id),
        "protocol_id": str(v.emergency_protocol_id),
        "protocol_code": v.protocol.code,
        "protocol_title": v.protocol.title,
        "version_no": v.version_no,
        "status": v.status,
        "current": version_is_current(v),
        "effective_from": v.effective_from,
        "effective_to": v.effective_to,
        "protocol_text": v.protocol_text,
        "minimum_match_confidence": v.minimum_match_confidence,
        "requires_manager_notification": v.requires_manager_notification,
        "requires_vet_notification": v.requires_vet_notification,
        "requires_pre_action_confirmation": v.requires_pre_action_confirmation,
        "offline_eligible": v.offline_eligible,
        "reassessment_minutes": v.reassessment_minutes,
        "approved_by_veterinarian_id": _s(v.approved_by_veterinarian_id),
        "approved_by_name": names.get(v.approved_by_veterinarian_id),
        "approved_at": v.approved_at,
        "approval_reference": v.approval_reference,
        "withdrawn_reason": v.withdrawn_reason,
        "created_at": v.created_at,
        "trigger_rules": [
            {
                "id": str(r.id),
                "observation_code": r.observation_code,
                "operator": r.operator,
                "threshold_numeric": r.threshold_numeric,
                "threshold_unit": r.threshold_unit,
                "expected_value_code": r.expected_value_code,
                "required": r.required,
                "weight": r.weight,
                "danger_sign": r.danger_sign,
                "label": r.label,
            }
            for r in v.trigger_rules
        ],
        "eligibility_rules": [
            {
                "id": str(r.id),
                "rule_type": r.rule_type,
                "operator": r.operator,
                "rule_payload": r.rule_payload,
                "failure_action": r.failure_action,
                "message": r.message,
            }
            for r in v.eligibility_rules
        ],
        "steps": [step_dict(db, s) for s in v.steps],
    }


def protocol_dict(
    db: Session, p: em.EmergencyProtocol, names: NameCache, *, with_versions: bool = True
) -> dict:
    current = db.get(em.EmergencyProtocolVersion, p.current_version_id) if p.current_version_id else None
    return {
        "id": str(p.id),
        "farm_id": str(p.tenant_id),
        "code": p.code,
        "title": p.title,
        "condition_family_code": p.condition_family_code,
        "species_code": p.species_code,
        "subject_scope": p.subject_scope,
        "status": p.status,
        "current_version_id": _s(p.current_version_id),
        "current_version_no": current.version_no if current else None,
        "created_at": p.created_at,
        "created_by": _s(p.created_by),
        "current_version": version_dict(db, current, names) if current else None,
        "versions": [version_dict(db, v, names) for v in p.versions] if with_versions else None,
    }


def match_dict(db: Session, m: em.ProtocolMatch) -> dict:
    v = db.get(em.EmergencyProtocolVersion, m.protocol_version_id)
    return {
        "id": str(m.id),
        "protocol_version_id": str(m.protocol_version_id),
        "protocol_code": v.protocol.code if v else None,
        "protocol_title": v.protocol.title if v else None,
        "version_no": v.version_no if v else None,
        "match_score": m.match_score,
        "minimum_match_confidence": v.minimum_match_confidence if v else None,
        "eligibility_result": m.eligibility_result,
        "blocking_reasons": m.blocking_reasons,
        "selected": m.selected,
        "explanation": m.match_explanation,
        "evaluated_at": m.evaluated_at,
    }


def assessment_dict(db: Session, a: em.EmergencyAssessment, names: NameCache) -> dict:
    subject_type = "animal" if a.animal_id else "group"
    subject: Any = db.get(Animal, a.animal_id) if a.animal_id else db.get(AnimalGroup, a.animal_group_id)
    run = db.scalar(
        select(em.EmergencyProtocolRun)
        .where(em.EmergencyProtocolRun.emergency_assessment_id == a.id)
        .order_by(em.EmergencyProtocolRun.started_at.desc())
        .limit(1)
    )
    task = db.scalar(
        select(Task)
        .where(Task.source_type == ESCALATION_TASK, Task.source_id == str(a.id))
        .order_by(Task.due_at.desc())
        .limit(1)
    )
    return {
        "id": str(a.id),
        "subject_type": subject_type,
        "subject_id": str(a.animal_id or a.animal_group_id),
        "subject_name": getattr(subject, "name", None),
        "species": getattr(subject, "species", None),
        "parent_run_id": _s(a.parent_run_id),
        "health_case_id": _s(a.health_case_id),
        "started_at": a.started_at,
        "source": a.source,
        "observed_signs": a.observed_signs,
        "animal_facts": a.animal_facts,
        "triage_level": a.triage_level,
        "ai_model_reference": a.ai_model_reference,
        "ai_model_version": MODEL_VERSION,
        "ai_confidence": a.ai_confidence,
        "status": a.status,
        "escalation_reasons": a.escalation_reasons,
        "explanation": a.explanation,
        "created_by": _s(a.created_by),
        "created_by_name": names.get(a.created_by),
        "closed_at": a.closed_at,
        "device_id": a.device_id,
        "offline_operation_id": a.offline_operation_id,
        "package_signature": a.package_signature,
        "offline_discrepancies": a.offline_discrepancies or [],
        "matches": [match_dict(db, m) for m in a.matches],
        "selected_match_id": next((str(m.id) for m in a.matches if m.selected), None),
        "run_id": str(run.id) if run else None,
        "run_status": run.status if run else None,
        "escalation_task_id": str(task.id) if task else None,
    }


def run_step_dict(db: Session, rs: em.ProtocolRunStep, names: NameCache) -> dict:
    return {
        "id": str(rs.id),
        "sequence": rs.sequence,
        "status": rs.status,
        "due_at": rs.due_at,
        "presented_at": rs.presented_at,
        "confirmed_at": rs.confirmed_at,
        "confirmed_by": _s(rs.confirmed_by),
        "confirmed_by_name": names.get(rs.confirmed_by),
        "completed_at": rs.completed_at,
        "medication_administration_id": _s(rs.medication_administration_id),
        "result": rs.result_payload or {},
        "skip_reason": rs.skip_reason,
        "step": step_dict(db, rs.step),
    }


def run_dict(db: Session, r: em.EmergencyProtocolRun, names: NameCache) -> dict:
    v = db.get(em.EmergencyProtocolVersion, r.protocol_version_id)
    subject: Any = db.get(Animal, r.animal_id) if r.animal_id else db.get(AnimalGroup, r.animal_group_id)
    due = ensure_utc(r.next_reassessment_at)
    return {
        "id": str(r.id),
        "assessment_id": str(r.emergency_assessment_id),
        "protocol_version_id": str(r.protocol_version_id),
        "protocol_code": v.protocol.code if v else None,
        "protocol_title": v.protocol.title if v else None,
        "version_no": v.version_no if v else None,
        "approval_reference": v.approval_reference if v else None,
        "subject_type": "animal" if r.animal_id else "group",
        "subject_id": str(r.animal_id or r.animal_group_id),
        "subject_name": getattr(subject, "name", None),
        "health_case_id": _s(r.health_case_id),
        "device_id": r.device_id,
        "offline": r.offline,
        "offline_operation_id": r.offline_operation_id,
        "offline_discrepancies": r.offline_discrepancies or [],
        "started_at": r.started_at,
        "started_by": _s(r.started_by),
        "started_by_name": names.get(r.started_by),
        "status": r.status,
        "manager_notified_at": r.manager_notified_at,
        "veterinarian_notified_at": r.veterinarian_notified_at,
        "next_reassessment_at": r.next_reassessment_at,
        "reassessment_overdue": bool(due and due < now_utc() and r.status == "awaiting_reassessment"),
        "escalated_at": r.escalated_at,
        "escalation_reason": r.escalation_reason,
        "completed_at": r.completed_at,
        "outcome": r.outcome,
        "steps": [run_step_dict(db, s, names) for s in r.steps],
        "next_step_id": next((str(s.id) for s in r.steps if s.status in ("pending", "presented")), None),
    }


def summary(db: Session) -> dict:
    protocols = db.scalars(
        select(em.EmergencyProtocol).where(em.EmergencyProtocol.deleted_at.is_(None))
    ).all()
    open_assessments = (
        db.scalar(
            select(func.count(em.EmergencyAssessment.id)).where(em.EmergencyAssessment.status == "open")
        )
        or 0
    )
    escalated = (
        db.scalar(
            select(func.count(em.EmergencyAssessment.id)).where(em.EmergencyAssessment.status == "escalated")
        )
        or 0
    )
    runs = db.scalars(
        select(em.EmergencyProtocolRun).where(
            em.EmergencyProtocolRun.status.in_(("in_progress", "awaiting_reassessment"))
        )
    ).all()
    at = now_utc()
    return {
        "protocols": len(protocols),
        "active_protocols": sum(1 for p in protocols if p.status == "active"),
        "open_assessments": open_assessments,
        "escalated": escalated,
        "runs_in_progress": sum(1 for r in runs if r.status == "in_progress"),
        "awaiting_reassessment": sum(1 for r in runs if r.status == "awaiting_reassessment"),
        "reassessments_overdue": sum(
            1 for r in runs if r.next_reassessment_at and (ensure_utc(r.next_reassessment_at) or at) < at
        ),
        "model_reference": MODEL_REFERENCE,
        "model_version": MODEL_VERSION,
    }
