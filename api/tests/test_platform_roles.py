from __future__ import annotations

from app.common.enums import PlatformRole, TenantStatus
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import create_tenant, grant_platform_role


def test_commercial_admin_cannot_terminate_tenant(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-ROLE"), status=TenantStatus.ACTIVE)
    grant_platform_role(control_db, "commercial@test.com", PlatformRole.PLATFORM_COMMERCIAL_ADMIN)
    control_db.commit()

    token = dev_login(client, "commercial@test.com")

    # Commercial admins ARE allowed to suspend...
    suspend = client.post(
        f"/platform/v1/tenants/{tenant.id}/status",
        json={"status": "SUSPENDED", "reason": "overdue invoice"},
        headers=auth_headers(token),
    )
    assert suspend.status_code == 200, suspend.text

    reactivate = client.post(
        f"/platform/v1/tenants/{tenant.id}/status",
        json={"status": "ACTIVE", "reason": "invoice paid"},
        headers=auth_headers(token),
    )
    assert reactivate.status_code == 200

    # ...but terminating an account is reserved for super admins only.
    terminate = client.post(
        f"/platform/v1/tenants/{tenant.id}/status",
        json={"status": "TERMINATED", "reason": "trying to close the account myself"},
        headers=auth_headers(token),
    )
    assert terminate.status_code == 403
    assert terminate.json()["error"]["code"] == "PLATFORM_ROLE_REQUIRED"


def test_super_admin_can_terminate_tenant(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-ROLE2"), status=TenantStatus.ACTIVE)
    grant_platform_role(control_db, "super3@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()

    token = dev_login(client, "super3@test.com")
    terminate = client.post(
        f"/platform/v1/tenants/{tenant.id}/status",
        json={"status": "TERMINATED", "reason": "contract ended"},
        headers=auth_headers(token),
    )
    assert terminate.status_code == 200
    assert terminate.json()["status"] == "TERMINATED"


def test_auditor_is_read_only(client, control_db):
    create_tenant(control_db, company_code=unique_code("FARM-AUD"))
    grant_platform_role(control_db, "auditor@test.com", PlatformRole.PLATFORM_AUDITOR)
    control_db.commit()

    token = dev_login(client, "auditor@test.com")
    listing = client.get("/platform/v1/tenants", headers=auth_headers(token))
    assert listing.status_code == 200

    create = client.post(
        "/platform/v1/tenants",
        json={
            "company_code": unique_code("FARM-BYAUD"),
            "legal_name": "Nope",
            "display_name": "Nope",
            "country": "US",
        },
        headers=auth_headers(token),
    )
    assert create.status_code == 403
    assert create.json()["error"]["code"] == "PLATFORM_ROLE_REQUIRED"
