"""Reference data and fixed vocabularies for the farm pharmacy
(OrigamiFarmOS database/MEDICINE-PHARMACY-SCHEMA.md,
database/MEDICINE-NOTIFICATION-RULES.md).

Code-defined and the same for every tenant, like app/farmos/feed_catalog.py:
the medicine categories (organisation and policy, never a diagnosis), the
built-in active-ingredient list (a farm may use any other code; the name
travels with the product), dosage forms, routes, lot and alert
vocabularies, and the route each alert type travels.
"""

from __future__ import annotations

from app.farmos.feed_catalog import FeedError


class PharmacyError(FeedError):
    """A pharmacy rule refused the operation; answered like a feed rule."""


LOT_STATUSES = ("active", "quarantined", "blocked", "recalled", "expired", "depleted")
STORAGE_STATUSES = ("COMPLIANT", "EXCEPTION")
ALERT_TYPES = (
    "BELOW_MINIMUM_STOCK",
    "CRITICAL_STOCK",
    "OUT_OF_STOCK",
    "LOW_DAYS_COVER",
    "EXPIRING_SOON",
    "EXPIRED_STOCK",
    "RECALL_AFFECTED",
    "STORAGE_EXCEPTION",
)
STOCK_ALERT_TYPES = ("BELOW_MINIMUM_STOCK", "CRITICAL_STOCK", "OUT_OF_STOCK")
DOSAGE_FORMS = (
    "injectable",
    "oral",
    "intravenous_fluid",
    "topical",
    "intramammary",
    "pour_on",
    "vaccine",
    "powder",
    "bolus",
    "other",
)
ROUTES = ("IM", "IV", "SC", "PO", "topical", "intramammary", "pour_on", "intranasal", "ocular", "other")
RESERVATION_PURPOSES = ("emergency_run", "treatment", "planned_procedure", "other")
LOT_SOURCE_TYPES = ("purchased", "opening_balance", "donation", "return")
ADJUSTMENT_REASONS = ("adjustment", "waste", "return", "count_correction", "transfer")
QUALITY_FOR_STATUS = {
    "quarantined": "QUARANTINED",
    "blocked": "BLOCKED",
    "recalled": "RECALLED",
    "expired": "EXPIRED",
}
# Which notification route each alert type travels (MEDICINE-NOTIFICATION-RULES).
ROUTE_FOR_ALERT = {
    "OUT_OF_STOCK": "pharmacy_critical",
    "CRITICAL_STOCK": "pharmacy_critical",
    "BELOW_MINIMUM_STOCK": "pharmacy_stock",
    "LOW_DAYS_COVER": "pharmacy_stock",
    "EXPIRED_STOCK": "pharmacy_expiry",
    "EXPIRING_SOON": "pharmacy_expiry",
    "RECALL_AFFECTED": "pharmacy_critical",
    "STORAGE_EXCEPTION": "pharmacy_critical",
}
SEVERITY = {
    "OUT_OF_STOCK": "critical",
    "CRITICAL_STOCK": "high",
    "BELOW_MINIMUM_STOCK": "medium",
    "LOW_DAYS_COVER": "medium",
    "EXPIRED_STOCK": "medium",
    "EXPIRING_SOON": "low",
    "RECALL_AFFECTED": "high",
    "STORAGE_EXCEPTION": "high",
}
SAFETY = "A stock alert is a logistics finding against the farm's own policy, not treatment advice."

# (code, name, name_ar)
CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("FEVER_SUPPORT", "Fever support", "دعم الحمّى"),
    ("PAIN_INFLAMMATION", "Pain & inflammation", "الألم والالتهاب"),
    ("COLIC_SUPPORT", "Colic support", "دعم المغص"),
    ("IV_FLUID", "IV fluids", "سوائل وريدية"),
    ("ELECTROLYTE", "Electrolytes", "إلكتروليتات"),
    ("ANTIBIOTIC", "Antibiotics", "مضادات حيوية"),
    ("ANTIPARASITIC", "Antiparasitics", "مضادات الطفيليات"),
    ("VACCINE", "Vaccines", "لقاحات"),
    ("WOUND_CARE", "Wound care", "العناية بالجروح"),
    ("OTHER", "Other", "أخرى"),
)
CATEGORY_BY_CODE: dict[str, dict] = {
    code: {"code": code, "name": name, "name_ar": name_ar, "active": True}
    for code, name, name_ar in CATEGORIES
}
# (code, name) — the built-in list; any other code is accepted with its name.
INGREDIENTS: tuple[tuple[str, str], ...] = (
    ("oxytetracycline", "Oxytetracycline"),
    ("flunixin_meglumine", "Flunixin meglumine"),
    ("sodium_chloride", "Sodium chloride"),
    ("ivermectin", "Ivermectin"),
    ("penicillin_g", "Penicillin G"),
    ("dextrose", "Dextrose"),
    ("calcium_borogluconate", "Calcium borogluconate"),
    ("meloxicam", "Meloxicam"),
    ("clostridial_toxoid", "Clostridial toxoid"),
    ("glucose_electrolyte", "Glucose / electrolyte"),
)
INGREDIENT_NAME_BY_CODE: dict[str, str] = dict(INGREDIENTS)


def category(code: str) -> dict:
    row = CATEGORY_BY_CODE.get((code or "").strip().upper())
    if row is None:
        raise PharmacyError(f"Unknown medicine category '{code}'")
    return row


def ingredient_code(code: str) -> str:
    key = (code or "").strip().lower().replace(" ", "_")
    if not key:
        raise PharmacyError("An ingredient needs a code.")
    return key


def ingredient_name(code: str, name: str | None = None) -> str:
    key = ingredient_code(code)
    return (name or "").strip() or INGREDIENT_NAME_BY_CODE.get(key, code.strip())
