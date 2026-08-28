"""Stage 2 of the FarmOS tablet contract: the daily loop — animals, tasks,
notifications, priorities, and the morning briefing. See docs/FARMOS_API.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.common.tenant_router import TenantDataRouter
from app.farmos.farm_models import Notification
from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant

# --- Animals ---------------------------------------------------------------


def test_animal_crud_round_trip(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-AN"))
    add_farmos_user(control_db, tenant, "owner@origami-demo.com", role="owner")
    control_db.commit()
    token = farmos_login(client, "owner@origami-demo.com", FARMOS_DEMO_PASSWORD)

    created = client.post(
        "/api/v1/animals",
        json={"tag": "COW-001", "name": "Bessie", "species": "cow", "breed": "Holstein"},
        headers=farmos_headers(token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["farm_id"] == str(tenant.id)
    assert body["tag"] == "COW-001"
    assert body["status"] == "healthy"
    assert body["health_score"] == 100
    assert body["recent_observations"] == []
    animal_id = body["id"]

    listing = client.get(
        "/api/v1/animals", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert "recent_observations" not in listing.json()[0]  # list view is the slim AnimalOut

    got = client.get(f"/api/v1/animals/{animal_id}", headers=farmos_headers(token))
    assert got.status_code == 200
    assert got.json()["name"] == "Bessie"

    moved = client.patch(
        f"/api/v1/animals/{animal_id}", json={"location_label": "Barn 2"}, headers=farmos_headers(token)
    )
    assert moved.status_code == 200
    assert moved.json()["location_label"] == "Barn 2"

    updated = client.put(
        f"/api/v1/animals/{animal_id}",
        json={"name": "Bessie II", "health_score": 90},
        headers=farmos_headers(token),
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Bessie II"
    assert updated.json()["health_score"] == 90
    # A field not sent in the PUT payload keeps its previous value.
    assert updated.json()["location_label"] == "Barn 2"


def test_animal_search_and_filters(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-AN2"))
    add_farmos_user(control_db, tenant, "owner2@origami-demo.com", role="owner")
    control_db.commit()
    token = farmos_login(client, "owner2@origami-demo.com", FARMOS_DEMO_PASSWORD)

    client.post(
        "/api/v1/animals",
        json={"tag": "COW-100", "name": "Daisy", "species": "cow"},
        headers=farmos_headers(token),
    )
    client.post(
        "/api/v1/animals",
        json={"tag": "GOAT-1", "name": "Billy", "species": "goat"},
        headers=farmos_headers(token),
    )

    by_species = client.get(
        "/api/v1/animals",
        params={"farm_id": str(tenant.id), "species": "goat"},
        headers=farmos_headers(token),
    )
    assert [a["tag"] for a in by_species.json()] == ["GOAT-1"]

    by_search = client.get(
        "/api/v1/animals",
        params={"farm_id": str(tenant.id), "search": "daisy"},
        headers=farmos_headers(token),
    )
    assert [a["tag"] for a in by_search.json()] == ["COW-100"]


# --- Tasks -------------------------------------------------------------


def test_task_crud_round_trip(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-TK"))
    add_farmos_user(control_db, tenant, "owner3@origami-demo.com", role="owner")
    control_db.commit()
    token = farmos_login(client, "owner3@origami-demo.com", FARMOS_DEMO_PASSWORD)

    created = client.post(
        "/api/v1/tasks",
        json={"farm_id": str(tenant.id), "title": "Milk the cows", "priority": "high"},
        headers=farmos_headers(token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["farm_id"] == str(tenant.id)
    assert body["status"] == "open"
    task_id = body["id"]

    listing = client.get(
        "/api/v1/tasks", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert len(listing.json()) == 1

    updated = client.patch(
        f"/api/v1/tasks/{task_id}", json={"status": "done"}, headers=farmos_headers(token)
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "done"

    deleted = client.delete(f"/api/v1/tasks/{task_id}", headers=farmos_headers(token))
    assert deleted.status_code == 204

    listing_after = client.get(
        "/api/v1/tasks", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert listing_after.json() == []


def test_worker_cannot_assign_task_to_someone_else(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-ASSIGN"))
    worker_user, worker_membership = add_farmos_user(
        control_db, tenant, "worker@origami-demo.com", role="worker", grid={"tasks": ["view", "create"]}
    )
    other_user, _ = add_farmos_user(
        control_db, tenant, "coworker@origami-demo.com", role="worker", grid={"tasks": ["view"]}
    )
    control_db.commit()
    token = farmos_login(client, "worker@origami-demo.com", FARMOS_DEMO_PASSWORD)

    # Self-assignment (or leaving it unassigned) is fine without full_access.
    self_assigned = client.post(
        "/api/v1/tasks",
        json={"farm_id": str(tenant.id), "title": "My own task", "assigned_to": str(worker_user.id)},
        headers=farmos_headers(token),
    )
    assert self_assigned.status_code == 201, self_assigned.text

    # Assigning to someone else needs full_access (owner/manager).
    to_other = client.post(
        "/api/v1/tasks",
        json={"farm_id": str(tenant.id), "title": "Do this", "assigned_to": str(other_user.id)},
        headers=farmos_headers(token),
    )
    assert to_other.status_code == 403
    assert to_other.json()["detail"]


def test_owner_can_assign_task_to_anyone(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-ASSIGN2"))
    add_farmos_user(control_db, tenant, "owner4@origami-demo.com", role="owner")
    employee_user, _ = add_farmos_user(control_db, tenant, "emp@origami-demo.com", role="worker")
    control_db.commit()
    token = farmos_login(client, "owner4@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.post(
        "/api/v1/tasks",
        json={
            "farm_id": str(tenant.id),
            "title": "Fix the fence",
            "assigned_to": str(employee_user.id),
        },
        headers=farmos_headers(token),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["assigned_to"] == str(employee_user.id)


# --- Notifications -------------------------------------------------------


def _seed_notification(tenant_id: uuid.UUID, *, title: str, priority: str = "medium", read: bool = False):
    with TenantDataRouter.session_for(tenant_id) as db:
        note = Notification(
            tenant_id=tenant_id,
            module_code="animal_health",
            notification_type="alert",
            title=title,
            priority=priority,
            read_at=datetime.now(timezone.utc) if read else None,
        )
        db.add(note)
        db.flush()
        return note.id


def test_notifications_list_and_mark_read(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-NOTIF"))
    add_farmos_user(control_db, tenant, "owner5@origami-demo.com", role="owner")
    control_db.commit()
    token = farmos_login(client, "owner5@origami-demo.com", FARMOS_DEMO_PASSWORD)

    note_id = _seed_notification(tenant.id, title="Cow 12 needs attention", priority="high")
    _seed_notification(tenant.id, title="Already handled", read=True)

    listing = client.get("/api/v1/notifications", headers=farmos_headers(token))
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 2
    assert body["unread_count"] == 1

    unread_only = client.get(
        "/api/v1/notifications", params={"unread_only": True}, headers=farmos_headers(token)
    )
    assert [n["title"] for n in unread_only.json()["notifications"]] == ["Cow 12 needs attention"]

    marked = client.post(f"/api/v1/notifications/{note_id}/read", headers=farmos_headers(token))
    assert marked.status_code == 200
    assert marked.json()["read_at"] is not None

    after = client.get("/api/v1/notifications", headers=farmos_headers(token))
    assert after.json()["unread_count"] == 0


def test_mark_all_notifications_read(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-NOTIF2"))
    add_farmos_user(control_db, tenant, "owner6@origami-demo.com", role="owner")
    control_db.commit()
    token = farmos_login(client, "owner6@origami-demo.com", FARMOS_DEMO_PASSWORD)

    _seed_notification(tenant.id, title="One")
    _seed_notification(tenant.id, title="Two")

    resp = client.post("/api/v1/notifications/read-all", headers=farmos_headers(token))
    assert resp.status_code == 200
    assert resp.json() == {"marked_read": 2}

    after = client.get("/api/v1/notifications", headers=farmos_headers(token))
    assert after.json()["unread_count"] == 0


# --- Priorities & morning briefing --------------------------------------


def test_priorities_aggregates_tasks_and_notifications(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-PRI"))
    add_farmos_user(control_db, tenant, "owner7@origami-demo.com", role="owner")
    control_db.commit()
    token = farmos_login(client, "owner7@origami-demo.com", FARMOS_DEMO_PASSWORD)

    client.post(
        "/api/v1/tasks",
        json={"farm_id": str(tenant.id), "title": "Urgent task", "priority": "critical"},
        headers=farmos_headers(token),
    )
    _seed_notification(tenant.id, title="High priority alert", priority="high")
    _seed_notification(tenant.id, title="Already read", read=True)

    resp = client.get(
        "/api/v1/priorities", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["counts_by_priority"] == {"critical": 1, "high": 1}
    assert body["counts_by_module"] == {"tasks": 1, "animal_health": 1}
    # Critical outranks high.
    assert body["priorities"][0]["title"] == "Urgent task"
    kinds = {p["kind"] for p in body["priorities"]}
    assert kinds == {"task", "notification"}


def test_morning_briefing_reports_kpis(client, control_db):
    tenant = create_tenant(
        control_db, company_code=unique_code("FARM-BRIEF"), display_name="Origami Farms"
    )
    add_farmos_user(control_db, tenant, "owner8@origami-demo.com", role="owner", display_name="Farm Owner")
    control_db.commit()
    token = farmos_login(client, "owner8@origami-demo.com", FARMOS_DEMO_PASSWORD)

    client.post(
        "/api/v1/animals",
        json={"tag": "COW-1", "name": "Bessie", "species": "cow"},
        headers=farmos_headers(token),
    )
    overdue = client.post(
        "/api/v1/tasks",
        json={
            "farm_id": str(tenant.id),
            "title": "Overdue task",
            "due_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        },
        headers=farmos_headers(token),
    )
    assert overdue.status_code == 201, overdue.text
    _seed_notification(tenant.id, title="Critical alert", priority="critical")

    resp = client.get(
        "/api/v1/morning-briefing", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["farm_name"] == "Origami Farms"
    assert body["manager_name"] == "Farm Owner"
    assert body["kpis"]["animals"] == 1
    assert body["kpis"]["tasks_due"] == 1
    assert body["kpis"]["open_alerts"] == 1
    assert body["kpis"]["milk_today_l"] == 0
    assert len(body["tasks"]) == 1
    assert body["tasks"][0]["title"] == "Overdue task"


# --- Idempotency ---------------------------------------------------------


def test_idempotency_key_replays_verbatim_without_duplicating(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-IDEM"))
    add_farmos_user(control_db, tenant, "owner9@origami-demo.com", role="owner")
    control_db.commit()
    token = farmos_login(client, "owner9@origami-demo.com", FARMOS_DEMO_PASSWORD)

    key = str(uuid.uuid4())
    headers = {**farmos_headers(token), "Idempotency-Key": key}
    payload = {"tag": "IDEM-1", "name": "Bessie", "species": "cow"}

    first = client.post("/api/v1/animals", json=payload, headers=headers)
    assert first.status_code == 201
    assert "Idempotency-Replayed" not in first.headers

    replay = client.post("/api/v1/animals", json=payload, headers=headers)
    assert replay.status_code == 201
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.json() == first.json()

    listing = client.get(
        "/api/v1/animals", params={"farm_id": str(tenant.id)}, headers=farmos_headers(token)
    )
    assert len(listing.json()) == 1


def test_idempotency_key_not_remembered_after_failure(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-IDEM2"))
    add_farmos_user(
        control_db, tenant, "readonlyworker@origami-demo.com", role="worker", grid={"animals": ["view"]}
    )
    control_db.commit()
    token = farmos_login(client, "readonlyworker@origami-demo.com", FARMOS_DEMO_PASSWORD)

    key = str(uuid.uuid4())
    headers = {**farmos_headers(token), "Idempotency-Key": key}
    payload = {"tag": "IDEM-2", "name": "Bessie", "species": "cow"}

    denied = client.post("/api/v1/animals", json=payload, headers=headers)
    assert denied.status_code == 403

    # Same key, retried after nothing changed — still not permitted, and
    # not silently replayed as if it had once succeeded.
    denied_again = client.post("/api/v1/animals", json=payload, headers=headers)
    assert denied_again.status_code == 403
    assert "Idempotency-Replayed" not in denied_again.headers
