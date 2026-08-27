from __future__ import annotations

from enum import Enum
from typing import TypeVar

from sqlalchemy import Enum as SAEnum

E = TypeVar("E", bound=Enum)


def str_enum(enum_cls: type[E], **kwargs):
    """A VARCHAR-backed enum column (no native PG enum type).

    Native PostgreSQL enum types make adding a new state a schema migration
    with ALTER TYPE quirks; a validated string column keeps state-machine
    changes to application code + a simple Alembic data migration instead.
    """
    return SAEnum(enum_cls, native_enum=False, validate_strings=True, length=64, **kwargs)
