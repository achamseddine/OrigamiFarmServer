"""Staff/role administration, tenant memberships and issued license leases —
the control-plane surface the admin console manages.
"""

from __future__ import annotations

from app.common.enums import PlatformRole, TenantRole
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import add_farmos_user, create_tenant, grant_platform_role

STRONG_PASSWORD = "a-sufficiently-long-secret"


def super_admin_token(client, control_db, email: str = "super-mgmt@test.com") -> str:
    grant_platform_role(control_db, email, PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()
    return dev_login(client, email)


# --- staff ---------------------------------------------------------------


def test_super_admin_creates_staff_who_can_then_sign_in(client, control_db):
    token = super_admin_token(client, control_db)

    created = client.post(
        "/platform/v1/staff",
        json={
            "email": "colleague@test.com",
            "display_name": "A Colleague",
            "platform_role": PlatformRole.PLATFORM_SUPPORT_ADMIN.value,
            "password": STRONG_PASSWORD,
        },
        headers=auth_headers(token),
    )
    assert created.status_code == 201, created.text
    assert created.json()["platform_roles"] == [PlatformRole.PLATFORM_SUPPORT_ADMIN.value]
    assert created.json()["has_password"] is True

    signed_in = client.post(
        "/platform/v1/auth/login",
        json={"email": "colleague@test.com", "password": STRONG_PASSWORD},
    )
    assert signed_in.status_code == 200

    listed = client.get("/platform/v1/staff", headers=auth_headers(token))
    assert listed.status_code == 200
    assert "colleague@test.com" in [row["email"] for row in listed.json()]


def test_creating_staff_on_an_existing_email_is_a_conflict(client, control_db):
    token = super_admin_token(client, control_db, "super-dup@test.com")
    body = {
        "email": "taken@test.com",
        "display_name": "First",
        "platform_role": PlatformRole.PLATFORM_AUDITOR.value,
        "password": STRONG_PASSWORD,
    }
    assert client.post("/platform/v1/staff", json=body, headers=auth_headers(token)).status_code == 201

    again = client.post("/platform/v1/staff", json=body, headers=auth_headers(token))
    assert again.status_code == 409


def test_staff_passwords_have_a_minimum_length(client, control_db):
    token = super_admin_token(client, control_db, "super-shortpw@test.com")
    resp = client.post(
        "/platform/v1/staff",
        json={
            "email": "weak@test.com",
            "display_name": "Weak",
            "platform_role": PlatformRole.PLATFORM_AUDITOR.value,
            "password": "short",
        },
        headers=auth_headers(token),
    )
    assert resp.status_code == 422


def test_roles_can_be_granted_and_revoked(client, control_db):
    token = super_admin_token(client, control_db, "super-roles@test.com")
    user = grant_platform_role(control_db, "mover@test.com", PlatformRole.PLATFORM_AUDITOR)
    control_db.commit()

    granted = client.post(
        f"/platform/v1/staff/{user.id}/roles",
        json={"platform_role": PlatformRole.PLATFORM_COMMERCIAL_ADMIN.value},
        headers=auth_headers(token),
    )
    assert granted.status_code == 200
    assert set(granted.json()["platform_roles"]) == {
        PlatformRole.PLATFORM_AUDITOR.value,
        PlatformRole.PLATFORM_COMMERCIAL_ADMIN.value,
    }

    revoked = client.delete(
        f"/platform/v1/staff/{user.id}/roles/{PlatformRole.PLATFORM_AUDITOR.value}",
        headers=auth_headers(token),
    )
    assert revoked.status_code == 200
    assert revoked.json()["platform_roles"] == [PlatformRole.PLATFORM_COMMERCIAL_ADMIN.value]


def test_a_super_admin_cannot_revoke_their_own_super_admin(client, control_db):
    """Otherwise a platform can be left with nobody able to grant it back."""
    email = "self-revoke@test.com"
    user = grant_platform_role(control_db, email, PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()
    token = dev_login(client, email)

    resp = client.delete(
        f"/platform/v1/staff/{user.id}/roles/{PlatformRole.PLATFORM_SUPER_ADMIN.value}",
        headers=auth_headers(token),
    )
    assert resp.status_code == 422
    assert client.get("/platform/v1/staff", headers=auth_headers(token)).status_code == 200


def test_non_super_admins_can_read_staff_but_not_change_it(client, control_db):
    grant_platform_role(control_db, "support-ro@test.com", PlatformRole.PLATFORM_SUPPORT_ADMIN)
    victim = grant_platform_role(control_db, "victim@test.com", PlatformRole.PLATFORM_AUDITOR)
    control_db.commit()
    token = dev_login(client, "support-ro@test.com")

    assert client.get("/platform/v1/staff", headers=auth_headers(token)).status_code == 200

    escalate = client.post(
        f"/platform/v1/staff/{victim.id}/roles",
        json={"platform_role": PlatformRole.PLATFORM_SUPER_ADMIN.value},
        headers=auth_headers(token),
    )
    assert escalate.status_code == 403


def test_super_admin_can_reset_another_admins_password(client, control_db):
    token = super_admin_token(client, control_db, "super-reset@test.com")
    locked_out = grant_platform_role(control_db, "locked@test.com", PlatformRole.PLATFORM_AUDITOR)
    control_db.commit()

    resp = client.post(
        f"/platform/v1/staff/{locked_out.id}/password",
        json={"new_password": STRONG_PASSWORD},
        headers=auth_headers(token),
    )
    assert resp.status_code == 204

    assert (
        client.post(
            "/platform/v1/auth/login",
            json={"email": "locked@test.com", "password": STRONG_PASSWORD},
        ).status_code
        == 200
    )


# --- tenant memberships --------------------------------------------------


def test_memberships_list_shows_who_can_reach_a_tenant(client, control_db):
    token = super_admin_token(client, control_db, "super-mem@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-MEM"))
    add_farmos_user(control_db, tenant, "owner@memtest.com", role="owner")
    control_db.commit()

    resp = client.get(f"/platform/v1/tenants/{tenant.id}/memberships", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["email"] for r in rows] == ["owner@memtest.com"]
    assert rows[0]["tenant_role"] == TenantRole.TENANT_OWNER.value
    assert rows[0]["has_password"] is True


def test_membership_access_can_be_suspended_and_restored(client, control_db):
    token = super_admin_token(client, control_db, "super-susp@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-SUSP"))
    _, membership = add_farmos_user(control_db, tenant, "worker@susptest.com")
    control_db.commit()

    off = client.post(
        f"/platform/v1/tenants/{tenant.id}/memberships/{membership.id}/status",
        json={"active": False, "reason": "left the company"},
        headers=auth_headers(token),
    )
    assert off.status_code == 200, off.text
    assert off.json()["status"] == "INACTIVE"

    # Their tablet stops being able to sign in — as the same generic 401 the
    # tablet login gives any failure, so a deactivated worker learns nothing
    # about why.
    tablet = client.post(
        "/api/v1/auth/login",
        json={"email": "worker@susptest.com", "password": "farmos-demo-2026"},
    )
    assert tablet.status_code == 401

    on = client.post(
        f"/platform/v1/tenants/{tenant.id}/memberships/{membership.id}/status",
        json={"active": True},
        headers=auth_headers(token),
    )
    assert on.json()["status"] == "ACTIVE"


def test_membership_status_rejects_a_membership_from_another_tenant(client, control_db):
    token = super_admin_token(client, control_db, "super-xten@test.com")
    tenant_a = create_tenant(control_db, company_code=unique_code("FARM-XA"))
    tenant_b = create_tenant(control_db, company_code=unique_code("FARM-XB"))
    _, membership_b = add_farmos_user(control_db, tenant_b, "b-user@test.com")
    control_db.commit()

    resp = client.post(
        f"/platform/v1/tenants/{tenant_a.id}/memberships/{membership_b.id}/status",
        json={"active": False},
        headers=auth_headers(token),
    )
    assert resp.status_code == 404


# --- license leases ------------------------------------------------------


def test_leases_list_is_empty_for_a_tenant_with_no_devices(client, control_db):
    token = super_admin_token(client, control_db, "super-lease@test.com")
    tenant = create_tenant(control_db, company_code=unique_code("FARM-LEASE"))
    control_db.commit()

    resp = client.get(f"/platform/v1/tenants/{tenant.id}/leases", headers=auth_headers(token))
    assert resp.status_code == 200
    assert resp.json() == []
