"""Tablets put themselves on the device list by signing in.

Devices used to arrive by being paired: an admin generated an ORG- key,
somebody typed it into the app, and the device existed because it had
been permitted. With device licences gone nothing was left to create one,
which would have turned the Devices tab into a permanently empty list.

So the app sends a stable installation id with every sign-in and the
server records what it sees. The list now answers "which tablets is this
customer using" instead of "which tablets may they use" — which was
always the question anybody actually asked.
"""

from __future__ import annotations

from app.common.enums import DeviceStatus, PlatformRole
from app.devices.models import Device
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import (
    FARMOS_DEMO_PASSWORD,
    add_farmos_user,
    create_tenant,
    grant_platform_role,
)


def sign_in(client, email: str, **extra):
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": FARMOS_DEMO_PASSWORD, **extra},
    )


def a_farm(control_db, code: str) -> tuple[object, str]:
    tenant = create_tenant(control_db, company_code=unique_code(code))
    email = f"dev-{unique_code('x').lower()}@farm-dev.com"
    add_farmos_user(control_db, tenant, email, role="owner")
    control_db.commit()
    return tenant, email


def devices_of(control_db, tenant) -> list[Device]:
    return control_db.query(Device).filter(Device.tenant_id == tenant.id).order_by(Device.created_at).all()


def test_signing_in_puts_the_tablet_on_the_list(client, control_db):
    tenant, email = a_farm(control_db, "DEV-NEW")

    resp = sign_in(client, email, installation_id="install-abc-123", device_name="Zahle tablet")
    assert resp.status_code == 200, resp.text

    devices = devices_of(control_db, tenant)
    assert len(devices) == 1
    assert devices[0].installation_id == "install-abc-123"
    assert devices[0].display_name == "Zahle tablet"
    assert devices[0].status == DeviceStatus.ACTIVE
    assert devices[0].last_seen_at is not None


def test_signing_in_again_updates_rather_than_duplicates(client, control_db):
    tenant, email = a_farm(control_db, "DEV-AGAIN")

    sign_in(client, email, installation_id="install-same", device_name="Old name")
    first = devices_of(control_db, tenant)[0]
    first_seen = first.last_seen_at

    sign_in(client, email, installation_id="install-same", device_name="Renamed tablet")
    control_db.expire_all()

    devices = devices_of(control_db, tenant)
    assert len(devices) == 1, "one tablet is one row, however often somebody signs in"
    assert devices[0].display_name == "Renamed tablet"
    assert devices[0].last_seen_at >= first_seen


def test_a_revoked_tablet_stays_revoked(client, control_db):
    """Revoking is how an operator says a tablet has been lost. Somebody
    signing in on it is the least convincing possible argument for
    undoing that."""
    tenant, email = a_farm(control_db, "DEV-REVOKED")
    sign_in(client, email, installation_id="install-lost")

    device = devices_of(control_db, tenant)[0]
    device.status = DeviceStatus.REVOKED
    control_db.commit()

    sign_in(client, email, installation_id="install-lost")
    control_db.expire_all()

    assert devices_of(control_db, tenant)[0].status == DeviceStatus.REVOKED


def test_a_tablet_that_sends_no_id_can_still_sign_in(client, control_db):
    """A device list is worth having. It is not worth blocking a farm
    worker's morning over."""
    tenant, email = a_farm(control_db, "DEV-SILENT")

    assert sign_in(client, email).status_code == 200
    assert devices_of(control_db, tenant) == []


def test_the_console_sees_the_tablet_that_registered_itself(client, control_db):
    tenant, email = a_farm(control_db, "DEV-CONSOLE")
    sign_in(client, email, installation_id="install-visible", device_name="Riyak tablet")

    grant_platform_role(control_db, "dev-console@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()
    headers = auth_headers(dev_login(client, "dev-console@test.com"))

    resp = client.get(f"/platform/v1/tenants/{tenant.id}/devices", headers=headers)
    assert resp.status_code == 200, resp.text
    names = [row["display_name"] for row in resp.json()]
    assert "Riyak tablet" in names
