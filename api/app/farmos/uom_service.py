"""Units of measure as references (FOUNDATION-SCHEMA uom).

app/farmos/feed_catalog.py is the conversion engine over the built-in
catalogue; this module is the authority on *which* unit codes exist. A
unit a document names must be one of them: a medicine's strength, a lot's
unit, a dose unit, a requisition line's unit are references, never free
text. A farm's own units are a later stage (the platform masters).
"""

from __future__ import annotations

from app.farmos import feed_catalog as catalog
from app.farmos.feed_catalog import FeedError

KNOWN_UNITS: frozenset[str] = frozenset(code for code, *_rest in catalog.UNITS)


class UnitError(FeedError):
    """An unknown or incompatible unit."""


def normalise(code: str | None) -> str | None:
    if code is None or not str(code).strip():
        return None
    return catalog.normalise(str(code))


def require(code: str | None, *, what: str = "unit") -> str:
    """The canonical code for `code`, or a refusal naming the catalogue."""
    if code is None or not str(code).strip():
        raise UnitError(f"A {what} is required.")
    key = catalog.normalise(str(code))
    if key not in KNOWN_UNITS:
        raise UnitError(
            f"Unknown {what} '{code}'. Use one of the units of measure the farm knows "
            f"(for example {', '.join(sorted(KNOWN_UNITS)[:12])}, ...)."
        )
    return key


def optional(code: str | None, *, what: str = "unit") -> str | None:
    return None if code is None or not str(code).strip() else require(code, what=what)


def compatible(a: str | None, b: str | None) -> bool:
    return catalog.compatible(a, b)


def convert(quantity: float, from_unit: str | None, to_unit: str | None) -> float:
    return catalog.convert(quantity, from_unit, to_unit)


def unit_dict(code: str) -> dict:
    for c, name_en, name_ar, dimension, base, factor, order in catalog.UNITS:
        if c == code:
            return {
                "code": c,
                "name_en": name_en,
                "name_ar": name_ar,
                "dimension": dimension,
                "base_unit_code": base,
                "factor_to_base": factor,
                "active": True,
                "sort_order": order,
            }
    return {
        "code": code,
        "name_en": code,
        "name_ar": None,
        "dimension": "count",
        "base_unit_code": None,
        "factor_to_base": 1.0,
        "active": True,
        "sort_order": 999,
    }


def list_units() -> list[dict]:
    return [unit_dict(code) for code, *_rest in catalog.UNITS]
