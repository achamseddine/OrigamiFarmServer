"""Database engine/session wiring.

Two logically separate PostgreSQL databases are used, matching
ARCHITECTURE.md:

- ``control`` — the control plane (tenants, plans, entitlements, devices,
  billing metadata, audit, support). No Row-Level Security is required here
  because every query is already scoped by explicit tenant_id filters in
  application code and platform staff need cross-tenant visibility.
- ``tenant`` (shared) — the default farm operational data plane. Every table
  here has RLS enabled; see api/migrations for the policies. Access always
  goes through ``TenantDataRouter`` (app/common/tenant_router.py) so a
  dedicated-database tenant can be introduced later without changing call
  sites.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

# Every datetime in this system is timezone-aware UTC end to end (issued_at,
# expires_at, effective_from, etc.). Without this override, SQLAlchemy's
# default type inference for a bare `Mapped[datetime]` column produces
# TIMESTAMP WITHOUT TIME ZONE, which silently returns naive datetimes from
# Postgres and blows up the first time application code compares one
# against datetime.now(timezone.utc). Map it once here instead of
# annotating DateTime(timezone=True) on every single column.
_DATETIME_MAP = {datetime: DateTime(timezone=True)}


class ControlBase(DeclarativeBase):
    type_annotation_map = _DATETIME_MAP


class TenantBase(DeclarativeBase):
    type_annotation_map = _DATETIME_MAP


control_engine = create_engine(settings.control_database_url, pool_pre_ping=True, future=True)
ControlSessionLocal = sessionmaker(bind=control_engine, expire_on_commit=False, future=True)

tenant_shared_engine = create_engine(settings.tenant_database_url, pool_pre_ping=True, future=True)
TenantSharedSessionLocal = sessionmaker(
    bind=tenant_shared_engine, expire_on_commit=False, future=True
)


def get_control_db() -> Generator[Session, None, None]:
    db = ControlSessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
