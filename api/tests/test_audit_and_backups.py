from __future__ import annotations

from sqlalchemy import select

from app.audit.models import AuditEvent
from app.common.enums import PlatformRole
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import create_tenant, grant_platform_role


def test_privileged_actions_create_audit_records(client, control_db):
    grant_platform_role(control_db, "audit-admin@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()

    token = dev_login(client, "audit-admin@test.com")
    company_code = unique_code("FARM-AUDITED")
    created = client.post(
        "/platform/v1/tenants",
        json={
            "company_code": company_code,
            "legal_name": "Audited Farm",
            "display_name": "Audited Farm",
            "country": "US",
        },
        headers=auth_headers(token),
    )
    assert created.status_code == 201
    tenant_id = created.json()["id"]

    events = control_db.execute(
        select(AuditEvent).where(AuditEvent.action == "tenant.created", AuditEvent.entity_id == tenant_id)
    ).scalars().all()
    assert len(events) == 1
    assert events[0].actor_type.value == "PLATFORM_USER"
    assert events[0].after_summary["company_code"] == company_code


def test_backup_and_export_jobs_are_tenant_scoped(client, control_db):
    tenant_a = create_tenant(control_db, company_code=unique_code("FARM-BK-A"))
    tenant_b = create_tenant(control_db, company_code=unique_code("FARM-BK-B"))
    grant_platform_role(control_db, "backup-admin@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()

    token = dev_login(client, "backup-admin@test.com")
    export = client.post(
        f"/platform/v1/tenants/{tenant_a.id}/exports",
        json={"reason": "customer requested a copy of their data"},
        headers=auth_headers(token),
    )
    assert export.status_code == 201, export.text

    exports_for_a = client.get(
        f"/platform/v1/tenants/{tenant_a.id}/exports", headers=auth_headers(token)
    ).json()
    exports_for_b = client.get(
        f"/platform/v1/tenants/{tenant_b.id}/exports", headers=auth_headers(token)
    ).json()

    assert len(exports_for_a) == 1
    assert exports_for_a[0]["tenant_id"] == str(tenant_a.id)
    assert exports_for_b == []
