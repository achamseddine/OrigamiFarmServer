"""Issuing a licence hands over both halves at once.

The failure this prevents is a customer left holding one half: a tablet
key with no way to sign in, or an account with nothing to pair a device
with. Those were separate actions on separate tabs, so doing one and
believing the customer was set up was the obvious mistake, and no screen
said which half was missing.
"""

from __future__ import annotations

from sqlalchemy import select

from app.auth.models import UserIdentity
from app.auth.passwords import GENERATED_PASSWORD_ALPHABET
from app.common.enums import PlatformRole
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import create_tenant, ensure_farmos_catalog, grant_platform_role


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
        client.post("/api/v1/auth/login", json={"email": email, "password": "owner-chosen-1"}).status_code
        == 200
    )


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
    # The sign-in link still comes back for the admin to pass on.
    assert resp.json()["activation_url"]


def test_a_licence_can_hand_over_a_password_instead_of_a_link(client, control_db):
    """The whole point of this mode: no mail server, no link to pass on.

    An admin issues the handover, reads an email address and a password
    to the customer over the phone, and the customer is working. There is
    no third thing to read any more: no key, because no tablet is paired.
    """
    headers = admin_headers(client, control_db, "pack-pw@test.com")
    tenant_id, email = tenant_with_owner(client, headers, control_db, "FARM-PW")

    pack = issue(client, headers, tenant_id, credential="password")

    assert pack["owner_password"], "a password mode must return a password"
    assert pack["activation_url"] is None, "and not also a link — one way in, as asked"
    assert "licence_key" not in pack, "the pairing key is gone with device licences"

    # It is a working password straight away.
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": email, "password": pack["owner_password"]}
        ).status_code
        == 200
    )


def test_the_generated_password_can_be_read_down_a_phone_line(client, control_db):
    headers = admin_headers(client, control_db, "pack-pwshape@test.com")
    tenant_id, _ = tenant_with_owner(client, headers, control_db, "FARM-PWS")

    password = issue(client, headers, tenant_id, credential="password")["owner_password"]
    groups = password.split("-")
    assert len(groups) == 3 and all(len(group) == 4 for group in groups)
    assert all(ch in GENERATED_PASSWORD_ALPHABET for ch in password.replace("-", ""))
    # Lower case and no ORG prefix, so it is never mistaken for the pairing
    # key it is handed over beside.
    assert password.islower()
    assert not password.startswith("org")


def test_an_admin_set_password_is_flagged_until_its_owner_replaces_it(client, control_db):
    """Somebody else chose it, and every screen should keep saying so."""
    headers = admin_headers(client, control_db, "pack-flag@test.com")
    tenant_id, email = tenant_with_owner(client, headers, control_db, "FARM-FLAG")
    membership_id = client.get(f"/platform/v1/tenants/{tenant_id}/memberships", headers=headers).json()[0][
        "id"
    ]

    resp = client.post(
        f"/platform/v1/tenants/{tenant_id}/memberships/{membership_id}/password",
        json={},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    password = resp.json()["password"]
    assert resp.json()["must_change"] is True

    user = control_db.execute(select(UserIdentity).where(UserIdentity.email == email)).scalar_one()
    control_db.refresh(user)
    assert user.password_changed_at is None, "not a password its holder chose"

    # And once they change it themselves, the flag clears.
    token = client.post("/api/v1/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    changed = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": password, "new_password": "my-own-choice-9"},
        headers=auth_headers(token),
    )
    assert changed.status_code == 204, changed.text
    control_db.refresh(user)
    assert user.password_changed_at is not None


def test_a_farm_user_can_change_their_own_password(client, control_db):
    """Without this, an admin-set password could never be taken back."""
    headers = admin_headers(client, control_db, "pack-selfchange@test.com")
    tenant_id, email = tenant_with_owner(client, headers, control_db, "FARM-SELF")
    password = issue(client, headers, tenant_id, credential="password")["owner_password"]

    token = client.post("/api/v1/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]

    wrong = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "not-it", "new_password": "something-else-1"},
        headers=auth_headers(token),
    )
    assert wrong.status_code == 401

    client.post(
        "/api/v1/auth/change-password",
        json={"current_password": password, "new_password": "chosen-by-them-1"},
        headers=auth_headers(token),
    )
    assert (
        client.post("/api/v1/auth/login", json={"email": email, "password": password}).status_code == 401
    ), "the old password must stop working"
    assert (
        client.post("/api/v1/auth/login", json={"email": email, "password": "chosen-by-them-1"}).status_code
        == 200
    )


def test_an_admin_can_choose_the_password_rather_than_generate_one(client, control_db):
    headers = admin_headers(client, control_db, "pack-chosen@test.com")
    tenant_id, email = tenant_with_owner(client, headers, control_db, "FARM-CHOSEN")
    membership_id = client.get(f"/platform/v1/tenants/{tenant_id}/memberships", headers=headers).json()[0][
        "id"
    ]

    resp = client.post(
        f"/platform/v1/tenants/{tenant_id}/memberships/{membership_id}/password",
        json={"new_password": "riyak-farm-2026"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["password"] == "riyak-farm-2026"
    assert (
        client.post("/api/v1/auth/login", json={"email": email, "password": "riyak-farm-2026"}).status_code
        == 200
    )


def test_setting_a_password_is_audited_against_the_admin_who_did_it(client, control_db):
    """It matters who could have known it, not just that one was set."""
    headers = admin_headers(client, control_db, "pack-audit@test.com")
    tenant_id, _ = tenant_with_owner(client, headers, control_db, "FARM-PAUD")
    issue(client, headers, tenant_id, credential="password")

    events = client.get(f"/platform/v1/audit-events?tenant_id={tenant_id}", headers=headers).json()
    actions = [event["action"] for event in events]
    assert "tenant_user.password_set_by_admin" in actions
