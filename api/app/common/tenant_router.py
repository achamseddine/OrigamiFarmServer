"""Tenant Data Router.

Farm-data repositories call ``TenantDataRouter.session_for(tenant_id)``
instead of importing an engine directly. Today every tenant resolves to the
shared RLS database; introducing a dedicated-database tenant later is a
change to this file and a row in ``tenant_data_locator``, not a change to
every repository or to the FarmOS API contract (see TENANCY.md).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.common.db import ControlSessionLocal, tenant_shared_engine
from app.common.enums import TenantDataMode
from app.tenants.models import TenantDataLocator

_dedicated_engine_cache: dict[uuid.UUID, Engine] = {}


def _dedicated_engine_for(locator: TenantDataLocator) -> Engine:
    if locator.tenant_id in _dedicated_engine_cache:
        return _dedicated_engine_cache[locator.tenant_id]

    if not locator.connection_secret_ref:
        raise RuntimeError(
            f"Tenant {locator.tenant_id} is DEDICATED_DB but has no connection_secret_ref"
        )
    conn_str = os.environ.get(locator.connection_secret_ref)
    if not conn_str:
        raise RuntimeError(
            f"Environment variable {locator.connection_secret_ref} is not set for "
            f"dedicated tenant {locator.tenant_id}"
        )
    engine = create_engine(conn_str, pool_pre_ping=True, future=True)
    _dedicated_engine_cache[locator.tenant_id] = engine
    return engine


class TenantDataRouter:
    @staticmethod
    def _locator(tenant_id: uuid.UUID) -> TenantDataLocator | None:
        with ControlSessionLocal() as control_db:
            return control_db.get(TenantDataLocator, tenant_id)

    @classmethod
    @contextmanager
    def session_for(cls, tenant_id: uuid.UUID) -> Generator[Session, None, None]:
        locator = cls._locator(tenant_id)
        mode = locator.mode if locator else TenantDataMode.SHARED_RLS

        if mode == TenantDataMode.DEDICATED_DB:
            assert locator is not None  # implied by mode being DEDICATED_DB
            engine = _dedicated_engine_for(locator)
        else:
            engine = tenant_shared_engine

        session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        session = session_factory()
        try:
            # Trusted, server-resolved tenant_id only — never a client-supplied
            # value. RLS policies key off current_setting('app.tenant_id').
            # set_config(..., true) is used instead of "SET LOCAL x = :param"
            # because Postgres's SET command does not accept bind
            # parameters at all (only literals) — set_config is a regular
            # SQL function and the `true` third argument gives it the same
            # transaction-local scope SET LOCAL would have.
            session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
            )
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
