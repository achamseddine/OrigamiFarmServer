"""Reference data and shared primitives for the generic feed architecture
(OrigamiFarmOS docs/GENERIC-FEED-ARCHITECTURE.md): the nutrient catalog,
the fixed vocabularies, the unit-of-measure conversion engine and the
error type every feed rule refuses with.

Like app/farmos/livestock_catalog.py this is code-defined and the same
for every tenant; a farm's own nutrients and units are a later stage.
"""

from __future__ import annotations

from fastapi.exceptions import HTTPException


class FeedError(HTTPException):
    """A rule of the feed architecture refused the operation. Carries the
    HTTP status the API answers with and a sentence written for the
    farmer, not the log — the contract's `{"detail": "..."}` shape."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(status_code=status_code, detail=message)
        self.message = message


# --------------------------------------------------------------- nutrients
# Nutrients are rows, not columns (§8): the profile tables carry
# (nutrient, value, unit, basis) and never need a new column.
# (code, name_en, name_ar, unit, category, sort_order)
NUTRIENTS: tuple[tuple[str, str, str, str, str, int], ...] = (
    ("DM", "Dry matter", "المادة الجافة", "%", "composition", 10),
    ("MOISTURE", "Moisture", "الرطوبة", "%", "composition", 11),
    ("CP", "Crude protein", "البروتين الخام", "%", "protein", 20),
    ("ME", "Metabolisable energy", "الطاقة الأيضية", "MJ/kg", "energy", 30),
    ("NEL", "Net energy for lactation", "طاقة الإدرار الصافية", "MJ/kg", "energy", 31),
    ("NDF", "Neutral detergent fibre", "الألياف NDF", "%", "fibre", 40),
    ("ADF", "Acid detergent fibre", "الألياف ADF", "%", "fibre", 41),
    ("CF", "Crude fibre", "الألياف الخام", "%", "fibre", 42),
    ("FAT", "Crude fat", "الدهون الخام", "%", "fat", 50),
    ("ASH", "Ash", "الرماد", "%", "minerals", 60),
    ("CA", "Calcium", "الكالسيوم", "%", "minerals", 61),
    ("P", "Phosphorus", "الفوسفور", "%", "minerals", 62),
    ("NA", "Sodium", "الصوديوم", "%", "minerals", 63),
    ("MG", "Magnesium", "المغنيسيوم", "%", "minerals", 64),
    ("VIT_A", "Vitamin A", "فيتامين أ", "IU/kg", "vitamins", 70),
    ("VIT_D", "Vitamin D", "فيتامين د", "IU/kg", "vitamins", 71),
    ("VIT_E", "Vitamin E", "فيتامين هـ", "mg/kg", "vitamins", 72),
)
NUTRIENT_BY_CODE: dict[str, dict] = {
    code: {"code": code, "name_en": en, "name_ar": ar, "unit": unit, "category": cat, "sort_order": order}
    for code, en, ar, unit, cat, order in NUTRIENTS
}

# Values are stored as-fed or on a dry-matter basis; the profile says which.
BASES = ("as_fed", "dry_matter")
PROFILE_SOURCES = ("declared", "calculated", "lab")
PROFILE_SUBJECTS = ("feed_product", "formula_version", "feed_lot", "feed_batch")

# Assignment kinds (§9). `inherited` is never stored — it is what an animal
# gets from its group when it has no explicit assignment of its own.
ASSIGNMENT_TYPES = ("explicit", "supplement", "override", "restriction")
SUBJECT_TYPES = ("animal", "group")
FEEDING_EVENT_TYPES = ("offered", "delivered", "consumed_estimate", "refusal")
LOT_STATUSES = ("active", "quarantined", "blocked", "recalled", "expired", "depleted")
# Lot states that make stock unusable without an authorised exception (§16).
LOT_UNUSABLE = ("quarantined", "blocked", "recalled", "expired")
POLICY_EFFECTS = ("allow", "block", "limit")
PRODUCT_SOURCES = ("purchased", "farm_produced")
ADJUSTMENT_REASONS = ("adjustment", "waste", "return", "reconciliation_adjustment", "count_correction")

# Inventory categories that are stock but not feed: they keep the plain
# item ledger and never become feed products or lots.
NOT_FEED_CATEGORIES = frozenset({"medicine", "medication", "vet", "veterinary", "produce"})


# ------------------------------------------------------------------- units
# The units-of-measure catalogue (FOUNDATION-SCHEMA uom). Units of one
# dimension that name a base unit convert through their factor (g → kg,
# ml → l). Count-like units deliberately carry no base: a sachet is never
# silently a dose.
# (code, name_en, name_ar, dimension, base_unit_code, factor_to_base, sort_order)
UNITS: tuple[tuple[str, str, str, str, str | None, float, int], ...] = (
    ("kg", "Kilogram", "كيلوغرام", "mass", None, 1.0, 10),
    ("g", "Gram", "غرام", "mass", "kg", 0.001, 11),
    ("mg", "Milligram", "ميليغرام", "mass", "kg", 0.000001, 12),
    ("t", "Tonne", "طن", "mass", "kg", 1000.0, 13),
    ("lb", "Pound", "رطل", "mass", "kg", 0.45359237, 14),
    ("l", "Litre", "لتر", "volume", None, 1.0, 20),
    ("ml", "Millilitre", "ميليلتر", "volume", "l", 0.001, 21),
    ("items", "Items", "قطع", "count", None, 1.0, 30),
    ("head", "Head", "رأس", "count", None, 1.0, 31),
    ("bale", "Bale", "بالة", "count", None, 1.0, 32),
    ("bag", "Bag", "كيس", "count", None, 1.0, 33),
    ("dose", "Dose", "جرعة", "count", None, 1.0, 34),
    ("sachet", "Sachet", "ظرف", "count", None, 1.0, 35),
    ("tube", "Tube", "أنبوب", "count", None, 1.0, 36),
    ("tablet", "Tablet", "قرص", "count", None, 1.0, 37),
    ("bolus", "Bolus", "بلعة", "count", None, 1.0, 38),
    ("vial", "Vial", "قارورة", "count", None, 1.0, 39),
    ("bottle", "Bottle", "زجاجة", "count", None, 1.0, 40),
    ("pack", "Pack", "عبوة", "count", None, 1.0, 41),
    ("dozen", "Dozen", "دزينة", "count", None, 1.0, 42),
    ("mg/ml", "Milligram per millilitre", "ملغ/مل", "concentration", None, 1.0, 50),
    ("iu/ml", "International units per millilitre", "وحدة دولية/مل", "concentration", None, 1.0, 51),
    ("%", "Percent", "بالمئة", "concentration", None, 1.0, 52),
    ("mg/g", "Milligram per gram", "ملغ/غ", "concentration", None, 1.0, 53),
    ("g/l", "Gram per litre", "غ/ل", "concentration", None, 1.0, 54),
    ("iu", "International units", "وحدة دولية", "concentration", None, 1.0, 55),
    ("mg/kg", "Milligram per kilogram", "ملغ/كغ", "dose_rate", None, 1.0, 60),
    ("ml/kg", "Millilitre per kilogram", "مل/كغ", "dose_rate", None, 1.0, 61),
    ("iu/kg", "International units per kilogram", "وحدة دولية/كغ", "dose_rate", None, 1.0, 62),
    ("mj", "Megajoule", "ميغاجول", "energy", None, 1.0, 70),
    ("mj/kg", "Megajoule per kilogram", "ميغاجول/كغ", "energy", None, 1.0, 71),
    ("day", "Day", "يوم", "time", None, 1.0, 80),
    ("hour", "Hour", "ساعة", "time", "day", 1 / 24, 81),
    ("minute", "Minute", "دقيقة", "time", "day", 1 / 1440, 82),
    ("dunum", "Dunum", "دونم", "area", None, 1.0, 90),
    ("ha", "Hectare", "هكتار", "area", "dunum", 10.0, 91),
)

# unit -> (family, factor to the family's base unit). Count-like units are
# each their own family so a bale is never silently a bag.
_UNIT_FAMILIES: dict[str, tuple[str, float]] = {}
for _code, _en, _ar, _dimension, _base, _factor, _order in UNITS:
    _family = _dimension if _base is not None or _dimension in ("mass", "volume", "time", "area") else _code
    _UNIT_FAMILIES[_code] = (_family, _factor)

_ALIASES = {
    "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
    "gram": "g", "grams": "g", "milligram": "mg", "milligrams": "mg",
    "ton": "t", "tonne": "t", "tonnes": "t", "mt": "t",
    "lbs": "lb", "pound": "lb", "pounds": "lb",
    "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "milliliter": "ml", "millilitre": "ml", "cc": "ml",
    "item": "items", "unit": "items", "units": "items", "pcs": "items",
    "bales": "bale", "bags": "bag", "doses": "dose", "sachets": "sachet", "tubes": "tube",
    "tablets": "tablet",
    "vials": "vial", "bottles": "bottle", "packs": "pack",
    "percent": "%", "pct": "%",
    "hours": "hour", "minutes": "minute", "min": "minute", "days": "day",
    "hectare": "ha", "hectares": "ha", "dunums": "dunum",
}  # fmt: skip


class IncompatibleUnits(FeedError):
    """A conversion that crosses families — kilograms to litres."""

    def __init__(self, src: str, dst: str):
        super().__init__(f"Cannot convert {describe(src)} to {describe(dst)}: they measure different things.")


def normalise(unit: str | None) -> str:
    code = (unit or "kg").strip().lower()
    return _ALIASES.get(code, code)


def family(unit: str | None) -> str:
    code = normalise(unit)
    return _UNIT_FAMILIES.get(code, (code, 1.0))[0]


def compatible(a: str | None, b: str | None) -> bool:
    return family(a) == family(b)


def convert(quantity: float, from_unit: str | None, to_unit: str | None) -> float:
    """Converts within a family; raises IncompatibleUnits across them.
    Anything outside the catalog is its own family with no conversions,
    so an unknown unit can never be silently mistaken for a known one."""
    src, dst = normalise(from_unit), normalise(to_unit)
    if src == dst:
        return quantity
    if family(src) != family(dst):
        raise IncompatibleUnits(src, dst)
    factor_src = _UNIT_FAMILIES.get(src, (src, 1.0))[1]
    factor_dst = _UNIT_FAMILIES.get(dst, (dst, 1.0))[1]
    return quantity * factor_src / factor_dst


def describe(unit: str | None) -> str:
    """The unit as the farm writes it: `kg`, `L`, `items`."""
    code = normalise(unit)
    return "L" if code == "l" else code
