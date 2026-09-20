"""Password sign-in for the admin console (app/platform/auth_routes.py)."""

from __future__ import annotations

from app.auth.models import UserIdentity
from app.auth.passwords import hash_password
from app.common.enums import PlatformRole
from tests.conftest import auth_headers, unique_code
from tests.helpers import add_farmos_user, create_tenant, grant_platform_role

PASSWORD = "correct-horse-battery"


def make_staff(db, email: str, *, password: str | None = PASSWORD) -> UserIdentity:
    user = UserIdentity(
        idp_subject=email,
        email=email,
        display_name="Staff Member",
        password_hash=hash_password(password) if password else None,
    )
    db.add(user)
    db.flush()
    return user


def login(client, email: str, password: str):
    return client.post("/platform/v1/auth/login", json={"email": email, "password": password})


def test_login_returns_a_session_that_platform_routes_accept(client, control_db):
    make_staff(control_db, "signin@test.com")
    grant_platform_role(control_db, "signin@test.com", PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()

    resp = login(client, "signin@test.com", PASSWORD)
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    assert resp.json()["expires_at"] > 0

    me = client.get("/platform/v1/me", headers=auth_headers(token))
    assert me.status_code == 200
    assert me.json()["email"] == "signin@test.com"
    assert me.json()["platform_roles"] == [PlatformRole.PLATFORM_SUPER_ADMIN.value]

    # and the session actually opens a role-gated route
    assert client.get("/platform/v1/tenants", headers=auth_headers(token)).status_code == 200


def test_login_is_case_insensitive_on_email(client, control_db):
    make_staff(control_db, "mixedcase@test.com")
    control_db.commit()

    assert login(client, "MixedCase@Test.com", PASSWORD).status_code == 200


def test_wrong_password_and_unknown_account_are_indistinguishable(client, control_db):
    make_staff(control_db, "real@test.com")
    control_db.commit()

    wrong = login(client, "real@test.com", "not-the-password")
    unknown = login(client, "ghost@test.com", PASSWORD)

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_account_without_a_password_cannot_sign_in(client, control_db):
    """Identities created by OIDC or dev-login carry no password_hash."""
    make_staff(control_db, "oidc-only@test.com", password=None)
    control_db.commit()

    assert login(client, "oidc-only@test.com", PASSWORD).status_code == 401


def test_signing_in_without_a_platform_role_succeeds_but_grants_nothing(client, control_db):
    make_staff(control_db, "norole@test.com")
    control_db.commit()

    token = login(client, "norole@test.com", PASSWORD).json()["access_token"]

    assert client.get("/platform/v1/me", headers=auth_headers(token)).json()["platform_roles"] == []
    assert client.get("/platform/v1/tenants", headers=auth_headers(token)).status_code == 403


def test_a_farmos_tablet_token_is_not_a_console_session(client, control_db):
    """Both are HS256 over APP_SECRET_KEY, so only the type claim separates
    them. Without that check a tablet token would resolve to a platform
    identity — harmless only for as long as that account holds no role.
    """
    tenant = create_tenant(control_db, company_code=unique_code("FARM-TOK"))
    email = "worker@test.com"
    add_farmos_user(control_db, tenant, email, role="owner", password=PASSWORD)
    grant_platform_role(control_db, email, PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()

    tablet = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert tablet.status_code == 200, tablet.text
    tablet_token = tablet.json()["access_token"]

    # That token opens the tablet's own routes but must not open the console's.
    assert client.get("/api/v1/auth/me", headers=auth_headers(tablet_token)).status_code == 200
    assert client.get("/platform/v1/me", headers=auth_headers(tablet_token)).status_code == 401


def test_change_password_replaces_the_credential(client, control_db):
    make_staff(control_db, "rotate@test.com")
    control_db.commit()

    token = login(client, "rotate@test.com", PASSWORD).json()["access_token"]

    resp = client.post(
        "/platform/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "a-much-longer-new-secret"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 204, resp.text

    assert login(client, "rotate@test.com", PASSWORD).status_code == 401
    assert login(client, "rotate@test.com", "a-much-longer-new-secret").status_code == 200


def test_change_password_rejects_a_wrong_current_password(client, control_db):
    make_staff(control_db, "nochange@test.com")
    control_db.commit()
    token = login(client, "nochange@test.com", PASSWORD).json()["access_token"]

    resp = client.post(
        "/platform/v1/auth/change-password",
        json={"current_password": "guessing", "new_password": "a-much-longer-new-secret"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 401
    assert login(client, "nochange@test.com", PASSWORD).status_code == 200


def test_change_password_enforces_a_minimum_length(client, control_db):
    make_staff(control_db, "shortpw@test.com")
    control_db.commit()
    token = login(client, "shortpw@test.com", PASSWORD).json()["access_token"]

    resp = client.post(
        "/platform/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 422
