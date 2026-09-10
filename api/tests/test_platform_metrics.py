"""Dashboard metrics — derived from real rows, and still RLS-scoped."""

from __future__ import annotations

from app.common.enums import PlatformRole, TenantStatus
from app.common.tenant_router import TenantDataRouter
from app.tenant_api.models import Animal
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import add_farmos_user, create_tenant, grant_module, grant_platform_role


def admin_token(client, control_db, email: str) -> str:
    grant_platform_role(control_db, email, PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()
    return dev_login(client, email)


def test_overview_counts_tenants_devices_and_audit_volume(client, control_db):
    token = admin_token(client, control_db, "metrics-overview@test.com")
    create_tenant(control_db, company_code=unique_code("FARM-OV"), status=TenantStatus.ACTIVE)
    control_db.commit()

    resp = client.get("/platform/v1/metrics/overview", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["tenants_by_status"]["ACTIVE"] >= 1
    assert body["tenants_total"] >= 1
    assert body["staff_count"] >= 1
    # Zero-filled, so a quiet day is a zero rather than a gap in the series.
    assert len(body["audit_events_per_day"]) == 14
    assert all(set(point) == {"day", "events"} for point in body["audit_events_per_day"])


def test_overview_audit_window_is_adjustable(client, control_db):
    token = admin_token(client, control_db, "metrics-window@test.com")
    resp = client.get("/platform/v1/metrics/overview?audit_days=30", headers=auth_headers(token))
    assert len(resp.json()["audit_events_per_day"]) == 30


def test_licensing_reports_entitlement_counts_per_module(client, control_db):
    token = admin_token(client, control_db, "metrics-lic@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-LIC"))
    grant_module(control_db, tenant, "animals")
    control_db.commit()

    body = client.get("/platform/v1/metrics/licensing", headers=auth_headers(token)).json()
    animals = next(m for m in body["modules"] if m["module_code"] == "animals")
    assert animals["active"] >= 1
    assert animals["is_permission_module"] is True


def test_usage_counts_only_the_tenants_own_rows(client, control_db):
    """The point of routing through TenantDataRouter: two tenants holding
    identical-looking data must never see each other's totals.
    """
    token = admin_token(client, control_db, "metrics-usage@test.com")
    quiet = create_tenant(control_db, company_code=unique_code("FARM-QUIET"))
    busy = create_tenant(control_db, company_code=unique_code("FARM-BUSY"))
    add_farmos_user(control_db, busy, "busy-owner@test.com", role="owner")
    control_db.commit()

    with TenantDataRouter.session_for(busy.id) as db:
        for tag in ("COW-1", "COW-2", "COW-3"):
            db.add(Animal(tenant_id=busy.id, tag=tag, species="cow", name=tag))

    busy_usage = client.get(f"/platform/v1/metrics/usage/{busy.id}", headers=auth_headers(token)).json()
    assert busy_usage["records_by_module"]["animals"] == 3
    assert busy_usage["total_records"] == 3
    assert busy_usage["modules_with_data"] == ["animals"]
    assert busy_usage["active_users"] == 1
    assert busy_usage["last_activity_at"] is not None

    quiet_usage = client.get(f"/platform/v1/metrics/usage/{quiet.id}", headers=auth_headers(token)).json()
    assert quiet_usage["total_records"] == 0
    assert quiet_usage["modules_with_data"] == []
    assert quiet_usage["last_activity_at"] is None


def test_usage_list_reports_what_it_could_not_measure(client, control_db):
    token = admin_token(client, control_db, "metrics-cap@test.com")
    create_tenant(control_db, company_code=unique_code("FARM-CAP"))
    control_db.commit()

    body = client.get("/platform/v1/metrics/usage?limit=1", headers=auth_headers(token)).json()
    assert body["tenants_measured"] == 1
    assert body["tenants_total"] >= 1
    assert len(body["items"]) == 1


def test_usage_for_an_unknown_tenant_is_404(client, control_db):
    token = admin_token(client, control_db, "metrics-404@test.com")
    resp = client.get(
        "/platform/v1/metrics/usage/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(token),
    )
    assert resp.status_code == 404


def test_metrics_require_a_platform_role(client, control_db):
    token = dev_login(client, "outsider-metrics@test.com")
    for path in (
        "/platform/v1/metrics/overview",
        "/platform/v1/metrics/licensing",
        "/platform/v1/metrics/usage",
    ):
        assert client.get(path, headers=auth_headers(token)).status_code == 403
