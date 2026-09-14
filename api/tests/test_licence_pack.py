"""Issuing a licence hands over both halves at once.

The failure this prevents is a customer left holding one half: a tablet
key with no way to sign in, or an account with nothing to pair a device
with. Those were separate actions on separate tabs, so doing one and
believing the customer was set up was the obvious mistake, and no screen
said which half was missing.
"""

from __future__ import annotations

import uuid

from app.common.enums import DeviceActivationStatus, PlatformRole
from app.devices.models import DeviceActivation
from app.devices.service import LICENCE_KEY_ALPHABET
from app.tenants.models import Tenant
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import create_tenant, ensure_farmos_catalog, grant_module, grant_platform_role


def admin_headers(client, control_db, email: str) -> dict:
    grant_platform_role(control_db, email, PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()
    return auth_headers(dev_login(client, email))


def tenant_with_owner(client, headers, control_db, code: str) -> tuple[str, str]:
    tenant = create_tenant(control_db, company_code=unique_code(code))
    ensure_farmos_catalog(control_db)
    control_db.commit()
    email = f"owner-{unique_code('x').lower()}@pack-farm.com"
    resp = client.post(
        f"/platform/v1/tenants/{tenant.id}/memberships",
        json={"email": email, "display_name": "Pack Owner", "tenant_role": "TENANT_OWNER"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return str(tenant.id), email


def issue(client, headers, tenant_id: str, **body) -> dict:
    resp = client.post(
        f"/platform/v1/tenants/{tenant_id}/licence",
        json={"send_email": False, **body},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_issuing_a_licence_returns_the_key_and_the_activation_link(client, control_db):
    """One action, both halves."""
    headers = admin_headers(client, control_db, "pack-both@test.com")
    tenant_id, email = tenant_with_owner(client, headers, control_db, "FARM-PACK")

    pack = issue(client, headers, tenant_id)

    assert pack["licence_key"].startswith("ORG-")
    assert "/activate/?token=" in pack["activation_url"]
    assert pack["owner_email"] == email
    assert pack["licence_key_expires_at"] and pack["activation_expires_at"]


def test_the_key_is_shaped_to_be_read_aloud(client, control_db):
    """It gets read down a phone line and typed on a tablet in a field."""
    headers = admin_headers(client, control_db, "pack-readable@test.com")
    tenant_id, _ = tenant_with_owner(client, headers, control_db, "FARM-READ")

    key = issue(client, headers, tenant_id)["licence_key"]
    prefix, *groups = key.split("-")
    assert prefix == "ORG"
    assert len(groups) == 3 and all(len(group) == 4 for group in groups)
    assert all(ch in LICENCE_KEY_ALPHABET for group in groups for ch in group)


def test_no_two_characters_in_the_key_alphabet_are_confusable():
    """The alphabet is the guarantee, not any one key.

    Only one of each confusable pair may be present — dropping both would
    shrink the alphabet for nothing, and keeping both is what turns a
    read-aloud key into a support call.
    """
    # Pairs people actually mix up reading a code aloud or off a screen.
    # U/V is not one of them — they sound nothing alike — so both stay.
    for pair in ("0O", "1I", "1L", "IL", "5S", "8B", "2Z"):
        present = [ch for ch in pair if ch in LICENCE_KEY_ALPHABET]
        assert len(present) <= 1, f"{pair} are both in the alphabet and get misread"


def test_the_key_pairs_a_device_however_it_was_typed(client, control_db):
    """Lower case, no dashes — a support call, not a security boundary."""
    headers = admin_headers(client, control_db, "pack-typed@test.com")
    tenant_id, _ = tenant_with_owner(client, headers, control_db, "FARM-TYPED")
    key = issue(client, headers, tenant_id)["licence_key"]

    sloppy = key.lower().replace("-", "")
    activated = client.post(
        "/api/v1/device/activate",
        json={
            "activation_code": sloppy,
            "installation_id": unique_code("inst"),
            "platform": "ANDROID",
            "display_name": "Field tablet",
        },
    )
    assert activated.status_code == 200, activated.text


def test_the_activation_link_sets_the_owners_password(client, control_db):
    """The other half, end to end: link -> password -> signed in."""
    headers = admin_headers(client, control_db, "pack-link@test.com")
    tenant_id, email = tenant_with_owner(client, headers, control_db, "FARM-LINK")
    pack = issue(client, headers, tenant_id)

    token = pack["activation_url"].split("token=", 1)[1]
    accepted = client.post(
        "/api/v1/auth/invitation/accept", json={"token": token, "password": "owner-chosen-1"}
    )
    assert accepted.status_code == 200, accepted.text
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": email, "password": "owner-chosen-1"}
        ).status_code
        == 200
    )


def test_reissuing_supersedes_the_previous_key(client, control_db):
    """Two live keys for one customer is how a revoked pack keeps working."""
    headers = admin_headers(client, control_db, "pack-reissue@test.com")
    tenant_id, _ = tenant_with_owner(client, headers, control_db, "FARM-REISS")

    first = issue(client, headers, tenant_id)["licence_key"]
    issue(client, headers, tenant_id)

    stale = client.post(
        "/api/v1/device/activate",
        json={
            "activation_code": first,
            "installation_id": unique_code("inst"),
            "platform": "ANDROID",
            "display_name": "Old tablet",
        },
    )
    assert stale.status_code >= 400, "the superseded key must stop working"

    pending = (
        control_db.query(DeviceActivation)
        .filter(
            DeviceActivation.tenant_id == tenant_id,
            DeviceActivation.status == DeviceActivationStatus.PENDING,
        )
        .count()
    )
    assert pending == 1, "exactly one key should be live for a customer"


def test_the_pack_says_what_the_customer_actually_holds(client, control_db):
    """So whoever hands it over can tell the customer what they bought."""
    headers = admin_headers(client, control_db, "pack-licences@test.com")
    tenant_id, _ = tenant_with_owner(client, headers, control_db, "FARM-HOLD")
    grant_module(control_db, control_db.get(Tenant, uuid.UUID(tenant_id)), "MILK")
    control_db.commit()

    pack = issue(client, headers, tenant_id)
    assert "MILK" in pack["licences"]


def test_a_customer_with_no_owner_is_told_what_to_do(client, control_db):
    """The link sets somebody's password, so there has to be a somebody."""
    headers = admin_headers(client, control_db, "pack-noowner@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-NOOWN"))
    control_db.commit()

    resp = client.post(
        f"/platform/v1/tenants/{tenant.id}/licence",
        json={"send_email": False},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "no active owner" in resp.text
    assert "Access tab" in resp.text, "an error should name the fix"


def test_delivery_is_reported_honestly_when_there_is_no_mail_server(client, control_db):
    headers = admin_headers(client, control_db, "pack-mail@test.com")
    tenant_id, _ = tenant_with_owner(client, headers, control_db, "FARM-PMAIL")

    resp = client.post(
        f"/platform/v1/tenants/{tenant_id}/licence",
        json={"send_email": True},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["delivery"] == "manual"
    assert "No mail server" in resp.json()["delivery_detail"]
    # Both halves still come back for the admin to pass on.
    assert resp.json()["licence_key"]
    assert resp.json()["activation_url"]


def test_the_key_is_never_stored_in_the_clear(client, control_db):
    headers = admin_headers(client, control_db, "pack-hash@test.com")
    tenant_id, _ = tenant_with_owner(client, headers, control_db, "FARM-PHASH")
    key = issue(client, headers, tenant_id)["licence_key"]

    row = (
        control_db.query(DeviceActivation)
        .filter(
            DeviceActivation.tenant_id == tenant_id,
            DeviceActivation.status == DeviceActivationStatus.PENDING,
        )
        .one()
    )
    assert key not in row.code_hash
    assert len(row.code_hash) == 64


def test_a_farm_from_another_customer_is_refused(client, control_db):
    headers = admin_headers(client, control_db, "pack-cross@test.com")
    tenant_id, _ = tenant_with_owner(client, headers, control_db, "FARM-PX1")
    other = create_tenant(control_db, company_code=unique_code("FARM-PX2"))
    control_db.commit()

    farm = client.post(
        f"/platform/v1/tenants/{other.id}/farms",
        json={"farm_code": unique_code("F"), "name": "Their field"},
        headers=headers,
    )
    assert farm.status_code == 201, farm.text

    resp = client.post(
        f"/platform/v1/tenants/{tenant_id}/licence",
        json={"send_email": False, "farm_id": farm.json()["id"]},
        headers=headers,
    )
    assert resp.status_code == 404
