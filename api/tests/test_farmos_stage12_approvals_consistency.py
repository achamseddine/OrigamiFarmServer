"""Stage 12 of the FarmOS tablet contract: employees enter the day's data,
the manager approves what changes the farm (approval requests), corrections
of milk records and feedings, buying feed from the Feed module, and the
record-consistency rules (nothing dated in the future, milk only from an
animal in milk, no records on an animal that left, a raw ingredient never
fed directly, a mix drawing no lot that had not arrived). Ports the
reference backend's test_employee_approvals.py, test_record_consistency.py
and the approval part of test_duplicates_and_approvals.py (OrigamiFarmOS
PR 23 and commit 32356c2; app/farmos/approval_service.py,
milk_service.py, record_time.py). See docs/FARMOS_API.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.audit.models import AuditEvent
from app.common.tenant_router import TenantDataRouter
from app.farmos import feed_models as fm
from app.farmos.approval_models import ApprovalRequest
from app.farmos.duplicates import PREFIX
from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant

API = "/api/v1"

# Karim: the day's data (feed and milk *create*), never *approve*.
WORKER_GRID = {
    "feed_nutrition": ["view", "create"],
    "milk_production": ["view", "create"],
    "animals": ["view", "create"],
    "inventory": ["view"],
}


def _at(days: float = 0) -> str:
    """ISO time [days] ago; negative is in the future."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def _login(client, email: str) -> dict:
    return farmos_headers(farmos_login(client, email, FARMOS_DEMO_PASSWORD))


def _refused(r, *words: str, code: int = 422) -> None:
    assert r.status_code == code, r.text
    detail = str(r.json()["detail"]).lower()
    for w in words:
        assert w.lower() in detail, r.text


def _tag() -> str:
    return f"T-{uuid.uuid4().hex[:6].upper()}"


class Farm:
    """A farm built through the API: Rami (owner), Karim (feed and milk
    *create*), Bella the dairy cow in milk, Thunder the horse, barley and
    alfalfa in stock."""

    def __init__(self, client, control_db, prefix: str, *, worker_grid: dict | None = WORKER_GRID):
        self.client = client
        self.control_db = control_db
        self.tenant = create_tenant(control_db, company_code=unique_code(prefix))
        self.owner, _ = add_farmos_user(
            control_db, self.tenant, f"owner-{prefix}@origami-demo.com", role="owner", display_name="Rami"
        )
        self.worker, _ = add_farmos_user(
            control_db,
            self.tenant,
            f"worker-{prefix}@origami-demo.com",
            role="worker",
            display_name="Karim",
            grid=worker_grid,
        )
        control_db.commit()
        self.h = _login(client, self.owner.email)
        self.worker_h = _login(client, self.worker.email)
        self.farm_id = str(self.tenant.id)

    # -- transport
    def post(self, path, body=None, expect=201, h=None):
        r = self.client.post(f"{API}{path}", json=body if body is not None else {}, headers=h or self.h)
        assert r.status_code == expect, f"POST {path}: {r.status_code} {r.text}"
        return r.json()

    def raw_post(self, path, body=None, h=None):
        return self.client.post(f"{API}{path}", json=body if body is not None else {}, headers=h or self.h)

    def get(self, path, params=None, h=None):
        r = self.client.get(f"{API}{path}", params=params, headers=h or self.h)
        assert r.status_code == 200, f"GET {path}: {r.status_code} {r.text}"
        return r.json()

    # -- the farm
    def stocked(self) -> Farm:
        self.barley = self.post(
            "/feed-products",
            {"name": "Barley", "unit_cost": 0.36, "opening_quantity": 1000, "is_feedable": True},
        )
        self.alfalfa = self.post(
            "/feed-products",
            {"name": "Alfalfa", "unit_cost": 0.3, "opening_quantity": 500, "is_feedable": True},
        )
        self.bella = self.cow("Bella", lactating=True, lactation_cycle=2, lactation_started_at=_at(200))
        self.thunder = self.post(
            "/animals",
            {
                "name": "Thunder",
                "species": "horse",
                "sex": "M",
                "identifiers": [{"identifier_type": "MICROCHIP", "identifier_value": uuid.uuid4().hex[:15]}],
            },
        )
        return self

    def cow(self, name: str, **fields) -> dict:
        return self.post(
            "/animals",
            {
                "name": name,
                "species": "cow",
                "sex": "F",
                "life_stage": "adult",
                "management_profile": "dairy",
                "tag": _tag(),
                **fields,
            },
        )

    def on_hand(self, product_id: str) -> float:
        return self.get("/feed-inventory/availability", {"feed_product_id": product_id})[0]["on_hand"]

    def milk_of(self, animal_id: str) -> list[dict]:
        rows = self.get("/production/milk", {"farm_id": self.farm_id})
        return [r for r in rows if r["animal_id"] == animal_id]

    def bell(self, notification_type: str | None = None) -> list[dict]:
        rows = self.get("/notifications", {"limit": 200})["notifications"]
        return [n for n in rows if notification_type is None or n["notification_type"] == notification_type]

    def audit(self, action: str) -> list[AuditEvent]:
        return list(
            self.control_db.scalars(
                select(AuditEvent).where(AuditEvent.tenant_id == self.tenant.id, AuditEvent.action == action)
            )
        )

    def feed(self, animal_id: str, product_id: str, qty: float, h=None, **extra) -> dict:
        return self.post(
            "/feeding-events",
            {
                "subject_type": "animal",
                "subject_id": animal_id,
                "event_type": "offered",
                "components": [{"feed_product_id": product_id, "quantity_offered": qty}],
                **extra,
            },
            h=h,
        )

    def mix_body(self) -> dict:
        """A recipe the manager has approved by making its first mix."""
        made = self.post(
            "/feed-mixes",
            {
                "new_product_name": "Heifer mix",
                "components": [{"feed_product_id": self.barley["id"], "actual_quantity": 30}],
            },
        )
        return {
            "feed_product_id": made["feed_product_id"],
            "components": [{"feed_product_id": self.barley["id"], "actual_quantity": 25}],
        }


# ------------------------------------------------------------- day's data
def test_an_employee_records_milk_and_feedings_directly(client, control_db):
    f = Farm(client, control_db, "S12-DAY").stocked()
    fed = f.feed(f.thunder["id"], f.alfalfa["id"], 4, h=f.worker_h)
    assert fed["status"] == "recorded" and fed["recorded_by"] == str(f.worker.id)
    milk = f.post(
        "/production/milk", {"animal_id": f.bella["id"], "session": "midday", "liters": 6}, h=f.worker_h
    )
    assert milk["liters"] == 6 and milk["recorded_by"] == str(f.worker.id)


# ---------------------------------------------------------------- buying
def test_buying_feed_is_requested_approved_then_received(client, control_db):
    f = Farm(client, control_db, "S12-BUY").stocked()
    delivery = {
        "feed_product_id": f.barley["id"],
        "quantity": 500,
        "unit": "kg",
        "supplier_label": "Bekaa Grains",
    }

    refused = f.raw_post("/feed-lots/receive", delivery, h=f.worker_h)
    _refused(refused, "purchase request", code=403)
    legacy = f.raw_post(
        "/feed/transactions",
        {"item_id": f.barley["inventory_item_id"], "direction": "in", "quantity": 100, "unit": "kg"},
        h=f.worker_h,
    )
    assert legacy.status_code in (403, 404), legacy.text

    asked = f.post(
        "/procurement/requisitions",
        {
            "lines": [{"feed_product_id": f.barley["id"], "requested_quantity": 500}],
            "justification": "Two weeks left",
            "purpose_code": "feed_purchase",
            "submit": True,
        },
        h=f.worker_h,
    )
    assert asked["status"] == "submitted"
    assert asked["lines"][0]["inventory_item_id"] == f.barley["inventory_item_id"]
    assert asked["lines"][0]["uom_code"] == "kg"
    told = [n for n in f.bell("purchase_request") if n["entity_id"] == asked["id"]]
    assert told and "Karim" in (told[0]["description"] or ""), "the managers hear of the request"

    early = f.raw_post("/feed-lots/receive", {**delivery, "requisition_id": asked["id"]}, h=f.worker_h)
    assert early.status_code == 409, "a delivery waits for the approval"
    assert f.raw_post(f"/procurement/requisitions/{asked['id']}/approve", {}, h=f.worker_h).status_code in (
        403,
        422,
    )
    approved = f.raw_post(
        f"/procurement/requisitions/{asked['id']}/approve", {"comments": "Order from Bekaa"}
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved", approved.text
    assert [
        n for n in f.bell("purchase_request_decision") if n["entity_id"] == asked["id"]
    ], "Karim hears the answer"

    too_much = f.raw_post(
        "/feed-lots/receive", {**delivery, "quantity": 600, "requisition_id": asked["id"]}, h=f.worker_h
    )
    _refused(too_much, "approved")
    before = f.on_hand(f.barley["id"])
    received = f.post(
        "/feed-lots/receive", {**delivery, "quantity": 520, "requisition_id": asked["id"]}, h=f.worker_h
    )
    assert received["accepted_quantity"] == 520
    assert f.on_hand(f.barley["id"]) == before + 520
    assert f.get(f"/procurement/requisitions/{asked['id']}", h=f.worker_h)["status"] == "received"


def test_a_rejected_purchase_request_says_why_and_buys_nothing(client, control_db):
    f = Farm(client, control_db, "S12-BUYNO").stocked()
    req = f.post(
        "/procurement/requisitions",
        {"lines": [{"feed_product_id": f.barley["id"], "requested_quantity": 200}], "submit": True},
        h=f.worker_h,
    )
    assert f.raw_post(f"/procurement/requisitions/{req['id']}/reject", {}).status_code == 422
    rejected = f.raw_post(f"/procurement/requisitions/{req['id']}/reject", {"comments": "Enough in the barn"})
    assert rejected.json()["status"] == "rejected"
    r = f.raw_post(
        "/feed-lots/receive",
        {"feed_product_id": f.barley["id"], "quantity": 200, "requisition_id": req["id"]},
        h=f.worker_h,
    )
    assert r.status_code == 409, r.text
    # A line that names neither an item nor a feed is refused; an unknown feed is 404.
    assert (
        f.raw_post(
            "/procurement/requisitions", {"lines": [{"requested_quantity": 5}]}, h=f.worker_h
        ).status_code
        == 422
    )
    assert (
        f.raw_post(
            "/procurement/requisitions",
            {"lines": [{"feed_product_id": str(uuid.uuid4()), "requested_quantity": 5}]},
            h=f.worker_h,
        ).status_code
        == 404
    )


def test_a_feed_approver_decides_a_request_that_is_for_feed_only(client, control_db):
    f = Farm(client, control_db, "S12-FEEDAPP").stocked()
    add_farmos_user(
        control_db,
        f.tenant,
        "feedlead-s12@origami-demo.com",
        role="worker",
        display_name="Nour",
        grid={"feed_nutrition": ["view", "create", "approve"]},
    )
    control_db.commit()
    lead = _login(client, "feedlead-s12@origami-demo.com")
    feed_only = f.post(
        "/procurement/requisitions",
        {"lines": [{"feed_product_id": f.barley["id"], "requested_quantity": 100}], "submit": True},
        h=f.worker_h,
    )
    decided = f.raw_post(f"/procurement/requisitions/{feed_only['id']}/approve", {}, h=lead)
    assert decided.status_code == 200, decided.text
    assert decided.json()["approvals"][0]["authority"]["basis"] == "feed_nutrition.approve"
    # Not for something that is not feed: a plain inventory item needs Inventory approval.
    item = f.post(
        "/feed/items", {"name": "Saline", "category": "medicine", "unit": "bottle", "initial_qty": 2}
    )
    other = f.post(
        "/procurement/requisitions",
        {"lines": [{"inventory_item_id": item["id"], "requested_quantity": 3}], "submit": True},
    )
    assert f.raw_post(f"/procurement/requisitions/{other['id']}/approve", {}, h=lead).status_code == 403
    # A delivery by the feed approver needs no request; one against the approved request closes it.
    assert (
        f.raw_post(
            "/feed-lots/receive", {"feed_product_id": f.barley["id"], "quantity": 50}, h=lead
        ).status_code
        == 201
    )


# ------------------------------------------------------------------ mixes
def test_a_workers_mix_waits_for_the_manager(client, control_db):
    """An employee's mix — even of an approved recipe — counts only once
    the manager approves it (the full request flow is below)."""
    f = Farm(client, control_db, "S12-MIXW").stocked()
    body = {
        "new_product_name": "Goat pellets",
        "components": [{"feed_product_id": f.barley["id"], "actual_quantity": 50}],
    }
    refused = f.raw_post("/feed-mixes", body, h=f.worker_h)
    assert refused.status_code == 403 and "approval" in refused.json()["detail"], refused.text
    assert refused.json()["detail"] == (
        "Recording a feed mix needs the farm manager (Feed & Nutrition approval). "
        "Send the mix for the manager's approval."
    )
    made = f.post("/feed-mixes", body)
    again_body = {
        "feed_product_id": made["feed_product_id"],
        "components": [{"feed_product_id": f.barley["id"], "actual_quantity": 40}],
    }
    assert f.raw_post("/feed-mixes", again_body, h=f.worker_h).status_code == 403
    sent = f.post("/approval-requests", {"kind": "feed_mix", "payload": again_body}, h=f.worker_h)
    assert sent["status"] == "pending" and sent["requested_by_name"] == "Karim"
    # Starting or completing a numbered batch is the same decision.
    started = f.raw_post(
        "/feed-batches",
        {"formula_version_id": made["formula_version_id"], "target_quantity": 100},
        h=f.worker_h,
    )
    assert started.status_code == 403 and "Feed & Nutrition approval" in started.json()["detail"]


def test_an_employees_mix_counts_only_once_the_manager_approves(client, control_db):
    f = Farm(client, control_db, "S12-MIX").stocked()
    body = f.mix_body()
    assert f.raw_post("/feed-mixes", body, h=f.worker_h).status_code == 403
    barley = f.on_hand(f.barley["id"])
    mixes = len(f.get("/feed-batches"))

    request = f.post("/approval-requests", {"kind": "feed_mix", "payload": body}, h=f.worker_h)
    assert request["status"] == "pending" and "Heifer mix" in request["summary"]
    assert request["kind"] == "feed_mix" and request["module_code"] == "feed_nutrition"
    assert f.on_hand(f.barley["id"]) == barley, "a request moves no stock"
    assert len(f.get("/feed-batches")) == mixes, "and numbers no mix"
    waiting_bell = [n for n in f.bell("approval_request") if n["entity_id"] == request["id"]]
    assert waiting_bell and waiting_bell[0]["title"].startswith(
        "Waiting for your approval: Mix of Heifer mix"
    )

    refused = f.raw_post(f"/approval-requests/{request['id']}/approve", h=f.worker_h)
    assert refused.status_code == 403 and "Feed & Nutrition approval" in refused.json()["detail"]
    waiting = f.get("/approval-requests", {"status": "pending"})
    assert request["id"] in [w["id"] for w in waiting]

    approved = f.raw_post(f"/approval-requests/{request['id']}/approve", {})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved" and approved.json()["result_type"] == "feed_batch"
    assert approved.json()["decided_by_name"] == "Rami"
    assert f.on_hand(f.barley["id"]) == barley - 25
    batch = f.get(f"/feed-batches/{approved.json()['result_id']}")
    assert batch["mix_number"] > 0 and batch["status"] == "completed"
    assert batch["operator_id"] == str(f.worker.id), "the mix stays the employee's work"
    answer = [n for n in f.bell("approval_decision") if n["entity_id"] == request["id"]]
    assert answer and answer[0]["title"].startswith("Rami approved:"), "the employee hears the answer"
    assert not [
        n for n in f.bell("approval_request") if n["entity_id"] == request["id"] and n["read_at"] is None
    ]
    assert f.raw_post(f"/approval-requests/{request['id']}/approve", {}).status_code == 409
    assert [a.summary for a in f.audit("approval_request.created")] == [
        f"Karim asked for approval: {request['summary']}"
    ]
    assert len(f.audit("approval_request.approved")) == 1


def test_a_rejected_mix_changes_nothing(client, control_db):
    f = Farm(client, control_db, "S12-MIXNO").stocked()
    body = f.mix_body()
    request = f.post("/approval-requests", {"kind": "feed_mix", "payload": body}, h=f.worker_h)
    barley = f.on_hand(f.barley["id"])
    assert (
        f.raw_post(f"/approval-requests/{request['id']}/reject", {}).status_code == 422
    ), "a rejection says why"
    rejected = f.raw_post(f"/approval-requests/{request['id']}/reject", {"note": "Weigh the barley again"})
    assert (
        rejected.json()["status"] == "rejected"
        and rejected.json()["decision_note"] == "Weigh the barley again"
    )
    assert f.on_hand(f.barley["id"]) == barley
    mine = f.get("/approval-requests", h=f.worker_h)
    assert [m["status"] for m in mine if m["id"] == request["id"]] == ["rejected"]
    answer = [n for n in f.bell("approval_decision") if n["entity_id"] == request["id"]]
    assert answer and answer[0]["description"] == "Weigh the barley again"


def test_a_mix_request_is_checked_when_it_is_sent(client, control_db):
    f = Farm(client, control_db, "S12-MIXCHK").stocked()
    _refused(
        f.raw_post("/approval-requests", {"kind": "feed_mix", "payload": {"components": []}}, h=f.worker_h),
        "ingredient",
    )
    _refused(
        f.raw_post(
            "/approval-requests",
            {
                "kind": "feed_mix",
                "payload": {
                    "new_product_name": "X",
                    "components": [{"feed_product_id": str(uuid.uuid4()), "actual_quantity": 5}],
                },
            },
            h=f.worker_h,
        ),
        "ingredient not found",
        code=404,
    )
    _refused(
        f.raw_post(
            "/approval-requests",
            {
                "kind": "feed_mix",
                "payload": {"components": [{"feed_product_id": f.barley["id"], "actual_quantity": 5}]},
            },
            h=f.worker_h,
        ),
        "which feed",
    )
    _refused(
        f.raw_post(
            "/approval-requests",
            {
                "kind": "feed_mix",
                "payload": {
                    "new_product_name": "X",
                    "produced_at": _at(-2),
                    "components": [{"feed_product_id": f.barley["id"], "actual_quantity": 5}],
                },
            },
            h=f.worker_h,
        ),
        "future",
    )
    _refused(f.raw_post("/approval-requests", {"kind": "unknown", "payload": {}}, h=f.worker_h), "kind")
    # Someone without *create* in the module cannot even ask.
    add_farmos_user(
        control_db,
        f.tenant,
        "acct-s12@origami-demo.com",
        role="accountant",
        grid={"finance": ["view", "create"]},
    )
    control_db.commit()
    acct = _login(client, "acct-s12@origami-demo.com")
    _refused(
        f.raw_post("/approval-requests", {"kind": "feed_mix", "payload": f.mix_body()}, h=acct),
        "Feed & Nutrition",
        code=403,
    )


# ------------------------------------------------------------- corrections
def test_a_milk_correction_by_an_employee_waits_for_the_manager(client, control_db):
    f = Farm(client, control_db, "S12-MILKC").stocked()
    bella = f.bella["id"]
    record = f.post("/production/milk", {"animal_id": bella, "session": "midday", "liters": 61}, h=f.worker_h)
    fix = {"liters": 16.1}

    assert (
        f.raw_post(
            f"/production/milk/{record['id']}/correct", {**fix, "reason": "decimal"}, h=f.worker_h
        ).status_code
        == 403
    )
    no_reason = f.raw_post(
        "/approval-requests",
        {"kind": "milk_correction", "target_id": record["id"], "payload": fix},
        h=f.worker_h,
    )
    _refused(no_reason, "say why")
    sent = f.post(
        "/approval-requests",
        {"kind": "milk_correction", "target_id": record["id"], "payload": fix, "reason": "Typed 61 for 16.1"},
        h=f.worker_h,
    )
    assert "61 L → 16.1 L" in sent["summary"]
    assert (
        sent["subject_type"] == "animal" and sent["subject_id"] == bella and sent["target_id"] == record["id"]
    )
    again = f.raw_post(
        "/approval-requests",
        {"kind": "milk_correction", "target_id": record["id"], "payload": {"liters": 16}, "reason": "again"},
        h=f.worker_h,
    )
    _refused(again, "Karim asked to correct this record", code=409)
    direct = f.raw_post(f"/production/milk/{record['id']}/correct", {"liters": 15, "reason": "manager"})
    assert direct.status_code == 409, "the manager decides the request first"
    assert [r["liters"] for r in f.milk_of(bella) if r["id"] == record["id"]] == [61]

    approved = f.raw_post(f"/approval-requests/{sent['id']}/approve", {})
    assert approved.status_code == 200, approved.text
    assert approved.json()["result_type"] == "milk_record" and approved.json()["result_id"] == record["id"]
    assert [r["liters"] for r in f.milk_of(bella) if r["id"] == record["id"]] == [16.1]
    audit = [a for a in f.audit("milk_record.corrected") if a.entity_id == record["id"]]
    assert audit and audit[-1].changes_json == {"liters": {"from": 61.0, "to": 16.1}}
    assert audit[-1].metadata_json["requested_by"] == str(f.worker.id)
    assert audit[-1].metadata_json["approved_by"] == str(f.owner.id)
    assert audit[-1].metadata_json["approval_request_id"] == sent["id"]


def test_the_manager_corrects_milk_directly_and_the_rules_still_hold(client, control_db):
    f = Farm(client, control_db, "S12-MILKM").stocked()
    bella = f.bella["id"]
    record = f.post("/production/milk", {"animal_id": bella, "session": "midday", "liters": 9})
    _refused(
        f.raw_post(f"/production/milk/{record['id']}/correct", {"liters": 9, "reason": "x"}),
        "changes nothing",
    )
    _refused(
        f.raw_post(f"/production/milk/{record['id']}/correct", {"liters": 400, "reason": "x"}),
        "check the amount",
    )
    _refused(f.raw_post(f"/production/milk/{record['id']}/correct", {"liters": 10}), "say why")
    _refused(
        f.raw_post(f"/production/milk/{record['id']}/correct", {"session": "noon", "reason": "x"}), "session"
    )
    _refused(
        f.raw_post(f"/production/milk/{record['id']}/correct", {"recorded_at": _at(-3), "reason": "x"}),
        "future",
    )
    fixed = f.raw_post(
        f"/production/milk/{record['id']}/correct",
        {"liters": 10.5, "session": "evening", "reason": "evening milking"},
    )
    assert fixed.status_code == 200, fixed.text
    assert (fixed.json()["liters"], fixed.json()["session"]) == (10.5, "evening")
    assert fixed.json()["id"] == record["id"], "corrected in place"
    # Moving it onto a session already recorded that day is refused.
    f.post("/production/milk", {"animal_id": bella, "session": "morning", "liters": 8})
    clash = f.raw_post(f"/production/milk/{record['id']}/correct", {"session": "morning", "reason": "x"})
    _refused(clash, "already recorded", code=409)
    # The dry cow cannot receive a corrected record either.
    dry = f.cow("Dalia")
    _refused(
        f.raw_post(
            f"/production/milk/{record['id']}/correct", {"animal_id": dry["id"], "reason": "wrong cow"}
        ),
        "not recorded as being milked",
        code=409,
    )
    assert (
        f.raw_post(f"/production/milk/{uuid.uuid4()}/correct", {"liters": 1, "reason": "x"}).status_code
        == 404
    )


def test_an_employee_withdraws_a_request_only_their_own(client, control_db):
    f = Farm(client, control_db, "S12-CANCEL").stocked()
    bella = f.bella["id"]
    record = f.post("/production/milk", {"animal_id": bella, "session": "midday", "liters": 8}, h=f.worker_h)
    sent = f.post(
        "/approval-requests",
        {"kind": "milk_correction", "target_id": record["id"], "payload": {"liters": 7}, "reason": "misread"},
        h=f.worker_h,
    )
    assert sent["id"] not in [r["id"] for r in f.get("/approval-requests", {"mine": True})]
    assert sent["id"] in [r["id"] for r in f.get("/approval-requests")], "the manager's list has it"
    # Another employee cannot withdraw it.
    add_farmos_user(control_db, f.tenant, "other-s12@origami-demo.com", role="worker", grid=WORKER_GRID)
    control_db.commit()
    other = _login(client, "other-s12@origami-demo.com")
    _refused(
        f.raw_post(f"/approval-requests/{sent['id']}/cancel", h=other), "only the person who asked", code=403
    )
    cancelled = f.raw_post(f"/approval-requests/{sent['id']}/cancel", h=f.worker_h)
    assert cancelled.json()["status"] == "cancelled"
    assert [r["liters"] for r in f.milk_of(bella) if r["id"] == record["id"]] == [8]
    assert f.raw_post(f"/approval-requests/{sent['id']}/cancel", h=f.worker_h).status_code == 409
    assert f.raw_post(f"/approval-requests/{sent['id']}/approve", {}).status_code == 409
    # The record is free again: a second request may be sent.
    assert (
        f.raw_post(
            "/approval-requests",
            {
                "kind": "milk_correction",
                "target_id": record["id"],
                "payload": {"liters": 7.5},
                "reason": "again",
            },
            h=f.worker_h,
        ).status_code
        == 201
    )


def test_a_feeding_correction_returns_the_stock_and_records_what_was_fed(client, control_db):
    f = Farm(client, control_db, "S12-FEEDC").stocked()
    alfalfa = f.on_hand(f.alfalfa["id"])
    event = f.feed(f.thunder["id"], f.alfalfa["id"], 12, h=f.worker_h)
    assert f.on_hand(f.alfalfa["id"]) == alfalfa - 12
    reverse = f.raw_post(f"/feeding-events/{event['id']}/reverse", {"reason": "x"}, h=f.worker_h)
    assert reverse.status_code == 403 and "send the correction for approval" in reverse.json()["detail"]
    assert (
        f.raw_post(f"/feeding-events/{event['id']}/correct", {"reason": "x"}, h=f.worker_h).status_code == 403
    )

    fix = {"components": [{"feed_product_id": f.alfalfa["id"], "quantity_offered": 2}]}
    sent = f.post(
        "/approval-requests",
        {
            "kind": "feeding_correction",
            "target_id": event["id"],
            "payload": fix,
            "reason": "It was 2 kg, not 12",
        },
        h=f.worker_h,
    )
    assert (
        sent["summary"].startswith("Correct the feeding of Thunder on") and "12 kg → 2 kg" in sent["summary"]
    )
    assert f.on_hand(f.alfalfa["id"]) == alfalfa - 12, "nothing moves while it waits"
    # The manager's direct correction waits for the request to be decided.
    assert f.raw_post(f"/feeding-events/{event['id']}/correct", {"reason": "mine"}).status_code == 409
    approved = f.raw_post(f"/approval-requests/{sent['id']}/approve", {})
    assert approved.status_code == 200, approved.text
    assert f.on_hand(f.alfalfa["id"]) == alfalfa - 2
    with TenantDataRouter.session_for(f.tenant.id) as db:
        original = db.get(fm.FeedingEvent, uuid.UUID(event["id"]))
        assert original is not None and original.status == "reversed"
        replacement = db.get(fm.FeedingEvent, uuid.UUID(approved.json()["result_id"]))
        assert replacement is not None and replacement.status == "recorded"
        assert replacement.recorded_by == f.worker.id
        assert [c.quantity_offered for c in replacement.components] == [2]
        assert replacement.notes is not None and replacement.notes.startswith(f"Correction of {event['id']}")
    corrected = f.audit("feeding_event.corrected")
    assert corrected and corrected[0].changes_json["requested_by"] == str(f.worker.id)
    # The corrected feeding cannot be corrected again.
    assert (
        f.raw_post(
            "/approval-requests",
            {"kind": "feeding_correction", "target_id": event["id"], "payload": {}, "reason": "again"},
            h=f.worker_h,
        ).status_code
        == 409
    )


def test_the_manager_undoes_a_feeding_directly(client, control_db):
    f = Farm(client, control_db, "S12-UNDO").stocked()
    alfalfa = f.on_hand(f.alfalfa["id"])
    event = f.feed(f.thunder["id"], f.alfalfa["id"], 3)
    _refused(f.raw_post(f"/feeding-events/{event['id']}/correct", {}), "say why")
    undone = f.raw_post(f"/feeding-events/{event['id']}/correct", {"reason": "Wrong horse"})
    assert undone.status_code == 201, undone.text
    assert undone.json()["status"] == "reversal" and undone.json()["reversal_of_id"] == event["id"]
    assert f.on_hand(f.alfalfa["id"]) == alfalfa
    assert f.raw_post(f"/feeding-events/{event['id']}/correct", {"reason": "again"}).status_code in (409, 422)
    assert f.raw_post(f"/feeding-events/{uuid.uuid4()}/correct", {"reason": "x"}).status_code == 404


def test_an_employee_sees_only_their_own_requests(client, control_db):
    f = Farm(client, control_db, "S12-OWN").stocked()
    record = f.post("/production/milk", {"animal_id": f.bella["id"], "session": "midday", "liters": 5})
    mine = f.post(
        "/approval-requests",
        {
            "kind": "milk_correction",
            "target_id": record["id"],
            "payload": {"liters": 6},
            "reason": "manager's own",
        },
    )
    assert mine["id"] not in [r["id"] for r in f.get("/approval-requests", h=f.worker_h)]
    assert client.get(f"{API}/approval-requests/{mine['id']}", headers=f.worker_h).status_code == 404
    assert f.get(f"/approval-requests/{mine['id']}")["id"] == mine["id"]
    assert client.get(f"{API}/approval-requests", params={"status": "weird"}, headers=f.h).status_code == 422
    # The manager's own request: approving it is still an approval, by someone with the right.
    approved = f.raw_post(f"/approval-requests/{mine['id']}/approve", {})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert not [n for n in f.bell("approval_decision") if n["entity_id"] == mine["id"]], "nobody else to tell"


def test_approval_requests_belong_to_their_farm(client, control_db):
    f = Farm(client, control_db, "S12-ISO").stocked()
    other = Farm(client, control_db, "S12-ISO2")
    record = f.post(
        "/production/milk", {"animal_id": f.bella["id"], "session": "midday", "liters": 5}, h=f.worker_h
    )
    sent = f.post(
        "/approval-requests",
        {"kind": "milk_correction", "target_id": record["id"], "payload": {"liters": 6}, "reason": "misread"},
        h=f.worker_h,
    )
    assert client.get(f"{API}/approval-requests/{sent['id']}", headers=other.h).status_code == 404
    assert (
        client.post(f"{API}/approval-requests/{sent['id']}/approve", json={}, headers=other.h).status_code
        == 404
    )
    assert client.post(f"{API}/approval-requests/{sent['id']}/cancel", headers=other.h).status_code == 404
    assert other.get("/approval-requests") == []
    # Nor can another farm's record be named in a request.
    assert (
        client.post(
            f"{API}/approval-requests",
            json={
                "kind": "milk_correction",
                "target_id": record["id"],
                "payload": {"liters": 6},
                "reason": "x",
            },
            headers=other.h,
        ).status_code
        == 404
    )
    with TenantDataRouter.session_for(other.tenant.id) as db:
        assert db.scalars(select(ApprovalRequest)).all() == []


# ------------------------------------------------------------ nothing from the future
def test_no_record_is_dated_in_the_future(client, control_db):
    f = Farm(client, control_db, "S12-FUTURE").stocked()
    future = _at(-2)
    _refused(
        f.raw_post("/animals", {"name": "Tomorrow", "species": "cow", "sex": "F", "birth_date": future}),
        "birth date",
        "future",
    )
    _refused(
        client.put(f"{API}/animals/{f.bella['id']}", json={"birth_date": future}, headers=f.h),
        "birth date",
        "future",
    )
    _refused(
        f.raw_post(
            "/production/milk",
            {"animal_id": f.bella["id"], "session": "evening", "liters": 9, "recorded_at": future},
        ),
        "milk",
        "future",
    )
    layers = f.post(
        "/animal-groups",
        {
            "name": "Layer Flock",
            "species": "layer_hen",
            "count": 120,
            "sex_composition": "female",
            "management_profile": "layer",
        },
    )
    _refused(
        f.raw_post(
            "/production/eggs",
            {"flock_id": layers["id"], "total_eggs": 30, "sellable_eggs": 30, "recorded_at": future},
        ),
        "egg collection",
        "future",
    )
    _refused(
        f.raw_post(
            "/feeding-events",
            {
                "subject_type": "animal",
                "subject_id": f.thunder["id"],
                "occurred_at": future,
                "components": [{"feed_product_id": f.alfalfa["id"], "quantity_offered": 3}],
            },
        ),
        "feeding",
        "future",
    )
    _refused(
        f.raw_post(
            "/feed-lots/receive", {"feed_product_id": f.barley["id"], "quantity": 100, "received_at": future}
        ),
        "delivery",
        "future",
    )
    _refused(
        f.raw_post(
            "/feed-mixes",
            {
                "new_product_name": "Tomorrow's mix",
                "produced_at": future,
                "components": [{"feed_product_id": f.barley["id"], "actual_quantity": 50}],
            },
        ),
        "mix",
        "future",
    )
    _refused(
        f.raw_post(
            "/observations",
            {
                "farm_id": f.farm_id,
                "entity_type": "animal",
                "entity_id": f.bella["id"],
                "observation_type": "limping",
                "observer_id": str(f.owner.id),
                "observed_at": future,
            },
        ),
        "observation",
        "future",
    )
    _refused(
        f.raw_post(
            "/health/cases", {"subject_type": "animal", "subject_id": f.bella["id"], "opened_at": future}
        ),
        "health case",
        "future",
    )
    _refused(
        f.raw_post(
            f"/animal-groups/{layers['id']}/quantity-events",
            {"event_type": "DEATH", "quantity_delta": -1, "occurred_at": future},
        ),
        "future",
    )
    field = f.post("/fields", {"name": "South plot"})
    _refused(
        f.raw_post(
            "/production/harvest",
            {"field_id": field["id"], "product_name": "Tomato", "quantity": 80, "recorded_at": future},
        ),
        "harvest",
        "future",
    )
    _refused(
        f.raw_post(
            "/production/harvest",
            {"field_id": field["id"], "product_name": "Tomato", "quantity": 80, "waste_qty": 90},
        ),
        "waste",
    )
    _refused(
        f.raw_post(
            "/harvest",
            {"field_id": field["id"], "product_name": "Tomato", "total_quantity": 80, "recorded_at": future},
        ),
        "harvest",
        "future",
    )
    # A few minutes ahead is a tablet clock, not the future.
    soon = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    assert (
        f.raw_post(
            "/production/milk",
            {"animal_id": f.bella["id"], "session": "evening", "liters": 9, "recorded_at": soon},
        ).status_code
        == 201
    )


def test_an_animal_does_not_arrive_before_it_is_born(client, control_db):
    f = Farm(client, control_db, "S12-BORN")
    r = f.raw_post(
        "/animals",
        {"name": "Early", "species": "cow", "sex": "F", "birth_date": _at(100), "acquisition_date": _at(200)},
    )
    _refused(r, "acquisition", "birth")
    ok = f.post(
        "/animals",
        {"name": "Late", "species": "cow", "sex": "F", "birth_date": _at(200), "acquisition_date": _at(100)},
    )
    _refused(
        client.put(f"{API}/animals/{ok['id']}", json={"acquisition_date": _at(300)}, headers=f.h),
        "acquisition",
        "birth",
    )


# ------------------------------------------------------------ milk
def test_milk_needs_an_animal_in_milk_a_known_session_and_a_plausible_amount(client, control_db):
    f = Farm(client, control_db, "S12-INMILK")
    dry = f.cow("Dalia")
    r = f.raw_post("/production/milk", {"animal_id": dry["id"], "session": "morning", "liters": 8})
    _refused(r, "not recorded as being milked", "start her milking", code=409)
    # Starting her milking is the one step that makes the milk record valid.
    assert f.raw_post(f"/animals/{dry['id']}/lactations", {}).status_code == 201
    assert (
        f.raw_post(
            "/production/milk", {"animal_id": dry["id"], "session": "morning", "liters": 8}
        ).status_code
        == 201
    )

    fresh = f.cow("Farah", lactating=True, lactation_started_at=_at(20))
    _refused(
        f.raw_post(
            "/production/milk",
            {"animal_id": fresh["id"], "session": "evening", "liters": 9, "recorded_at": _at(40)},
        ),
        "started this lactation",
        "calving date",
    )
    _refused(
        f.raw_post("/production/milk", {"animal_id": fresh["id"], "session": "noon", "liters": 9}), "session"
    )
    _refused(
        f.raw_post("/production/milk", {"animal_id": fresh["id"], "session": "evening", "liters": 185}),
        "check the amount",
    )
    assert (
        f.raw_post(
            "/production/milk", {"animal_id": fresh["id"], "session": "midday", "liters": 18.5}
        ).status_code
        == 201
    )
    # Yesterday's milk, within the lactation, is fine; so is a day of grace at its start.
    assert (
        f.raw_post(
            "/production/milk",
            {"animal_id": fresh["id"], "session": "evening", "liters": 9, "recorded_at": _at(20.5)},
        ).status_code
        == 201
    )


# ------------------------------------------------------------ after an animal left
def test_nothing_is_recorded_on_an_animal_that_left_the_farm(client, control_db):
    f = Farm(client, control_db, "S12-LEFT").stocked()
    cow = f.cow("Salma", lactating=True)
    sale_weight = f.raw_post(f"/animals/{cow['id']}/weights", {"weight_kg": 510, "measured_at": _at(1)})
    assert sale_weight.status_code == 201, sale_weight.text
    assert (
        f.raw_post(f"/animals/{cow['id']}/exit", {"exit_type": "sold", "reason": "market"}).status_code == 200
    )
    gone = "no longer on the farm"
    _refused(f.raw_post(f"/animals/{cow['id']}/weights", {"weight_kg": 520}), gone, code=409)
    # A weight from before she left (the sale weight) is still accepted.
    assert (
        f.raw_post(
            f"/animals/{cow['id']}/weights",
            {"weight_kg": 512, "measured_at": _at(2), "confirm_duplicate": True},
        ).status_code
        == 201
    )
    _refused(f.raw_post("/health/cases", {"subject_type": "animal", "subject_id": cow["id"]}), gone, code=409)
    _refused(
        f.raw_post(
            "/health/treatments",
            {
                "entity_type": "animal",
                "entity_id": cow["id"],
                "medication": "Oxytetracycline",
                "dose": "10 ml",
                "route": "IM",
                "responsible_user_id": str(f.owner.id),
            },
        ),
        gone,
        code=409,
    )
    _refused(
        f.raw_post(
            "/observations",
            {
                "farm_id": f.farm_id,
                "entity_type": "animal",
                "entity_id": cow["id"],
                "observation_type": "limping",
                "observer_id": str(f.owner.id),
            },
        ),
        gone,
        code=409,
    )
    _refused(
        f.raw_post("/production/milk", {"animal_id": cow["id"], "session": "morning", "liters": 5}),
        gone,
        code=409,
    )
    _refused(
        f.raw_post(
            "/feeding-events",
            {
                "subject_type": "animal",
                "subject_id": cow["id"],
                "components": [{"feed_product_id": f.alfalfa["id"], "quantity_offered": 3}],
            },
        ),
        gone,
        code=409,
    )
    _refused(f.raw_post(f"/animals/{cow['id']}/exit", {"exit_type": "dead"}), "already left", code=409)


def test_a_dose_is_not_given_to_an_animal_that_left_nor_dated_ahead(client, control_db):
    f = Farm(client, control_db, "S12-DOSE").stocked()
    medicine = f.post(
        "/pharmacy/medicines",
        {
            "name": "Oxytetracycline 200",
            "unit": "ml",
            "dosage_form": "injectable",
            "species_codes": ["cow"],
            "administration_routes": ["IM"],
            "prescription_required": False,
        },
    )
    f.post(
        "/pharmacy/lots/receive",
        {
            "inventory_item_id": medicine["inventory_item_id"],
            "quantity": 500,
            "lot_code": "OXY-1",
            "expiry_date": _at(-365),
        },
    )
    dose = {
        "subject_type": "animal",
        "subject_id": f.bella["id"],
        "inventory_item_id": medicine["inventory_item_id"],
        "dose_quantity": 10,
        "dose_unit": "ml",
        "route_code": "IM",
    }
    _refused(f.raw_post("/pharmacy/administrations", {**dose, "administered_at": _at(-1)}), "dose", "future")
    cow = f.cow("Salma", lactating=True)
    f.raw_post(f"/animals/{cow['id']}/exit", {"exit_type": "sold"})
    _refused(
        f.raw_post("/pharmacy/administrations", {**dose, "subject_id": cow["id"]}),
        "no longer on the farm",
        code=409,
    )
    assert f.raw_post("/pharmacy/administrations", dose).status_code == 201


# ------------------------------------------------------------ feed
def test_an_ingredient_is_not_fed_directly_and_a_cost_is_not_negative(client, control_db):
    f = Farm(client, control_db, "S12-INGR").stocked()
    corn = f.post(
        "/feed-products",
        {
            "name": "Corn grain",
            "unit": "kg",
            "is_ingredient": True,
            "is_feedable": False,
            "category": "concentrate",
        },
    )
    _refused(
        f.raw_post("/feed-lots/receive", {"feed_product_id": corn["id"], "quantity": 500, "unit_cost": -1}),
        "unit_cost",
    )
    assert (
        f.raw_post(
            "/feed-lots/receive", {"feed_product_id": corn["id"], "quantity": 500, "unit_cost": 0.3}
        ).status_code
        == 201
    )
    r = f.raw_post(
        "/feeding-events",
        {
            "subject_type": "animal",
            "subject_id": f.bella["id"],
            "event_type": "offered",
            "components": [{"feed_product_id": corn["id"], "quantity_offered": 2}],
        },
    )
    _refused(r, "ingredient", "mix")
    assert f.on_hand(corn["id"]) == 500, "the refused feeding drew nothing"


def test_a_mix_cannot_use_feed_that_had_not_arrived_yet(client, control_db):
    f = Farm(client, control_db, "S12-ARRIVE")
    corn = f.post(
        "/feed-products",
        {
            "name": "Corn grain",
            "unit": "kg",
            "is_ingredient": True,
            "is_feedable": False,
            "category": "concentrate",
        },
    )
    assert (
        f.raw_post(
            "/feed-lots/receive", {"feed_product_id": corn["id"], "quantity": 800, "received_at": _at(3)}
        ).status_code
        == 201
    )
    body = {
        "new_product_name": "Heifer mix",
        "components": [{"feed_product_id": corn["id"], "actual_quantity": 200}],
    }
    _refused(f.raw_post("/feed-mixes", {**body, "produced_at": _at(10)}), "had not arrived yet", "corn grain")
    # Nothing was drawn by the refused mix: the ledger is untouched.
    stock = next(p for p in f.get("/feed-products") if p["id"] == corn["id"])
    assert stock["availability"]["on_hand"] == 800
    assert len(f.get("/feed-batches")) == 0, "and no number was taken"
    ok = f.raw_post("/feed-mixes", {**body, "produced_at": _at(1)})
    assert ok.status_code == 201, ok.text


def test_a_species_change_is_refused_once_the_animal_has_feeding_records(client, control_db):
    f = Farm(client, control_db, "S12-SPECIES").stocked()
    cow = f.post("/animals", {"name": "Nadia", "species": "cow", "sex": "F", "tag": _tag()})
    assert client.put(f"{API}/animals/{cow['id']}", json={"species": "goat"}, headers=f.h).status_code == 200
    f.feed(cow["id"], f.alfalfa["id"], 2)
    locked = client.put(f"{API}/animals/{cow['id']}", json={"species": "sheep"}, headers=f.h)
    _refused(locked, "feeding", "cannot be changed", code=409)


# ------------------------------------------------------------ tasks and head counts
def test_a_task_needs_a_title_and_is_not_written_down_twice(client, control_db):
    f = Farm(client, control_db, "S12-TASK")
    _refused(f.raw_post("/tasks", {"farm_id": f.farm_id, "title": "   "}), "title")
    task = {"farm_id": f.farm_id, "title": "Mend the north fence"}
    assert f.raw_post("/tasks", task).status_code == 201
    again = f.raw_post("/tasks", {**task, "title": "mend the North fence"})
    assert again.status_code == 409 and again.json()["detail"].startswith(PREFIX), again.text
    assert f.raw_post("/tasks", {**task, "confirm_duplicate": True}).status_code == 201


def test_the_same_flock_loss_counted_twice_is_asked(client, control_db):
    f = Farm(client, control_db, "S12-FLOCK")
    layers = f.post(
        "/animal-groups",
        {
            "name": "Layer Flock",
            "species": "layer_hen",
            "count": 120,
            "sex_composition": "female",
            "management_profile": "layer",
        },
    )
    loss = {"event_type": "DEATH", "quantity_delta": -2}
    assert f.raw_post(f"/animal-groups/{layers['id']}/quantity-events", loss).status_code == 201
    again = f.raw_post(f"/animal-groups/{layers['id']}/quantity-events", loss)
    assert again.status_code == 409 and again.json()["detail"].startswith(PREFIX), again.text
    assert "death -2 on Layer Flock" in again.json()["detail"]
    assert f.get(f"/animal-groups/{layers['id']}")["count"] == 118
    # A real second loss is confirmed and written down as such.
    assert (
        f.raw_post(
            f"/animal-groups/{layers['id']}/quantity-events", {**loss, "confirm_duplicate": True}
        ).status_code
        == 201
    )
    assert f.get(f"/animal-groups/{layers['id']}")["count"] == 116
    assert [e.metadata_json["record"][:15] for e in f.audit("duplicate.confirmed")] == ["quantity_event:"]
    # A different loss the same half hour is simply another row.
    assert (
        f.raw_post(
            f"/animal-groups/{layers['id']}/quantity-events", {**loss, "quantity_delta": -3}
        ).status_code
        == 201
    )


# ------------------------------------------------------------ one key, asked then confirmed
def test_a_confirmed_resend_reuses_the_key_of_the_refused_write(client, control_db):
    """The tablet resends a refused "Possible duplicate" with the same
    Idempotency-Key: the refusal was not remembered, the confirmed write is,
    and a replay of it records nothing more."""
    f = Farm(client, control_db, "S12-KEY").stocked()
    h = {**f.h, "Idempotency-Key": "review-confirm-1"}
    body = {"animal_id": f.bella["id"], "session": "evening", "liters": 6.5}
    assert f.raw_post("/production/milk", {**body, "confirm_duplicate": True}).status_code == 201
    asked = client.post(f"{API}/production/milk", json=body, headers=h)
    assert asked.status_code == 409
    confirmed = client.post(f"{API}/production/milk", json={**body, "confirm_duplicate": True}, headers=h)
    assert confirmed.status_code == 201 and confirmed.headers.get("Idempotency-Replayed") is None
    replay = client.post(f"{API}/production/milk", json={**body, "confirm_duplicate": True}, headers=h)
    assert (
        replay.headers.get("Idempotency-Replayed") == "true" and replay.json()["id"] == confirmed.json()["id"]
    )
    assert len(f.milk_of(f.bella["id"])) == 2


# ------------------------------------------------------------ milk entered later
def test_milk_entered_later_is_recorded_for_its_own_day(client, control_db):
    """Milk noted on paper and typed in days later (the app's "Milked on"
    date): it is filed under that day, today's same session is not taken
    for a repeat of it, and the same session of that day still is."""
    f = Farm(client, control_db, "S12-LATER")
    cow = f.cow("Hiba", lactating=True, lactation_started_at=_at(30))
    that_day = (datetime.now(timezone.utc) - timedelta(days=3)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    body = {"animal_id": cow["id"], "session": "morning", "liters": 10, "recorded_at": that_day.isoformat()}
    assert f.raw_post("/production/milk", body).status_code == 201
    assert (
        f.raw_post(
            "/production/milk", {"animal_id": cow["id"], "session": "morning", "liters": 11}
        ).status_code
        == 201
    )
    again = f.raw_post("/production/milk", {**body, "liters": 10.5})
    assert again.status_code == 409 and again.json()["detail"].startswith(PREFIX), again.text
    listed = f.milk_of(cow["id"])
    days = sorted(datetime.fromisoformat(m["recorded_at"].replace("Z", "+00:00")).date() for m in listed)
    assert days == [that_day.date(), datetime.now(timezone.utc).date()]


# ------------------------------------------------------------ the manager's rights
def test_the_feed_list_rations_and_reservations_are_the_managers(client, control_db):
    f = Farm(client, control_db, "S12-RIGHTS").stocked()
    w = f.worker_h
    sentence = "needs the farm manager (Feed & Nutrition approval)"
    for path, body in (
        ("/feed-products", {"name": "Bran"}),
        ("/feed-products/standard", {}),
        (
            f"/livestock-subjects/{f.bella['id']}/feeding-assignments",
            {"assignment_type": "supplement", "feed_product_id": f.alfalfa["id"], "quantity_per_head": 1},
        ),
        ("/feed-allocations", {"feed_product_id": f.alfalfa["id"], "quantity": 10, "species_code": "cow"}),
    ):
        r = f.raw_post(path, body, h=w)
        assert r.status_code == 403 and sentence in r.json()["detail"], f"{path}: {r.status_code} {r.text}"
    r = client.put(
        f"{API}/feed-reorder-policies",
        json={"feed_product_id": f.alfalfa["id"], "reorder_point": 50},
        headers=w,
    )
    assert r.status_code == 403 and sentence in r.json()["detail"], r.text
    r = client.patch(f"{API}/feed-products/{f.alfalfa['id']}", json={"name": "Lucerne"}, headers=w)
    assert r.status_code == 403 and sentence in r.json()["detail"], r.text
    # *create* still covers the day's work and a draft recipe.
    starter = f.post(
        "/feed-products",
        {
            "name": "Calf starter",
            "source_type": "farm_produced",
            "category": "concentrate",
            "is_feedable": True,
        },
    )
    draft = f.raw_post(
        "/feed-formulas",
        {
            "name": "Calf starter",
            "feed_product_id": starter["id"],
            "batch_size": 100,
            "components": [{"feed_product_id": f.barley["id"], "target_quantity": 100}],
        },
        h=w,
    )
    assert draft.status_code == 201 and draft.json()["active_version"] is None, draft.text
    assert (
        f.raw_post("/feed-lots/receive", {"feed_product_id": f.barley["id"], "quantity": 5}, h=w).status_code
        == 403
    )
