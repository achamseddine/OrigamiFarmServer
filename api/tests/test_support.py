from __future__ import annotations

from app.common.enums import PlatformRole
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import create_tenant, grant_platform_role


def test_support_session_expires(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-SUP"))
    grant_platform_role(control_db, "support@test.com", PlatformRole.PLATFORM_SUPPORT_ADMIN)
    control_db.commit()

    token = dev_login(client, "support@test.com")
    created = client.post(
        f"/platform/v1/tenants/{tenant.id}/support-sessions",
        json={"reason": "customer sync issue", "scope": ["sync:diagnose"], "ttl_minutes": 0},
        headers=auth_headers(token),
    )
    assert created.status_code == 201, created.text
    # ttl_minutes=0 means expires_at == starts_at, so it is inactive the
    # instant it's created — proving expiry is enforced, not just stored.
    assert created.json()["is_active"] is False

    listing = client.get(
        f"/platform/v1/tenants/{tenant.id}/support-sessions", headers=auth_headers(token)
    )
    assert listing.status_code == 200
    assert listing.json()[0]["is_active"] is False


def test_support_session_can_be_ended_early(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-SUP2"))
    grant_platform_role(control_db, "support2@test.com", PlatformRole.PLATFORM_SUPPORT_ADMIN)
    control_db.commit()

    token = dev_login(client, "support2@test.com")
    created = client.post(
        f"/platform/v1/tenants/{tenant.id}/support-sessions",
        json={"reason": "investigating device anomaly", "ttl_minutes": 60},
        headers=auth_headers(token),
    )
    assert created.json()["is_active"] is True
    session_id = created.json()["id"]

    ended = client.post(f"/platform/v1/support-sessions/{session_id}/end", headers=auth_headers(token))
    assert ended.status_code == 200
    assert ended.json()["is_active"] is False
    assert ended.json()["ended_at"] is not None
