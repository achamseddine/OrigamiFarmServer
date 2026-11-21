"""An email address is one spelling wherever it is written or typed: a
manager who creates an employee with a capital letter, or a worker whose
keyboard capitalises the first letter at sign-in, must not be locked out
(app/farmos/routes_auth.py normalise_email, routes_employees_admin)."""

from __future__ import annotations

from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant

API = "/api/v1"


def _manager(client, control_db, prefix: str):
    tenant = create_tenant(control_db, company_code=unique_code(prefix))
    add_farmos_user(control_db, tenant, f"manager-{prefix}@origami-demo.com", role="manager")
    control_db.commit()
    return tenant, farmos_headers(
        farmos_login(client, f"manager-{prefix}@origami-demo.com", FARMOS_DEMO_PASSWORD)
    )


def test_an_employee_signs_in_whatever_the_case_of_the_address(client, control_db):
    _tenant, h = _manager(client, control_db, "EMAILCASE")
    r = client.post(
        f"{API}/employees",
        json={
            "name": "Mohamad",
            "email": " Mohamad@Origami.Farm ",
            "password": "milk-at-six",
            "role": "worker",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "mohamad@origami.farm"

    for typed in (
        "mohamad@origami.farm",
        "Mohamad@origami.farm",
        "MOHAMAD@ORIGAMI.FARM",
        " mohamad@origami.farm",
    ):
        login = client.post(f"{API}/auth/login", json={"email": typed, "password": "milk-at-six"})
        assert login.status_code == 200, f"{typed!r}: {login.status_code} {login.text}"
        assert login.json()["user"]["name"] == "Mohamad"
    wrong = client.post(
        f"{API}/auth/login", json={"email": "mohamad@origami.farm", "password": "Milk-At-Six"}
    )
    assert wrong.status_code == 401  # the password itself stays case-sensitive


def test_the_same_address_twice_is_refused_in_any_spelling(client, control_db):
    _tenant, h = _manager(client, control_db, "EMAILDUP")
    first = client.post(
        f"{API}/employees",
        json={"name": "Hala", "email": "hala@origami.farm", "password": "eggs-at-seven"},
        headers=h,
    )
    assert first.status_code == 201, first.text
    again = client.post(
        f"{API}/employees",
        json={"name": "Hala B", "email": "HALA@origami.farm", "password": "eggs-at-seven"},
        headers=h,
    )
    assert again.status_code == 409 and "hala@origami.farm" in again.json()["detail"]

    # Renaming someone onto a taken address is refused the same way; a
    # change of case on their own address is not a change.
    other = client.post(
        f"{API}/employees",
        json={"name": "Nour", "email": "nour@origami.farm", "password": "hay-at-eight"},
        headers=h,
    )
    assert other.status_code == 201, other.text
    clash = client.patch(
        f"{API}/employees/{other.json()['id']}", json={"email": "Hala@Origami.farm"}, headers=h
    )
    assert clash.status_code == 409
    same = client.patch(
        f"{API}/employees/{other.json()['id']}", json={"email": "NOUR@origami.farm"}, headers=h
    )
    assert same.status_code == 200 and same.json()["email"] == "nour@origami.farm"
