"""Reference data for the generic animal model — species, life stages,
management profiles, the capability catalog, and the rules that connect
them (OrigamiFarmOS docs/GENERIC-ANIMAL-CAPABILITY-MODEL.md).

This is *configuration*, kept as plain data on purpose and identical to
the reference backend's catalog so a tablet talking to this server and
one talking to the reference backend agree on what a hen can and cannot
do. It is code-defined rather than a tenant table for now: every farm
sees the same catalog, and nothing in the tablet contract edits it. A
per-farm override layer (a farm's own breeds, a disabled capability) is
a later stage — see docs/FARMOS_API.md "Known, deliberate gaps".

Codes match the values the API and the tablet already exchange
(`cow`, `layer_hen`, `F`/`M`) — changing them would break every existing
row and the published contract for no gain.
"""

from __future__ import annotations

import uuid
from typing import Any

# --------------------------------------------------------------------------
# Sex codes. `U` is the spec's explicit "unknown / undetermined" value.
# --------------------------------------------------------------------------
FEMALE = "F"
MALE = "M"
UNKNOWN = "U"
SEXES = (FEMALE, MALE, UNKNOWN)
SEX_ALIASES = {"FEMALE": FEMALE, "MALE": MALE, "UNKNOWN": UNKNOWN, "UNDETERMINED": UNKNOWN}

# Reproduction modes. These drive the biological invariants the resolver
# enforces regardless of configuration — a hen cannot be configured
# pregnant, however the rules are written.
VIVIPAROUS = "viviparous"  # live birth: cattle, sheep, goats, horses
OVIPAROUS = "oviparous"  # eggs: chickens, ducks, turkeys
UNSPECIFIED = "unspecified"  # "other": no invariant beyond sex


# --------------------------------------------------------------------------
# Capability codes. Grouped by category because the profile screen and
# the validation layer both work a category at a time.
# --------------------------------------------------------------------------
class Cap:
    # identification
    EAR_TAG = "EAR_TAG"
    RFID = "RFID"
    MICROCHIP = "MICROCHIP"
    LEG_BAND = "LEG_BAND"
    PASSPORT = "PASSPORT"
    INDIVIDUAL_TRACKING = "INDIVIDUAL_TRACKING"
    GROUP_TRACKING = "GROUP_TRACKING"
    # reproduction
    BREEDING = "BREEDING"
    PREGNANCY = "PREGNANCY"
    LIVE_BIRTH = "LIVE_BIRTH"
    INCUBATION = "INCUBATION"
    HATCHING = "HATCHING"
    # production
    MILK_PRODUCTION = "MILK_PRODUCTION"
    LACTATION = "LACTATION"
    EGG_PRODUCTION = "EGG_PRODUCTION"
    WOOL_PRODUCTION = "WOOL_PRODUCTION"
    GROWTH_TRACKING = "GROWTH_TRACKING"
    # management / health
    WEIGHT_TRACKING = "WEIGHT_TRACKING"
    BODY_CONDITION = "BODY_CONDITION"
    HOOF_CARE = "HOOF_CARE"
    HEALTH = "HEALTH"
    FEED = "FEED"
    MORTALITY = "MORTALITY"


IDENTIFICATION = "identification"
REPRODUCTION = "reproduction"
PRODUCTION = "production"
HEALTH = "health"
MANAGEMENT = "management"

# (code, category, label_en, label_ar)
CAPABILITIES: tuple[tuple[str, str, str, str], ...] = (
    (Cap.EAR_TAG, IDENTIFICATION, "Ear tag", "رقم الأذن"),
    (Cap.RFID, IDENTIFICATION, "RFID", "شريحة RFID"),
    (Cap.MICROCHIP, IDENTIFICATION, "Microchip", "شريحة إلكترونية"),
    (Cap.LEG_BAND, IDENTIFICATION, "Leg band", "حلقة الساق"),
    (Cap.PASSPORT, IDENTIFICATION, "Passport", "جواز الحيوان"),
    (Cap.INDIVIDUAL_TRACKING, IDENTIFICATION, "Tracked individually", "متابعة فردية"),
    (Cap.GROUP_TRACKING, IDENTIFICATION, "Tracked as a group", "متابعة جماعية"),
    (Cap.BREEDING, REPRODUCTION, "Breeding", "التكاثر"),
    (Cap.PREGNANCY, REPRODUCTION, "Pregnancy", "الحمل"),
    (Cap.LIVE_BIRTH, REPRODUCTION, "Live birth", "الولادة"),
    (Cap.INCUBATION, REPRODUCTION, "Incubation", "التحضين"),
    (Cap.HATCHING, REPRODUCTION, "Hatching", "الفقس"),
    (Cap.MILK_PRODUCTION, PRODUCTION, "Milk production", "إنتاج الحليب"),
    (Cap.LACTATION, PRODUCTION, "Lactation", "الإدرار"),
    (Cap.EGG_PRODUCTION, PRODUCTION, "Egg production", "إنتاج البيض"),
    (Cap.WOOL_PRODUCTION, PRODUCTION, "Wool production", "إنتاج الصوف"),
    (Cap.GROWTH_TRACKING, PRODUCTION, "Growth tracking", "متابعة النمو"),
    (Cap.WEIGHT_TRACKING, MANAGEMENT, "Weight tracking", "متابعة الوزن"),
    (Cap.BODY_CONDITION, MANAGEMENT, "Body condition", "الحالة الجسدية"),
    (Cap.HOOF_CARE, MANAGEMENT, "Hoof / farrier care", "العناية بالحوافر"),
    (Cap.HEALTH, HEALTH, "Health & treatment", "الصحة والعلاج"),
    (Cap.FEED, MANAGEMENT, "Feed", "العلف"),
    (Cap.MORTALITY, MANAGEMENT, "Mortality", "النفوق"),
)
CAPABILITY_BY_CODE: dict[str, tuple[str, str, str]] = {
    code: (category, label_en, label_ar) for code, category, label_en, label_ar in CAPABILITIES
}
CAPABILITY_ORDER: dict[str, int] = {code: i for i, (code, *_rest) in enumerate(CAPABILITIES)}

# Which identifier types an identification capability permits. NAME,
# FARM_NUMBER, REGISTRATION_NUMBER and OTHER are always allowed — every
# animal may be called something.
IDENTIFIER_TYPES_ALWAYS_ALLOWED = frozenset({"NAME", "FARM_NUMBER", "REGISTRATION_NUMBER", "OTHER"})
IDENTIFIER_TYPE_FOR_CAPABILITY = {
    Cap.EAR_TAG: "EAR_TAG",
    Cap.RFID: "RFID",
    Cap.MICROCHIP: "MICROCHIP",
    Cap.LEG_BAND: "LEG_BAND",
    Cap.PASSPORT: "PASSPORT",
}
IDENTIFIER_TYPES = frozenset(IDENTIFIER_TYPES_ALWAYS_ALLOWED) | frozenset(
    IDENTIFIER_TYPE_FOR_CAPABILITY.values()
)

# --------------------------------------------------------------------------
# Life stages and management profiles. Generic codes; a species may add
# its own rows (e.g. `heifer` for cattle) — the resolver treats a
# species-specific stage exactly like a generic one.
# --------------------------------------------------------------------------
# (code, species_code or None, label_en, label_ar, sort_order)
LIFE_STAGES: tuple[tuple[str, str | None, str, str, int], ...] = (
    ("newborn", None, "Newborn", "حديث الولادة", 1),
    ("young", None, "Young", "صغير", 2),
    ("adult", None, "Adult", "بالغ", 3),
    ("senior", None, "Senior", "مسنّ", 4),
)
LIFE_STAGE_CODES = frozenset(code for code, *_rest in LIFE_STAGES)

# (code, species_code or None, label_en, label_ar)
MANAGEMENT_PROFILES: tuple[tuple[str, str | None, str, str], ...] = (
    ("dairy", None, "Dairy", "حليب"),
    ("meat", None, "Meat", "لحم"),
    ("breeding", None, "Breeding", "تكاثر"),
    ("dual_purpose", None, "Dual purpose", "مزدوج الغرض"),
    ("wool", None, "Wool", "صوف"),
    ("layer", None, "Layer", "بيّاض"),
    ("broiler", None, "Broiler", "لاحم"),
    ("work", None, "Work / riding", "عمل / ركوب"),
    ("companion", None, "Companion", "مرافقة"),
)
MANAGEMENT_PROFILE_CODES = frozenset(code for code, *_rest in MANAGEMENT_PROFILES)


# --------------------------------------------------------------------------
# Species. Terminology is what the UI says for a generic event — the
# event type stays `birth`, the screen says "Calving".
# --------------------------------------------------------------------------
def _species(
    code: str,
    name_en: str,
    name_ar: str,
    mode: str,
    icon: str,
    default_mgmt: str,
    *,
    birth: tuple[str, str],
    offspring: tuple[str, str],
    group: tuple[str, str],
    female: tuple[str, str],
    male: tuple[str, str],
    sort: int,
    profiles: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "code": code,
        "name_en": name_en,
        "name_ar": name_ar,
        "reproduction_mode": mode,
        "icon": icon,
        "default_management": default_mgmt,
        # Which management profiles the Add Animal flow offers for this
        # species. Empty means all of them.
        "profiles_json": list(profiles),
        "terminology_json": {
            "birth_en": birth[0],
            "birth_ar": birth[1],
            "offspring_en": offspring[0],
            "offspring_ar": offspring[1],
            "group_en": group[0],
            "group_ar": group[1],
            "female_en": female[0],
            "female_ar": female[1],
            "male_en": male[0],
            "male_ar": male[1],
        },
        "active": True,
        "sort_order": sort,
    }


SPECIES: tuple[dict[str, Any], ...] = (
    _species(
        "cow",
        "Cattle",
        "أبقار",
        VIVIPAROUS,
        "cow",
        "individual",
        birth=("Calving", "ولادة"),
        offspring=("Calf", "عجل"),
        group=("Herd", "قطيع"),
        female=("Cow", "بقرة"),
        male=("Bull", "ثور"),
        sort=1,
        profiles=("dairy", "meat", "breeding", "dual_purpose"),
    ),
    _species(
        "sheep",
        "Sheep",
        "أغنام",
        VIVIPAROUS,
        "sheep",
        "either",
        birth=("Lambing", "ولادة"),
        offspring=("Lamb", "حمل"),
        group=("Flock", "قطيع"),
        female=("Ewe", "نعجة"),
        male=("Ram", "كبش"),
        sort=2,
        profiles=("dairy", "meat", "wool", "breeding", "dual_purpose"),
    ),
    _species(
        "goat",
        "Goats",
        "ماعز",
        VIVIPAROUS,
        "goat",
        "either",
        birth=("Kidding", "ولادة"),
        offspring=("Kid", "جدي"),
        group=("Herd", "قطيع"),
        female=("Doe", "عنزة"),
        male=("Buck", "تيس"),
        sort=3,
        profiles=("dairy", "meat", "breeding", "dual_purpose"),
    ),
    _species(
        "horse",
        "Horses",
        "خيول",
        VIVIPAROUS,
        "horse",
        "individual",
        birth=("Foaling", "ولادة"),
        offspring=("Foal", "مهر"),
        group=("Herd", "قطيع"),
        female=("Mare", "فرس"),
        male=("Stallion", "حصان"),
        sort=4,
        profiles=("breeding", "work", "companion"),
    ),
    _species(
        "layer_hen",
        "Chickens",
        "دجاج",
        OVIPAROUS,
        "poultry",
        "group",
        birth=("Hatching", "فقس"),
        offspring=("Chick", "صوص"),
        group=("Flock", "سرب"),
        female=("Hen", "دجاجة"),
        male=("Rooster", "ديك"),
        sort=5,
        profiles=("layer", "broiler", "breeding"),
    ),
    _species(
        "duck",
        "Ducks",
        "بط",
        OVIPAROUS,
        "duck",
        "group",
        birth=("Hatching", "فقس"),
        offspring=("Duckling", "فرخ بط"),
        group=("Flock", "سرب"),
        female=("Duck", "بطة"),
        male=("Drake", "ذكر البط"),
        sort=6,
        profiles=("layer", "broiler", "breeding"),
    ),
    _species(
        "turkey",
        "Turkeys",
        "ديك رومي",
        OVIPAROUS,
        "poultry",
        "group",
        birth=("Hatching", "فقس"),
        offspring=("Poult", "فرخ"),
        group=("Flock", "سرب"),
        female=("Hen", "أنثى"),
        male=("Tom", "ذكر"),
        sort=7,
        profiles=("layer", "broiler", "breeding"),
    ),
    _species(
        "other",
        "Other",
        "أخرى",
        UNSPECIFIED,
        "barn",
        "either",
        birth=("Birth", "ولادة"),
        offspring=("Offspring", "صغير"),
        group=("Group", "مجموعة"),
        female=("Female", "أنثى"),
        male=("Male", "ذكر"),
        sort=99,
    ),
)
SPECIES_BY_CODE: dict[str, dict[str, Any]] = {s["code"]: s for s in SPECIES}

# A fixed namespace so the catalog's breeds and rules carry the same ids on
# every server and across restarts — the tablet caches them.
_NAMESPACE = uuid.UUID("3b1f2d8e-5c4a-4a6e-9f0b-7d2e1c9a4f66")


def stable_id(*parts: object) -> str:
    return str(uuid.uuid5(_NAMESPACE, ":".join(str(p) for p in parts)))


# Breeds this farm's region actually keeps — a starting catalog, not a
# closed list; the form accepts free text too.
# (species_code, name, name_ar)
_BREEDS: tuple[tuple[str, str, str | None], ...] = (
    ("cow", "Holstein Friesian", "هولشتاين"),
    ("cow", "Jersey", "جيرسي"),
    ("cow", "Baladi", "بلدي"),
    ("sheep", "Awassi", "عواسي"),
    ("sheep", "Assaf", "عساف"),
    ("goat", "Damascus (Shami)", "شامي"),
    ("goat", "Baladi", "بلدي"),
    ("goat", "Saanen", "سانين"),
    ("horse", "Arabian", "عربي"),
    ("horse", "Anglo-Arab", "أنجلو عربي"),
    ("layer_hen", "Lohmann Brown", "لوهمان براون"),
    ("layer_hen", "Hy-Line", "هاي لاين"),
    ("layer_hen", "Baladi", "بلدي"),
    ("duck", "Pekin", "بكيني"),
    ("duck", "Muscovy", "مسكوفي"),
    ("turkey", "Broad Breasted Bronze", "برونزي"),
)
BREEDS: tuple[dict[str, Any], ...] = tuple(
    {
        "id": stable_id("breed", species_code, name),
        "species_code": species_code,
        "name": name,
        "name_ar": name_ar,
        "active": True,
    }
    for species_code, name, name_ar in _BREEDS
)


# --------------------------------------------------------------------------
# Rules. A rule with no sex / life stage / profile applies to the whole
# species; a more specific rule overrides it for that combination.
# `required` marks the identifier an animal must have to be registered.
# --------------------------------------------------------------------------
def _r(
    species: str,
    cap: str,
    *,
    sex: str | None = None,
    stage: str | None = None,
    profile: str | None = None,
    enabled: bool = True,
    required: bool = False,
    config: dict | None = None,
    priority: int = 0,
) -> dict[str, Any]:
    return {
        "species_code": species,
        "capability_code": cap,
        "sex": sex,
        "life_stage": stage,
        "management_profile": profile,
        "enabled": enabled,
        "required": required,
        "configuration_json": config,
        "priority": priority,
    }


def _mammal_base(
    species: str, *, ear_tag_required: bool = True, hoof: bool = False, wool: bool = False
) -> list:
    rules = [
        _r(species, Cap.EAR_TAG, required=ear_tag_required),
        _r(species, Cap.RFID),
        _r(species, Cap.INDIVIDUAL_TRACKING, required=True),
        _r(species, Cap.HEALTH, required=True),
        _r(species, Cap.FEED),
        _r(species, Cap.WEIGHT_TRACKING),
        _r(species, Cap.BODY_CONDITION),
        _r(species, Cap.GROWTH_TRACKING),
        _r(species, Cap.BREEDING, stage="adult"),
        # Females carry the pregnancy line; the resolver's invariants
        # strip it from males even if a rule were ever written that way.
        _r(species, Cap.PREGNANCY, sex=FEMALE, stage="adult"),
        _r(species, Cap.LIVE_BIRTH, sex=FEMALE, stage="adult"),
        _r(species, Cap.MORTALITY),
    ]
    if hoof:
        rules.append(_r(species, Cap.HOOF_CARE))
    if wool:
        rules.append(_r(species, Cap.WOOL_PRODUCTION))
    return rules


def _poultry_base(species: str) -> list:
    return [
        _r(species, Cap.LEG_BAND),
        _r(species, Cap.GROUP_TRACKING, required=True),
        _r(species, Cap.INDIVIDUAL_TRACKING),
        _r(species, Cap.HEALTH, required=True),
        _r(species, Cap.FEED),
        _r(species, Cap.WEIGHT_TRACKING),
        _r(species, Cap.GROWTH_TRACKING),
        _r(species, Cap.MORTALITY),
        _r(species, Cap.BREEDING, stage="adult"),
        _r(species, Cap.EGG_PRODUCTION, sex=FEMALE, stage="adult"),
        _r(species, Cap.INCUBATION, profile="breeding"),
        _r(species, Cap.HATCHING, profile="breeding"),
        # A broiler flock is grown, not laid from.
        _r(species, Cap.EGG_PRODUCTION, profile="broiler", enabled=False, priority=10),
        _r(species, Cap.BREEDING, profile="broiler", enabled=False, priority=10),
    ]


def _with_ids(rules: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    seen: dict[str, int] = {}
    out = []
    for rule in rules:
        key = stable_id(
            "rule",
            rule["species_code"],
            rule["capability_code"],
            rule["sex"],
            rule["life_stage"],
            rule["management_profile"],
            rule["enabled"],
            rule["required"],
            rule["priority"],
        )
        # A duplicate line would otherwise share an id; suffix the repeat.
        n = seen.get(key, 0)
        seen[key] = n + 1
        out.append({"id": key if n == 0 else stable_id(key, n), **rule})
    return tuple(out)


RULES: tuple[dict[str, Any], ...] = _with_ids(
    # ---- cattle: dairy females milk; everyone else does not
    _mammal_base("cow", hoof=True)
    + [
        _r("cow", Cap.MILK_PRODUCTION, sex=FEMALE, stage="adult", profile="dairy"),
        _r("cow", Cap.LACTATION, sex=FEMALE, stage="adult", profile="dairy"),
        _r("cow", Cap.MILK_PRODUCTION, sex=FEMALE, stage="adult", profile="dual_purpose"),
        _r("cow", Cap.LACTATION, sex=FEMALE, stage="adult", profile="dual_purpose"),
    ]
    # ---- sheep: dairy flocks milk, meat flocks do not; wool is a profile
    + _mammal_base("sheep", hoof=True)
    + [
        _r("sheep", Cap.MILK_PRODUCTION, sex=FEMALE, stage="adult", profile="dairy"),
        _r("sheep", Cap.LACTATION, sex=FEMALE, stage="adult", profile="dairy"),
        _r("sheep", Cap.WOOL_PRODUCTION, profile="wool"),
        _r("sheep", Cap.WOOL_PRODUCTION, profile="dual_purpose"),
        _r("sheep", Cap.GROUP_TRACKING),
    ]
    # ---- goats
    + _mammal_base("goat", hoof=True)
    + [
        _r("goat", Cap.MILK_PRODUCTION, sex=FEMALE, stage="adult", profile="dairy"),
        _r("goat", Cap.LACTATION, sex=FEMALE, stage="adult", profile="dairy"),
        _r("goat", Cap.MILK_PRODUCTION, sex=FEMALE, stage="adult", profile="dual_purpose"),
        _r("goat", Cap.LACTATION, sex=FEMALE, stage="adult", profile="dual_purpose"),
        _r("goat", Cap.GROUP_TRACKING),
    ]
    # ---- horses: microchip/passport, farrier, never a dairy line
    + [
        _r("horse", Cap.MICROCHIP, required=True),
        _r("horse", Cap.PASSPORT),
        _r("horse", Cap.EAR_TAG, enabled=False),
        _r("horse", Cap.INDIVIDUAL_TRACKING, required=True),
        _r("horse", Cap.HEALTH, required=True),
        _r("horse", Cap.FEED),
        _r("horse", Cap.WEIGHT_TRACKING),
        _r("horse", Cap.BODY_CONDITION),
        _r("horse", Cap.GROWTH_TRACKING, stage="young"),
        _r("horse", Cap.HOOF_CARE, required=True),
        _r("horse", Cap.MORTALITY),
        _r("horse", Cap.BREEDING, stage="adult"),
        _r("horse", Cap.PREGNANCY, sex=FEMALE, stage="adult"),
        _r("horse", Cap.LIVE_BIRTH, sex=FEMALE, stage="adult"),
    ]
    # ---- poultry
    + _poultry_base("layer_hen")
    + _poultry_base("duck")
    + _poultry_base("turkey")
    # ---- other: the safe minimum; a farm configures the rest
    + [
        _r("other", Cap.EAR_TAG),
        _r("other", Cap.RFID),
        _r("other", Cap.MICROCHIP),
        _r("other", Cap.INDIVIDUAL_TRACKING),
        _r("other", Cap.GROUP_TRACKING),
        _r("other", Cap.HEALTH, required=True),
        _r("other", Cap.FEED),
        _r("other", Cap.WEIGHT_TRACKING),
        _r("other", Cap.MORTALITY),
    ]
)
RULES_BY_SPECIES: dict[str, list[dict[str, Any]]] = {}
for _rule in RULES:
    RULES_BY_SPECIES.setdefault(_rule["species_code"], []).append(_rule)

# The default life stage when none is given: an animal registered with no
# stage is assumed adult — that is when almost every capability applies,
# and the form asks for a date of birth to refine it.
DEFAULT_LIFE_STAGE = "adult"

# Animal statuses the tablet contract exchanges.
ANIMAL_STATUSES = frozenset({"healthy", "under_observation", "under_treatment"})

# Health-care vocabularies the care record uses.
CONDITION_TYPES = ("allergy", "adverse_reaction", "chronic_condition", "contraindication")
WEIGHT_METHODS = ("scale", "tape", "visual_estimate", "point_of_care", "legacy")


def normalise_sex(value: str | None) -> str | None:
    """Accepts F/M/U and the spec's FEMALE/MALE/UNKNOWN; returns the code,
    or raises ValueError for anything else."""
    if value is None or not str(value).strip():
        return None
    code = str(value).strip().upper()
    code = SEX_ALIASES.get(code, code)
    if code not in SEXES:
        raise ValueError(f"sex must be one of {list(SEXES)} (or FEMALE / MALE / UNKNOWN)")
    return code
