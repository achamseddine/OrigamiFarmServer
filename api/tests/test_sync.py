from __future__ import annotations

import uuid

from app.common.enums import TenantRole
from tests.conftest import auth_headers, dev_login, unique_code
from tests.helpers import add_membership, create_tenant, grant_module


def _bootstrap(control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-SYNC"))
    grant_module(control_db, tenant, "ANIMALS")
    add_membership(
        control_db,
        tenant,
        "sync@test.com",
        role=TenantRole.TENANT_OWNER,
        permissions=["ANIMALS:create", "ANIMALS:read", "ANIMALS:update"],
    )
    control_db.commit()
    return tenant


def test_duplicate_sync_push_is_idempotent(client, control_db):
    _bootstrap(control_db)
    token = dev_login(client, "sync@test.com")

    event_id = str(uuid.uuid4())
    entity_id = str(uuid.uuid4())
    push_body = {
        "changes": [
            {
                "event_id": event_id,
                "entity_type": "animal",
                "entity_id": entity_id,
                "operation": "CREATE",
                "payload": {"tag": "SYNC-1", "name": "Bessie", "species": "cow"},
            }
        ]
    }

    first = client.post("/api/v1/sync/push", json=push_body, headers=auth_headers(token))
    assert first.status_code == 200, first.text
    assert first.json()["results"][0]["status"] == "APPLIED"

    replay = client.post("/api/v1/sync/push", json=push_body, headers=auth_headers(token))
    assert replay.status_code == 200
    assert replay.json()["results"][0]["status"] == "REPLAYED"

    # Confirm it wasn't double-applied: pulling shows exactly one row.
    pulled = client.get("/api/v1/sync/pull", headers=auth_headers(token))
    matching = [c for c in pulled.json()["changes"] if c["entity_id"] == entity_id]
    assert len(matching) == 1
    assert matching[0]["version"] == 1


def test_sync_conflict_is_structured(client, control_db):
    _bootstrap(control_db)
    token = dev_login(client, "sync@test.com")
    entity_id = str(uuid.uuid4())

    create_resp = client.post(
        "/api/v1/sync/push",
        json={
            "changes": [
                {
                    "event_id": str(uuid.uuid4()),
                    "entity_type": "animal",
                    "entity_id": entity_id,
                    "operation": "CREATE",
                    "payload": {"tag": "SYNC-2", "name": "Clover", "species": "goat"},
                }
            ]
        },
        headers=auth_headers(token),
    )
    assert create_resp.json()["results"][0]["status"] == "APPLIED"

    # Two devices both edited the same stale (version=1) copy offline.
    first_update = client.post(
        "/api/v1/sync/push",
        json={
            "changes": [
                {
                    "event_id": str(uuid.uuid4()),
                    "entity_type": "animal",
                    "entity_id": entity_id,
                    "operation": "UPDATE",
                    "base_version": 1,
                    "payload": {"name": "Bessie"},
                }
            ]
        },
        headers=auth_headers(token),
    )
    assert first_update.json()["results"][0]["status"] == "APPLIED"
    assert first_update.json()["results"][0]["current_version"] == 2

    second_update = client.post(
        "/api/v1/sync/push",
        json={
            "changes": [
                {
                    "event_id": str(uuid.uuid4()),
                    "entity_type": "animal",
                    "entity_id": entity_id,
                    "operation": "UPDATE",
                    "base_version": 1,
                    "payload": {"name": "Clover"},
                }
            ]
        },
        headers=auth_headers(token),
    )
    result = second_update.json()["results"][0]
    assert result["status"] == "CONFLICT"
    assert result["current_version"] == 2
