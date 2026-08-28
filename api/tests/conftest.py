"""Test configuration.

Environment variables MUST be set before any `app.*` module is imported —
several modules (app.common.db, app.main) create engines / read settings
at import time via a cached get_settings(). Tests run against real local
PostgreSQL databases (origami_control_test / origami_tenant_shared_test),
not mocks, because the behavior under test — RLS isolation, entitlement
enforcement, idempotent sync — only means something against a real
Postgres. AUTH_DEV_MODE is enabled so tests can mint session tokens
without a live Keycloak.
"""

from __future__ import annotations

import os
import pathlib
import uuid

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# setdefault, not direct assignment: CI (see .github/workflows/ci.yml) sets
# these to point at two separate service containers on different ports;
# local runs fall back to one local Postgres instance with two databases.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "CONTROL_DATABASE_URL",
    "postgresql+psycopg://origami:origami_dev_password@localhost:5432/origami_control_test",
)
os.environ.setdefault(
    "TENANT_DATABASE_URL",
    "postgresql+psycopg://origami:origami_dev_password@localhost:5432/origami_tenant_shared_test",
)
os.environ.setdefault("AUTH_DEV_MODE", "true")
os.environ.setdefault("APP_SECRET_KEY", "test-only-secret-key")
os.environ.setdefault(
    "LICENSE_LEASE_PRIVATE_KEY_PATH",
    str(_REPO_ROOT / "infrastructure" / "keys" / "license_lease_private.pem"),
)
os.environ.setdefault(
    "LICENSE_LEASE_PUBLIC_KEY_PATH",
    str(_REPO_ROOT / "infrastructure" / "keys" / "license_lease_public.pem"),
)

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

API_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _run_migrations(ini_name: str, script_dir: str) -> None:
    cfg = Config(str(API_ROOT / ini_name))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations" / script_dir))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def _migrated_databases():
    _run_migrations("alembic_control.ini", "control")
    _run_migrations("alembic_tenant.ini", "tenant")
    yield


def _seed_farmos_module_catalog() -> None:
    """The FarmOS contract's fixed 20-code permission catalog plus its 2
    licence SKUs ("mouneh", "visits_agritourism") — reseeded after every
    per-test TRUNCATE (see _clean_slate below), same rows scripts/seed.py
    writes for a real environment. Imported from there rather than
    duplicated so the two can't drift apart.
    """
    import importlib.util

    from app.common.db import ControlSessionLocal
    from app.plans.models import ModuleCatalog

    seed_path = _REPO_ROOT / "scripts" / "seed.py"
    spec = importlib.util.spec_from_file_location("_seed_module", seed_path)
    seed_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_module)

    with ControlSessionLocal() as db:
        for code, (label_en, label_ar, group, license_code) in seed_module.FARMOS_MODULE_CATALOG.items():
            if db.get(ModuleCatalog, code) is None:
                db.add(
                    ModuleCatalog(
                        module_code=code,
                        name_en=label_en,
                        name_ar=label_ar,
                        group=group,
                        license_code=license_code,
                    )
                )
        db.commit()


@pytest.fixture(scope="session")
def app():
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture()
def client(app, _clean_slate):
    with TestClient(app) as c:
        yield c


_CONTROL_TABLES = [
    "farmos_idempotency_record",
    "audit_event",
    "support_session",
    "support_case",
    "invoice",
    "billing_account",
    "tenant_export",
    "backup_job",
    "usage_meter",
    "feature_flag",
    "file_object",
    "license_lease",
    "device_activation",
    "device",
    "tenant_entitlement",
    "subscription_item",
    "subscription",
    "plan_module",
    "membership_module_permission",
    "membership_farm_access",
    "tenant_membership",
    "platform_role_assignment",
    "tenant_data_locator",
    "farm",
    "module_catalog",
    "plan",
    "tenant",
    "user_identity",
]
_TENANT_TABLES = ["sync_event", "inventory_movement", "inventory_item", "task", "field", "animal"]


@pytest.fixture()
def _clean_slate():
    from app.common.db import ControlSessionLocal, TenantSharedSessionLocal

    with ControlSessionLocal() as db:
        db.execute(text(f"TRUNCATE TABLE {', '.join(_CONTROL_TABLES)} CASCADE"))
        db.commit()
    with TenantSharedSessionLocal() as db:
        db.execute(text(f"TRUNCATE TABLE {', '.join(_TENANT_TABLES)} CASCADE"))
        db.commit()
    # module_catalog is truncated above (tests create their own ad-hoc rows
    # via helpers.ensure_module) — the FarmOS contract's 20+2 fixed codes
    # need to exist again for every test, not just once per session.
    _seed_farmos_module_catalog()
    yield


@pytest.fixture()
def control_db():
    from app.common.db import ControlSessionLocal

    with ControlSessionLocal() as db:
        yield db
        db.commit()


def unique_code(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def dev_login(client: TestClient, email: str, display_name: str = "") -> str:
    resp = client.post(
        "/api/v1/auth/dev-login", json={"email": email, "display_name": display_name or email}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth_headers(token: str, *, device_id: str | None = None, membership_id: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if device_id:
        headers["X-Device-Id"] = device_id
    if membership_id:
        headers["X-Membership-Id"] = membership_id
    return headers


def farmos_login(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def farmos_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
