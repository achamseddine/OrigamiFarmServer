"""A tenant owner arrives able to run their own farm.

The customer buys the product, signs in on their tablet for the first
time, and has to be able to open every module they are licensed for and
add their own staff. That is the whole proposition: their business, their
people, their responsibility.

It did not work. `role` — the tablet's job role, and the only thing
full_access is computed from — took its column default, "worker", because
the console set `tenant_role` and nothing else. The owner opened nothing
and could add nobody, while the same screen called them tenant owner.
"""

from __future__ import annotations

from app.auth.passwords import hash_password
from app.common.enums import PlatformRole, TenantRole
from app.tenants.models import TenantMembership
from tests.conftest import auth_headers, dev_login, farmos_headers, farmos_login, unique_code
from tests.helpers import create_tenant, ensure_user, grant_platform_role

PASSWORD = "first-login-pw-1"


def admin_headers(client, control_db, email: str = "staff@origami-platform.com") -> dict:
    grant_platform_role(control_db, email, PlatformRole.PLATFORM_SUPER_ADMIN)
    control_db.commit()
    return auth_headers(dev_login(client, email))


def add_member(client, headers, tenant_id, email: str, tenant_role: str) -> dict:
    resp = client.post(
        f"/platform/v1/tenants/{tenant_id}/memberships",
        json={"email": email, "display_name": "Rami Ali Sweidane", "tenant_role": tenant_role},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def give_password(control_db, email: str) -> None:
    user = ensure_user(control_db, email)
    user.password_hash = hash_password(PASSWORD)
    control_db.commit()


def test_the_platform_role_decides_the_job_role():
    """One person, two vocabularies — and they must agree."""
    assert TenantMembership.job_role_for(TenantRole.TENANT_OWNER) == "owner"
    assert TenantMembership.job_role_for(TenantRole.FARM_MANAGER) == "manager"
    assert TenantMembership.job_role_for(TenantRole.EMPLOYEE) == "worker"


def test_an_owner_created_in_the_console_is_an_owner_on_the_tablet(client, control_db):
    headers = admin_headers(client, control_db)
    tenant = create_tenant(control_db, company_code=unique_code("OWNER"), display_name="Riyak Farm")
    control_db.commit()

    member = add_member(client, headers, tenant.id, "boss@origami-demo.com", "TENANT_OWNER")
    # The Access tab reads this column; it used to say "worker" beside a
    # platform role of tenant owner, on the very same row.
    assert member["role"] == "owner"


def test_a_farm_manager_becomes_a_manager(client, control_db):
    headers = admin_headers(client, control_db)
    tenant = create_tenant(control_db, company_code=unique_code("OWNER"))
    control_db.commit()

    member = add_member(client, headers, tenant.id, "manager@origami-demo.com", "FARM_MANAGER")
    assert member["role"] == "manager"


def test_an_employee_stays_a_worker(client, control_db):
    """The fix must not hand everyone the keys — only the roles that mean it."""
    headers = admin_headers(client, control_db)
    tenant = create_tenant(control_db, company_code=unique_code("OWNER"))
    control_db.commit()

    member = add_member(client, headers, tenant.id, "hand@origami-demo.com", "EMPLOYEE")
    assert member["role"] == "worker"


def test_the_owner_opens_every_module_and_can_add_staff(client, control_db):
    """The first-login promise, end to end through the tablet API."""
    headers = admin_headers(client, control_db)
    tenant = create_tenant(control_db, company_code=unique_code("OWNER"), display_name="Riyak Farm")
    control_db.commit()

    add_member(client, headers, tenant.id, "firstlogin@origami-demo.com", "TENANT_OWNER")
    give_password(control_db, "firstlogin@origami-demo.com")

    token = farmos_login(client, "firstlogin@origami-demo.com", PASSWORD)
    tablet = farmos_headers(token)

    access = client.get("/api/v1/me/access", headers=tablet).json()
    assert access["full_access"] is True
    assert access["role"] == "owner"
    # Every module, every action — a farm can never lock itself out.
    assert len(access["modules"]) == 20
    for grid in access["modules"].values():
        assert all(grid.values())

    # And the thing they are there to do on day one: add their own people.
    created = client.post(
        "/api/v1/employees",
        headers=tablet,
        json={
            "name": "Nour the Vet",
            "email": "nour@origami-demo.com",
            "role": "veterinarian",
            "password": "a-strong-password",
        },
    )
    assert created.status_code in (200, 201), created.text


def test_the_login_response_tells_the_app_the_owner_is_an_owner(client, control_db):
    """The app decides what to show from the profile it is handed."""
    headers = admin_headers(client, control_db)
    tenant = create_tenant(control_db, company_code=unique_code("OWNER"))
    control_db.commit()

    add_member(client, headers, tenant.id, "shown@origami-demo.com", "TENANT_OWNER")
    give_password(control_db, "shown@origami-demo.com")

    body = client.post(
        "/api/v1/auth/login",
        json={"email": "shown@origami-demo.com", "password": PASSWORD},
    ).json()
    assert body["user"]["role"] == "owner"
