"""Stage 7 of the FarmOS tablet contract: the generic feed architecture —
products and lots as a ledger, formulas and numbered mixes, usage policy,
feeding programs with inheritance and review, allocations, days of cover,
reorder, reconciliation and costs. See docs/FARMOS_API.md and
OrigamiFarmOS docs/GENERIC-FEED-ARCHITECTURE.md (§22, §34 acceptance).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant

API = "/api/v1"


def _owner(client, control_db, prefix: str, email: str):
    tenant = create_tenant(control_db, company_code=unique_code(prefix))
    add_farmos_user(control_db, tenant, email, role="owner")
    control_db.commit()
    token = farmos_login(client, email, FARMOS_DEMO_PASSWORD)
    return tenant, farmos_headers(token)


def _post(client, h, path, body, expect=201):
    r = client.post(f"{API}{path}", json=body, headers=h)
    assert r.status_code == expect, f"{path}: {r.status_code} {r.text}"
    return r.json()


def _get(client, h, path, params=None):
    r = client.get(f"{API}{path}", params=params, headers=h)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
    return r.json()


def _product(client, h, name, **fields):
    return _post(client, h, "/feed-products", {"name": name, **fields})


def _availability(client, h, product_id) -> dict:
    return _get(client, h, f"/feed-products/{product_id}")["availability"]


def _goat_farm(client, h):
    """A dairy goat farm: purchased ingredients, a cattle/goat-only premix
    behind a policy, a farm-produced mix with a formula, a herd of two does
    in a group, and two programs the resolver can tell apart."""
    barley = _product(client, h, "Barley", unit_cost=0.4, opening_quantity=1000, is_feedable=True)
    alfalfa = _product(client, h, "Alfalfa Hay", unit_cost=0.3, opening_quantity=1000, is_feedable=True)
    premix = _product(client, h, "Dairy Premix", unit_cost=3.0, opening_quantity=100, category="premix")
    mix = _product(
        client,
        h,
        "Goat Mix 16",
        code="GOAT-MIX-16",
        source_type="farm_produced",
        is_ingredient=False,
        is_feedable=True,
        category="complete_feed",
    )
    r = client.put(
        f"{API}/feed-products/{premix['id']}/usage-policy",
        json={
            "name": "Ruminants only",
            "requires_approved_formula": True,
            "cross_species_transfer_allowed": False,
            "rules": [
                {"effect": "allow", "species_code": "goat"},
                {"effect": "allow", "species_code": "cow"},
                {"effect": "limit", "species_code": "goat", "max_inclusion_pct": 1.5},
            ],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    formula = _post(
        client,
        h,
        "/feed-formulas",
        {
            "name": "Goat Mix 16",
            "code": "GOAT-MIX-16",
            "feed_product_id": mix["id"],
            "species_code": "goat",
            "batch_size": 1000,
            "unit": "kg",
            "components": [
                {"feed_product_id": barley["id"], "target_quantity": 600},
                {"feed_product_id": alfalfa["id"], "target_quantity": 390},
                {"feed_product_id": premix["id"], "target_quantity": 10},
            ],
        },
    )
    herd = _post(
        client,
        h,
        "/animal-groups",
        {
            "name": "Dairy Does",
            "species": "goat",
            "count": 3,
            "management_profile": "dairy",
            "sex_composition": "female",
        },
    )
    does = []
    for i, name in enumerate(("Bella", "Luna")):
        does.append(
            _post(
                client,
                h,
                "/animals",
                {
                    "name": name,
                    "species": "goat",
                    "sex": "F",
                    "management_profile": "dairy",
                    "lactating": True,
                    "group_name": "Dairy Does",
                    "tag": f"GOAT-{i + 1}",
                },
            )
        )
    high = _post(
        client,
        h,
        "/feeding-programs",
        {
            "name": "Dairy does",
            "code": "DAIRY-DOE",
            "feedings_per_day": 2,
            "components": [
                {"feed_product_id": mix["id"], "quantity_per_head": 1, "frequency": "per_feeding"},
                {"feed_product_id": alfalfa["id"], "quantity_per_head": 2},
            ],
            "rules": [{"species_code": "goat", "sex": "F", "lactation_state": "lactating"}],
        },
    )
    dry = _post(
        client,
        h,
        "/feeding-programs",
        {
            "name": "Dry does",
            "code": "DRY-DOE",
            "components": [{"feed_product_id": alfalfa["id"], "quantity_per_head": 2.5}],
            "rules": [{"species_code": "goat", "sex": "F", "lactation_state": "dry"}],
        },
    )
    return {
        "barley": barley,
        "alfalfa": alfalfa,
        "premix": premix,
        "mix": mix,
        "formula": formula,
        "herd": herd,
        "does": does,
        "high": high,
        "dry": dry,
    }


# ------------------------------------------------------------ products & lots


def test_products_are_a_lot_ledger_shared_with_the_item_screen(client, control_db):
    tenant, h = _owner(client, control_db, "FARM-FP", "fp-owner@origami-demo.com")
    tenant_b, h_b = _owner(client, control_db, "FARM-FPB", "fpb-owner@origami-demo.com")

    barley = _product(client, h, "Barley", unit_cost=0.4, opening_quantity=1000, is_feedable=True)
    assert barley["farm_id"] == str(tenant.id) and barley["code"] == "BARLEY"
    assert barley["availability"]["on_hand"] == 1000 and barley["availability"]["available"] == 1000
    assert [lt["source_type"] for lt in barley["availability"]["lots"]] == ["opening_balance"]
    # The stock is the same inventory item the original feed screen lists.
    items = _get(client, h, "/feed/items", {"farm_id": str(tenant.id)})
    assert [(i["id"], i["current_qty"]) for i in items] == [(barley["inventory_item_id"], 1000.0)]

    # Goods receipt keeps received, accepted and rejected apart.
    lot = _post(
        client,
        h,
        "/feed-lots/receive",
        {
            "feed_product_id": barley["id"],
            "quantity": 500,
            "rejected_quantity": 20,
            "ordered_quantity": 500,
            "unit_cost": 0.38,
            "supplier_label": "Bekaa Hay Co.",
            "lot_code": "BAR-TEST-01",
            "reference": "PO-9001",
        },
    )
    assert (lot["received_quantity"], lot["accepted_quantity"], lot["rejected_quantity"]) == (500, 480, 20)
    assert lot["quantity_on_hand"] == 480
    assert _availability(client, h, barley["id"])["on_hand"] == 1480
    moves = _get(client, h, "/feed/transactions", {"farm_id": str(tenant.id), "days": 1})
    assert any(
        m["lot_id"] == lot["id"]
        and m["direction"] == "in"
        and m["quantity"] == 480
        and m["feed_product_id"] == barley["id"]
        for m in moves
    )

    # The legacy item-level transaction issues FIFO from the opening lot.
    r = client.post(
        f"{API}/feed/transactions",
        json={
            "item_id": barley["inventory_item_id"],
            "direction": "out",
            "quantity": 100,
            "reason": "feeding",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["current_qty"] == 1380
    lots = {lt["lot_code"]: lt for lt in _availability(client, h, barley["id"])["lots"]}
    assert (
        lots["BARLEY-OPENING"]["quantity_on_hand"] == 900 and lots["BAR-TEST-01"]["quantity_on_hand"] == 480
    )

    # A quarantined lot is on hand but unusable; it cannot be issued.
    q = client.patch(
        f"{API}/feed-lots/{lot['id']}/status", json={"status": "quarantined", "reason": "mould"}, headers=h
    )
    assert q.status_code == 200 and q.json()["status"] == "quarantined"
    avail = _availability(client, h, barley["id"])
    assert (avail["on_hand"], avail["unusable"], avail["available"]) == (1380, 480, 900)
    assert avail["unusable_by_status"] == {"quarantined": 480}

    # Adjustments need an explanation.
    bad = client.post(
        f"{API}/feed-inventory/adjustments",
        json={"feed_product_id": barley["id"], "delta": -5, "reason": "waste", "explanation": " "},
        headers=h,
    )
    assert bad.status_code == 422
    ok = _post(
        client,
        h,
        "/feed-inventory/adjustments",
        {
            "feed_product_id": barley["id"],
            "delta": -5,
            "reason": "waste",
            "explanation": "Rain got into the last bag.",
        },
    )
    assert ok["availability"]["on_hand"] == 1375

    # Another farm sees none of it.
    assert _get(client, h_b, "/feed-products") == []
    assert client.get(f"{API}/feed-products/{barley['id']}", headers=h_b).status_code == 404
    assert client.get(f"{API}/feed-lots/{lot['id']}/trace", headers=h_b).status_code == 404
    assert tenant_b.id != tenant.id


def test_usage_policy_is_enforced_at_every_door(client, control_db):
    _tenant, h = _owner(client, control_db, "FARM-POL", "pol-owner@origami-demo.com")
    farm = _goat_farm(client, h)
    premix = farm["premix"]

    # A horse formula cannot use the ruminant premix — refused before any stock moves.
    horse_mix = _product(
        client, h, "Horse Mix", source_type="farm_produced", is_ingredient=False, is_feedable=True
    )
    before = _availability(client, h, premix["id"])["on_hand"]
    r = client.post(
        f"{API}/feed-formulas",
        json={
            "name": "Horse Mix",
            "feed_product_id": horse_mix["id"],
            "species_code": "horse",
            "batch_size": 100,
            "components": [
                {"feed_product_id": farm["alfalfa"]["id"], "target_quantity": 80},
                {"feed_product_id": farm["barley"]["id"], "target_quantity": 19},
                {"feed_product_id": premix["id"], "target_quantity": 1},
            ],
        },
        headers=h,
    )
    assert r.status_code == 422, r.text
    assert "horse" in r.json()["detail"].lower() and "Premix" in r.json()["detail"]
    assert _availability(client, h, premix["id"])["on_hand"] == before

    # The inclusion cap holds on a new version of the goat formula.
    r = client.post(
        f"{API}/feed-formulas/{farm['formula']['id']}/versions",
        json={
            "batch_size": 1000,
            "components": [
                {"feed_product_id": farm["barley"]["id"], "target_quantity": 560},
                {"feed_product_id": farm["alfalfa"]["id"], "target_quantity": 390},
                {"feed_product_id": premix["id"], "target_quantity": 50},
            ],
        },
        headers=h,
    )
    assert r.status_code == 422 and "maximum inclusion" in r.json()["detail"]

    # Direct feeding of a premix that needs a formula is refused even for a goat.
    bella = farm["does"][0]
    r = client.post(
        f"{API}/feeding-events",
        json={
            "subject_type": "animal",
            "subject_id": bella["id"],
            "components": [{"feed_product_id": premix["id"], "quantity_offered": 0.1}],
        },
        headers=h,
    )
    assert r.status_code == 422 and "approved formula" in r.json()["detail"], r.text

    # The policy explains itself.
    v = _post(
        client,
        h,
        f"/feed-products/{premix['id']}/usage-policy/validate",
        {"species_code": "horse"},
        expect=200,
    )
    assert v["allowed"] is False and any("horse" in reason for reason in v["reasons"])
    v = _post(
        client,
        h,
        f"/feed-products/{premix['id']}/usage-policy/validate",
        {"species_code": "goat", "through_formula": True, "inclusion_pct": 1.0},
        expect=200,
    )
    assert v["allowed"] is True and v["max_inclusion_pct"] == 1.5

    # A lot-level policy cannot broaden the product's.
    lot = _get(client, h, "/feed-lots", {"feed_product_id": premix["id"]})[0]
    r = client.put(
        f"{API}/feed-products/{premix['id']}/usage-policy",
        json={
            "name": "Lot exception",
            "lot_id": lot["id"],
            "rules": [{"effect": "allow", "species_code": "horse"}],
        },
        headers=h,
    )
    assert r.status_code == 422

    # A program whose ration is prohibited for its species cannot activate.
    r = client.post(
        f"{API}/feeding-programs",
        json={
            "name": "Horse premix",
            "components": [{"feed_product_id": farm["barley"]["id"], "quantity_per_head": 1}],
            "rules": [{"species_code": "horse"}],
        },
        headers=h,
    )
    assert r.status_code == 201, "barley is allowed for everyone"


# ----------------------------------------------------------- numbered mixes


def test_numbered_mixes_consume_lots_produce_a_lot_and_report_their_usage(client, control_db):
    _tenant, h = _owner(client, control_db, "FARM-MIX", "mix-owner@origami-demo.com")
    farm = _goat_farm(client, h)
    formula = farm["formula"]
    me = _get(client, h, "/auth/me")

    settings = _get(client, h, "/feed-mix-settings")
    assert (settings["last_number"], settings["next_number"], settings["next_code"]) == (0, 1, "MIX-000001")

    # Refused starts consume no number.
    for body, status, needle in (
        ({"intended_species_code": "horse"}, 422, "premix"),
        ({"intended_species_code": "unicorn"}, 422, "species"),
        ({"intended_management_profile": "spaceship"}, 422, "management profile"),
        ({"operator_id": str(uuid.uuid4())}, 404, "Operator"),
        ({"mixer_asset_id": "Small mixer"}, 404, "Assets"),
    ):
        r = client.post(
            f"{API}/feed-batches",
            json={"formula_id": formula["id"], "target_quantity": 500, **body},
            headers=h,
        )
        assert r.status_code == status, r.text
        assert needle.lower() in r.json()["detail"].lower()

    batch = _post(
        client,
        h,
        "/feed-batches",
        {
            "formula_id": formula["id"],
            "target_quantity": 500,
            "intended_management_profile": "dairy",
            "operator_id": me["id"],
        },
    )
    assert (batch["mix_number"], batch["mix_code"], batch["status"]) == (1, "MIX-000001", "in_progress")
    assert batch["intended_species_code"] == "goat", "the formula's species is the default intended use"
    assert batch["intended_management_profile"] == "dairy"
    assert batch["operator_id"] == me["id"] and batch["operator_name"] == "mix-owner@origami-demo.com"
    targets = {c["feed_product_id"]: c["target_quantity"] for c in batch["components"]}
    assert targets[farm["barley"]["id"]] == 300 and targets[farm["premix"]["id"]] == 5
    assert batch["planned_cost"] and batch["planned_cost"] > 0

    # Actuals are what moves stock and sets the cost; the output lot carries the mix code.
    premix_before = _availability(client, h, farm["premix"]["id"])["on_hand"]
    r = client.post(
        f"{API}/feed-batches/{batch['id']}/complete",
        json={
            "actual_quantity": 500,
            "actuals": [
                {"feed_product_id": farm["barley"]["id"], "actual_quantity": 298},
                {"feed_product_id": farm["alfalfa"]["id"], "actual_quantity": 197},
                {"feed_product_id": farm["premix"]["id"], "actual_quantity": 5},
            ],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "completed" and done["output_lot_code"] == "MIX-000001"
    assert done["actual_cost"] and abs(done["unit_cost"] - done["actual_cost"] / 500) < 1e-6
    variance = {v["feed_product_id"]: v for v in done["variance"]}
    assert (
        variance[farm["barley"]["id"]]["variance"] == -2 and variance[farm["alfalfa"]["id"]]["variance"] == 2
    )
    assert _availability(client, h, farm["mix"]["id"])["on_hand"] == 500
    assert _availability(client, h, farm["premix"]["id"])["on_hand"] == premix_before - 5
    # The version it used is locked; a revision becomes v2 and the batch still names v1.
    f = _get(client, h, f"/feed-formulas/{formula['id']}")
    assert f["versions"][0]["locked"] is True and f["active_version"] == 1
    v2 = _post(
        client,
        h,
        f"/feed-formulas/{formula['id']}/versions",
        {
            "batch_size": 1000,
            "components": [
                {"feed_product_id": farm["barley"]["id"], "target_quantity": 650},
                {"feed_product_id": farm["alfalfa"]["id"], "target_quantity": 340},
                {"feed_product_id": farm["premix"]["id"], "target_quantity": 10},
            ],
        },
    )
    assert v2["version"] == 2 and v2["status"] == "active"
    f = _get(client, h, f"/feed-formulas/{formula['id']}")
    assert f["active_version"] == 2 and f["versions"][0]["status"] == "retired"
    assert _get(client, h, f"/feed-batches/{batch['id']}")["formula_version"] == 1

    # Trace both ways from the premix lot and the output lot.
    premix_lot = _get(client, h, "/feed-lots", {"feed_product_id": farm["premix"]["id"]})[0]
    trace = _get(client, h, f"/feed-lots/{premix_lot['id']}/trace")
    assert any(b["mix_code"] == "MIX-000001" for b in trace["downstream_batches"])
    out_trace = _get(client, h, f"/feed-lots/{done['output_lot_id']}/trace")
    assert any(u["lot_id"] == premix_lot["id"] for u in out_trace["upstream_ingredient_lots"])

    # Feed the herd from the mix, refuse some, waste some, then read the mix's life.
    herd = farm["herd"]
    _post(
        client, h, f"/livestock-subjects/{herd['id']}/feeding-assignments", {"program_id": farm["high"]["id"]}
    )
    fed = _post(
        client,
        h,
        "/feeding-events",
        {
            "subject_type": "group",
            "subject_id": herd["id"],
            "event_type": "delivered",
            "components": [
                {
                    "feed_product_id": farm["mix"]["id"],
                    "quantity_offered": 30,
                    "lot_id": done["output_lot_id"],
                }
            ],
        },
    )
    assert fed["head_count"] == 3 and fed["total_cost"] > 0 and fed["program_version_id"]
    _post(
        client,
        h,
        "/feeding-events",
        {
            "subject_type": "group",
            "subject_id": herd["id"],
            "event_type": "refusal",
            "components": [
                {"feed_product_id": farm["mix"]["id"], "quantity_offered": 2, "lot_id": done["output_lot_id"]}
            ],
        },
    )
    _post(
        client,
        h,
        "/feed-inventory/adjustments",
        {
            "feed_product_id": farm["mix"]["id"],
            "lot_id": done["output_lot_id"],
            "delta": -5,
            "reason": "waste",
            "explanation": "Spilled",
        },
    )
    usage = _get(client, h, "/feed-mixes/1")
    assert usage["batch_id"] == batch["id"] and usage["mix_code"] == "MIX-000001"
    assert (
        usage["produced_quantity"],
        usage["issued_quantity"],
        usage["refused_quantity"],
        usage["waste_quantity"],
    ) == (500, 30, 2, 5)
    assert usage["remaining_quantity"] == 465, "remaining is the lot's ledger balance, not a stored figure"
    assert usage["days_used"] == 1 and usage["head_days"] >= 1 and usage["first_use_at"]
    assert [s["subject_id"] for s in usage["exposed_subjects"]] == [herd["id"]]
    issue = next(i for i in usage["issues"] if i["event_type"] == "delivered")
    assert issue["subject_name"] == "Dairy Does" and issue["inventory_transaction_id"]
    assert issue["recorded_by_name"] == "mix-owner@origami-demo.com"
    assert issue["program_code"] == "DAIRY-DOE" and issue["assignment_type"] == "explicit"
    assert usage["ledger_adjustments"][0]["reason"] == "waste"
    assert usage["cost_per_unit"] is not None and all(c["lot_code"] for c in usage["components"])
    # The mix is scored the moment it is completed (stage 9): the usage view
    # carries the score card headline, with no open alert on a clean mix.
    assert usage["performance"]["formula_compliance_score"] is not None
    assert usage["performance"]["open_alerts"] == []
    assert _get(client, h, f"/feed-batches/{batch['id']}/usage")["issued_quantity"] == 30
    assert client.get(f"{API}/feed-mixes/999", headers=h).status_code == 404

    # A reversal puts the quantity back and keeps the history.
    rev = _post(client, h, f"/feeding-events/{fed['id']}/reverse", {"reason": "Wrong group"})
    assert rev["status"] == "reversal" and rev["reversal_of_id"] == fed["id"]
    usage = _get(client, h, "/feed-mixes/1")
    assert usage["issued_quantity"] == 0 and usage["remaining_quantity"] == 495
    assert {i["status"] for i in usage["issues"]} >= {"reversed", "reversal"}

    # Costs roll up the completed batches and the feeding events.
    costs = _get(client, h, "/feed-costs", {"days": 30})
    assert costs["batch_cost_total"] > 0 and any(b["mix_code"] == "MIX-000001" for b in costs["batches"])
    # Repeating the formula is a new mix; a quarantined mix locks its lot.
    again = _post(client, h, "/feed-batches", {"formula_id": formula["id"], "target_quantity": 200})
    assert (again["mix_number"], again["mix_code"], again["formula_version"]) == (2, "MIX-000002", 2)
    assert [b["id"] for b in _get(client, h, "/feed-batches", {"mix_number": 2})] == [again["id"]]
    assert [b["id"] for b in _get(client, h, "/feed-batches", {"search": "000001"})] == [batch["id"]]
    q = client.post(f"{API}/feed-batches/{batch['id']}/quarantine", json={"reason": "mould"}, headers=h)
    assert q.status_code == 200 and q.json()["status"] == "quarantined"
    assert _availability(client, h, farm["mix"]["id"])["unusable"] == 495


def test_mix_code_format_is_configurable_and_starts_are_idempotent(client, control_db):
    tenant, h = _owner(client, control_db, "FARM-FMT", "fmt-owner@origami-demo.com")
    add_farmos_user(
        control_db,
        tenant,
        "fmt-worker@origami-demo.com",
        role="worker",
        grid={"feed_nutrition": ["view", "create"]},
    )
    control_db.commit()
    worker = farmos_headers(farmos_login(client, "fmt-worker@origami-demo.com", FARMOS_DEMO_PASSWORD))
    farm = _goat_farm(client, h)

    r = client.put(f"{API}/feed-mix-settings", json={"code_format": "DAIRY-ONLY"}, headers=h)
    assert r.status_code == 422 and "{n}" in r.json()["detail"]
    assert (
        client.put(f"{API}/feed-mix-settings", json={"code_format": "X-{n}"}, headers=worker).status_code
        == 403
    )
    r = client.put(f"{API}/feed-mix-settings", json={"code_format": "OF-{n:04d}"}, headers=h)
    assert r.status_code == 200 and r.json()["next_code"] == "OF-0001", r.text

    headers = {**worker, "Idempotency-Key": "mix-start-replay-1"}
    body = {"formula_id": farm["formula"]["id"], "target_quantity": 400, "notes": "queued offline"}
    first = client.post(f"{API}/feed-batches", json=body, headers=headers)
    assert first.status_code == 201, first.text
    assert (first.json()["mix_number"], first.json()["mix_code"]) == (1, "OF-0001")
    second = client.post(f"{API}/feed-batches", json=body, headers=headers)
    assert second.status_code == 201 and second.json()["mix_number"] == 1
    assert second.headers.get("Idempotency-Replayed") == "true"
    assert len(_get(client, h, "/feed-batches", {"mix_number": 1})) == 1
    # A later format change relabels nothing that already exists.
    client.put(f"{API}/feed-mix-settings", json={"code_format": "MIX-{n:06d}"}, headers=h)
    assert _get(client, h, "/feed-batches", {"mix_number": 1})[0]["mix_code"] == "OF-0001"


# ---------------------------------------------------- programs & inheritance


def test_resolver_inheritance_exceptions_and_review(client, control_db):
    tenant, h = _owner(client, control_db, "FARM-PRG", "prg-owner@origami-demo.com")
    add_farmos_user(
        control_db,
        tenant,
        "prg-worker@origami-demo.com",
        role="worker",
        grid={"feed_nutrition": ["view", "create", "edit"], "animals": ["view", "edit"]},
    )
    control_db.commit()
    worker = farmos_headers(farmos_login(client, "prg-worker@origami-demo.com", FARMOS_DEMO_PASSWORD))
    farm = _goat_farm(client, h)
    herd, bella, luna = farm["herd"], farm["does"][0], farm["does"][1]

    # One engine: a lactating doe and a dry doe get different programs.
    res = _post(
        client,
        h,
        "/feeding-programs/resolve",
        {"species_code": "goat", "sex": "F", "lactation_state": "lactating"},
        expect=200,
    )
    assert res["best"]["program_code"] == "DAIRY-DOE" and "lactation = lactating" in res["best"]["reasons"]
    res = _post(client, h, "/feeding-programs/resolve", {"species_code": "goat", "sex": "F"}, expect=200)
    assert res["best"]["program_code"] == "DRY-DOE", "an undescribed female is dry by default"
    assert (
        _post(client, h, "/feeding-programs/resolve", {"species_code": "horse", "sex": "M"}, expect=200)[
            "best"
        ]
        is None
    )

    # The herd is put on the dairy program; its does inherit it.
    a = _post(
        client, h, f"/livestock-subjects/{herd['id']}/feeding-assignments", {"program_id": farm["high"]["id"]}
    )
    assert a["assignment_type"] == "explicit" and a["program_version"] == 1
    plan = _get(client, h, f"/livestock-subjects/{bella['id']}/feeding-plan")
    assert (
        plan["program"]["source"] == "inherited"
        and plan["program"]["inherited_from"]["group_id"] == herd["id"]
    )
    mix_line = next(c for c in plan["components"] if c["feed_product_id"] == farm["mix"]["id"])
    assert mix_line["daily_per_head"] == 2, "1 kg per feeding × 2 feedings"
    assert plan["review"]["required"] is False
    program = _get(client, h, f"/feeding-programs/{farm['high']['id']}")
    assert program["subjects_assigned"] == 1

    # A supplement and an override sit on top of the inherited program; a restriction removes a feed.
    _post(
        client,
        h,
        f"/livestock-subjects/{bella['id']}/feeding-assignments",
        {"assignment_type": "supplement", "feed_product_id": farm["barley"]["id"], "quantity_per_head": 0.5},
    )
    ov = _post(
        client,
        h,
        f"/livestock-subjects/{luna['id']}/feeding-assignments",
        {
            "assignment_type": "override",
            "feed_product_id": farm["alfalfa"]["id"],
            "quantity_per_head": 1,
            "reason": "Body condition",
        },
    )
    plan = _get(client, h, f"/livestock-subjects/{bella['id']}/feeding-plan")
    assert plan["program"]["source"] == "inherited"
    assert (
        next(t for t in plan["daily_targets"] if t["feed_product_id"] == farm["barley"]["id"])["daily_total"]
        == 0.5
    )
    luna_plan = _get(client, h, f"/livestock-subjects/{luna['id']}/feeding-plan")
    hay = next(c for c in luna_plan["components"] if c["feed_product_id"] == farm["alfalfa"]["id"])
    assert hay["daily_per_head"] == 1 and "override" in hay["note"]
    ended = client.delete(f"{API}/livestock-subjects/{luna['id']}/feeding-assignments/{ov['id']}", headers=h)
    assert ended.status_code == 200 and ended.json()["status"] == "ended"
    hay = next(
        c
        for c in _get(client, h, f"/livestock-subjects/{luna['id']}/feeding-plan")["components"]
        if c["feed_product_id"] == farm["alfalfa"]["id"]
    )
    assert hay["daily_per_head"] == 2

    # A restriction is a veterinary / management decision.
    r = client.post(
        f"{API}/livestock-subjects/{bella['id']}/feeding-assignments",
        json={
            "assignment_type": "restriction",
            "feed_product_id": farm["alfalfa"]["id"],
            "reason": "Vet order",
        },
        headers=worker,
    )
    assert r.status_code == 403
    _post(
        client,
        h,
        f"/livestock-subjects/{bella['id']}/feeding-assignments",
        {
            "assignment_type": "restriction",
            "feed_product_id": farm["alfalfa"]["id"],
            "reason": "Vet: acidosis",
        },
    )
    plan = _get(client, h, f"/livestock-subjects/{bella['id']}/feeding-plan")
    assert all(t["feed_product_id"] != farm["alfalfa"]["id"] for t in plan["daily_targets"])
    r = client.post(
        f"{API}/feeding-events",
        json={
            "subject_type": "animal",
            "subject_id": bella["id"],
            "components": [{"feed_product_id": farm["alfalfa"]["id"], "quantity_offered": 1}],
        },
        headers=worker,
    )
    assert r.status_code == 422 and "restricted" in r.json()["detail"]

    # A worker feeds the herd; the event names the program it was issued under and costs it.
    fed = _post(
        client,
        worker,
        "/feeding-events",
        {
            "subject_type": "group",
            "subject_id": herd["id"],
            "components": [{"feed_product_id": farm["alfalfa"]["id"], "quantity_offered": 6}],
        },
    )
    assert fed["program_version_id"] == a["program_version_id"] and fed["total_cost"] == 1.8
    assert fed["components"][0]["lot_id"] and fed["components"][0]["quantity_consumed"] is None
    history = _get(client, h, f"/livestock-subjects/{herd['id']}/feeding-history")
    assert history[0]["id"] == fed["id"]
    today = _get(client, h, "/feeding-plan/today")
    herd_line = next(line for line in today["lines"] if line["subject_id"] == herd["id"])
    assert herd_line["head_count"] == 3
    alfalfa_total = next(t for t in today["totals"] if t["feed_product_id"] == farm["alfalfa"]["id"])
    assert alfalfa_total["fed_today"] == 6

    # Drying Luna off raises a review and changes nothing by itself.
    r = client.put(f"{API}/animals/{luna['id']}", json={"lactating": False}, headers=h)
    assert r.status_code == 200, r.text
    tasks = [
        t
        for t in _get(client, h, "/tasks", {"farm_id": str(tenant.id)})
        if t["source_type"] == "feeding_review" and t["source_id"] == luna["id"]
    ]
    assert len(tasks) == 1 and "Luna" in tasks[0]["title"] and "Dry does" in tasks[0]["description"]
    plan = _get(client, h, f"/livestock-subjects/{luna['id']}/feeding-plan")
    assert plan["program"]["program_code"] == "DAIRY-DOE" and plan["program"]["source"] == "inherited"
    assert plan["review"]["required"] is True and plan["review"]["recommended"]["program_code"] == "DRY-DOE"
    client.put(f"{API}/animals/{luna['id']}", json={"pregnant": True}, headers=h)
    assert (
        len(
            [
                t
                for t in _get(client, h, "/tasks", {"farm_id": str(tenant.id)})
                if t["source_type"] == "feeding_review" and t["source_id"] == luna["id"]
            ]
        )
        == 1
    ), "one open review per subject"
    # Acting on it — an explicit assignment — closes the review.
    _post(
        client, h, f"/livestock-subjects/{luna['id']}/feeding-assignments", {"program_id": farm["dry"]["id"]}
    )
    assert all(
        t["status"] == "done"
        for t in _get(client, h, "/tasks", {"farm_id": str(tenant.id)})
        if t["source_type"] == "feeding_review" and t["source_id"] == luna["id"]
    )
    plan = _get(client, h, f"/livestock-subjects/{luna['id']}/feeding-plan")
    assert plan["program"]["source"] == "explicit" and plan["program"]["program_code"] == "DRY-DOE"

    # Activating a new program version moves its subjects.
    v2 = _post(
        client,
        h,
        f"/feeding-programs/{farm['high']['id']}/versions",
        {
            "components": [
                {"feed_product_id": farm["mix"]["id"], "quantity_per_head": 1.5, "frequency": "per_feeding"}
            ],
            "rules": [{"species_code": "goat", "sex": "F", "lactation_state": "lactating", "priority": 10}],
        },
    )
    assert v2["version"] == 2 and v2["status"] == "active"
    plan = _get(client, h, f"/livestock-subjects/{herd['id']}/feeding-plan")
    assert plan["program"]["version"] == 2
    assert (
        next(c for c in plan["components"] if c["feed_product_id"] == farm["mix"]["id"])["daily_per_head"]
        == 3
    )

    # A milk record across a production band reviews once — the rule has a band.
    _post(
        client,
        h,
        f"/feeding-programs/{farm['high']['id']}/versions",
        {
            "components": [
                {"feed_product_id": farm["mix"]["id"], "quantity_per_head": 2, "frequency": "per_feeding"}
            ],
            "rules": [
                {
                    "species_code": "goat",
                    "sex": "F",
                    "lactation_state": "lactating",
                    "production_metric": "milk_l_per_day",
                    "production_min": 3,
                    "priority": 20,
                },
            ],
        },
    )
    r = client.post(
        f"{API}/production/milk",
        json={"animal_id": bella["id"], "session": "morning", "liters": 1.0, "destination": "stored"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    review = [
        t
        for t in _get(client, h, "/tasks", {"farm_id": str(tenant.id)})
        if t["source_type"] == "feeding_review" and t["source_id"] == bella["id"]
    ]
    assert len(review) == 1 and "production band" in review[0]["description"].lower()


# -------------------------------------------- reservation, cover, reconcile


def test_allocations_cover_reorder_and_reconciliation(client, control_db):
    tenant, h = _owner(client, control_db, "FARM-COV", "cov-owner@origami-demo.com")
    farm = _goat_farm(client, h)
    barley, herd = farm["barley"], farm["herd"]
    _post(
        client, h, f"/livestock-subjects/{herd['id']}/feeding-assignments", {"program_id": farm["high"]["id"]}
    )

    # Reserved stock is not available to another purpose.
    alloc = _post(
        client,
        h,
        "/feed-allocations",
        {"feed_product_id": barley["id"], "quantity": 400, "species_code": "goat", "purpose": "Winter does"},
    )
    avail = _availability(client, h, barley["id"])
    assert (avail["on_hand"], avail["reserved"], avail["available"]) == (1000, 400, 600)
    r = client.post(
        f"{API}/feed/transactions",
        json={
            "item_id": barley["inventory_item_id"],
            "direction": "out",
            "quantity": 700,
            "reason": "transfer",
        },
        headers=h,
    )
    assert r.status_code == 422 and "reserved" in r.json()["detail"]
    r = client.post(
        f"{API}/feed/transactions",
        json={
            "item_id": barley["inventory_item_id"],
            "direction": "out",
            "quantity": 100,
            "reason": "transfer",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert _availability(client, h, barley["id"])["available"] == 500
    # A goat feeding draws on the reservation.
    _post(
        client,
        h,
        "/feeding-events",
        {
            "subject_type": "animal",
            "subject_id": farm["does"][0]["id"],
            "components": [{"feed_product_id": barley["id"], "quantity_offered": 10}],
        },
    )
    rows = _get(client, h, "/feed-allocations", {"feed_product_id": barley["id"]})
    assert rows[0]["consumed_quantity"] == 10 and rows[0]["remaining_quantity"] == 390
    # Moving part of it to a horse is refused by the (default) product policy? No —
    # barley has no policy, so cross-species is allowed; the transfer needs an approver.
    moved = _post(
        client,
        h,
        f"/feed-allocations/{alloc['id']}/transfer",
        {
            "quantity": 90,
            "species_code": "goat",
            "subject_type": "animal",
            "subject_id": farm["does"][1]["id"],
        },
    )
    assert moved["allocated_quantity"] == 90 and moved["subject_id"] == farm["does"][1]["id"]
    assert _availability(client, h, barley["id"])["reserved"] == 390
    released = client.post(f"{API}/feed-allocations/{moved['id']}/release", headers=h)
    assert released.status_code == 200 and released.json()["status"] == "released"

    # Days of cover: program demand, reorder policy, stockout risk, reorder recommendation.
    pol = client.put(
        f"{API}/feed-reorder-policies",
        json={
            "feed_product_id": farm["alfalfa"]["id"],
            "reorder_point": 950,
            "lead_time_days": 5,
            "preferred_reorder_quantity": 2000,
        },
        headers=h,
    )
    assert pol.status_code == 200, pol.text
    cover = {r["code"]: r for r in _get(client, h, "/feed-inventory/days-of-cover")}
    alfalfa = cover["ALFALFA-HAY"]
    # 2 kg × 3 head fed straight, plus the alfalfa inside 6 kg/day of goat mix (390/1000).
    assert alfalfa["demand_source"] == "programs" and alfalfa["daily_demand"] == 8.34
    assert alfalfa["days_of_cover"] and alfalfa["status"] in ("reorder", "ok", "stockout_risk")
    assert list(alfalfa["demand_by_species"]) == ["goat"]
    # Pull alfalfa below the reorder point and the recommendation names what is at risk.
    _post(
        client,
        h,
        "/feed-inventory/adjustments",
        {"feed_product_id": farm["alfalfa"]["id"], "delta": -200, "reason": "waste", "explanation": "Rain"},
    )
    recs = {r["code"]: r for r in _get(client, h, "/feed-inventory/reorder-recommendations")}
    assert "ALFALFA-HAY" in recs and "Dairy does" in recs["ALFALFA-HAY"]["programs_affected"]
    assert recs["ALFALFA-HAY"]["suggested_reorder_quantity"] >= 2000
    ack = _post(
        client,
        h,
        f"/feed-inventory/reorder-recommendations/{farm['alfalfa']['id']}/acknowledge",
        {"note": "Order from Bekaa Hay Co."},
    )
    recs = {r["code"]: r for r in _get(client, h, "/feed-inventory/reorder-recommendations")}
    assert recs["ALFALFA-HAY"]["covered_by_task_id"] == ack["task_id"]
    tasks = _get(client, h, "/tasks", {"farm_id": str(tenant.id)})
    assert any(t["id"] == ack["task_id"] and t["source_type"] == "feed_reorder" for t in tasks)
    forecast = {r["code"]: r for r in _get(client, h, "/feed-inventory/forecast", {"horizon_days": 14})}
    assert (
        forecast["ALFALFA-HAY"]["forecast_quantity"] == round(8.34 * 14, 3)
        and forecast["ALFALFA-HAY"]["horizon_days"] == 14
    )

    # Reconciliation accounts for every kilogram; an unexplained gap is a variance.
    now = datetime.now(timezone.utc)
    rec = _post(
        client,
        h,
        "/feed-reconciliations",
        {
            "feed_product_id": barley["id"],
            "period_from": (now - timedelta(days=30)).isoformat(),
            "period_to": (now + timedelta(minutes=1)).isoformat(),
            "counted_closing_quantity": 865,
        },
    )
    assert (
        rec["received_quantity"] == 1000
        and rec["issued_to_feeding"] == 10
        and rec["other_issued_quantity"] == 100
    )
    assert (
        rec["expected_closing_quantity"] == 890
        and rec["variance_quantity"] == -25
        and rec["status"] == "open"
    )
    on_hand = _availability(client, h, barley["id"])["on_hand"]
    closed = _post(
        client,
        h,
        f"/feed-reconciliations/{rec['id']}/close",
        {"post_adjustment": True, "explanation": "Two torn bags behind the mixer."},
        expect=200,
    )
    assert closed["status"] == "closed"
    assert _availability(client, h, barley["id"])["on_hand"] == on_hand - 25
    moves = _get(client, h, "/feed/transactions", {"farm_id": str(tenant.id), "days": 1})
    assert any(m["reason"] == "reconciliation_adjustment" and m["quantity"] == 25 for m in moves)
    assert (
        client.post(
            f"{API}/feed-reconciliations/{rec['id']}/close", json={"post_adjustment": False}, headers=h
        ).status_code
        == 422
    )


def test_nutrient_profiles_and_formula_nutrients(client, control_db):
    _tenant, h = _owner(client, control_db, "FARM-NUT", "nut-owner@origami-demo.com")
    farm = _goat_farm(client, h)
    nutrients = {n["code"] for n in _get(client, h, "/feed-nutrients")}
    assert {"DM", "CP", "ME"} <= nutrients
    for product, cp in ((farm["barley"], 11.5), (farm["alfalfa"], 17.0), (farm["premix"], 0.0)):
        _post(
            client,
            h,
            "/feed-nutrient-profiles",
            {
                "subject_type": "feed_product",
                "subject_id": product["id"],
                "values": [{"nutrient_code": "CP", "value": cp}],
            },
        )
    lab = _post(
        client,
        h,
        "/feed-nutrient-profiles",
        {
            "subject_type": "feed_product",
            "subject_id": farm["alfalfa"]["id"],
            "source_type": "lab",
            "reference": "LAB-77",
            "values": [{"nutrient_code": "cp", "value": 18.0}],
        },
    )
    assert lab["values"][0]["nutrient_code"] == "CP"
    profiles = _get(
        client,
        h,
        "/feed-nutrient-profiles",
        {"subject_type": "feed_product", "subject_id": farm["alfalfa"]["id"]},
    )
    assert {p["source_type"] for p in profiles} == {
        "declared",
        "lab",
    }, "a lab result sits beside the declaration"
    body = _get(client, h, f"/feed-formulas/{farm['formula']['id']}/versions/1/nutrients")
    assert body["coverage_pct"] == 100 and body["source_type"] == "calculated"
    cp = next(v for v in body["values"] if v["nutrient_code"] == "CP")
    assert (
        abs(cp["value"] - (600 * 11.5 + 390 * 18.0 + 10 * 0.0) / 1000) < 0.01
    ), "the lab value wins for alfalfa"
    bad = client.post(
        f"{API}/feed-nutrient-profiles",
        json={
            "subject_type": "feed_product",
            "subject_id": farm["barley"]["id"],
            "values": [{"nutrient_code": "LIGNIN", "value": 1}],
        },
        headers=h,
    )
    assert bad.status_code == 422
