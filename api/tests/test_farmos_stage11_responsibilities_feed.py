"""Stage 11 of the FarmOS tablet contract: work responsibilities, the
feed-room's one-step mix and standard feeds, lot quality at receipt, the
duplicate-entry guards of the record workflows and the approval levels
behind stock overrides. Ports the reference backend's
test_feed_mix_calendar_groups_responsibilities.py (responsibilities and
mix parts), test_registration_status_and_standard_feeds.py (standard
feeds) and test_duplicates_and_approvals.py (everything that is not the
animal record), plus the permission alignment of its PR 12. See
docs/FARMOS_API.md and app/farmos/duplicates.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.audit.models import AuditEvent
from app.farmos.duplicates import PREFIX
from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant

API = "/api/v1"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _tag() -> str:
    return f"T-{uuid.uuid4().hex[:6].upper()}"


def _microchip() -> list[dict]:
    return [{"identifier_type": "MICROCHIP", "identifier_value": uuid.uuid4().hex[:15]}]


def _login(client, email: str) -> dict:
    return farmos_headers(farmos_login(client, email, FARMOS_DEMO_PASSWORD))


def _post(client, h, path, body=None, expect=201):
    r = client.post(f"{API}{path}", json=body if body is not None else {}, headers=h)
    assert r.status_code == expect, f"{path}: {r.status_code} {r.text}"
    return r.json()


def _get(client, h, path, params=None):
    r = client.get(f"{API}{path}", params=params, headers=h)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
    return r.json()


def _is_possible_duplicate(r) -> bool:
    return r.status_code == 409 and r.json()["detail"].startswith(PREFIX)


def _confirmed(control_db, tenant_id) -> list[AuditEvent]:
    """The confirmed duplicates on this farm's audit trail, each naming the
    record it resembles."""
    return list(
        control_db.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == tenant_id, AuditEvent.action == "duplicate.confirmed"
            )
        )
    )


class Farm:
    """A farm built through the API: an owner, a worker without any grant,
    a cow in the dairy herd, and the feeds the scenarios draw on."""

    def __init__(self, client, control_db, prefix: str, *, worker_grid: dict | None = None):
        self.client = client
        self.control_db = control_db
        self.tenant = create_tenant(control_db, company_code=unique_code(prefix))
        self.owner, _ = add_farmos_user(
            control_db, self.tenant, f"owner-{prefix}@origami-demo.com", role="owner", display_name="Rami"
        )
        self.worker, self.worker_membership = add_farmos_user(
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

    def post(self, path, body=None, expect=201, h=None):
        return _post(self.client, h or self.h, path, body, expect)

    def get(self, path, params=None, h=None):
        return _get(self.client, h or self.h, path, params)

    def product(self, name: str, **fields) -> dict:
        return self.post("/feed-products", {"name": name, **fields})

    def animal(self, name: str, **fields) -> dict:
        """A dairy cow with the ear tag its species requires at registration."""
        body = {
            "name": name,
            "species": "cow",
            "sex": "F",
            "management_profile": "dairy",
            "tag": _tag(),
            **fields,
        }
        return self.post("/animals", body)

    def availability(self, product_id: str) -> dict:
        return self.get(f"/feed-products/{product_id}")["availability"]

    def refresh_worker(self) -> None:
        """A new token is not needed — permissions are read per request —
        but the headers are rebuilt for symmetry with a real re-login."""
        self.worker_h = _login(self.client, self.worker.email)


# ------------------------------------------------------------ feed: purchase, mix, feed
def test_grain_purchase_with_quality_then_a_mix_then_feeding(client, control_db):
    f = Farm(client, control_db, "S11-MIX")
    bella = f.animal("Bella", group_name="Dairy Herd")
    barley = f.product("Barley", unit_cost=0.36, opening_quantity=1000, is_feedable=True)
    corn = f.product("Corn grain", unit="kg", is_ingredient=True, is_feedable=False, category="concentrate")
    lot = client.post(
        f"{API}/feed-lots/receive",
        json={
            "feed_product_id": corn["id"],
            "quantity": 1000,
            "unit_cost": 0.31,
            "supplier_label": "Bekaa Grain Co.",
            "received_at": _iso(3),
            "quality_grade": "good",
            "moisture_pct": 13.5,
            "quality_notes": "Clean, no mould",
        },
        headers=f.h,
    )
    assert lot.status_code == 201, lot.text
    body = lot.json()
    assert (body["quality_grade"], body["moisture_pct"], body["quality_notes"]) == (
        "good",
        13.5,
        "Clean, no mould",
    )
    assert body["supplier_label"] == "Bekaa Grain Co."
    bad = client.post(
        f"{API}/feed-lots/receive",
        json={"feed_product_id": corn["id"], "quantity": 10, "quality_grade": "excellent"},
        headers=f.h,
    )
    assert bad.status_code == 422
    assert "quality_grade" in bad.json()["detail"]
    listed = f.get("/feed-lots", {"feed_product_id": corn["id"]})
    assert listed[0]["quality_grade"] == "good" and listed[0]["moisture_pct"] == 13.5

    # A one-step mix into a new farm feed: formula v1 is created from the mix.
    mix = client.post(
        f"{API}/feed-mixes",
        json={
            "new_product_name": "Cow concentrate",
            "produced_at": _iso(1),
            "components": [
                {"feed_product_id": corn["id"], "actual_quantity": 300},
                {"feed_product_id": barley["id"], "actual_quantity": 150},
            ],
        },
        headers=f.h,
    )
    assert mix.status_code == 201, mix.text
    m = mix.json()
    assert m["status"] == "completed" and m["mix_code"] and m["actual_quantity"] == 450
    assert m["mix_number"] == 1 and m["formula_version"] == 1 and m["output_lot_id"]
    used = {c["feed_product_id"]: c["actual_quantity"] for c in m["components"]}
    assert used[corn["id"]] == 300 and used[barley["id"]] == 150
    product_id = m["feed_product_id"]
    stock = f.get(f"/feed-products/{product_id}")
    assert stock["availability"]["on_hand"] == 450
    assert stock["source_type"] == "farm_produced" and stock["has_active_formula"] is True
    assert stock["is_feedable"] is True
    # The ledger, not a typed balance: the corn lot was drawn on.
    assert f.availability(corn["id"])["on_hand"] == 700
    formulas = f.get("/feed-formulas")
    assert [(x["product_name"], x["active_version"]) for x in formulas] == [("Cow concentrate", 1)]

    # The second mix of the same feed uses its formula; a left-out
    # ingredient is recorded as zero, not refused.
    again = client.post(
        f"{API}/feed-mixes",
        json={
            "feed_product_id": product_id,
            "components": [{"feed_product_id": corn["id"], "actual_quantity": 200}],
        },
        headers=f.h,
    )
    assert again.status_code == 201, again.text
    assert again.json()["mix_number"] == m["mix_number"] + 1
    zero = next(c for c in again.json()["components"] if c["feed_product_id"] == barley["id"])
    assert zero["actual_quantity"] == 0 and zero["lot_id"] is None
    assert f.get("/feed-mix-settings")["last_number"] == 2

    # FIFO: the oldest mix's output lot is fed first, and the feeding names the mix.
    fed = client.post(
        f"{API}/feeding-events",
        json={
            "subject_type": "animal",
            "subject_id": bella["id"],
            "event_type": "offered",
            "components": [{"feed_product_id": product_id, "quantity_offered": 8}],
        },
        headers=f.h,
    )
    assert fed.status_code == 201, fed.text
    assert fed.json()["components"][0]["batch_id"] == m["id"]
    usage = f.get(f"/feed-mixes/{m['mix_number']}")
    assert usage["issued_quantity"] == 8 and usage["remaining_quantity"] == 442
    # The farm's own concentrate now fills the feeding form's concentrate row.
    slots = {s["slot"]: s for s in f.get("/feed-products/standard-slots")}
    assert slots["concentrate"]["feed_product_id"] == product_id


def test_a_mix_cannot_make_a_bought_feed_or_itself(client, control_db):
    f = Farm(client, control_db, "S11-MIXNO")
    barley = f.product("Barley", opening_quantity=100, is_feedable=True)
    alfalfa = f.product("Alfalfa", opening_quantity=100, is_feedable=True)
    bought = client.post(
        f"{API}/feed-mixes",
        json={
            "feed_product_id": barley["id"],
            "components": [{"feed_product_id": alfalfa["id"], "actual_quantity": 10}],
        },
        headers=f.h,
    )
    assert bought.status_code == 422 and "bought-in" in bought.json()["detail"]
    empty = client.post(
        f"{API}/feed-mixes",
        json={
            "new_product_name": "X",
            "components": [{"feed_product_id": barley["id"], "actual_quantity": 0}],
        },
        headers=f.h,
    )
    assert empty.status_code == 422
    nameless = client.post(
        f"{API}/feed-mixes",
        json={"components": [{"feed_product_id": barley["id"], "actual_quantity": 5}]},
        headers=f.h,
    )
    assert nameless.status_code == 422 and "name a new one" in nameless.json()["detail"]
    mix = f.post(
        "/feed-mixes",
        {
            "new_product_name": "Heifer mix",
            "components": [{"feed_product_id": barley["id"], "actual_quantity": 10}],
        },
    )
    itself = client.post(
        f"{API}/feed-mixes",
        json={
            "feed_product_id": mix["feed_product_id"],
            "components": [{"feed_product_id": mix["feed_product_id"], "actual_quantity": 5}],
        },
        headers=f.h,
    )
    assert itself.status_code == 422 and "itself" in itself.json()["detail"]


# ------------------------------------------------------------ standard feeds
def test_standard_slots_match_the_farms_own_feeds(client, control_db):
    f = Farm(client, control_db, "S11-SLOTS")
    silage = f.product("Corn silage", code="CORN-SILAGE", category="forage", is_feedable=True)
    straw = f.product("Barn straw", category="forage", is_feedable=True)
    f.product("Barley", category="concentrate", is_feedable=True)
    concentrate = f.product(
        "Dairy Concentrate 18", category="complete_feed", is_ingredient=False, is_feedable=True
    )
    f.product("Dairy Premix", category="premix", is_feedable=False)  # not feedable: never a slot
    slots = {s["slot"]: s for s in f.get("/feed-products/standard-slots")}
    assert list(slots) == ["concentrate", "corn_silage", "wheat_straw"]
    assert slots["corn_silage"]["feed_product_id"] == silage["id"]
    assert slots["wheat_straw"]["feed_product_id"] == straw["id"]
    # A feed named for the slot wins over one that is only a concentrate by category (barley).
    assert slots["concentrate"]["feed_product_id"] == concentrate["id"]
    assert slots["concentrate"]["feed_product_name"] == "Dairy Concentrate 18"
    assert (slots["corn_silage"]["label_en"], slots["corn_silage"]["label_ar"]) == (
        "Corn silage",
        "سيلاج الذرة",
    )
    # Reading the slots needs the feed module.
    assert client.get(f"{API}/feed-products/standard-slots", headers=f.worker_h).status_code == 403


def test_ensure_standard_feeds_creates_only_what_is_missing(client, control_db):
    f = Farm(client, control_db, "S11-STD")
    silage = f.product("Maize silage", category="forage", is_feedable=True)
    before = len(f.get("/feed-products"))
    slots = {s["slot"]: s for s in client.post(f"{API}/feed-products/standard", headers=f.h).json()}
    after = f.get("/feed-products")
    assert len(after) == before + 2
    assert slots["corn_silage"]["feed_product_id"] == silage["id"]
    straw = next(p for p in after if p["id"] == slots["wheat_straw"]["feed_product_id"])
    assert straw["name"] == "Wheat straw" and straw["is_feedable"] is True and straw["code"] == "WHEAT-STRAW"
    assert straw["availability"]["on_hand"] == 0  # no invented stock
    concentrate = next(p for p in after if p["id"] == slots["concentrate"]["feed_product_id"])
    assert concentrate["source_type"] == "farm_produced" and concentrate["name_ar"] == "علف مركّز"
    again = client.post(f"{API}/feed-products/standard", headers=f.h).json()
    assert len(f.get("/feed-products")) == before + 2
    assert {s["slot"]: s["feed_product_id"] for s in again} == {
        s: v["feed_product_id"] for s, v in slots.items()
    }
    # The standard feeds appear on the item-level feed screen too, with no stock.
    items = f.get("/feed/items", {"farm_id": f.farm_id})
    assert {i["name"] for i in items} >= {"Wheat straw", "Concentrate", "Maize silage"}
    assert all(i["current_qty"] == 0 for i in items)
    # Creating them needs Feed create.
    assert client.post(f"{API}/feed-products/standard", headers=f.worker_h).status_code == 403


def test_feeding_beyond_entered_stock_needs_the_explicit_override(client, control_db):
    f = Farm(client, control_db, "S11-NEG")
    bella = f.animal("Bella")
    straw = f.product("Barley straw", unit="kg", is_feedable=True, category="forage")
    body = {
        "subject_type": "animal",
        "subject_id": bella["id"],
        "event_type": "offered",
        "components": [{"feed_product_id": straw["id"], "quantity_offered": 6}],
    }
    refused = client.post(f"{API}/feeding-events", json=body, headers=f.h)
    assert refused.status_code == 422 and refused.json()["detail"].startswith("Not enough")
    ok = client.post(f"{API}/feeding-events", json={**body, "allow_negative": True}, headers=f.h)
    assert ok.status_code == 201, ok.text
    assert ok.json()["components"][0]["lot_id"] is None  # the shortfall is against no lot


# ------------------------------------------------------------ possible duplicates
def test_the_same_feeding_round_twice_is_asked(client, control_db):
    f = Farm(client, control_db, "S11-DUPFEED")
    thunder = f.post(
        "/animals", {"name": "Thunder", "species": "horse", "sex": "M", "identifiers": _microchip()}
    )
    alfalfa = f.product("Alfalfa Hay", opening_quantity=1000, is_feedable=True)
    body = {
        "subject_type": "animal",
        "subject_id": thunder["id"],
        "event_type": "offered",
        "components": [{"feed_product_id": alfalfa["id"], "quantity_offered": 4.5}],
    }
    assert client.post(f"{API}/feeding-events", json=body, headers=f.h).status_code == 201
    again = client.post(f"{API}/feeding-events", json=body, headers=f.h)
    assert _is_possible_duplicate(again), again.text
    assert "Thunder" in again.json()["detail"] and "half an hour" in again.json()["detail"]
    # A different quantity is a different round.
    other = {**body, "components": [{"feed_product_id": alfalfa["id"], "quantity_offered": 2}]}
    assert client.post(f"{API}/feeding-events", json=other, headers=f.h).status_code == 201
    assert _confirmed(control_db, f.tenant.id) == []
    confirmed = client.post(f"{API}/feeding-events", json={**body, "confirm_duplicate": True}, headers=f.h)
    assert confirmed.status_code == 201, confirmed.text
    # The confirmed duplicate is on the audit trail, naming what it resembles.
    events = _confirmed(control_db, f.tenant.id)
    assert len(events) == 1 and events[0].metadata_json["record"] == f"feeding:{confirmed.json()['id']}"
    assert events[0].metadata_json["resembles"] and events[0].module_code == "feed_nutrition"
    # Four events, all recorded: the ledger took all of them.
    assert f.availability(alfalfa["id"])["on_hand"] == 1000 - 4.5 - 2 - 4.5


def test_a_delivery_note_typed_in_twice_is_asked_and_a_used_lot_code_is_refused(client, control_db):
    f = Farm(client, control_db, "S11-DUPLOT")
    barley = f.product("Barley", opening_quantity=100)
    body = {
        "feed_product_id": barley["id"],
        "quantity": 250,
        "unit_cost": 0.35,
        "supplier_label": "Bekaa Grain Co.",
        "reference": "DN-7781",
    }
    assert client.post(f"{API}/feed-lots/receive", json=body, headers=f.h).status_code == 201
    again = client.post(f"{API}/feed-lots/receive", json={**body, "quantity": 260}, headers=f.h)
    assert _is_possible_duplicate(again) and "DN-7781" in again.json()["detail"], again.text
    assert (
        client.post(
            f"{API}/feed-lots/receive", json={**body, "confirm_duplicate": True}, headers=f.h
        ).status_code
        == 201
    )
    # The same quantity from the same supplier within half an hour, no note.
    plain = {"feed_product_id": barley["id"], "quantity": 300, "supplier_label": "Bekaa Grain Co."}
    assert client.post(f"{API}/feed-lots/receive", json=plain, headers=f.h).status_code == 201
    twin = client.post(f"{API}/feed-lots/receive", json=plain, headers=f.h)
    assert _is_possible_duplicate(twin) and "300 kg of Barley from Bekaa Grain Co." in twin.json()["detail"]

    coded = {"feed_product_id": barley["id"], "quantity": 100, "lot_code": "BAR-DUP-01"}
    assert client.post(f"{API}/feed-lots/receive", json=coded, headers=f.h).status_code == 201
    reused = client.post(f"{API}/feed-lots/receive", json={**coded, "confirm_duplicate": True}, headers=f.h)
    assert reused.status_code == 409 and not reused.json()["detail"].startswith(
        PREFIX
    ), "a lot code is never confirmed twice"
    assert "BAR-DUP-01" in reused.json()["detail"]


def test_a_mix_saved_twice_is_asked(client, control_db):
    f = Farm(client, control_db, "S11-DUPMIX")
    barley = f.product("Barley", opening_quantity=1000)
    body = {
        "new_product_name": "Heifer mix",
        "components": [{"feed_product_id": barley["id"], "actual_quantity": 120}],
    }
    first = client.post(f"{API}/feed-mixes", json=body, headers=f.h)
    assert first.status_code == 201, first.text
    product_id = first.json()["feed_product_id"]
    again = client.post(
        f"{API}/feed-mixes",
        json={"feed_product_id": product_id, "components": body["components"]},
        headers=f.h,
    )
    assert _is_possible_duplicate(again), again.text
    assert first.json()["mix_code"] in again.json()["detail"]
    confirmed = client.post(
        f"{API}/feed-mixes",
        json={"feed_product_id": product_id, "components": body["components"], "confirm_duplicate": True},
        headers=f.h,
    )
    assert confirmed.status_code == 201 and confirmed.json()["mix_number"] == first.json()["mix_number"] + 1
    assert [e.metadata_json["record"] for e in _confirmed(control_db, f.tenant.id)] == [
        f"mix:{confirmed.json()['mix_code']}"
    ]
    # Typing the new feed's name again reuses it rather than making a second "Heifer mix".
    by_name = client.post(
        f"{API}/feed-mixes",
        json={**body, "components": [{"feed_product_id": barley["id"], "actual_quantity": 80}]},
        headers=f.h,
    )
    assert by_name.status_code == 201 and by_name.json()["feed_product_id"] == product_id
    assert len([p for p in f.get("/feed-products") if p["name"] == "Heifer mix"]) == 1


def test_names_that_must_stay_unique(client, control_db):
    f = Farm(client, control_db, "S11-NAMES")
    feed = {
        "name": "Corn grain",
        "unit": "kg",
        "is_ingredient": True,
        "is_feedable": False,
        "category": "concentrate",
    }
    assert client.post(f"{API}/feed-products", json=feed, headers=f.h).status_code == 201
    clash = client.post(f"{API}/feed-products", json={**feed, "name": "corn GRAIN "}, headers=f.h)
    assert clash.status_code == 409 and "Corn grain" in clash.json()["detail"]

    field = {"name": "North terrace", "area_value": 2, "area_unit": "dunum"}
    assert client.post(f"{API}/fields", json=field, headers=f.h).status_code == 201
    assert client.post(f"{API}/fields", json=field, headers=f.h).status_code == 409

    material = {"name": "Baby Eggplant", "unit": "kg", "default_unit_cost": 1.2, "current_stock": 320}
    assert client.post(f"{API}/mouneh/raw-materials", json=material, headers=f.h).status_code == 201
    assert (
        client.post(
            f"{API}/mouneh/raw-materials", json={**material, "name": "baby eggplant"}, headers=f.h
        ).status_code
        == 409
    )


def test_health_treatment_and_observation_repeats_are_asked(client, control_db):
    f = Farm(client, control_db, "S11-DUPCLIN")
    cow = f.animal("Rima")
    case = {"subject_type": "animal", "subject_id": cow["id"], "primary_problem_code": "LAMENESS"}
    first = f.post("/health/cases", case)
    dup_case = client.post(f"{API}/health/cases", json=case, headers=f.h)
    assert _is_possible_duplicate(dup_case) and "already open" in dup_case.json()["detail"]
    assert first["case_code"] in dup_case.json()["detail"]
    assert (
        client.post(
            f"{API}/health/cases", json={**case, "primary_problem_code": "MASTITIS"}, headers=f.h
        ).status_code
        == 201
    )
    second_lameness = client.post(
        f"{API}/health/cases", json={**case, "confirm_duplicate": True}, headers=f.h
    )
    assert second_lameness.status_code == 201

    treatment = {
        "entity_type": "animal",
        "entity_id": cow["id"],
        "medication": "Amoxicillin",
        "dose": "15 mg/kg",
        "route": "IM",
        "responsible_user_id": str(f.owner.id),
    }
    assert client.post(f"{API}/health/treatments", json=treatment, headers=f.h).status_code == 201
    again = client.post(
        f"{API}/health/treatments", json={**treatment, "medication": "amoxicillin"}, headers=f.h
    )
    assert _is_possible_duplicate(again) and "12 hours" in again.json()["detail"], again.text
    assert (
        client.post(
            f"{API}/health/treatments", json={**treatment, "confirm_duplicate": True}, headers=f.h
        ).status_code
        == 201
    )

    seen = {
        "farm_id": f.farm_id,
        "entity_type": "animal",
        "entity_id": cow["id"],
        "observation_type": "limping",
        "severity": "mild",
        "observer_id": str(f.owner.id),
    }
    assert client.post(f"{API}/observations", json=seen, headers=f.h).status_code == 201
    repeat = client.post(f"{API}/observations", json=seen, headers=f.h)
    assert _is_possible_duplicate(repeat) and "limping observation" in repeat.json()["detail"]
    # A different value is a different observation.
    assert (
        client.post(f"{API}/observations", json={**seen, "value_text": "left hind"}, headers=f.h).status_code
        == 201
    )
    assert (
        client.post(f"{API}/observations", json={**seen, "confirm_duplicate": True}, headers=f.h).status_code
        == 201
    )
    kinds = {e.metadata_json["record"].split(":")[0] for e in _confirmed(control_db, f.tenant.id)}
    assert kinds == {"health case", "treatment", "observation"}


def test_a_medicine_given_twice_within_twelve_hours_is_asked(client, control_db):
    f = Farm(client, control_db, "S11-DUPDOSE")
    goat = f.post("/animals", {"name": "Zahra", "species": "goat", "sex": "F", "tag": _tag()})
    item = f.post(
        "/pharmacy/medicines",
        {
            "name": "Oral electrolyte",
            "unit": "sachet",
            "dosage_form": "powder",
            "administration_routes": ["PO"],
            "category_codes": ["ELECTROLYTE"],
            "ingredients": [{"code": "glucose_electrolyte"}],
        },
    )["inventory_item_id"]
    f.post(
        "/pharmacy/lots/receive",
        {"inventory_item_id": item, "quantity": 20, "lot_code": "ELE-01", "expiry_date": _iso(-365)},
    )
    dose = {
        "subject_type": "animal",
        "subject_id": goat["id"],
        "inventory_item_id": item,
        "dose_quantity": 1,
        "dose_unit": "sachet",
        "route_code": "PO",
    }
    assert client.post(f"{API}/pharmacy/administrations", json=dose, headers=f.h).status_code == 201
    again = client.post(f"{API}/pharmacy/administrations", json=dose, headers=f.h)
    assert _is_possible_duplicate(again) and "double dose" in again.json()["detail"], again.text
    # A twice-daily course is confirmed each time.
    assert (
        client.post(
            f"{API}/pharmacy/administrations", json={**dose, "confirm_duplicate": True}, headers=f.h
        ).status_code
        == 201
    )
    assert len(f.get("/pharmacy/administrations", {"subject_id": goat["id"]})) == 2


def test_eggs_harvests_and_tasks_entered_twice_are_asked(client, control_db):
    f = Farm(client, control_db, "S11-DUPPROD")
    layers = f.post(
        "/animal-groups",
        {"name": "Layers", "species": "layer_hen", "count": 120, "sex_composition": "female"},
    )
    eggs = {"flock_id": layers["id"], "total_eggs": 420, "sellable_eggs": 395, "broken_eggs": 15}
    assert client.post(f"{API}/production/eggs", json=eggs, headers=f.h).status_code == 201
    again = client.post(f"{API}/production/eggs", json=eggs, headers=f.h)
    assert _is_possible_duplicate(again) and "420 eggs from Layers" in again.json()["detail"]
    assert (
        client.post(f"{API}/production/eggs", json={**eggs, "total_eggs": 500}, headers=f.h).status_code
        == 201
    )
    assert (
        client.post(
            f"{API}/production/eggs", json={**eggs, "confirm_duplicate": True}, headers=f.h
        ).status_code
        == 201
    )

    field = f.post("/fields", {"name": "Greenhouse 1", "area_value": 1, "area_unit": "dunum"})
    harvest = {"field_id": field["id"], "product_name": "Tomatoes", "quantity": 96, "waste_qty": 6}
    assert client.post(f"{API}/production/harvest", json=harvest, headers=f.h).status_code == 201
    twin = client.post(f"{API}/production/harvest", json=harvest, headers=f.h)
    assert _is_possible_duplicate(twin) and "96 kg of Tomatoes from Greenhouse 1" in twin.json()["detail"]
    assert (
        client.post(f"{API}/production/harvest", json={**harvest, "quantity": 40}, headers=f.h).status_code
        == 201
    )
    missing = client.post(
        f"{API}/production/harvest", json={**harvest, "field_id": str(uuid.uuid4())}, headers=f.h
    )
    assert missing.status_code == 404

    pick = {"field_id": field["id"], "product_name": "Basil", "total_quantity": 20, "waste_quantity": 2}
    assert client.post(f"{API}/harvest", json=pick, headers=f.h).status_code == 201
    repeat = client.post(f"{API}/harvest", json=pick, headers=f.h)
    assert _is_possible_duplicate(repeat) and "20 kg of Basil from Greenhouse 1" in repeat.json()["detail"]
    confirmed = client.post(f"{API}/harvest", json={**pick, "confirm_duplicate": True}, headers=f.h)
    assert confirmed.status_code == 201 and confirmed.json()["inventory_qty_after"] == 36.0

    task = {"farm_id": f.farm_id, "title": "Fix the fence"}
    first = f.post("/tasks", task)
    same = client.post(f"{API}/tasks", json={**task, "title": "fix the FENCE"}, headers=f.h)
    assert _is_possible_duplicate(same) and "Fix the fence" in same.json()["detail"]
    assert (
        client.post(f"{API}/tasks", json={**task, "confirm_duplicate": True}, headers=f.h).status_code == 201
    )
    # Once the job is done, the same title is a new job.
    assert client.patch(f"{API}/tasks/{first['id']}", json={"status": "done"}, headers=f.h).status_code == 200
    assert (
        client.post(f"{API}/tasks", json={**task, "title": "Mend the gate"}, headers=f.h).status_code == 201
    )
    kinds = sorted(e.metadata_json["record"].split(":")[0] for e in _confirmed(control_db, f.tenant.id))
    assert kinds == ["eggs", "harvest", "task"]


def test_a_mouneh_sale_typed_in_twice_is_asked(client, control_db):
    f = Farm(client, control_db, "S11-DUPSALE")
    material = f.post(
        "/mouneh/raw-materials",
        {"name": "Baby Eggplant", "unit": "kg", "default_unit_cost": 1.2, "current_stock": 320},
    )
    product = f.post("/mouneh/products", {"name": "Makdous", "output_unit": "jar", "default_batch_size": 100})
    f.post(
        f"/mouneh/products/{product['id']}/recipes",
        {
            "basis_quantity": 100,
            "basis_unit": "jar",
            "items": [{"material_id": material["id"], "quantity": 45, "unit": "kg"}],
        },
    )
    batch = f.post("/mouneh/batches", {"product_id": product["id"], "planned_qty": 60})
    r = client.post(
        f"{API}/mouneh/batches/{batch['id']}/complete", json={"actual_output_qty": 58}, headers=f.h
    )
    assert r.status_code == 200, r.text
    sale = {"product_id": product["id"], "quantity": 2, "unit_price": 6.5, "channel": "retail"}
    assert client.post(f"{API}/mouneh/sales", json=sale, headers=f.h).status_code == 201
    again = client.post(f"{API}/mouneh/sales", json=sale, headers=f.h)
    assert _is_possible_duplicate(again) and "2 × Makdous at 6.5" in again.json()["detail"], again.text
    # A different quantity is another customer.
    assert client.post(f"{API}/mouneh/sales", json={**sale, "quantity": 3}, headers=f.h).status_code == 201
    assert (
        client.post(f"{API}/mouneh/sales", json={**sale, "confirm_duplicate": True}, headers=f.h).status_code
        == 201
    )
    assert (
        client.post(
            f"{API}/mouneh/sales", json={**sale, "product_id": str(uuid.uuid4())}, headers=f.h
        ).status_code
        == 404
    )


# ------------------------------------------------------------ approval levels
def test_only_an_approver_records_more_than_the_stock_on_record(client, control_db):
    f = Farm(
        client, control_db, "S11-OVR", worker_grid={"feed_nutrition": ["view", "create"], "animals": ["view"]}
    )
    thunder = f.post(
        "/animals", {"name": "Thunder", "species": "horse", "sex": "M", "identifiers": _microchip()}
    )
    alfalfa = f.product("Alfalfa Hay", opening_quantity=10, is_feedable=True)
    body = {
        "subject_type": "animal",
        "subject_id": thunder["id"],
        "event_type": "offered",
        "allow_negative": True,
        "components": [{"feed_product_id": alfalfa["id"], "quantity_offered": 99999}],
    }
    refused = client.post(f"{API}/feeding-events", json=body, headers=f.worker_h)
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"].startswith(
        "Recording more than the stock on record needs Feed & Nutrition or Inventory approval"
    )
    # Without the override the worker gets the ordinary arithmetic, not a permission refusal.
    plain = client.post(f"{API}/feeding-events", json={**body, "allow_negative": False}, headers=f.worker_h)
    assert plain.status_code == 422 and plain.json()["detail"].startswith("Not enough")
    assert client.post(f"{API}/feeding-events", json=body, headers=f.h).status_code == 201

    legacy = client.post(
        f"{API}/feed/transactions",
        json={
            "item_id": alfalfa["inventory_item_id"],
            "direction": "out",
            "quantity": 99999,
            "unit": "kg",
            "allow_negative": True,
        },
        headers=f.worker_h,
    )
    assert legacy.status_code == 403, legacy.text
    # Inventory approval counts as much as Feed approval.
    inventory_approver, _ = add_farmos_user(
        control_db,
        f.tenant,
        "stock-s11@origami-demo.com",
        role="worker",
        grid={"inventory": ["view", "create", "approve"]},
    )
    control_db.commit()
    approver_h = _login(client, inventory_approver.email)
    assert (
        client.post(
            f"{API}/feed/transactions",
            json={
                "item_id": alfalfa["inventory_item_id"],
                "direction": "out",
                "quantity": 5,
                "unit": "kg",
                "allow_negative": True,
            },
            headers=approver_h,
        ).status_code
        == 201
    )
    # A completed mix drawing more than the stock on record is the same exception.
    mix = f.post(
        "/feed-mixes",
        {
            "new_product_name": "Horse mix",
            "components": [{"feed_product_id": alfalfa["id"], "actual_quantity": 1}],
            "allow_negative": True,  # the owner approves: the alfalfa ledger is already below zero
        },
    )
    started = f.post(
        "/feed-batches", {"formula_version_id": mix["formula_version_id"], "target_quantity": 500}
    )
    over = client.post(
        f"{API}/feed-batches/{started['id']}/complete",
        json={
            "actuals": [{"feed_product_id": alfalfa["id"], "actual_quantity": 500}],
            "actual_quantity": 500,
            "allow_negative": True,
        },
        headers=f.worker_h,
    )
    assert over.status_code == 403 and "approval" in over.json()["detail"]


def test_a_workers_recipe_is_a_draft_until_approved(client, control_db):
    f = Farm(client, control_db, "S11-DRAFT", worker_grid={"feed_nutrition": ["view", "create"]})
    barley = f.product("Barley", opening_quantity=1000)
    starter = f.product(
        "Calf starter", unit="kg", source_type="farm_produced", category="concentrate", is_feedable=True
    )
    body = {
        "name": "Calf starter",
        "feed_product_id": starter["id"],
        "batch_size": 100,
        "components": [{"feed_product_id": barley["id"], "target_quantity": 100}],
    }
    r = client.post(f"{API}/feed-formulas", json=body, headers=f.worker_h)
    assert r.status_code == 201, r.text
    assert r.json()["active_version"] is None and r.json()["versions"][0]["status"] == "draft"
    # A draft cannot be mixed from; the manager activates it, then it can.
    refused = client.post(
        f"{API}/feed-batches", json={"formula_id": r.json()["id"], "target_quantity": 100}, headers=f.worker_h
    )
    assert refused.status_code == 422 and "no active version" in refused.json()["detail"]
    assert (
        client.post(
            f"{API}/feed-formulas/{r.json()['id']}/versions/1/activate", headers=f.worker_h
        ).status_code
        == 403
    )
    activated = client.post(f"{API}/feed-formulas/{r.json()['id']}/versions/1/activate", headers=f.h)
    assert activated.status_code == 200 and activated.json()["status"] == "active"
    # The same for a feeding program: a worker's program waits for approval.
    program = client.post(
        f"{API}/feeding-programs",
        json={
            "name": "Calf program",
            "components": [{"feed_product_id": starter["id"], "quantity_per_head": 1, "unit": "kg"}],
            "rules": [{"species_code": "cow", "life_stage": "calf"}],
        },
        headers=f.worker_h,
    )
    assert program.status_code == 201, program.text
    assert program.json()["active_version"] is None


def test_a_worker_cannot_create_a_recipe_by_mixing_a_new_feed(client, control_db):
    f = Farm(client, control_db, "S11-MIXW", worker_grid={"feed_nutrition": ["view", "create"]})
    barley = f.product("Barley", opening_quantity=1000)
    body = {
        "new_product_name": "Goat pellets",
        "components": [{"feed_product_id": barley["id"], "actual_quantity": 50}],
    }
    refused = client.post(f"{API}/feed-mixes", json=body, headers=f.worker_h)
    assert refused.status_code == 403 and "approved recipe" in refused.json()["detail"], refused.text
    made = client.post(f"{API}/feed-mixes", json=body, headers=f.h)
    assert made.status_code == 201, made.text
    # Once the manager's first mix approved the recipe, the worker mixes it.
    again = client.post(
        f"{API}/feed-mixes",
        json={
            "feed_product_id": made.json()["feed_product_id"],
            "components": [{"feed_product_id": barley["id"], "actual_quantity": 40}],
        },
        headers=f.worker_h,
    )
    assert again.status_code == 201, again.text
    assert again.json()["mix_number"] == made.json()["mix_number"] + 1
    assert f.availability(barley["id"])["on_hand"] == 910


def test_a_poor_grade_delivery_is_held_until_released(client, control_db):
    f = Farm(client, control_db, "S11-POOR")
    barley = f.product("Barley", is_feedable=True)
    r = client.post(
        f"{API}/feed-lots/receive",
        json={
            "feed_product_id": barley["id"],
            "quantity": 300,
            "quality_grade": "poor",
            "quality_notes": "Wet sacks",
        },
        headers=f.h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "quarantined" and r.json()["quality_notes"] == "Wet sacks"
    avail = f.availability(barley["id"])
    assert (
        avail["on_hand"] == 300
        and avail["available"] == 0
        and avail["unusable_by_status"] == {"quarantined": 300}
    )
    released = client.patch(
        f"{API}/feed-lots/{r.json()['id']}/status",
        json={"status": "active", "reason": "Dried and inspected"},
        headers=f.h,
    )
    assert released.status_code == 200 and released.json()["status"] == "active"
    assert f.availability(barley["id"])["available"] == 300
    moisture = client.post(
        f"{API}/feed-lots/receive",
        json={"feed_product_id": barley["id"], "quantity": 10, "moisture_pct": 140},
        headers=f.h,
    )
    assert moisture.status_code == 422


# ------------------------------------------------------------ responsibilities
def test_a_worker_is_made_responsible_for_a_herd_with_duties(client, control_db):
    f = Farm(client, control_db, "S11-RESP")
    # A dairy goat herd: goats are kept in groups, and the animals that name
    # the herd as their group are the ones the responsibility covers.
    goat = {"species": "goat", "sex": "F", "management_profile": "dairy"}
    bella = f.post("/animals", {"name": "Bella", "tag": _tag(), "group_name": "Dairy Herd", **goat})
    f.post("/animals", {"name": "Luna", "tag": _tag(), "group_name": "Dairy Herd", **goat})
    f.post("/animals", {"name": "Clover", "tag": _tag(), "group_name": "Kids", **goat})
    bess = f.post("/animals", {"name": "Old Bess", "tag": _tag(), "group_name": "Dairy Herd", **goat})
    assert client.put(f"{API}/animals/{bess['id']}", json={"active": False}, headers=f.h).status_code == 200
    herd = f.post("/animal-groups", {"name": "Dairy Herd", "species": "goat", "group_type": "herd"})
    worker_id = str(f.worker.id)
    assert "milk_production" not in f.get("/me/access", h=f.worker_h)["modules"]

    r = client.post(
        f"{API}/employees/{worker_id}/responsibilities",
        json={
            "scope_type": "group",
            "scope_id": herd["id"],
            "duties": ["milking", "observation"],
            "notes": "Mornings",
        },
        headers=f.h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scope_name"] == "Dairy Herd" and body["duties"] == ["milking", "observation"]
    assert body["status"] == "active" and body["user_id"] == worker_id and body["notes"] == "Mornings"
    assert body["modules_granted"] == ["animals", "milk_production"]
    # The duties granted the worker the module access they need — the
    # day-to-day grant, never approve or configure — effective at once.
    access = f.get("/me/access", h=f.worker_h)["modules"]
    assert (
        access["milk_production"]["view"]
        and access["milk_production"]["create"]
        and access["milk_production"]["edit"]
    )
    assert not access["milk_production"]["approve"] and not access["milk_production"]["configure"]
    assert access["animals"]["view"]
    employee = next(e for e in f.get("/employees") if e["id"] == worker_id)
    assert {p["module_code"] for p in employee["permissions"]} == {"animals", "milk_production"}

    mine = f.get("/me/responsibilities", h=f.worker_h)
    assert len(mine) == 1 and mine[0]["scope_name"] == "Dairy Herd"
    assert [a["name"] for a in mine[0]["animals"]] == ["Bella", "Luna"]  # the herd's active animals, by name
    assert {a["id"] for a in mine[0]["animals"]} >= {bella["id"]}
    assert set(mine[0]["animals"][0]) == {"id", "name", "tag", "species", "lactating"}
    listed = f.get(f"/employees/{worker_id}/responsibilities")
    assert [x["id"] for x in listed] == [body["id"]] and "animals" not in listed[0]

    repeat = client.post(
        f"{API}/employees/{worker_id}/responsibilities",
        json={"scope_type": "group", "scope_id": herd["id"], "duties": ["feeding"]},
        headers=f.h,
    )
    assert repeat.status_code == 409 and "already looks after" in repeat.json()["detail"]
    wrong_duty = client.post(
        f"{API}/employees/{worker_id}/responsibilities",
        json={"scope_type": "group", "scope_id": herd["id"], "duties": ["harvest"]},
        headers=f.h,
    )
    assert wrong_duty.status_code == 422 and "not a duty for animals" in wrong_duty.json()["detail"]
    unknown = client.post(
        f"{API}/employees/{worker_id}/responsibilities",
        json={"scope_type": "animal", "scope_id": bella["id"], "duties": ["singing"]},
        headers=f.h,
    )
    assert unknown.status_code == 422 and "Unknown duty" in unknown.json()["detail"]
    assert (
        client.post(
            f"{API}/employees/{worker_id}/responsibilities",
            json={"scope_type": "animal", "scope_id": str(uuid.uuid4()), "duties": ["milking"]},
            headers=f.h,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{API}/employees/{worker_id}/responsibilities",
            json={"scope_type": "species", "scope_id": "dragon", "duties": ["feeding"]},
            headers=f.h,
        ).status_code
        == 422
    )

    end = client.post(
        f"{API}/employees/{worker_id}/responsibilities/{body['id']}/end",
        json={"reason": "moved to the clinic"},
        headers=f.h,
    )
    assert end.status_code == 200 and end.json()["status"] == "ended" and end.json()["valid_to"]
    assert f.get("/me/responsibilities", h=f.worker_h) == []
    assert f.get(f"/employees/{worker_id}/responsibilities") == []
    assert [
        x["status"] for x in f.get(f"/employees/{worker_id}/responsibilities", {"include_ended": True})
    ] == ["ended"]
    # Ending a responsibility never silently takes the access away again.
    assert f.get("/me/access", h=f.worker_h)["modules"]["milk_production"]["create"]
    # The manager's audit trail says who was made responsible for what.
    actions = [e["action"] for e in f.get("/audit", {"module": "employees"})]
    assert {
        "responsibility.assigned",
        "responsibility.ended",
        "employee.permissions_granted_by_responsibility",
    } <= set(actions)


def test_responsibilities_by_species_animal_and_field(client, control_db):
    f = Farm(client, control_db, "S11-RESP2")
    bella = f.animal("Bella")
    goat = f.post("/animals", {"name": "Zahra", "species": "goat", "sex": "F", "tag": _tag()})
    field = f.post("/fields", {"name": "North terrace", "area_value": 2, "area_unit": "dunum"})
    worker_id = str(f.worker.id)
    cows = f.post(
        f"/employees/{worker_id}/responsibilities",
        {"scope_type": "species", "scope_id": "cow", "duties": ["feeding", "health_care"]},
    )
    assert cows["scope_name"] == "Cattle" and cows["modules_granted"] == [
        "animal_health",
        "animals",
        "feed_nutrition",
    ]
    one = f.post(
        f"/employees/{worker_id}/responsibilities",
        {"scope_type": "animal", "scope_id": goat["id"], "duties": ["observation"]},
    )
    assert one["scope_name"] == "Zahra" and one["modules_granted"] == []  # animals already granted
    terrace = f.post(
        f"/employees/{worker_id}/responsibilities",
        {"scope_type": "field", "scope_id": field["id"], "duties": ["crop_care", "harvest"]},
    )
    assert terrace["scope_name"] == "North terrace" and terrace["modules_granted"] == [
        "agriculture",
        "produce_harvest",
    ]
    assert (
        client.post(
            f"{API}/employees/{worker_id}/responsibilities",
            json={"scope_type": "field", "scope_id": field["id"], "duties": ["crop_care"]},
            headers=f.h,
        ).status_code
        == 409
    )  # already looks after it — the duties are changed by ending it first
    assert (
        client.post(
            f"{API}/employees/{worker_id}/responsibilities",
            json={"scope_type": "field", "scope_id": field["id"], "duties": ["milking"]},
            headers=f.h,
        ).status_code
        == 422
    )  # not a duty for a field
    mine = {r["scope_type"]: r for r in f.get("/me/responsibilities", h=f.worker_h)}
    assert {a["id"] for a in mine["species"]["animals"]} == {bella["id"]}
    assert [a["id"] for a in mine["animal"]["animals"]] == [goat["id"]]
    assert mine["field"]["animals"] == []
    # A manager has every module already: nothing is granted, the responsibility still routes the work.
    manager, _ = add_farmos_user(control_db, f.tenant, "mgr-s11@origami-demo.com", role="manager")
    control_db.commit()
    r = f.post(
        f"/employees/{manager.id}/responsibilities",
        {"scope_type": "animal", "scope_id": bella["id"], "duties": ["milking"]},
    )
    assert r["modules_granted"] == []


def test_only_a_manager_assigns_responsibilities_and_farms_stay_apart(client, control_db):
    f = Farm(client, control_db, "S11-RESP3", worker_grid={"employees": ["view"]})
    bella = f.animal("Bella")
    worker_id = str(f.worker.id)
    body = {"scope_type": "animal", "scope_id": bella["id"], "duties": ["milking"]}
    assert (
        client.post(
            f"{API}/employees/{worker_id}/responsibilities", json=body, headers=f.worker_h
        ).status_code
        == 403
    )
    assert client.get(f"{API}/employees/{worker_id}/responsibilities", headers=f.worker_h).status_code == 200
    made = f.post(f"/employees/{worker_id}/responsibilities", body)
    assert (
        client.post(
            f"{API}/employees/{worker_id}/responsibilities/{made['id']}/end", json={}, headers=f.worker_h
        ).status_code
        == 403
    )
    # Employees assign (not configure) is the manager's grant for this screen.
    assigner, _ = add_farmos_user(
        control_db, f.tenant, "hr-s11@origami-demo.com", role="worker", grid={"employees": ["view", "assign"]}
    )
    control_db.commit()
    assigner_h = _login(client, assigner.email)
    ended = client.post(
        f"{API}/employees/{worker_id}/responsibilities/{made['id']}/end", json={}, headers=assigner_h
    )
    assert ended.status_code == 200 and ended.json()["status"] == "ended"
    # Ending it twice is idempotent.
    assert (
        client.post(
            f"{API}/employees/{worker_id}/responsibilities/{made['id']}/end", json={}, headers=f.h
        ).json()["status"]
        == "ended"
    )
    assert (
        client.post(
            f"{API}/employees/{worker_id}/responsibilities/{uuid.uuid4()}/end", json={}, headers=f.h
        ).status_code
        == 404
    )

    # Another farm's owner sees neither the employee nor the responsibility.
    other = Farm(client, control_db, "S11-RESP3B")
    assert client.get(f"{API}/employees/{worker_id}/responsibilities", headers=other.h).status_code == 404
    assert (
        client.post(f"{API}/employees/{worker_id}/responsibilities", json=body, headers=other.h).status_code
        == 404
    )
    other_worker = str(other.worker.id)
    assert (
        client.post(
            f"{API}/employees/{other_worker}/responsibilities", json=body, headers=other.h
        ).status_code
        == 404
    )  # Bella is not theirs
    assert (
        client.post(
            f"{API}/employees/{other_worker}/responsibilities/{made['id']}/end", json={}, headers=other.h
        ).status_code
        == 404
    )
    assert f.get("/me/responsibilities", h=other.worker_h) == []


# ------------------------------------------------------------ permission alignment
def test_reads_follow_the_modules_the_tablet_offers_them_to(client, control_db):
    f = Farm(client, control_db, "S11-PERM", worker_grid={"animals": ["view"]})
    bella = f.animal("Bella")
    params = {"farm_id": f.farm_id}
    # The animal's twin shows its observations and treatments to whoever holds Animals.
    assert client.get(f"{API}/observations", params=params, headers=f.worker_h).status_code == 200
    assert client.get(f"{API}/health/treatments", params=params, headers=f.worker_h).status_code == 200
    assert client.get(f"{API}/feed/items", params=params, headers=f.worker_h).status_code == 403
    assert client.get(f"{API}/morning-briefing", params=params, headers=f.worker_h).status_code == 403
    assert client.get(f"{API}/reports/daily-summary", params=params, headers=f.worker_h).status_code == 403
    assert client.get(f"{API}/production/eggs", params=params, headers=f.worker_h).status_code == 403

    def _user(email, grid):
        user, _ = add_farmos_user(control_db, f.tenant, email, role="worker", grid=grid)
        control_db.commit()
        return _login(client, user.email)

    stock_h = _user("stock-s11p@origami-demo.com", {"inventory": ["view", "create"]})
    assert client.get(f"{API}/feed/items", params=params, headers=stock_h).status_code == 200
    assert client.get(f"{API}/feed/transactions", params=params, headers=stock_h).status_code == 200
    assert (
        client.post(f"{API}/feed/items", json={"name": "Salt", "unit": "kg"}, headers=stock_h).status_code
        == 201
    )
    morning_h = _user("morning-s11p@origami-demo.com", {"morning_operations": ["view"]})
    assert client.get(f"{API}/morning-briefing", params=params, headers=morning_h).status_code == 200
    finance_h = _user("fin-s11p@origami-demo.com", {"finance": ["view"]})
    assert client.get(f"{API}/reports/daily-summary", params=params, headers=finance_h).status_code == 200
    assert client.get(f"{API}/sales", params=params, headers=finance_h).status_code == 200
    assert client.get(f"{API}/expenses", params=params, headers=finance_h).status_code == 200
    expenses_h = _user("exp-s11p@origami-demo.com", {"expenses": ["view"]})
    assert client.get(f"{API}/expenses", params=params, headers=expenses_h).status_code == 200
    assert client.get(f"{API}/sales", params=params, headers=expenses_h).status_code == 403
    produce_h = _user("produce-s11p@origami-demo.com", {"produce_harvest": ["view", "create"]})
    # Produce & Harvest loads fields, eggs and harvest in one batch.
    assert client.get(f"{API}/production/fields", params=params, headers=produce_h).status_code == 200
    assert client.get(f"{API}/production/eggs", params=params, headers=produce_h).status_code == 200
    assert client.get(f"{API}/production/harvest", params=params, headers=produce_h).status_code == 200
    assert client.get(f"{API}/production/milk", params=params, headers=produce_h).status_code == 403
    assert client.get(f"{API}/observations", params=params, headers=produce_h).status_code == 403
    assert bella["id"]


def test_observations_are_gated_by_their_subject_and_their_observer(client, control_db):
    f = Farm(client, control_db, "S11-OBS", worker_grid={"animals": ["view", "create"]})
    bella = f.animal("Bella")
    field = f.post("/fields", {"name": "South terrace", "area_value": 2, "area_unit": "dunum"})
    seen = {
        "farm_id": f.farm_id,
        "entity_type": "animal",
        "entity_id": bella["id"],
        "observation_type": "limping",
        "observer_id": str(f.worker.id),
    }
    # Animals create is enough for an animal; a field belongs to Agriculture.
    assert client.post(f"{API}/observations", json=seen, headers=f.worker_h).status_code == 201
    on_field = {**seen, "entity_type": "field", "entity_id": field["id"], "observation_type": "weeds"}
    assert client.post(f"{API}/observations", json=on_field, headers=f.worker_h).status_code == 403
    grower, _ = add_farmos_user(
        control_db,
        f.tenant,
        "grower-s11@origami-demo.com",
        role="worker",
        grid={"agriculture": ["view", "create"]},
    )
    control_db.commit()
    grower_h = _login(client, grower.email)
    assert (
        client.post(
            f"{API}/observations", json={**on_field, "observer_id": str(grower.id)}, headers=grower_h
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"{API}/observations", json={**seen, "observer_id": str(grower.id)}, headers=grower_h
        ).status_code
        == 403
    )
    assert (
        client.get(f"{API}/observations", params={"farm_id": f.farm_id}, headers=grower_h).status_code == 200
    )
    # The subject must exist on this farm, and the observer must be someone on it.
    assert (
        client.post(
            f"{API}/observations", json={**seen, "entity_id": str(uuid.uuid4())}, headers=f.h
        ).status_code
        == 404
    )
    assert (
        client.post(f"{API}/observations", json={**seen, "entity_type": "tractor"}, headers=f.h).status_code
        == 422
    )
    stranger = Farm(client, control_db, "S11-OBSB")
    planted = client.post(
        f"{API}/observations", json={**seen, "observer_id": str(stranger.worker.id)}, headers=f.h
    )
    assert planted.status_code == 422 and "someone on this farm" in planted.json()["detail"]
    assert (
        client.post(
            f"{API}/observations",
            json={**seen, "entity_id": bella["id"], "farm_id": stranger.farm_id},
            headers=stranger.h,
        ).status_code
        == 404
    )
    # A flock is observed like an animal.
    layers = f.post("/animal-groups", {"name": "Layers", "species": "layer_hen", "count": 50})
    on_flock = {
        **seen,
        "entity_type": "flock",
        "entity_id": layers["id"],
        "observation_type": "feather_pecking",
    }
    assert client.post(f"{API}/observations", json=on_flock, headers=f.worker_h).status_code == 201


def test_tasks_stay_with_their_assignee_and_their_farm(client, control_db):
    f = Farm(client, control_db, "S11-TASK", worker_grid={"tasks": ["view", "create", "edit"]})
    other, _ = add_farmos_user(
        control_db, f.tenant, "other-s11t@origami-demo.com", role="worker", grid={"tasks": ["view", "edit"]}
    )
    control_db.commit()
    other_h = _login(client, other.email)
    stranger = Farm(client, control_db, "S11-TASKB")
    # An assignee is someone on this farm.
    away = client.post(
        f"{API}/tasks",
        json={"farm_id": f.farm_id, "title": "Paint the barn", "assigned_to": str(stranger.worker.id)},
        headers=f.h,
    )
    assert away.status_code == 404 and away.json()["detail"] == "Assignee not found."
    task = f.post(
        "/tasks", {"farm_id": f.farm_id, "title": "Paint the barn", "assigned_to": str(f.worker.id)}
    )
    # Employees act on their own tasks: a colleague cannot change it, the assignee can.
    refused = client.patch(f"{API}/tasks/{task['id']}", json={"status": "in_progress"}, headers=other_h)
    assert refused.status_code == 403 and "assigned to you" in refused.json()["detail"]
    assert (
        client.patch(
            f"{API}/tasks/{task['id']}", json={"status": "in_progress"}, headers=f.worker_h
        ).status_code
        == 200
    )
    reassign = client.patch(
        f"{API}/tasks/{task['id']}", json={"assigned_to": str(other.id)}, headers=f.worker_h
    )
    assert reassign.status_code == 403
    assert (
        client.patch(
            f"{API}/tasks/{task['id']}", json={"assigned_to": str(stranger.worker.id)}, headers=f.h
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"{API}/tasks/{task['id']}", json={"assigned_to": str(other.id)}, headers=f.h
        ).status_code
        == 200
    )
    # A decision on a recommendation is made in the caller's own name only.
    assert client.get(f"{API}/tasks", params={"farm_id": f.farm_id}, headers=stranger.h).status_code == 404
