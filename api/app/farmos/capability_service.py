"""The capability resolver (OrigamiFarmOS docs/GENERIC-ANIMAL-CAPABILITY-MODEL.md §10).

    resolve(species, sex, life_stage, management_profile) -> CapabilitySet

Called by the API before it will accept a pregnancy flag, an identifier or
a milk record, and by the tablet (`POST /animal-capabilities/resolve`) to
decide which fields and tabs to draw. One function, two callers, so the
form can never offer something the server will refuse.

Resolution is configuration first, biology last:

1. Every rule for the species whose sex / stage / profile either match or
   are unset is a candidate. For each capability the most *specific*
   granting candidate wins (most dimensions set); `priority` breaks
   ties. Any matching rule that *disables* the capability is a veto and
   wins outright.
2. Then the invariants. A male is never pregnant; a hen is never in calf;
   a ewe never lays. These are not configurable, because a rule that got
   them wrong would corrupt a herd's records, and no farm needs the
   option.

The rules come from app/farmos/livestock_catalog.py (code-defined, the
same for every tenant) — see that module for why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.farmos import livestock_catalog as catalog


@dataclass(frozen=True)
class ResolvedCapability:
    code: str
    category: str
    label_en: str
    label_ar: str
    required: bool
    configuration: dict | None


@dataclass
class CapabilitySet:
    species: str
    sex: str | None
    life_stage: str | None
    management_profile: str | None
    reproduction_mode: str
    capabilities: list[ResolvedCapability] = field(default_factory=list)
    terminology: dict = field(default_factory=dict)
    # What the invariants removed, so a caller can explain *why* a tab is
    # missing ("hens do not have a pregnancy record").
    suppressed: list[str] = field(default_factory=list)

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(c.code for c in self.capabilities)

    def has(self, code: str) -> bool:
        return code in self.codes

    def required_codes(self) -> frozenset[str]:
        return frozenset(c.code for c in self.capabilities if c.required)

    @property
    def subject_kind(self) -> str:
        """individual | group | either — from the tracking capabilities."""
        individual = self.has(catalog.Cap.INDIVIDUAL_TRACKING)
        group = self.has(catalog.Cap.GROUP_TRACKING)
        if individual and group:
            return "either"
        if group:
            return "group"
        return "individual"

    def allowed_identifier_types(self) -> frozenset[str]:
        allowed = set(catalog.IDENTIFIER_TYPES_ALWAYS_ALLOWED)
        for cap_code, id_type in catalog.IDENTIFIER_TYPE_FOR_CAPABILITY.items():
            if self.has(cap_code):
                allowed.add(id_type)
        return frozenset(allowed)

    def required_identifier_types(self) -> frozenset[str]:
        required = self.required_codes()
        return frozenset(
            id_type
            for cap_code, id_type in catalog.IDENTIFIER_TYPE_FOR_CAPABILITY.items()
            if cap_code in required
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "species": self.species,
            "sex": self.sex,
            "life_stage": self.life_stage,
            "management_profile": self.management_profile,
            "reproduction_mode": self.reproduction_mode,
            "subject_kind": self.subject_kind,
            "capabilities": [
                {
                    "code": c.code,
                    "category": c.category,
                    "label_en": c.label_en,
                    "label_ar": c.label_ar,
                    "required": c.required,
                    "configuration": c.configuration,
                }
                for c in self.capabilities
            ],
            "allowed_identifier_types": sorted(self.allowed_identifier_types()),
            "required_identifier_types": sorted(self.required_identifier_types()),
            "terminology": self.terminology,
            "suppressed": self.suppressed,
        }


# The biological invariants. Keyed by what triggers them; the value is
# the set of capabilities that can never apply.
_NEVER_FOR_MALES = frozenset(
    {
        catalog.Cap.PREGNANCY,
        catalog.Cap.LIVE_BIRTH,
        catalog.Cap.LACTATION,
        catalog.Cap.MILK_PRODUCTION,
        catalog.Cap.EGG_PRODUCTION,
    }
)
_NEVER_FOR_OVIPAROUS = frozenset(
    {catalog.Cap.PREGNANCY, catalog.Cap.LIVE_BIRTH, catalog.Cap.LACTATION, catalog.Cap.MILK_PRODUCTION}
)
_NEVER_FOR_VIVIPAROUS = frozenset({catalog.Cap.EGG_PRODUCTION, catalog.Cap.INCUBATION, catalog.Cap.HATCHING})


class UnknownSpecies(ValueError):
    pass


def get_species(code: str) -> dict[str, Any]:
    species = catalog.SPECIES_BY_CODE.get(code)
    if species is None or not species["active"]:
        raise UnknownSpecies(code)
    return species


def known_species_codes() -> list[str]:
    return [s["code"] for s in catalog.SPECIES if s["active"]]


def resolve(
    species_code: str,
    sex: str | None,
    life_stage: str | None = None,
    management_profile: str | None = None,
) -> CapabilitySet:
    species = get_species(species_code)
    stage = life_stage or catalog.DEFAULT_LIFE_STAGE
    sex_code = (sex or catalog.UNKNOWN).upper()

    # Per capability: the most specific matching *grant* wins (priority
    # breaks ties) — unless any matching rule disables it. A disable is a
    # veto. "Broiler flocks do not lay" must hold even though "adult
    # females lay" is the more specific-looking rule.
    grants: dict[str, tuple[int, int, dict[str, Any]]] = {}
    vetoed: set[str] = set()
    for rule in catalog.RULES_BY_SPECIES.get(species_code, ()):
        if rule["sex"] is not None and rule["sex"].upper() != sex_code:
            continue
        if rule["life_stage"] is not None and rule["life_stage"] != stage:
            continue
        if rule["management_profile"] is not None and rule["management_profile"] != management_profile:
            continue
        if not rule["enabled"]:
            vetoed.add(rule["capability_code"])
            continue
        specificity = sum(
            x is not None for x in (rule["sex"], rule["life_stage"], rule["management_profile"])
        )
        key = (specificity, rule["priority"])
        current = grants.get(rule["capability_code"])
        if current is None or key > current[:2]:
            grants[rule["capability_code"]] = (specificity, rule["priority"], rule)

    labels = catalog.CAPABILITY_BY_CODE
    enabled = {code: rule for code, (_, _, rule) in grants.items() if code in labels and code not in vetoed}

    # Invariants — applied after configuration so a misconfigured rule
    # can never override biology. `suppressed` lists everything biology
    # rules out for this animal, granted or not, so a screen can say
    # "hens do not have a pregnancy record" rather than just omit the tab.
    forbidden: set[str] = set()
    if sex_code == catalog.MALE:
        forbidden |= _NEVER_FOR_MALES
    if species["reproduction_mode"] == catalog.OVIPAROUS:
        forbidden |= _NEVER_FOR_OVIPAROUS
    elif species["reproduction_mode"] == catalog.VIVIPAROUS:
        forbidden |= _NEVER_FOR_VIVIPAROUS
    suppressed = sorted(code for code in forbidden if code in labels)
    for code in suppressed:
        enabled.pop(code, None)

    resolved = [
        ResolvedCapability(
            code=code,
            category=labels[code][0],
            label_en=labels[code][1],
            label_ar=labels[code][2],
            required=bool(rule["required"]),
            configuration=rule["configuration_json"],
        )
        for code, rule in sorted(enabled.items(), key=lambda kv: catalog.CAPABILITY_ORDER.get(kv[0], 999))
    ]

    return CapabilitySet(
        species=species_code,
        sex=sex_code,
        life_stage=stage,
        management_profile=management_profile,
        reproduction_mode=species["reproduction_mode"],
        capabilities=resolved,
        terminology=dict(species["terminology_json"]),
        suppressed=suppressed,
    )


def resolve_for_animal(animal: Any) -> CapabilitySet:
    """The resolver as the animal itself is configured (an Animal row, or
    anything with species / sex / life_stage / management_profile)."""
    return resolve(animal.species, animal.sex, animal.life_stage, animal.management_profile)


def resolve_for_group(group: Any) -> CapabilitySet:
    """A group resolves by its stated sex composition. An unstated one is
    treated as female, because a group a farm bothers to keep records on
    is nearly always a laying or breeding flock; a `mixed` composition
    resolves as unknown, so the resolver will not claim every bird lays.
    """
    composition = (group.sex_composition or "").lower()
    if composition in ("", "female", "f", "hens", "all_female"):
        sex = catalog.FEMALE
    elif composition in ("male", "m", "all_male"):
        sex = catalog.MALE
    else:
        sex = catalog.UNKNOWN
    return resolve(group.species, sex, group.life_stage, group.management_profile)


def describe_missing(cap_set: CapabilitySet, code: str, subject_name: str) -> str:
    """A refusal a farmer can read: says what the animal is and what was
    asked, not which rule fired."""
    label = catalog.CAPABILITY_BY_CODE.get(code, ("", code.replace("_", " ").title(), ""))[1]
    term = cap_set.terminology
    if cap_set.sex == catalog.FEMALE:
        what = term.get("female_en")
    elif cap_set.sex == catalog.MALE:
        what = term.get("male_en")
    else:
        what = None
    kind = f"a {what.lower()}" if what else f"a {cap_set.species.replace('_', ' ')}"
    reason = ""
    if code in cap_set.suppressed:
        reason = " — that does not apply to this kind of animal"
    elif cap_set.management_profile:
        reason = f" for the {cap_set.management_profile.replace('_', ' ')} profile"
    return f"{subject_name} is {kind}; {label.lower()} is not available{reason}."
