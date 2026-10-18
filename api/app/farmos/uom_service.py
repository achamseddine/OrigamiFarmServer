"""Units of measure as references (FOUNDATION-SCHEMA uom).

app/farmos/feed_catalog.py is the conversion engine over the built-in
catalogue; this module is the authority on *which* unit codes exist for a
farm — the built-ins plus the farm's own `unit_of_measure` rows — and
converts through both, so a custom unit with a declared dimension and
factor converts like a built-in one. A unit a document names must be one
of them: a medicine's strength, a lot's unit, a dose unit, a requisition
line's unit are references, never free text.

Every function takes the farm's tenant session: a unit one farm invents
is never visible to another (RLS scopes the rows).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos import feed_catalog as catalog
from app.farmos.deps import AccessContext
from app.farmos.feed_catalog import FeedError
from app.farmos.platform_models import UOM_DIMENSIONS, UnitOfMeasure

KNOWN_UNITS: frozenset[str] = frozenset(code for code, *_rest in catalog.UNITS)
# Dimensions whose built-in base unit converts: the family is the dimension.
_CONVERTIBLE_DIMENSIONS = ("mass", "volume", "time", "area")
_BUILTIN_BASE = {"mass": "kg", "volume": "l", "time": "day", "area": "dunum"}


class UnitError(FeedError):
    """An unknown or incompatible unit."""


def normalise(code: str | None) -> str | None:
    if code is None or not str(code).strip():
        return None
    return catalog.normalise(str(code))


def farm_unit(db: Session, code: str | None) -> UnitOfMeasure | None:
    key = normalise(code)
    if key is None:
        return None
    return db.scalar(
        select(UnitOfMeasure).where(UnitOfMeasure.code == key, UnitOfMeasure.deleted_at.is_(None))
    )


def exists(db: Session, code: str | None) -> bool:
    key = normalise(code)
    if key is None:
        return False
    if key in KNOWN_UNITS:
        return True
    row = farm_unit(db, key)
    return row is not None and row.active


def require(db: Session, code: str | None, *, what: str = "unit") -> str:
    """The canonical code for `code`, or a refusal naming where to add it."""
    if code is None or not str(code).strip():
        raise UnitError(f"A {what} is required.")
    key = catalog.normalise(str(code))
    if key in KNOWN_UNITS:
        return key
    row = farm_unit(db, key)
    if row is None or not row.active:
        raise UnitError(
            f"Unknown {what} '{code}'. Add it under Settings → Units of measure "
            "(GET/POST /units-of-measure) before using it."
        )
    return row.code


def optional(db: Session, code: str | None, *, what: str = "unit") -> str | None:
    return None if code is None or not str(code).strip() else require(db, code, what=what)


def _family(db: Session, code: str | None, _depth: int = 0) -> tuple[str, float]:
    """(family, factor to the family's base) for a built-in or farm unit.
    A farm unit converts through its base unit; one with no base is its
    own family, like the built-in count units."""
    key = catalog.normalise(str(code or "kg"))
    if key in KNOWN_UNITS or _depth > 4:
        return catalog.family(key), catalog._UNIT_FAMILIES.get(key, (key, 1.0))[1]
    row = farm_unit(db, key)
    if row is None or not row.base_unit_code:
        return key, 1.0
    base_family, base_factor = _family(db, row.base_unit_code, _depth + 1)
    return base_family, (row.factor_to_base or 1.0) * base_factor


def compatible(db: Session, a: str | None, b: str | None) -> bool:
    return _family(db, a)[0] == _family(db, b)[0]


def convert(db: Session, quantity: float, from_unit: str | None, to_unit: str | None) -> float:
    src, dst = catalog.normalise(str(from_unit or "kg")), catalog.normalise(str(to_unit or "kg"))
    if src == dst:
        return quantity
    fam_src, factor_src = _family(db, src)
    fam_dst, factor_dst = _family(db, dst)
    if fam_src != fam_dst:
        raise catalog.IncompatibleUnits(src, dst)
    return quantity * factor_src / factor_dst


def builtin_dict(code: str) -> dict:
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
                "built_in": True,
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
        "built_in": True,
    }


def unit_dict(unit: UnitOfMeasure | str) -> dict:
    if isinstance(unit, str):
        return builtin_dict(unit)
    return {
        "code": unit.code,
        "name_en": unit.name_en,
        "name_ar": unit.name_ar,
        "dimension": unit.dimension,
        "base_unit_code": unit.base_unit_code,
        "factor_to_base": unit.factor_to_base,
        "active": unit.active,
        "sort_order": unit.sort_order,
        "built_in": False,
        "id": str(unit.id),
    }


def list_units(db: Session, *, include_inactive: bool = False) -> list[dict]:
    """The unit table every unit column references: built-in plus the
    farm's own, by dimension then sort order."""
    out = [builtin_dict(code) for code, *_rest in catalog.UNITS]
    stmt = select(UnitOfMeasure).where(UnitOfMeasure.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(UnitOfMeasure.active.is_(True))
    out += [unit_dict(u) for u in db.scalars(stmt)]
    order = {d: i for i, d in enumerate(UOM_DIMENSIONS)}
    out.sort(key=lambda u: (order.get(u["dimension"], 99), u["sort_order"], u["code"]))
    return out


def create_unit(
    db: Session,
    access: AccessContext,
    *,
    code: str,
    name_en: str,
    name_ar: str | None = None,
    dimension: str = "other",
    base_unit_code: str | None = None,
    factor_to_base: float = 1.0,
) -> UnitOfMeasure:
    """A farm's own unit. A unit that converts names a base unit of the
    same dimension and its factor; one without a base converts to nothing
    (a carton is never silently a dose)."""
    key = normalise(code)
    if not key or len(key) > 30:
        raise UnitError("A unit code is 1–30 characters.")
    if not name_en or not name_en.strip():
        raise UnitError("A unit needs a name.")
    if dimension not in UOM_DIMENSIONS:
        raise UnitError(f"dimension must be one of {list(UOM_DIMENSIONS)}")
    if key in KNOWN_UNITS or farm_unit(db, key) is not None:
        raise UnitError(f"Unit '{key}' already exists.")
    base = None
    if base_unit_code:
        base_key = normalise(base_unit_code)
        base_dimension = None
        if base_key in KNOWN_UNITS:
            base_dimension = builtin_dict(base_key)["dimension"]
        else:
            base_row = farm_unit(db, base_key)
            base_dimension = base_row.dimension if base_row is not None else None
        if base_dimension is None or base_dimension != dimension:
            raise UnitError("The base unit must exist and be of the same dimension.")
        if factor_to_base <= 0:
            raise UnitError("factor_to_base must be greater than zero.")
        base = base_key
    row = UnitOfMeasure(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        code=key,
        name_en=name_en.strip(),
        name_ar=name_ar,
        dimension=dimension,
        base_unit_code=base,
        factor_to_base=factor_to_base if base else 1.0,
        active=True,
        sort_order=500,
        created_by=access.user_id,
    )
    db.add(row)
    db.flush()
    return row
