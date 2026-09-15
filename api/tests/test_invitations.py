"""Getting an invited tenant user from "created" to "signed in".

The gap these protect against: inviting a tenant owner created an identity
with no password and sent nothing — no email, no code, no link — so the
person a farm had just been handed to could not sign in to anything, and
no screen said so. The whole path is exercised here over HTTP, because the
half that was missing was precisely the half no unit test would have
noticed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.common.enums import MembershipStatus, PlatformRole
from app.tenants.models import MembershipInvitation
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import create_tenant, grant_platform_role

PASSWORD = "farm-owner-pw-1"


def admin_headers(client, control_db, email: str) -> dict:
    grant_platform_role(control_db, email, PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()
    return auth_headers(dev_login(client, email))


def invite_owner(client, headers, tenant_id: str, email: str) -> dict:
    resp = client.post(
        f"/platform/v1/tenants/{tenant_id}/memberships",
        json={
            "email": email,
            "display_name": "Farm Owner",
            "tenant_role": "TENANT_OWNER",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def issue(client, headers, tenant_id: str, membership_id: str, **body) -> dict:
    resp = client.post(
        f"/platform/v1/tenants/{tenant_id}/memberships/{membership_id}/invitation",
        json={"send_email": False, **body},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def token_from(invitation: dict) -> str:
    return invitation["url"].split("token=", 1)[1]


def test_an_invited_owner_can_set_a_password_and_sign_in(client, control_db):
    """The path that did not exist: created -> link -> password -> in."""
    headers = admin_headers(client, control_db, "inv-admin@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-INV"))
    control_db.commit()

    email = f"owner-{unique_code('x').lower()}@farm-invite.com"
    membership = invite_owner(client, headers, str(tenant.id), email)

    # Before the invitation there is no way in at all.
    assert membership["has_password"] is False
    assert (
        client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code
        == 401
    )

    invitation = issue(client, headers, str(tenant.id), membership["id"])
    token = token_from(invitation)

    check = client.post("/api/v1/auth/invitation/check", json={"token": token})
    assert check.status_code == 200, check.text
    assert check.json()["valid"] is True
    assert check.json()["email"] == email

    accepted = client.post(
        "/api/v1/auth/invitation/accept", json={"token": token, "password": PASSWORD}
    )
    assert accepted.status_code == 200, accepted.text
    # Signed in immediately rather than bounced to a login screen.
    assert accepted.json()["access_token"]

    # And the password works from then on.
    assert (
        client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code
        == 200
    )


def test_the_link_only_works_once(client, control_db):
    headers = admin_headers(client, control_db, "inv-once@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-ONCE"))
    control_db.commit()
    membership = invite_owner(client, headers, str(tenant.id), f"once-{unique_code('x')}@farm-invite.com")
    token = token_from(issue(client, headers, str(tenant.id), membership["id"]))

    assert (
        client.post(
            "/api/v1/auth/invitation/accept", json={"token": token, "password": PASSWORD}
        ).status_code
        == 200
    )
    second = client.post(
        "/api/v1/auth/invitation/accept", json={"token": token, "password": "another-password"}
    )
    assert second.status_code == 401
    assert "already been used" in second.text


def test_checking_a_link_does_not_consume_it(client, control_db):
    """The activation page has to look before it asks for a password."""
    headers = admin_headers(client, control_db, "inv-check@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-CHK"))
    control_db.commit()
    membership = invite_owner(client, headers, str(tenant.id), f"chk-{unique_code('x')}@farm-invite.com")
    token = token_from(issue(client, headers, str(tenant.id), membership["id"]))

    for _ in range(3):
        assert client.post("/api/v1/auth/invitation/check", json={"token": token}).json()["valid"]

    assert (
        client.post(
            "/api/v1/auth/invitation/accept", json={"token": token, "password": PASSWORD}
        ).status_code
        == 200
    )


def test_reissuing_supersedes_the_previous_link(client, control_db):
    """"Resend" must leave one working link, not a growing pile of them."""
    headers = admin_headers(client, control_db, "inv-resend@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-RES"))
    control_db.commit()
    membership = invite_owner(client, headers, str(tenant.id), f"res-{unique_code('x')}@farm-invite.com")

    first = token_from(issue(client, headers, str(tenant.id), membership["id"]))
    second = token_from(issue(client, headers, str(tenant.id), membership["id"]))

    stale = client.post("/api/v1/auth/invitation/check", json={"token": first})
    assert stale.json()["valid"] is False
    assert "replaced" in stale.json()["message"]
    assert client.post("/api/v1/auth/invitation/check", json={"token": second}).json()["valid"]


def test_an_expired_link_says_so(client, control_db):
    headers = admin_headers(client, control_db, "inv-exp@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-EXP"))
    control_db.commit()
    membership = invite_owner(client, headers, str(tenant.id), f"exp-{unique_code('x')}@farm-invite.com")
    token = token_from(issue(client, headers, str(tenant.id), membership["id"]))

    row = control_db.execute(
        select(MembershipInvitation).where(
            MembershipInvitation.membership_id == membership["id"]
        )
    ).scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    control_db.commit()

    check = client.post("/api/v1/auth/invitation/check", json={"token": token})
    assert check.json()["valid"] is False
    assert "expired" in check.json()["message"]


def test_a_suspended_member_cannot_redeem_their_invitation(client, control_db):
    headers = admin_headers(client, control_db, "inv-susp@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-SUS"))
    control_db.commit()
    membership = invite_owner(client, headers, str(tenant.id), f"sus-{unique_code('x')}@farm-invite.com")
    token = token_from(issue(client, headers, str(tenant.id), membership["id"]))

    suspended = client.post(
        f"/platform/v1/tenants/{tenant.id}/memberships/{membership['id']}/status",
        json={"active": False, "reason": "Left the farm"},
        headers=headers,
    )
    assert suspended.status_code == 200, suspended.text

    check = client.post("/api/v1/auth/invitation/check", json={"token": token})
    assert check.json()["valid"] is False
    assert "no longer active" in check.json()["message"]


def test_an_unknown_token_reveals_nothing(client, control_db):
    check = client.post("/api/v1/auth/invitation/check", json={"token": "not-a-real-token"})
    assert check.status_code == 200
    body = check.json()
    assert body["valid"] is False
    assert body["email"] is None and body["tenant_name"] is None


def test_the_token_is_never_stored_in_the_clear(client, control_db):
    """A database dump must not be a way into a customer's farm."""
    headers = admin_headers(client, control_db, "inv-hash@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-HASH"))
    control_db.commit()
    membership = invite_owner(client, headers, str(tenant.id), f"h-{unique_code('x')}@farm-invite.com")
    token = token_from(issue(client, headers, str(tenant.id), membership["id"]))

    row = control_db.execute(
        select(MembershipInvitation).where(
            MembershipInvitation.membership_id == membership["id"]
        )
    ).scalar_one()
    assert token not in row.token_hash
    assert len(row.token_hash) == 64  # sha256 hex


def test_the_status_endpoint_tracks_progress_without_leaking_the_link(client, control_db):
    headers = admin_headers(client, control_db, "inv-status@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-ST"))
    control_db.commit()
    membership = invite_owner(client, headers, str(tenant.id), f"st-{unique_code('x')}@farm-invite.com")
    url = f"/platform/v1/tenants/{tenant.id}/memberships/{membership['id']}/invitation"

    before = client.get(url, headers=headers).json()
    assert before["state"] == "no_invitation"
    assert before["has_password"] is False

    token = token_from(issue(client, headers, str(tenant.id), membership["id"]))
    pending = client.get(url, headers=headers).json()
    assert pending["state"] == "pending"
    assert "url" not in pending and "token" not in pending

    client.post("/api/v1/auth/invitation/accept", json={"token": token, "password": PASSWORD})
    after = client.get(url, headers=headers).json()
    assert after["state"] == "accepted"
    assert after["has_password"] is True


def test_delivery_reports_manual_when_no_mail_server_is_configured(client, control_db):
    """Never claims to have emailed anybody when it has not."""
    headers = admin_headers(client, control_db, "inv-mail@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-MAIL"))
    control_db.commit()
    membership = invite_owner(client, headers, str(tenant.id), f"m-{unique_code('x')}@farm-invite.com")

    resp = client.post(
        f"/platform/v1/tenants/{tenant.id}/memberships/{membership['id']}/invitation",
        json={"send_email": True},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["delivery"] == "manual"
    assert "No mail server" in resp.json()["delivery_detail"]
    # The link is still returned, so the admin has something to pass on.
    assert "token=" in resp.json()["url"]


def test_an_invitation_cannot_be_issued_through_another_tenants_id(client, control_db):
    headers = admin_headers(client, control_db, "inv-cross@test.com")
    tenant_a = create_tenant(control_db, company_code=unique_code("FARM-A1"))
    tenant_b = create_tenant(control_db, company_code=unique_code("FARM-B1"))
    control_db.commit()
    membership = invite_owner(
        client, headers, str(tenant_a.id), f"x-{unique_code('x')}@farm-invite.com"
    )

    resp = client.post(
        f"/platform/v1/tenants/{tenant_b.id}/memberships/{membership['id']}/invitation",
        json={"send_email": False},
        headers=headers,
    )
    assert resp.status_code == 404


def test_accepting_enforces_a_minimum_password_length(client, control_db):
    headers = admin_headers(client, control_db, "inv-short@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-SH"))
    control_db.commit()
    membership = invite_owner(client, headers, str(tenant.id), f"sh-{unique_code('x')}@farm-invite.com")
    token = token_from(issue(client, headers, str(tenant.id), membership["id"]))

    resp = client.post("/api/v1/auth/invitation/accept", json={"token": token, "password": "short"})
    assert resp.status_code == 422


def test_the_membership_is_active_so_the_owner_lands_in_their_own_tenant(client, control_db):
    headers = admin_headers(client, control_db, "inv-land@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-LAND"))
    control_db.commit()
    email = f"land-{unique_code('x')}@farm-invite.com"
    membership = invite_owner(client, headers, str(tenant.id), email)
    token = token_from(issue(client, headers, str(tenant.id), membership["id"]))

    accepted = client.post(
        "/api/v1/auth/invitation/accept", json={"token": token, "password": PASSWORD}
    )
    assert accepted.json()["tenant_name"] == tenant.display_name

    me = client.get(
        "/api/v1/auth/me", headers=auth_headers(accepted.json()["access_token"])
    )
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email

    memberships = client.get(
        f"/platform/v1/tenants/{tenant.id}/memberships", headers=headers
    ).json()
    mine = next(item for item in memberships if item["email"] == email)
    assert mine["status"] == MembershipStatus.ACTIVE.value
    assert mine["has_password"] is True


def test_the_sign_in_link_is_not_called_activation(client, control_db):
    """The rename exists because one word for two credentials is a trap.

    An admin pasted a tablet pairing code into /activate/?token= and
    reasonably expected it to work, because the product called both things
    activation. The page is /welcome now; /activate stays as a forward so
    links already sent survive, which is why this asserts the built URL
    rather than the route's existence.
    """
    headers = admin_headers(client, control_db, "inv-name@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-NAME"))
    control_db.commit()
    membership = invite_owner(
        client, headers, str(tenant.id), f"name-{unique_code('x')}@farm-invite.com"
    )

    url = issue(client, headers, str(tenant.id), membership["id"])["url"]
    assert "/welcome/?token=" in url
    assert "/activate/" not in url


def test_a_pairing_key_is_not_accepted_as_a_sign_in_token(client, control_db):
    """The two credentials must never be interchangeable, however similar
    the words around them once were.
    """
    headers = admin_headers(client, control_db, "inv-notkey@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-NOTKEY"))
    control_db.commit()

    key = client.post(
        f"/platform/v1/tenants/{tenant.id}/device-activations",
        json={"ttl_hours": 24},
        headers=headers,
    )
    assert key.status_code == 201, key.text

    check = client.post(
        "/api/v1/auth/invitation/check", json={"token": key.json()["activation_code"]}
    )
    assert check.json()["valid"] is False
