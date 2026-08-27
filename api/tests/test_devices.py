from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.common.enums import PlatformRole
from app.devices.models import DeviceActivation
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import create_tenant, ensure_module, grant_module, grant_platform_role


def _bootstrap_tenant_and_admin(control_db, prefix: str):
    tenant = create_tenant(control_db, company_code=unique_code(prefix))
    ensure_module(control_db, "ANIMALS")
    grant_module(control_db, tenant, "ANIMALS")
    grant_platform_role(control_db, f"{prefix.lower()}-admin@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()
    return tenant


def test_activation_code_cannot_be_reused(client, control_db):
    tenant = _bootstrap_tenant_and_admin(control_db, "FARM-DEV1")
    admin_token = dev_login(client, "farm-dev1-admin@test.com")

    created = client.post(
        f"/platform/v1/tenants/{tenant.id}/device-activations",
        json={"ttl_hours": 24},
        headers=auth_headers(admin_token),
    )
    assert created.status_code == 201, created.text
    code = created.json()["activation_code"]

    first = client.post(
        "/api/v1/device/activate",
        json={"activation_code": code, "installation_id": "device-1", "display_name": "Tablet 1"},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/api/v1/device/activate",
        json={"activation_code": code, "installation_id": "device-2", "display_name": "Tablet 2"},
    )
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "ACTIVATION_CODE_ALREADY_USED"


def test_expired_activation_code_fails(client, control_db):
    tenant = _bootstrap_tenant_and_admin(control_db, "FARM-DEV2")
    admin_token = dev_login(client, "farm-dev2-admin@test.com")

    created = client.post(
        f"/platform/v1/tenants/{tenant.id}/device-activations",
        json={"ttl_hours": 24},
        headers=auth_headers(admin_token),
    )
    code = created.json()["activation_code"]
    activation_id = created.json()["activation_id"]

    # Force it into the past, as if 24h had already elapsed.
    activation = control_db.get(DeviceActivation, activation_id)
    activation.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    control_db.commit()

    resp = client.post(
        "/api/v1/device/activate",
        json={"activation_code": code, "installation_id": "device-3", "display_name": "Tablet 3"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "ACTIVATION_CODE_EXPIRED"


def test_revoked_device_cannot_refresh_license(client, control_db):
    tenant = _bootstrap_tenant_and_admin(control_db, "FARM-DEV3")
    admin_token = dev_login(client, "farm-dev3-admin@test.com")

    created = client.post(
        f"/platform/v1/tenants/{tenant.id}/device-activations",
        json={"ttl_hours": 24},
        headers=auth_headers(admin_token),
    )
    code = created.json()["activation_code"]

    activated = client.post(
        "/api/v1/device/activate",
        json={"activation_code": code, "installation_id": "device-4", "display_name": "Tablet 4"},
    )
    assert activated.status_code == 200
    device_id = activated.json()["device_id"]
    initial_lease_expiry = activated.json()["lease"]["expires_at"]
    assert initial_lease_expiry  # a lease was actually issued at activation

    revoke = client.post(
        f"/platform/v1/devices/{device_id}/revoke",
        json={"reason": "device reported lost"},
        headers=auth_headers(admin_token),
    )
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "REVOKED"

    refresh = client.post(
        "/api/v1/license/refresh",
        headers=auth_headers(admin_token, device_id=device_id),
    )
    assert refresh.status_code == 403
    assert refresh.json()["error"]["code"] == "DEVICE_REVOKED"
