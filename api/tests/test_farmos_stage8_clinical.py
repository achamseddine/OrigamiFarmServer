"""Stage 8 of the FarmOS tablet contract: the farm pharmacy, health cases,
purchase requisitions and the emergency protocol engine (incl. offline
runs). See docs/FARMOS_API.md and the OrigamiFarmOS schemas
MEDICINE-PHARMACY-SCHEMA.md §14, HEALTH-VETERINARY-SCHEMA.md,
PROCUREMENT-SCHEMA.md §5–§6 and
CLINICAL-DECISION-SUPPORT-EMERGENCY-PROTOCOLS.md §17.

Every test builds its own farm: one owner, one veterinarian, one worker,
one accountant; four medicines with lots; three cows, a goat and a flock
of layers; a bovine fever protocol and a poultry heat-stress protocol,
both approved by the veterinarian.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.common.tenant_router import TenantDataRouter
from app.farmos.livestock_models import AnimalWeightRecord
from app.farmos.production_models import Treatment
from app.tenant_api.models import Animal, InventoryItem, InventoryMovement, Task
from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant

API = "/api/v1"
ALL = ["view", "create", "edit", "delete", "approve", "export", "assign", "configure"]


def _days(n: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=n)).isoformat()


def _post(client, h, path, body=None, expect=201):
    r = client.post(f"{API}{path}", json=body, headers=h)
    assert r.status_code == expect, f"POST {path}: {r.status_code} {r.text}"
    return r.json()


def _get(client, h, path, params=None):
    r = client.get(f"{API}{path}", params=params, headers=h)
    assert r.status_code == 200, f"GET {path}: {r.status_code} {r.text}"
    return r.json()


def _put(client, h, path, body, expect=200):
    r = client.put(f"{API}{path}", json=body, headers=h)
    assert r.status_code == expect, f"PUT {path}: {r.status_code} {r.text}"
    return r.json()


def _patch(client, h, path, body, expect=200):
    r = client.patch(f"{API}{path}", json=body, headers=h)
    assert r.status_code == expect, f"PATCH {path}: {r.status_code} {r.text}"
    return r.json()


def _types(alerts) -> set[str]:
    return {a["alert_type"] for a in alerts}


def _step(run: dict, step_no: int) -> dict:
    return next(s for s in run["steps"] if s["step"]["step_no"] == step_no)


class Farm:
    """Everything the scenarios need, built through the API."""

    def __init__(self, client, control_db, prefix: str):
        self.client = client
        tenant = create_tenant(control_db, company_code=unique_code(prefix))
        self.tenant = tenant
        self.owner, _ = add_farmos_user(
            control_db, tenant, f"owner-{prefix}@origami-demo.com", role="owner", display_name="Rami Owner"
        )
        self.vet, _ = add_farmos_user(
            control_db,
            tenant,
            f"vet-{prefix}@origami-demo.com",
            role="veterinarian",
            display_name="Dr. Layla Haddad",
            grid={"animal_health": ALL, "inventory": ["view"], "animals": ["view", "edit"]},
        )
        self.worker, _ = add_farmos_user(
            control_db,
            tenant,
            f"worker-{prefix}@origami-demo.com",
            role="worker",
            display_name="Karim Worker",
            grid={"animal_health": ["view", "create"], "inventory": ["view"], "animals": ["view"]},
        )
        self.accountant, _ = add_farmos_user(
            control_db,
            tenant,
            f"acct-{prefix}@origami-demo.com",
            role="accountant",
            display_name="Nadine Accountant",
            grid={"finance": ["view"]},
        )
        control_db.commit()
        self.h = farmos_headers(farmos_login(client, self.owner.email, FARMOS_DEMO_PASSWORD))
        self.vet_h = farmos_headers(farmos_login(client, self.vet.email, FARMOS_DEMO_PASSWORD))
        self.worker_h = farmos_headers(farmos_login(client, self.worker.email, FARMOS_DEMO_PASSWORD))
        self.acct_h = farmos_headers(farmos_login(client, self.accountant.email, FARMOS_DEMO_PASSWORD))
        self.farm_id = str(tenant.id)

    # -- helpers --------------------------------------------------------
    def post(self, path, body=None, expect=201, h=None):
        return _post(self.client, h or self.h, path, body, expect)

    def get(self, path, params=None, h=None):
        return _get(self.client, h or self.h, path, params)

    def medicine(self, item_id: str) -> dict:
        return self.get(f"/pharmacy/medicines/{item_id}")

    def alerts(self, item_id: str | None = None, status: str = "open") -> list[dict]:
        return self.get("/pharmacy/alerts", {"status": status, "inventory_item_id": item_id})

    def receive(self, item_id: str, quantity: float, lot_code: str, days: float = 365, **extra) -> dict:
        return self.post(
            "/pharmacy/lots/receive",
            {
                "inventory_item_id": item_id,
                "quantity": quantity,
                "lot_code": lot_code,
                "expiry_date": _days(days),
                **extra,
            },
        )

    def animal(self, name: str, species: str = "cow", **fields) -> dict:
        return self.post("/animals", {"name": name, "species": species, "sex": "F", **fields})

    def observe(self, entity_type: str, entity_id: str, observation_type: str, h=None, **fields) -> dict:
        return self.post(
            "/observations",
            {
                "farm_id": self.farm_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "observation_type": observation_type,
                "observer_id": str(self.owner.id),
                **fields,
            },
            h=h,
        )

    def assess(self, subject_type: str, subject_id: str, signs: list[dict], expect=201) -> dict:
        return self.post(
            "/emergency/assessments",
            {"subject_type": subject_type, "subject_id": subject_id, "signs": signs},
            expect,
        )

    def start(self, assessment_id: str, expect=201) -> dict:
        return self.post(f"/emergency/assessments/{assessment_id}/start", {"confirmed": True}, expect)

    def confirm(self, run_id: str, step_id: str, expect=200, **body) -> dict:
        return self.post(f"/emergency/runs/{run_id}/steps/{step_id}/confirm", body, expect)

    def prepare(self, run_id: str, step_id: str, expect=200, **body) -> dict:
        return self.post(f"/emergency/runs/{run_id}/steps/{step_id}/prepare", body, expect)

    def tenant_session(self):
        return TenantDataRouter.session_for(self.tenant.id)

    # -- the farm -------------------------------------------------------
    def stock_pharmacy(self) -> "Farm":
        self.flunixin = self.post(
            "/pharmacy/medicines",
            {
                "name": "Flunixin meglumine 50 mg/mL",
                "unit": "ml",
                "generic_name": "flunixin meglumine",
                "dosage_form": "injectable",
                "strength_value": 50,
                "strength_uom": "mg/ml",
                "administration_routes": ["IM", "IV"],
                "prescription_required": True,
                "opened_shelf_life_days": 28,
                "species_codes": ["cow", "goat", "sheep", "horse"],
                "withdrawal_rules": {"milk_days": 4, "meat_days": 10},
                "category_codes": ["FEVER_SUPPORT", "PAIN_INFLAMMATION"],
                "ingredients": [
                    {"code": "flunixin_meglumine", "concentration_value": 50, "concentration_uom": "mg/ml"}
                ],
                "unit_cost": 0.8,
            },
        )["inventory_item_id"]
        self.oxytet = self.post(
            "/pharmacy/medicines",
            {
                "name": "Oxytetracycline 20%",
                "unit": "ml",
                "dosage_form": "injectable",
                "administration_routes": ["IM", "IV"],
                "prescription_required": True,
                "antimicrobial": True,
                "species_codes": ["cow", "goat", "sheep"],
                "withdrawal_rules": {"milk_days": 4, "meat_days": 28},
                "category_codes": ["ANTIBIOTIC"],
                "ingredients": [{"code": "oxytetracycline"}],
            },
        )["inventory_item_id"]
        self.nacl = self.post(
            "/pharmacy/medicines",
            {
                "name": "Sodium chloride 0.9%",
                "unit": "bag",
                "dosage_form": "intravenous_fluid",
                "administration_routes": ["IV"],
                "category_codes": ["IV_FLUID"],
                "ingredients": [{"code": "sodium_chloride"}],
                "unit_cost": 2.5,
            },
        )["inventory_item_id"]
        self.electrolyte = self.post(
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
        self.ivermectin = self.post(
            "/pharmacy/medicines",
            {
                "name": "Ivermectin pour-on",
                "unit": "ml",
                "dosage_form": "pour_on",
                "administration_routes": ["pour_on"],
                "species_codes": ["cow", "sheep"],
                "category_codes": ["ANTIPARASITIC"],
                "ingredients": [{"code": "ivermectin"}],
            },
        )["inventory_item_id"]
        _put(
            self.client,
            self.h,
            f"/pharmacy/policies/{self.flunixin}",
            {
                "essential": True,
                "minimum_stock_base": 40,
                "critical_stock_base": 10,
                "target_stock_base": 100,
            },
        )
        _put(self.client, self.h, f"/pharmacy/policies/{self.oxytet}", {"essential": True})
        _put(
            self.client,
            self.h,
            f"/pharmacy/policies/{self.nacl}",
            {"essential": True, "minimum_stock_base": 12, "critical_stock_base": 4, "target_stock_base": 24},
        )
        self.receive(self.flunixin, 48.5, "FLX-2611", 365, unit_cost=0.8)
        self.receive(self.oxytet, 60, "OXY-2602", -10, received_at=_days(-200))
        self.receive(self.oxytet, 100, "OXY-2605", 200)
        self.receive(self.nacl, 8, "NACL-2512", 400)
        self.receive(self.electrolyte, 25, "ELC-2606", 30)
        self.receive(self.ivermectin, 250, "IVM-2609", 500)
        return self

    def stock_animals(self) -> "Farm":
        self.bella = self.animal(
            "Bella", tag="COW-744", pregnant=True, lactating=True, management_profile="dairy"
        )
        self.luna = self.animal("Luna", tag="COW-214", management_profile="dairy")
        self.clover = self.animal("Clover", tag="COW-CLV", management_profile="dairy")
        self.rasha = self.animal("Rasha", species="goat", tag="GOAT-189")
        self.layers = self.post(
            "/animal-groups",
            {
                "name": "Layers",
                "species": "layer_hen",
                "count": 10,
                "sex_composition": "female",
                "group_type": "flock",
            },
        )
        return self

    def bovine_version_body(self, **overrides) -> dict:
        body = {
            "protocol_text": "Fever support for adult cattle.",
            "minimum_match_confidence": 0.6,
            "offline_eligible": True,
            "reassessment_minutes": 120,
            "trigger_rules": [
                {
                    "observation_code": "temperature_c",
                    "operator": ">=",
                    "threshold_numeric": 39.5,
                    "threshold_unit": "°C",
                    "weight": 2,
                    "label": "Rectal temperature",
                },
                {"observation_code": "fever", "operator": "present", "weight": 1},
                {
                    "observation_code": "appetite",
                    "operator": "==",
                    "expected_value_code": "reduced",
                    "weight": 1,
                },
                {
                    "observation_code": "rumination",
                    "operator": "==",
                    "expected_value_code": "reduced",
                    "weight": 1,
                },
                {
                    "observation_code": "temperature_c",
                    "operator": ">=",
                    "threshold_numeric": 41.5,
                    "threshold_unit": "°C",
                    "danger_sign": True,
                    "label": "Hyperthermia",
                },
            ],
            "eligibility_rules": [
                {
                    "rule_type": "species",
                    "rule_payload": {"in": ["cow"]},
                    "failure_action": "BLOCK",
                    "message": "Cattle only",
                },
                {
                    "rule_type": "pregnant",
                    "rule_payload": {"value": False},
                    "failure_action": "WARN",
                    "message": "Pregnant: the veterinarian should be told before dosing",
                },
            ],
            "steps": [
                {
                    "step_no": 1,
                    "step_type": "ASSESS",
                    "title": "Take temperature and hydration",
                    "instructions": "Rectal temperature, skin tent.",
                },
                {
                    "step_no": 2,
                    "step_type": "NON_DRUG_ACTION",
                    "title": "Shade and water",
                    "instructions": "Move to shade, fresh water.",
                },
                {
                    "step_no": 3,
                    "step_type": "MEDICATION",
                    "title": "Flunixin IM",
                    "instructions": "Per the approved rule.",
                    "medication": {
                        "medicine_product_id": self.flunixin,
                        "route_code": "IM",
                        "dose_rule_type": "PER_WEIGHT",
                        "dose_per_weight_quantity": 0.044,
                        "dose_unit": "ml",
                        "weight_unit": "kg",
                        "minimum_dose_quantity": 5,
                        "maximum_dose_quantity": 30,
                        "repeat_interval_minutes": 1440,
                        "maximum_administrations": 2,
                    },
                },
                {
                    "step_no": 4,
                    "step_type": "MEASUREMENT",
                    "title": "Record intake",
                    "instructions": "Water and feed intake.",
                },
                {
                    "step_no": 5,
                    "step_type": "REASSESS",
                    "title": "Reassess in two hours",
                    "instructions": "Re-take the signs.",
                    "timing_offset_minutes": 120,
                },
                {
                    "step_no": 6,
                    "step_type": "NOTIFY",
                    "title": "Tell the veterinarian the outcome",
                    "instructions": "Call.",
                    "required": False,
                },
            ],
        }
        body.update(overrides)
        return body

    def publish_protocols(self) -> "Farm":
        self.bovine = self.post(
            "/emergency/protocols",
            {
                "code": "BOVINE-FEVER-SUPPORT",
                "title": "Bovine fever support",
                "condition_family_code": "FEVER",
                "species_code": "cow",
                "version": self.bovine_version_body(),
            },
        )
        self.bovine_v1 = self.bovine["versions"][0]["id"]
        self.bovine = self.post(
            f"/emergency/protocols/{self.bovine['id']}/versions/{self.bovine_v1}/approve",
            {"approval_reference": "VET-2026-014"},
            200,
            h=self.vet_h,
        )
        poultry = self.post(
            "/emergency/protocols",
            {
                "code": "POULTRY-HEAT-STRESS",
                "title": "Poultry heat stress",
                "condition_family_code": "HEAT_STRESS",
                "species_code": "layer_hen",
                "subject_scope": "group",
                "version": {
                    "requires_vet_notification": False,
                    "offline_eligible": True,
                    "trigger_rules": [
                        {"observation_code": "panting", "operator": "present"},
                        {
                            "observation_code": "water_intake",
                            "operator": "==",
                            "expected_value_code": "reduced",
                        },
                        {
                            "observation_code": "ambient_temperature_c",
                            "operator": ">=",
                            "threshold_numeric": 32,
                        },
                    ],
                    "steps": [
                        {
                            "step_no": 1,
                            "step_type": "ASSESS",
                            "title": "Count panting birds",
                            "instructions": "Walk the house.",
                        },
                        {
                            "step_no": 2,
                            "step_type": "NON_DRUG_ACTION",
                            "title": "Ventilate",
                            "instructions": "Fans, misting.",
                        },
                        {
                            "step_no": 3,
                            "step_type": "MEDICATION",
                            "title": "Electrolytes in water",
                            "instructions": "One sachet per head.",
                            "medication": {
                                "medicine_product_id": self.electrolyte,
                                "route_code": "PO",
                                "dose_rule_type": "FIXED",
                                "fixed_dose_quantity": 1,
                                "dose_unit": "sachet",
                            },
                        },
                        {
                            "step_no": 4,
                            "step_type": "REASSESS",
                            "title": "Reassess in an hour",
                            "instructions": "Re-count.",
                            "timing_offset_minutes": 60,
                        },
                    ],
                },
            },
        )
        self.poultry = self.post(
            f"/emergency/protocols/{poultry['id']}/versions/{poultry['versions'][0]['id']}/approve",
            {"approval_reference": "VET-2026-015"},
            200,
            h=self.vet_h,
        )
        return self


def _farm(client, control_db, prefix: str) -> Farm:
    return Farm(client, control_db, prefix).stock_pharmacy().stock_animals().publish_protocols()


# ----------------------------------------------------------------- pharmacy
def test_pharmacy_dashboard_eligible_is_not_on_hand(client, control_db):
    """Acceptance 1 and 6: stock is kept with no patient in sight; expired
    and storage-exception lots do not count; one alert per condition."""
    f = Farm(client, control_db, "PH-DASH").stock_pharmacy()
    rows = {m["inventory_item_id"]: m for m in f.get("/pharmacy/medicines")}
    assert len(rows) == 5
    oxy = rows[f.oxytet]
    assert oxy["on_hand"] == 160 and oxy["eligible_available"] == 100 and oxy["stock"]["expired"] == 60
    assert oxy["status"] == "OK" and oxy["prescription_required"] and oxy["antimicrobial"]
    assert oxy["withdrawal_rules"] == {"milk_days": 4, "meat_days": 28}
    assert [c["code"] for c in oxy["categories"]] == ["ANTIBIOTIC"] and oxy["ingredients"][0][
        "code"
    ] == "oxytetracycline"
    nacl = rows[f.nacl]
    assert nacl["eligible_available"] == 8 and nacl["status"] == "LOW"
    assert nacl["policy"]["minimum_stock_base"] == 12 and nacl["recommended_reorder"] == 16
    assert rows[f.ivermectin]["policy"] is None and rows[f.ivermectin]["status"] == "OK"
    # Essential items first, worst status first.
    assert [m["inventory_item_id"] for m in f.get("/pharmacy/medicines")][0] == f.nacl

    by_item: dict[str, set[str]] = {}
    for a in f.alerts():
        by_item.setdefault(a["inventory_item_id"], set()).add(a["alert_type"])
    assert by_item == {
        f.nacl: {"BELOW_MINIMUM_STOCK"},
        f.oxytet: {"EXPIRED_STOCK"},
        f.electrolyte: {"EXPIRING_SOON"},
    }
    low = next(a for a in f.alerts() if a["inventory_item_id"] == f.nacl)
    assert (
        low["severity"] == "medium"
        and low["eligible_available_base"] == 8
        and low["recommended_reorder_base"] == 16
    )
    assert "not treatment advice" in low["explanation"] and low["route_code"] == "pharmacy_stock"
    # A second scan changes nothing: same rows, refreshed.
    ids = {a["id"] for a in f.alerts()}
    assert f.post("/pharmacy/evaluate", expect=200)["open_alerts"] == 3
    assert {a["id"] for a in f.alerts()} == ids
    s = f.get("/pharmacy/summary")
    assert s["medicines"] == 5 and s["essential"] == 3 and s["open_alerts"] == 3 and s["unseen_alerts"] == 3
    assert s["by_status"] == {"OK": 4, "LOW": 1, "CRITICAL": 0, "OUT": 0} and s["expiring_soon"] == 1
    codes = {c["code"] for c in f.get("/pharmacy/categories")}
    assert {"FEVER_SUPPORT", "IV_FLUID", "COLIC_SUPPORT", "VACCINE"} <= codes
    assert "oxytetracycline" in {i["code"] for i in f.get("/pharmacy/ingredients")}
    bell = f.get("/notifications", {"unread_only": True})["notifications"]
    assert sum(1 for n in bell if n["source_type"] == "pharmacy_stock_alert") == 3
    # Another farm sees none of it (RLS).
    other = Farm(client, control_db, "PH-OTHER")
    assert other.get("/pharmacy/medicines") == [] and other.alerts() == []
    r = client.get(f"{API}/pharmacy/medicines/{f.oxytet}", headers=other.h)
    assert r.status_code == 404


def test_receipts_lots_and_eligibility(client, control_db):
    f = Farm(client, control_db, "PH-LOT").stock_pharmacy()
    r = client.post(
        f"{API}/pharmacy/lots/receive",
        json={"inventory_item_id": f.nacl, "quantity": 12, "lot_code": "NACL-2612"},
        headers=f.h,
    )
    assert r.status_code == 422 and "expiry" in r.json()["detail"]
    r = client.post(
        f"{API}/pharmacy/lots/receive",
        json={"inventory_item_id": f.nacl, "quantity": 12, "expiry_date": _days(365)},
        headers=f.h,
    )
    assert r.status_code == 422 and "lot number" in r.json()["detail"]
    lot = f.receive(
        f.nacl, 12, "NACL-2612", 365, unit_cost=2.5, supplier_label="MedSupply Beirut", reference="INV-1042"
    )
    assert lot["lot_code"] == "NACL-2612" and lot["quantity_on_hand"] == 12 and lot["eligible"]
    assert lot["storage_status"] == "COMPLIANT" and lot["unit"] == "bag"
    with f.tenant_session() as db:
        moves = db.scalars(
            select(InventoryMovement).where(InventoryMovement.lot_id == uuid.UUID(lot["id"]))
        ).all()
        assert len(moves) == 1 and float(moves[0].quantity_delta) == 12 and moves[0].reason == "purchase"
        assert (
            float(db.get(InventoryItem, uuid.UUID(f.nacl)).current_qty) == 20
        ), "item balance and lots agree"
    # 20 bags ≥ minimum 12: the shortage alert resolved itself.
    m = f.medicine(f.nacl)
    assert m["eligible_available"] == 20 and m["status"] == "OK" and m["open_alerts"] == []
    resolved = next(a for a in f.alerts(f.nacl, "all") if a["alert_type"] == "BELOW_MINIMUM_STOCK")
    assert resolved["status"] == "resolved" and "above the threshold" in resolved["resolution_note"]

    # Quarantine, recall and release change eligibility, not on hand.
    oxy_lot = next(x for x in f.medicine(f.oxytet)["lots"] if x["lot_code"] == "OXY-2605")
    r = client.patch(
        f"{API}/pharmacy/lots/{oxy_lot['id']}/status", json={"status": "quarantined"}, headers=f.h
    )
    assert r.status_code == 422, "taking a lot out of use needs a reason"
    out = _patch(
        client,
        f.h,
        f"/pharmacy/lots/{oxy_lot['id']}/status",
        {"status": "quarantined", "reason": "Discoloured"},
    )
    assert out["ineligible_reason"] == "quarantined" and out["quarantine_reason"] == "Discoloured"
    m = f.medicine(f.oxytet)
    assert m["on_hand"] == 160 and m["eligible_available"] == 0 and m["status"] == "OUT"
    assert _types(f.alerts(f.oxytet)) == {"EXPIRED_STOCK", "OUT_OF_STOCK"}
    out = _patch(
        client,
        f.h,
        f"/pharmacy/lots/{oxy_lot['id']}/status",
        {"status": "recalled", "reason": "Manufacturer recall", "recall_reference": "RC-77"},
    )
    assert out["recall_reference"] == "RC-77" and "RECALL_AFFECTED" in _types(f.alerts(f.oxytet))
    out = _patch(
        client,
        f.h,
        f"/pharmacy/lots/{oxy_lot['id']}/status",
        {"status": "active", "reason": "Cleared by the vet"},
    )
    assert out["eligible"]
    assert _types(f.alerts(f.oxytet)) == {
        "EXPIRED_STOCK"
    }, "release resolves the out-of-stock and recall findings"

    # An opened container stops counting when its after-opening use-by passes.
    flx = f.medicine(f.flunixin)["lots"][0]
    out = f.post(f"/pharmacy/lots/{flx['id']}/open", {"opened_at": _days(-30)}, 200)
    assert out["opened_at"] and out["use_by_after_opening"] and out["ineligible_reason"] == "expired"
    m = f.medicine(f.flunixin)
    assert m["eligible_available"] == 0 and m["stock"]["expired"] == 48.5 and m["status"] == "OUT"

    # A storage exception is cleared by a person.
    cold = f.receive(f.flunixin, 20, "FLX-2701", 300, storage_status="EXCEPTION", cold_chain_exception=True)
    assert not cold["eligible"] and cold["ineligible_reason"] == "storage_exception"
    assert "STORAGE_EXCEPTION" in _types(f.alerts(f.flunixin))
    out = _patch(
        client,
        f.h,
        f"/pharmacy/lots/{cold['id']}/storage",
        {"storage_status": "COMPLIANT", "cold_chain_exception": False, "note": "Vet confirmed potency"},
    )
    assert out["eligible"] and "STORAGE_EXCEPTION" not in _types(f.alerts(f.flunixin))

    # An adjustment needs an explanation and cannot go negative.
    ivm = f.medicine(f.ivermectin)["lots"][0]
    body = {
        "inventory_item_id": f.ivermectin,
        "lot_id": ivm["id"],
        "delta": -10,
        "reason": "waste",
        "explanation": "",
    }
    assert client.post(f"{API}/pharmacy/adjustments", json=body, headers=f.h).status_code == 422
    r = client.post(
        f"{API}/pharmacy/adjustments", json={**body, "delta": -300, "explanation": "Spilled"}, headers=f.h
    )
    assert r.status_code == 422 and "negative" in r.json()["detail"]
    out = f.post("/pharmacy/adjustments", {**body, "explanation": "Spilled"})
    assert out["lot"]["quantity_on_hand"] == 240 and out["medicine"]["eligible_available"] == 240

    # Transfers move part of a lot between locations as a paired OUT / IN.
    out = f.post(
        "/pharmacy/transfers",
        {"inventory_item_id": f.ivermectin, "lot_id": ivm["id"], "quantity": 40, "to_location_id": "barn-b"},
    )
    assert out["quantity"] == 40 and out["correlation_id"] and out["lot"]["quantity_on_hand"] == 240
    assert {b["location_id"]: b["quantity"] for b in out["lot"]["location_balances"]} == {
        None: 200,
        "barn-b": 40,
    }
    assert f.medicine(f.ivermectin)["eligible_available"] == 240, "on hand is unchanged by a transfer"

    # Reservations hold eligible stock for a purpose.
    res = f.post(
        "/pharmacy/reservations",
        {
            "inventory_item_id": f.ivermectin,
            "quantity": 200,
            "purpose_type": "planned_procedure",
            "purpose_label": "Autumn drench",
        },
    )
    assert res["remaining"] == 200 and res["status"] == "active"
    assert (
        f.medicine(f.ivermectin)["eligible_available"] == 40 and f.medicine(f.ivermectin)["reserved"] == 200
    )
    r = client.post(
        f"{API}/pharmacy/reservations",
        json={"inventory_item_id": f.ivermectin, "quantity": 50, "purpose_type": "other"},
        headers=f.h,
    )
    assert r.status_code == 422 and "eligible and unreserved" in r.json()["detail"]
    out = f.post(f"/pharmacy/reservations/{res['id']}/release", {"reason": "Drench postponed"}, 200)
    assert out["status"] == "released" and f.medicine(f.ivermectin)["eligible_available"] == 240


def test_thresholds_drive_distinct_deduplicated_alerts(client, control_db):
    """Acceptance 3, 4, 5: the manager sets the levels; LOW, CRITICAL and
    OUT are distinct; one open alert per condition; a worker cannot set
    policy."""
    f = Farm(client, control_db, "PH-POL").stock_pharmacy()
    r = client.put(
        f"{API}/pharmacy/policies/{f.ivermectin}",
        json={"essential": True, "minimum_stock_base": 5},
        headers=f.worker_h,
    )
    assert r.status_code == 403
    out = _put(
        client,
        f.h,
        f"/pharmacy/policies/{f.ivermectin}",
        {"essential": True, "minimum_stock_base": 300, "critical_stock_base": 100, "target_stock_base": 600},
    )
    assert out["policy"]["version"] == 1 and out["medicine"]["status"] == "LOW"
    assert _types(f.alerts(f.ivermectin)) == {"BELOW_MINIMUM_STOCK"}
    low_id = f.alerts(f.ivermectin)[0]["id"]
    lot = f.medicine(f.ivermectin)["lots"][0]

    def waste(qty: float) -> None:
        f.post(
            "/pharmacy/adjustments",
            {
                "inventory_item_id": f.ivermectin,
                "lot_id": lot["id"],
                "delta": -qty,
                "reason": "waste",
                "explanation": "Leaking container",
            },
        )

    waste(160)  # 90 left ≤ critical 100
    open_now = f.alerts(f.ivermectin)
    assert _types(open_now) == {"CRITICAL_STOCK"} and open_now[0]["severity"] == "high"
    assert next(a for a in f.alerts(f.ivermectin, "all") if a["id"] == low_id)["status"] == "resolved"
    waste(90)  # nothing eligible, essential → OUT
    assert _types(f.alerts(f.ivermectin)) == {"OUT_OF_STOCK"}
    out_alert = f.alerts(f.ivermectin)[0]
    assert f.post(f"/pharmacy/alerts/{out_alert['id']}/acknowledge", expect=200)["status"] == "acknowledged"
    f.post("/pharmacy/evaluate", expect=200)
    again = f.alerts(f.ivermectin)
    assert [a["id"] for a in again] == [out_alert["id"]] and again[0]["status"] == "acknowledged"
    bell = f.get("/notifications", {"unread_only": True})["notifications"]
    assert not any(n["source_id"] == out_alert["id"] for n in bell), "acknowledged alerts leave the bell"
    # A receipt above the minimum resolves it.
    f.receive(f.ivermectin, 500, "IVM-2701", 500)
    assert f.alerts(f.ivermectin) == [] and f.medicine(f.ivermectin)["status"] == "OK"
    # Policy sanity: critical above minimum is refused.
    r = client.put(
        f"{API}/pharmacy/policies/{f.ivermectin}",
        json={"essential": True, "minimum_stock_base": 10, "critical_stock_base": 50},
        headers=f.h,
    )
    assert r.status_code == 422
    assert len(f.get("/pharmacy/policies")) == 4


def test_a_requisition_is_one_draft_never_an_order(client, control_db):
    f = Farm(client, control_db, "PH-REQ").stock_pharmacy()
    low = next(a for a in f.alerts(f.nacl) if a["alert_type"] == "BELOW_MINIMUM_STOCK")
    first = f.post(f"/pharmacy/alerts/{low['id']}/requisition", expect=200)
    assert first["created"] and first["status"] == "draft" and "16 bag" in first["title"]
    assert first["alert"]["requisition_task_id"] == first["task_id"]
    req = first["requisition"]
    assert req["requisition_no"].startswith("REQ-") and req["purpose_code"] == "pharmacy_replenishment"
    assert req["source_reference_id"] == low["id"] and req["requested_by_name"] == "Rami Owner"
    assert req["lines"][0]["inventory_item_id"] == f.nacl and req["lines"][0]["requested_quantity"] == 16
    assert req["lines"][0]["uom_code"] == "bag" and req["total_estimated_amount"] == 40
    again = f.post(f"/pharmacy/alerts/{low['id']}/requisition", expect=200)
    assert (
        again["requisition_id"] == req["id"] and again["task_id"] == first["task_id"] and not again["created"]
    )
    with f.tenant_session() as db:
        task = db.get(Task, uuid.UUID(first["task_id"]))
        assert task.source_type == "pharmacy_requisition" and "approve" in task.description.lower()
        assert task.assigned_to == f.owner.id and task.priority == "medium"
    assert f.medicine(f.nacl)["open_requisition"]
    # The draft is a procurement document: submitted and approved there,
    # never from the pharmacy; a worker lacks the authority.
    assert (
        client.post(
            f"{API}/procurement/requisitions/{req['id']}/approve", json={}, headers=f.worker_h
        ).status_code
        == 403
    )
    out = f.post(f"/procurement/requisitions/{req['id']}/submit", expect=200)
    assert out["status"] == "submitted"
    out = f.post(f"/procurement/requisitions/{req['id']}/approve", {"comments": "ok"}, 200)
    assert out["status"] == "approved" and out["approvals"][0]["authority"]["basis"] == "role"
    m = f.medicine(f.nacl)
    assert m["confirmed_incoming"] == 16 and m["recommended_reorder"] == 0 and m["status"] == "LOW"
    assert [r["requisition_no"] for r in f.get("/procurement/requisitions", {"status": "open"})] == [
        req["requisition_no"]
    ]
    # Delivery against the requisition closes it and clears the shortage.
    lot = f.receive(
        f.nacl,
        16,
        "NACL-2701",
        400,
        supplier_lot_code="MS-88211",
        source_reference_type="purchase_requisition",
        source_reference_id=req["id"],
    )
    assert lot["supplier_lot_code"] == "MS-88211" and lot["source_reference_id"] == req["id"]
    assert f.get(f"/procurement/requisitions/{req['id']}")["status"] == "received"
    m = f.medicine(f.nacl)
    assert m["status"] == "OK" and m["confirmed_incoming"] == 0 and not m["open_requisition"]
    assert next(a for a in f.alerts(f.nacl, "all") if a["id"] == low["id"])["status"] == "resolved"
    # A manual close needs a reason, and only an open condition can be closed.
    other = next(a for a in f.alerts(f.oxytet) if a["alert_type"] == "EXPIRED_STOCK")
    assert (
        client.post(
            f"{API}/pharmacy/alerts/{other['id']}/resolve", json={"note": " "}, headers=f.h
        ).status_code
        == 422
    )
    assert (
        f.post(f"/pharmacy/alerts/{other['id']}/resolve", {"note": "Expired lot disposed"}, 200)["status"]
        == "resolved"
    )
    r = client.post(f"{API}/pharmacy/alerts/{low['id']}/resolve", json={"note": "again"}, headers=f.h)
    assert r.status_code == 422 and "already resolved" in r.json()["detail"]
    # A manual requisition, rejected with a reason.
    manual = f.post(
        "/procurement/requisitions",
        {
            "lines": [{"inventory_item_id": f.oxytet, "requested_quantity": 500}],
            "justification": "Winter stock",
        },
    )
    assert manual["status"] == "draft" and manual["lines"][0]["uom_code"] == "ml"
    assert (
        client.post(
            f"{API}/procurement/requisitions/{manual['id']}/reject", json={"comments": ""}, headers=f.h
        ).status_code
        == 422
    )
    assert (
        f.post(f"/procurement/requisitions/{manual['id']}/reject", {"comments": "Too much"}, 200)["status"]
        == "rejected"
    )


def test_a_dose_consumes_the_exact_lot_and_applies_the_withdrawal(client, control_db):
    """Acceptance 7 and 9: stock never authorises use; the exact lot is
    consumed; the withdrawal comes from the product's rule; the threshold
    is re-checked immediately; ineligible stock is refused."""
    f = Farm(client, control_db, "PH-ADM").stock_pharmacy().stock_animals()
    bella = f.bella["id"]
    body = {
        "subject_type": "animal",
        "subject_id": bella,
        "inventory_item_id": f.flunixin,
        "dose_quantity": 12,
        "dose_unit": "ml",
        "route_code": "IM",
    }
    r = client.post(f"{API}/pharmacy/administrations", json=body, headers=f.h)
    assert r.status_code == 422 and "prescription" in r.json()["detail"], r.text
    assert (
        client.post(
            f"{API}/health/treatments",
            json={
                "entity_type": "animal",
                "entity_id": bella,
                "medication": "Flunixin",
                "dose": "2.2 mg/kg",
                "route": "IM",
                "responsible_user_id": str(f.worker.id),
            },
            headers=f.worker_h,
        ).status_code
        == 403
    ), "a worker does not prescribe"
    treatment = f.post(
        "/health/treatments",
        {
            "entity_type": "animal",
            "entity_id": bella,
            "medication": "Flunixin meglumine 50 mg/mL",
            "dose": "2.2 mg/kg",
            "route": "IM",
            "responsible_user_id": str(f.vet.id),
            "diagnosis": "Fever",
            "medicine_product_id": f.flunixin,
        },
        h=f.vet_h,
    )
    assert treatment["medicine_product_id"] == f.flunixin
    adm = f.post(
        "/pharmacy/administrations",
        {**body, "treatment_id": treatment["id"], "reason": "Fever 40.1 °C"},
        h=f.worker_h,
    )
    assert adm["lot_code"] == "FLX-2611" and adm["quantity_consumed"] == 12 and adm["unit"] == "ml"
    assert (
        adm["administered_by_name"] == "Karim Worker"
        and adm["withdrawal_milk_until"]
        and adm["withdrawal_meat_until"]
    )
    with f.tenant_session() as db:
        animal = db.get(Animal, uuid.UUID(bella))
        assert animal.withdrawal_until is not None and animal.withdrawal_reason.startswith(
            "Medication: Flunixin"
        )
        until = (
            animal.withdrawal_until
            if animal.withdrawal_until.tzinfo
            else animal.withdrawal_until.replace(tzinfo=timezone.utc)
        )
        assert abs((until - datetime.now(timezone.utc)) - timedelta(days=10)) < timedelta(minutes=5)
        mv = db.get(InventoryMovement, uuid.UUID(adm["inventory_transaction_id"]))
        assert (
            float(mv.quantity_delta) == -12
            and mv.reason == "administration"
            and str(mv.lot_id) == adm["inventory_lot_id"]
        )
        assert float(db.get(InventoryItem, uuid.UUID(f.flunixin)).current_qty) == 36.5
    m = f.medicine(f.flunixin)
    assert m["eligible_available"] == 36.5 and m["on_hand"] == 36.5 and m["status"] == "LOW"
    assert _types(f.alerts(f.flunixin)) == {
        "BELOW_MINIMUM_STOCK",
        "EXPIRING_SOON",
    }, "re-evaluated at once; the vial is open"
    history = f.get("/pharmacy/administrations", {"subject_id": bella})
    assert [a["id"] for a in history] == [adm["id"]] and history[0]["treatment_id"] == treatment["id"]
    assert client.get(f"{API}/pharmacy/administrations", headers=f.acct_h).status_code == 403
    # Reversal puts the quantity back on the same lot; the withdrawal stands.
    out = f.post(
        f"/pharmacy/administrations/{adm['id']}/reverse", {"reason": "Entered against the wrong cow"}
    )
    assert out["status"] == "reversed" and f.medicine(f.flunixin)["eligible_available"] == 48.5
    with f.tenant_session() as db:
        assert db.get(Animal, uuid.UUID(bella)).withdrawal_until is not None

    # Species restriction, like a feed usage policy.
    r = client.post(
        f"{API}/pharmacy/administrations",
        json={
            "subject_type": "group",
            "subject_id": f.layers["id"],
            "inventory_item_id": f.ivermectin,
            "dose_quantity": 1,
            "dose_unit": "ml",
            "route_code": "pour_on",
        },
        headers=f.h,
    )
    assert r.status_code == 422 and "not authorised for" in r.json()["detail"]
    # An expired lot named explicitly; FEFO never picks it and never over-draws; the route must be authorised.
    expired = next(x for x in f.medicine(f.oxytet)["lots"] if x["lot_code"] == "OXY-2602")
    tid = f.post(
        "/health/treatments",
        {
            "entity_type": "animal",
            "entity_id": bella,
            "medication": "Oxytetracycline",
            "dose": "20 mg/kg",
            "route": "IM",
            "responsible_user_id": str(f.owner.id),
            "medicine_product_id": f.oxytet,
        },
    )["id"]
    oxy = {
        "subject_type": "animal",
        "subject_id": bella,
        "inventory_item_id": f.oxytet,
        "dose_unit": "ml",
        "route_code": "IM",
        "treatment_id": tid,
    }
    r = client.post(
        f"{API}/pharmacy/administrations",
        json={**oxy, "lot_id": expired["id"], "dose_quantity": 10},
        headers=f.h,
    )
    assert r.status_code == 422 and "expired" in r.json()["detail"]
    r = client.post(f"{API}/pharmacy/administrations", json={**oxy, "dose_quantity": 150}, headers=f.h)
    assert r.status_code == 422 and "eligible 100" in r.json()["detail"]
    assert f.post("/pharmacy/administrations", {**oxy, "dose_quantity": 30})["lot_code"] == "OXY-2605"
    r = client.post(
        f"{API}/pharmacy/administrations", json={**oxy, "dose_quantity": 1, "route_code": "PO"}, headers=f.h
    )
    assert r.status_code == 422 and "authorised for IM, IV" in r.json()["detail"]
    # The prescription names the product: a dose of another medicine under it is refused.
    r = client.post(
        f"{API}/pharmacy/administrations",
        json={**oxy, "inventory_item_id": f.flunixin, "dose_quantity": 1},
        headers=f.h,
    )
    assert r.status_code == 422 and "prescribes" in r.json()["detail"]
    # A flock dose: head count × fixed dose, no withdrawal rule → none applied.
    ducks = f.post(
        "/pharmacy/administrations",
        {
            "subject_type": "group",
            "subject_id": f.layers["id"],
            "inventory_item_id": f.electrolyte,
            "dose_quantity": 1,
            "dose_unit": "sachet",
            "route_code": "PO",
            "head_count": 5,
        },
    )
    assert (
        ducks["head_count"] == 5
        and ducks["quantity_consumed"] == 5
        and ducks["withdrawal_milk_until"] is None
    )
    assert f.medicine(f.electrolyte)["on_hand"] == 20 and f.medicine(f.electrolyte)["average_daily_use"] > 0


# ---------------------------------------------------------------- emergency
def test_a_draft_is_not_operational_and_only_a_veterinarian_approves(client, control_db):
    f = Farm(client, control_db, "EM-APP").stock_pharmacy().stock_animals()
    draft = f.post(
        "/emergency/protocols",
        {
            "code": "BOVINE-FEVER-SUPPORT",
            "title": "Bovine fever support",
            "condition_family_code": "FEVER",
            "species_code": "cow",
            "version": f.bovine_version_body(),
        },
    )
    assert (
        draft["status"] == "draft"
        and draft["current_version"] is None
        and draft["versions"][0]["current"] is False
    )
    vid = draft["versions"][0]["id"]
    # Nothing is operational: a fever case escalates, no drug is shown.
    a = f.assess(
        "animal", f.luna["id"], [{"code": "temperature_c", "value_numeric": 40.4}, {"code": "fever"}]
    )
    assert a["status"] == "escalated" and a["matches"] == []
    assert any("no current veterinarian-approved protocol" in x for x in a["escalation_reasons"])
    assert "No drug, dose or route is shown" in a["explanation"]
    r = client.post(
        f"{API}/emergency/protocols/{draft['id']}/versions/{vid}/approve",
        json={"approval_reference": "MGR-1"},
        headers=f.h,
    )
    assert r.status_code == 403, "a manager may draft, not approve"
    assert (
        client.post(
            f"{API}/emergency/protocols/{draft['id']}/versions/{vid}/approve",
            json={"approval_reference": "X"},
            headers=f.worker_h,
        ).status_code
        == 403
    )
    out = f.post(
        f"/emergency/protocols/{draft['id']}/versions/{vid}/approve",
        {"approval_reference": "VET-2026-016"},
        200,
        h=f.vet_h,
    )
    assert out["status"] == "active" and out["current_version"]["approval_reference"] == "VET-2026-016"
    assert (
        out["current_version"]["approved_by_name"] == "Dr. Layla Haddad" and out["current_version"]["current"]
    )
    med = next(s for s in out["current_version"]["steps"] if s["step_type"] == "MEDICATION")["medication"]
    assert (
        med["medicine_name"].startswith("Flunixin")
        and med["dose_rule_type"] == "PER_WEIGHT"
        and med["maximum_dose_quantity"] == 30
    )
    r = client.post(
        f"{API}/emergency/protocols/{draft['id']}/versions/{vid}/approve",
        json={"approval_reference": "VET-2026-017"},
        headers=f.vet_h,
    )
    assert r.status_code == 422, "approval provenance is written once"
    # A medication step must name an authorised route and a complete dose rule.
    base = {
        "code": "TEST-1",
        "title": "Test",
        "condition_family_code": "TEST",
        "species_code": "cow",
        "version": {
            "trigger_rules": [{"observation_code": "fever", "operator": "present"}],
            "steps": [
                {
                    "step_no": 1,
                    "step_type": "MEDICATION",
                    "title": "x",
                    "instructions": "x",
                    "medication": {
                        "medicine_product_id": f.flunixin,
                        "route_code": "PO",
                        "dose_rule_type": "FIXED",
                        "fixed_dose_quantity": 5,
                        "dose_unit": "ml",
                    },
                }
            ],
        },
    }
    r = client.post(f"{API}/emergency/protocols", json=base, headers=f.h)
    assert r.status_code == 422 and "not an authorised route" in r.json()["detail"]
    base["version"]["steps"][0]["medication"] = {
        "medicine_product_id": f.flunixin,
        "route_code": "IM",
        "dose_rule_type": "PER_WEIGHT",
        "dose_unit": "ml",
    }
    r = client.post(f"{API}/emergency/protocols", json=base, headers=f.h)
    assert r.status_code == 422 and "PER_WEIGHT" in r.json()["detail"]
    base["version"]["steps"][0]["medication"] = {
        "medicine_product_id": f.flunixin,
        "route_code": "IM",
        "dose_rule_type": "FIXED",
        "fixed_dose_quantity": 5,
        "dose_unit": "cup",
    }
    r = client.post(f"{API}/emergency/protocols", json=base, headers=f.h)
    assert r.status_code == 422 and "Unknown dose unit" in r.json()["detail"]
    base["version"]["steps"][0]["medication"]["dose_unit"] = "ml"
    assert f.post("/emergency/protocols", base)["status"] == "draft"
    assert (
        client.post(
            f"{API}/emergency/protocols", json={**base, "code": "TEST-2"}, headers=f.worker_h
        ).status_code
        == 403
    )
    assert len(f.get("/emergency/protocols")) == 2


def test_triage_matches_or_escalates_with_an_explanation(client, control_db):
    f = _farm(client, control_db, "EM-TRI")
    # The morning's fever observation counts as evidence.
    f.observe("animal", f.bella["id"], "fever", severity="moderate", notes="Hot, dull", h=f.worker_h)
    bella = f.assess(
        "animal",
        f.bella["id"],
        [{"code": "temperature_c", "value_numeric": 40.1}, {"code": "appetite", "value_text": "reduced"}],
    )
    assert bella["status"] == "open" and bella["triage_level"] == "HIGH" and bella["selected_match_id"]
    assert bella["ai_confidence"] == 0.8 and bella["ai_model_reference"] == "origami.emergency.deterministic"
    match = next(m for m in bella["matches"] if m["selected"])
    assert match["protocol_code"] == "BOVINE-FEVER-SUPPORT" and match["eligibility_result"] == "WARN"
    supporting = {e["code"] for e in match["explanation"]["supporting"]}
    assert {"temperature_c", "fever", "appetite"} <= supporting
    assert any(e["source"] == "observation" for e in match["explanation"]["supporting"])
    assert "rumination" in {e["code"] for e in match["explanation"]["missing"]}
    assert any("Pregnant" in w for w in match["explanation"]["warnings"])
    assert "Confirm to start the protocol" in bella["explanation"] and "VET-2026-014" in bella["explanation"]
    assert bella["animal_facts"]["species"] == "cow" and bella["animal_facts"]["pregnant"] is True
    assert bella["health_case_id"] is None, "a match awaiting confirmation has no case yet"

    # A goat has no approved protocol: escalate, task to the vet, case open.
    rasha = f.assess(
        "animal", f.rasha["id"], [{"code": "temperature_c", "value_numeric": 40.6}, {"code": "fever"}]
    )
    assert rasha["status"] == "escalated" and rasha["selected_match_id"] is None and rasha["matches"] == []
    assert any("no current veterinarian-approved protocol" in x for x in rasha["escalation_reasons"])
    assert "ESCALATE — contact the veterinarian" in rasha["explanation"] and rasha["health_case_id"]
    with f.tenant_session() as db:
        task = db.get(Task, uuid.UUID(rasha["escalation_task_id"]))
        assert task.status == "open" and task.priority == "high" and task.title.startswith("EMERGENCY")
        assert task.assigned_to == f.vet.id, "the escalation is routed to the veterinarian"
    case = f.get(f"/health/cases/{rasha['health_case_id']}")
    assert (
        case["source_type"] == "emergency_assessment"
        and case["severity_code"] == "moderate"
        and case["active_diagnoses"] == []
    )
    bell = f.get("/notifications", {"unread_only": True})["notifications"]
    kinds = {n["notification_type"] for n in bell if n["source_type"] == "emergency_assessment"}
    assert kinds == {"emergency_escalated", "emergency_protocol_matched"}
    s = f.get("/emergency/summary")
    assert (
        s["protocols"] == 2
        and s["active_protocols"] == 2
        and s["open_assessments"] == 1
        and s["escalated"] == 1
    )
    rows = {a["subject_id"]: a for a in f.get("/emergency/assessments", {"status": "active"})}
    assert set(rows) == {f.bella["id"], f.rasha["id"]}

    # A danger sign escalates immediately.
    a = f.assess(
        "animal",
        f.luna["id"],
        [
            {"code": "temperature_c", "value_numeric": 41.8},
            {"code": "appetite", "value_text": "reduced"},
            {"code": "fever"},
        ],
    )
    assert a["status"] == "escalated" and a["triage_level"] == "CRITICAL" and a["selected_match_id"] is None
    assert any("danger sign" in x and "41.5" in x for x in a["escalation_reasons"])
    f.start(a["id"], 422)

    # A severe observation starts triage by itself; one sign alone is below the threshold.
    f.observe("animal", f.clover["id"], "fever", severity="severe", h=f.worker_h)
    hooked = [
        x
        for x in f.get("/emergency/assessments", {"subject_id": f.clover["id"]})
        if x["source"] == "observation_hook"
    ]
    assert len(hooked) == 1 and hooked[0]["status"] == "escalated" and hooked[0]["triage_level"] == "HIGH"
    assert 0 < hooked[0]["ai_confidence"] < 0.5
    assert any(
        "confidence threshold" in x and "BOVINE-FEVER-SUPPORT" in x for x in hooked[0]["escalation_reasons"]
    )
    f.observe("animal", f.clover["id"], "fever", severity="severe", h=f.worker_h)
    assert (
        len(
            [
                x
                for x in f.get("/emergency/assessments", {"subject_id": f.clover["id"]})
                if x["source"] == "observation_hook"
            ]
        )
        == 1
    )

    # Species and scope filter the candidates; the offline package carries only offline-eligible versions.
    a = f.assess(
        "group",
        f.layers["id"],
        [
            {"code": "panting"},
            {"code": "water_intake", "value_text": "reduced"},
            {"code": "ambient_temperature_c", "value_numeric": 34},
        ],
    )
    assert a["status"] == "open" and [m["protocol_code"] for m in a["matches"]] == ["POULTRY-HEAT-STRESS"]
    pkg = f.get("/emergency/offline-package")
    assert {v["protocol_code"] for v in pkg["versions"]} == {"BOVINE-FEVER-SUPPORT", "POULTRY-HEAT-STRESS"}
    assert (
        len(pkg["signature"]) == 64
        and all(v["current"] for v in pkg["versions"])
        and pkg["farm_id"] == f.farm_id
    )
    assert {m["medicine_product_id"] for m in pkg["medicines"]} == {f.flunixin, f.electrolyte}
    assert pkg["medicines"][0]["eligible_lots"]
    codes = {c["code"] for c in f.get("/emergency/sign-codes")}
    assert {"temperature_c", "panting", "ambient_temperature_c", "mortality_count"} <= codes
    assert client.get(f"{API}/emergency/summary", headers=f.acct_h).status_code == 403


def test_the_approved_dose_the_exact_lot_the_withdrawal_and_the_reassessment(client, control_db):
    f = _farm(client, control_db, "EM-RUN")
    bella = f.assess(
        "animal",
        f.bella["id"],
        [
            {"code": "temperature_c", "value_numeric": 40.1},
            {"code": "appetite", "value_text": "reduced"},
            {"code": "fever"},
        ],
    )
    run = f.start(bella["id"])
    assert (
        run["status"] == "in_progress"
        and len(run["steps"]) == 6
        and run["manager_notified_at"]
        and run["veterinarian_notified_at"]
    )
    assert (
        run["next_reassessment_at"] is not None
        and run["health_case_id"]
        and run["started_by_name"] == "Rami Owner"
    )
    with f.tenant_session() as db:
        notify = db.scalars(
            select(Task).where(Task.source_type == "emergency_notification", Task.source_id == run["id"])
        ).all()
        assert len(notify) == 2 and {t.assigned_to for t in notify} == {f.owner.id, f.vet.id}
    assert f.get(f"/emergency/assessments/{bella['id']}")["status"] == "protocol_started"
    case = f.get(f"/health/cases/{run['health_case_id']}")
    assert (
        case["source_type"] == "emergency_run"
        and case["primary_problem_code"] == "FEVER"
        and case["severity_code"] == "high"
    )

    run = f.confirm(run["id"], _step(run, 1)["id"], result={"temperature_c": 40.3, "skin_tent_seconds": 3})
    run = f.confirm(run["id"], _step(run, 2)["id"])
    assert _step(run, 1)["status"] == "completed" and _step(run, 1)["result"]["temperature_c"] == 40.3

    # The medication step: dose from the approved rule and the entered weight, the exact eligible lot.
    step3 = _step(run, 3)
    prepared = _step(f.prepare(run["id"], step3["id"], weight_kg=620), 3)
    assert prepared["status"] == "presented"
    p = prepared["result"]
    assert (
        p["dose_quantity"] == 27.28
        and p["dose_unit"] == "ml"
        and p["route_code"] == "IM"
        and p["lot_code"] == "FLX-2611"
    )
    assert (
        p["weight_used_kg"] == 620
        and p["weight_source"] == "entered now"
        and p["approved_min"] == 5
        and p["approved_max"] == 30
    )
    assert p["problems"] == [] and p["source"] == "approved protocol parameters"
    weights = f.get(f"/animals/{f.bella['id']}/weights")
    assert (
        weights[0]["weight_kg"] == 620
        and weights[0]["method"] == "point_of_care"
        and weights[0]["source_type"] == "emergency_run_step"
    )
    run = f.confirm(run["id"], step3["id"], notes="Given left neck")
    done = _step(run, 3)
    assert (
        done["status"] == "completed"
        and done["medication_administration_id"]
        and done["result"]["treatment_id"]
    )
    with f.tenant_session() as db:
        assert float(db.get(InventoryItem, uuid.UUID(f.flunixin)).current_qty) == round(48.5 - 27.28, 2)
        treatment = db.get(Treatment, uuid.UUID(done["result"]["treatment_id"]))
        assert treatment.vet_id == f.vet.id and "VET-2026-014" in treatment.notes and treatment.route == "IM"
        assert (
            str(treatment.health_case_id) == run["health_case_id"] and treatment.treatment_plan_id is not None
        )
        animal = db.get(Animal, uuid.UUID(f.bella["id"]))
        assert animal.withdrawal_until is not None and animal.withdrawal_reason.startswith("Medication")
    adm = f.get("/pharmacy/administrations", {"subject_id": f.bella["id"]})[0]
    assert (
        adm["protocol_run_step_id"] == step3["id"]
        and adm["lot_code"] == "FLX-2611"
        and adm["quantity_consumed"] == 27.28
    )
    assert adm["health_case_id"] == run["health_case_id"]
    twin = f.get(f"/animals/{f.bella['id']}")
    assert twin["recent_administrations"][0]["id"] == adm["id"] and twin["withdrawals"]["active"]
    assert twin["withdrawals"]["milk_until"] and twin["withdrawals"]["meat_until"]
    assert twin["open_health_case"]["id"] == run["health_case_id"]
    assert twin["emergency"]["id"] == bella["id"] and twin["emergency"]["run_id"] == run["id"]
    assert (
        twin["emergency"]["status"] == "protocol_started" and twin["emergency"]["run_status"] == "in_progress"
    )
    plan = f.get(f"/health/cases/{run['health_case_id']}")["plans"][0]
    assert (
        plan["source_type"] == "emergency_run"
        and plan["prescribed_by_name"] == "Dr. Layla Haddad"
        and plan["treatments"]
    )

    run = f.confirm(run["id"], _step(run, 4)["id"])
    run = f.confirm(run["id"], _step(run, 5)["id"])
    assert run["status"] == "awaiting_reassessment" and run["next_reassessment_at"]
    out = f.post(
        f"/emergency/runs/{run['id']}/reassess",
        {
            "signs": [
                {"code": "temperature_c", "value_numeric": 39.1},
                {"code": "appetite", "value_text": "normal"},
                {"code": "fever", "present": False},
            ]
        },
    )
    follow, run = out["assessment"], out["run"]
    assert (
        follow["source"] == "reassessment"
        and follow["parent_run_id"] == run["id"]
        and follow["status"] == "closed"
    )
    assert run["status"] == "in_progress", "improving: the approved steps continue"
    run = f.post(
        f"/emergency/runs/{run['id']}/steps/{_step(run, 6)['id']}/skip",
        {"reason": "Temperature falling, vet informed"},
        200,
    )
    assert run["status"] == "completed" and run["outcome"]
    assert f.get(f"/emergency/assessments/{bella['id']}")["status"] == "resolved"
    assert (
        f.get(f"/health/cases/{run['health_case_id']}")["status"] == "resolved"
    ), "the engine closes the case it opened"
    with f.tenant_session() as db:
        assert all(t.status == "done" for t in db.scalars(select(Task).where(Task.source_id == run["id"])))
    assert client.get(f"{API}/emergency/runs/{run['id']}", headers=f.worker_h).status_code == 200


def test_the_engine_escalates_rather_than_improvises(client, control_db):
    f = _farm(client, control_db, "EM-ESC")
    signs = [
        {"code": "temperature_c", "value_numeric": 40.4},
        {"code": "appetite", "value_text": "reduced"},
        {"code": "fever"},
    ]
    # A dose outside the approved range blocks and escalates.
    a = f.assess("animal", f.luna["id"], signs)
    run = f.start(a["id"])
    step3 = _step(run, 3)
    run = f.prepare(run["id"], step3["id"], weight_kg=900)
    assert run["status"] == "escalated" and _step(run, 3)["status"] == "blocked"
    assert any("exceeds the approved maximum" in p for p in _step(run, 3)["result"]["problems"])
    assert "exceeds the approved maximum" in run["escalation_reason"]
    assert (
        client.post(
            f"{API}/emergency/runs/{run['id']}/steps/{step3['id']}/confirm", json={}, headers=f.h
        ).status_code
        == 422
    )
    assert f.get(f"/health/cases/{run['health_case_id']}")["severity_code"] == "high"

    # A recorded adverse reaction blocks the medication step whatever the protocol says.
    assert (
        client.post(
            f"{API}/animals/{f.clover['id']}/conditions",
            json={"condition_type": "allergy", "code": "flunixin_meglumine", "label": "Flunixin reaction"},
            headers=f.worker_h,
        ).status_code
        == 403
    )
    cond = f.post(
        f"/animals/{f.clover['id']}/conditions",
        {
            "condition_type": "allergy",
            "code": "flunixin_meglumine",
            "label": "Flunixin reaction 2026",
            "severity": "high",
        },
        h=f.vet_h,
    )
    a = f.assess("animal", f.clover["id"], signs)
    assert a["status"] == "open" and a["animal_facts"]["allergies"] == ["flunixin_meglumine"]
    run = f.start(a["id"])
    run = f.prepare(run["id"], _step(run, 3)["id"], weight_kg=600)
    assert run["status"] == "escalated"
    assert any(
        "recorded allergy" in p and "flunixin_meglumine" in p for p in _step(run, 3)["result"]["problems"]
    )
    r = client.delete(f"{API}/animals/{f.clover['id']}/conditions/{cond['id']}", headers=f.vet_h)
    assert r.status_code == 200 and r.json()["active"] is False

    # A stale weight is refused when the protocol requires a fresh one;
    # a weight entered now is used and recorded.
    body = f.bovine_version_body()
    body["steps"][2]["medication"]["weight_max_age_days"] = 30
    body["eligibility_rules"] = [
        {
            "rule_type": "weight_age_days",
            "rule_payload": {"max": 30},
            "failure_action": "WARN",
            "message": "Weight older than 30 days: weigh before dosing",
        }
    ]
    v3 = f.post(f"/emergency/protocols/{f.bovine['id']}/versions", body)
    f.post(
        f"/emergency/protocols/{f.bovine['id']}/versions/{v3['id']}/approve",
        {"approval_reference": "VET-2026-020"},
        200,
        h=f.vet_h,
    )
    f.post(
        f"/animals/{f.clover['id']}/weights", {"weight_kg": 560, "measured_at": _days(-45), "method": "tape"}
    )
    with f.tenant_session() as db:  # the earlier prepare recorded a weight today; age every record
        for w in db.scalars(
            select(AnimalWeightRecord).where(AnimalWeightRecord.animal_id == uuid.UUID(f.clover["id"]))
        ):
            w.measured_at = datetime.now(timezone.utc) - timedelta(days=45)
    a = f.assess("animal", f.clover["id"], signs)
    assert a["status"] == "open", a
    warn = next(
        o for o in a["matches"][0]["explanation"]["eligibility"] if o["rule_type"] == "weight_age_days"
    )
    assert warn["passed"] is False and warn["failure_action"] == "WARN" and warn["fact"] == 45
    run = f.start(a["id"])
    run = f.prepare(run["id"], _step(run, 3)["id"])
    assert run["status"] == "escalated"
    assert any("no older than 30 day" in p and "45 day" in p for p in _step(run, 3)["result"]["problems"])
    a = f.assess("animal", f.clover["id"], signs)
    run = f.start(a["id"])
    p = _step(f.prepare(run["id"], _step(run, 3)["id"], weight_kg=580), 3)["result"]
    assert p["problems"] == [] and p["weight_source"] == "entered now" and p["dose_quantity"] == 25.52
    assert f.get(f"/animals/{f.clover['id']}/weights")[0]["weight_kg"] == 580

    # Without eligible stock the medication step escalates.
    lot = next(x for x in f.medicine(f.flunixin)["lots"] if x["lot_code"] == "FLX-2611")
    _patch(client, f.h, f"/pharmacy/lots/{lot['id']}/status", {"status": "quarantined", "reason": "Cloudy"})
    a = f.assess("animal", f.bella["id"], signs)
    run = f.start(a["id"])
    run = f.prepare(run["id"], _step(run, 3)["id"], weight_kg=500)
    assert run["status"] == "escalated"
    assert any(
        "no eligible lot" in p and "quarantined 48.5" in p for p in _step(run, 3)["result"]["problems"]
    )
    _patch(client, f.h, f"/pharmacy/lots/{lot['id']}/status", {"status": "active", "reason": "Cleared"})

    # A flock protocol with a fixed dose; no improvement at reassessment escalates.
    a = f.assess(
        "group",
        f.layers["id"],
        [
            {"code": "panting"},
            {"code": "water_intake", "value_text": "reduced"},
            {"code": "ambient_temperature_c", "value_numeric": 35},
        ],
    )
    run = f.start(a["id"])
    assert (
        run["veterinarian_notified_at"] is None and run["manager_notified_at"]
    ), "this version notifies the manager only"
    run = f.confirm(run["id"], _step(run, 1)["id"], result={"panting_birds": 40})
    run = f.confirm(run["id"], _step(run, 2)["id"])
    run = f.confirm(run["id"], _step(run, 3)["id"], head_count=10)
    done = _step(run, 3)
    assert (
        done["result"]["dose_quantity"] == 1
        and done["result"]["head_count"] == 10
        and done["result"]["quantity_consumed"] == 10
    )
    adm = f.get("/pharmacy/administrations", {"subject_id": f.layers["id"]})[0]
    assert (
        adm["protocol_run_step_id"] == done["id"]
        and adm["quantity_consumed"] == 10
        and adm["withdrawal_meat_until"] is None
    )
    assert f.medicine(f.electrolyte)["eligible_available"] == 15
    run = f.confirm(run["id"], _step(run, 4)["id"])
    assert run["status"] == "awaiting_reassessment"
    out = f.post(
        f"/emergency/runs/{run['id']}/reassess",
        {
            "signs": [
                {"code": "panting"},
                {"code": "water_intake", "value_text": "reduced"},
                {"code": "ambient_temperature_c", "value_numeric": 36},
            ]
        },
    )
    assert out["run"]["status"] == "escalated" and "no improvement" in out["run"]["escalation_reason"]

    # A withdrawn version cannot start; the matched case is escalated, not improvised.
    pending = f.assess("animal", f.luna["id"], signs)
    assert pending["status"] == "open"
    bovine = f.get(f"/emergency/protocols/{f.bovine['id']}")
    out = f.post(
        f"/emergency/protocols/{bovine['id']}/versions/{bovine['current_version_id']}/withdraw",
        {"reason": "Vet revising the dose"},
        200,
    )
    assert out["status"] == "withdrawn"
    r = client.post(
        f"{API}/emergency/assessments/{pending['id']}/start", json={"confirmed": True}, headers=f.h
    )
    assert r.status_code == 422 and "no longer current" in r.json()["detail"]
    assert (
        f.get(f"/emergency/assessments/{pending['id']}")["status"] == "escalated"
    ), "the escalation survived the refusal"
    a = f.assess(
        "animal", f.luna["id"], [{"code": "temperature_c", "value_numeric": 40.4}, {"code": "fever"}]
    )
    assert a["status"] == "escalated" and a["matches"] == []


def test_offline_execution_is_revalidated_at_sync(client, control_db):
    """§14, acceptance 9: an emergency run on the tablet from the signed
    package is recorded as it happened and every difference is listed;
    the sync is idempotent on the operation id."""
    f = _farm(client, control_db, "EM-OFF")
    pkg = f.get("/emergency/offline-package")
    bovine = next(v for v in pkg["versions"] if v["protocol_code"] == "BOVINE-FEVER-SUPPORT")
    med_step = next(s for s in bovine["steps"] if s["step_type"] == "MEDICATION")
    flx = next(m for m in pkg["medicines"] if m["medicine_product_id"] == f.flunixin)
    lot_id = flx["eligible_lots"][0]["lot_id"]
    occurred = _days(-0.1)
    payload = {
        "operation_id": "op-tablet-0001",
        "device_id": "tablet-7",
        "package_signature": pkg["signature"],
        "subject_type": "animal",
        "subject_id": f.luna["id"],
        "occurred_at": occurred,
        "signs": [
            {"code": "temperature_c", "value_numeric": 40.2},
            {"code": "appetite", "value_text": "reduced"},
            {"code": "fever"},
        ],
        "shown": {"protocol_version_id": bovine["id"], "match_score": 0.8, "triage_level": "HIGH"},
        "steps": [
            {
                "protocol_step_id": bovine["steps"][0]["id"],
                "occurred_at": occurred,
                "result": {"temperature_c": 40.2},
            },
            {"protocol_step_id": bovine["steps"][1]["id"], "occurred_at": occurred},
            {
                "protocol_step_id": med_step["id"],
                "occurred_at": occurred,
                "medication": {
                    "dose_quantity": 26.4,
                    "dose_unit": "ml",
                    "lot_id": lot_id,
                    "weight_used_kg": 600,
                },
            },
        ],
    }
    out = f.post("/emergency/offline-runs", payload)
    assert out["replayed"] is False and out["discrepancies"] == [] and out["run"]["offline"] is True
    assert out["assessment"]["source"] == "offline" and out["assessment"]["device_id"] == "tablet-7"
    assert (
        out["assessment"]["offline_operation_id"] == "op-tablet-0001"
        and out["run"]["status"] == "in_progress"
    )
    run = out["run"]
    assert _step(run, 1)["status"] == "completed" and _step(run, 3)["status"] == "completed"
    assert _step(run, 3)["result"]["offline"] is True and _step(run, 3)["result"]["device_id"] == "tablet-7"
    assert _step(run, 3)["result"]["dose_quantity"] == 26.4 and _step(run, 3)["result"]["lot_id"] == lot_id
    adm = f.get("/pharmacy/administrations", {"subject_id": f.luna["id"]})[0]
    assert adm["quantity_consumed"] == 26.4 and adm["lot_code"] == "FLX-2611"
    assert f.medicine(f.flunixin)["eligible_available"] == round(48.5 - 26.4, 2)
    # Replay: nothing is written twice.
    again = f.post("/emergency/offline-runs", payload)
    assert again["replayed"] is True and again["run"]["id"] == run["id"]
    assert len(f.get("/pharmacy/administrations", {"subject_id": f.luna["id"]})) == 1
    # The server's triage of the same signs disagrees with what the tablet
    # showed: what the tablet did is recorded against the version it used,
    # and the run is escalated — never rewritten.
    differs = {
        **payload,
        "operation_id": "op-tablet-0002",
        "subject_id": f.clover["id"],
        "signs": [{"code": "fever"}],
        "steps": [{"protocol_step_id": bovine["steps"][0]["id"], "occurred_at": occurred}],
    }
    out = f.post("/emergency/offline-runs", differs)
    assert {d["code"] for d in out["discrepancies"]} == {"match_differs"}
    assert out["run"] is not None and out["run"]["status"] == "escalated"
    assert _step(out["run"], 1)["status"] == "completed", "recorded as it happened on the device"
    assert "revalidated with differences" in out["run"]["escalation_reason"]
    assert (
        f.get(f"/emergency/assessments/{out['assessment']['id']}")["offline_discrepancies"]
        == out["discrepancies"]
    )
    # A stale package and a withdrawn version: the run is refused at sync and
    # the case escalated, with every difference on record.
    bov = f.get(f"/emergency/protocols/{f.bovine['id']}")
    f.post(
        f"/emergency/protocols/{bov['id']}/versions/{bov['current_version_id']}/withdraw",
        {"reason": "Revised"},
        200,
    )
    stale = {**payload, "operation_id": "op-tablet-0003", "subject_id": f.bella["id"]}
    out = f.post("/emergency/offline-runs", stale)
    codes = {d["code"] for d in out["discrepancies"]}
    assert {"package_stale", "version_not_current", "run_refused_at_sync"} <= codes
    assert out["run"] is None and out["assessment"]["status"] == "escalated"
    assert any("no longer current" in x for x in out["assessment"]["escalation_reasons"])
    assert len(f.get("/pharmacy/administrations", {"subject_id": f.bella["id"]})) == 0, "nothing was consumed"
    # The scheduled reassessment check opens one reminder per overdue run.
    assert f.post("/emergency/reassessments/check", expect=200)["awaiting_reassessment"] == 0


# ------------------------------------------------------------- health cases
def test_a_case_frames_observations_diagnosis_plan_and_treatment(client, control_db):
    f = Farm(client, control_db, "HC-CASE").stock_pharmacy().stock_animals()
    luna = f.luna["id"]
    obs = f.observe("animal", luna, "lameness", severity="moderate", notes="Left hind", h=f.worker_h)
    r = client.post(
        f"{API}/health/cases",
        json={"subject_type": "animal", "subject_id": luna, "severity_code": "moderate"},
        headers=f.acct_h,
    )
    assert r.status_code == 403, "opening a case needs animal-health create"
    case = f.post(
        "/health/cases",
        {
            "subject_type": "animal",
            "subject_id": luna,
            "severity_code": "moderate",
            "notes": "Lame since morning",
        },
    )
    assert case["case_code"].startswith("HC-") and case["status"] == "open" and case["subject_name"] == "Luna"
    assert case["active_diagnoses"] == [] and [o["id"] for o in case["observations"]] == [obs["id"]]
    with f.tenant_session() as db:
        assert db.get(Animal, uuid.UUID(luna)).status == "under_observation"
    # A later observation lands on the open case by itself.
    later = f.observe("animal", luna, "temperature_c", value_numeric=39.2, h=f.worker_h)
    detail = f.get(f"/health/cases/{case['id']}")
    assert {o["id"] for o in detail["observations"]} == {obs["id"], later["id"]}

    # Diagnosis is clinical: the worker is refused, the vet records one with a certainty.
    assert (
        client.post(
            f"{API}/health/cases/{case['id']}/diagnoses",
            json={"diagnosis_code": "digital_dermatitis"},
            headers=f.worker_h,
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{API}/health/cases/{case['id']}/diagnoses",
            json={"diagnosis_code": "x", "certainty_code": "guess"},
            headers=f.vet_h,
        ).status_code
        == 422
    )
    first = f.post(
        f"/health/cases/{case['id']}/diagnoses",
        {
            "diagnosis_code": "digital_dermatitis",
            "diagnosis_text": "Left hind",
            "certainty_code": "suspected",
        },
        h=f.vet_h,
    )
    assert first["status"] == "active" and first["diagnosed_by_name"] == "Dr. Layla Haddad"
    f.post(
        f"/health/cases/{case['id']}/diagnoses",
        {
            "diagnosis_code": "sole_ulcer",
            "certainty_code": "confirmed",
            "supersedes_diagnosis_id": first["id"],
        },
        h=f.vet_h,
    )
    detail = f.get(f"/health/cases/{case['id']}")
    assert detail["active_diagnoses"] == ["sole_ulcer"] and {d["status"] for d in detail["diagnoses"]} == {
        "superseded",
        "active",
    }
    assert detail["primary_problem_code"] == "digital_dermatitis"

    # The plan and the treatment under it, naming the stocked medicine.
    plan = f.post(
        f"/health/cases/{case['id']}/plans",
        {"instructions": "Trim, block, anti-inflammatory for 3 days"},
        h=f.vet_h,
    )
    treatment = f.post(
        "/health/treatments",
        {
            "entity_type": "animal",
            "entity_id": luna,
            "medication": "Flunixin",
            "dose": "2.2 mg/kg",
            "route": "IM",
            "responsible_user_id": str(f.vet.id),
            "medicine_product_id": f.flunixin,
            "treatment_plan_id": plan["id"],
        },
        h=f.vet_h,
    )
    assert treatment["health_case_id"] == case["id"] and treatment["treatment_plan_id"] == plan["id"]
    r = client.post(
        f"{API}/pharmacy/administrations",
        json={
            "subject_type": "animal",
            "subject_id": luna,
            "inventory_item_id": f.oxytet,
            "dose_quantity": 10,
            "dose_unit": "ml",
            "route_code": "IM",
            "treatment_id": treatment["id"],
        },
        headers=f.h,
    )
    assert r.status_code == 422 and "prescribes" in r.json()["detail"]
    adm = f.post(
        "/pharmacy/administrations",
        {
            "subject_type": "animal",
            "subject_id": luna,
            "inventory_item_id": f.flunixin,
            "dose_quantity": 10,
            "dose_unit": "ml",
            "route_code": "IM",
            "treatment_id": treatment["id"],
        },
    )
    assert adm["health_case_id"] == case["id"]
    detail = f.get(f"/health/cases/{case['id']}")
    assert (
        detail["plans"][0]["status"] == "active"
        and detail["plans"][0]["treatments"][0]["id"] == treatment["id"]
    )
    assert len(detail["treatments"]) == 1
    # A case can be moved to monitoring; a worker cannot edit it.
    assert (
        client.patch(
            f"{API}/health/cases/{case['id']}", json={"status": "monitoring"}, headers=f.worker_h
        ).status_code
        == 403
    )
    assert (
        _patch(client, f.h, f"/health/cases/{case['id']}", {"status": "monitoring"})["status"] == "monitoring"
    )

    # Closing needs an outcome; it resolves the diagnoses and completes the plan.
    assert (
        client.post(f"{API}/health/cases/{case['id']}/close", json={"outcome": " "}, headers=f.h).status_code
        == 422
    )
    closed = f.post(f"/health/cases/{case['id']}/close", {"outcome": "Sound after 5 days"}, 200)
    assert closed["status"] == "resolved" and closed["closed_at"]
    closed = f.get(f"/health/cases/{case['id']}")
    assert closed["active_diagnoses"] == [] and closed["plans"][0]["status"] == "completed"
    assert (
        client.post(
            f"{API}/health/cases/{case['id']}/diagnoses", json={"diagnosis_code": "x"}, headers=f.vet_h
        ).status_code
        == 422
    )
    assert [c["id"] for c in f.get("/health/cases", {"status": "all", "subject_id": luna})] == [case["id"]]
    assert f.get("/health/cases") == []


def test_the_emergency_engine_opens_and_closes_its_own_case(client, control_db):
    f = _farm(client, control_db, "HC-EM")
    rasha = f.assess(
        "animal", f.rasha["id"], [{"code": "temperature_c", "value_numeric": 40.6}, {"code": "fever"}]
    )
    assert rasha["status"] == "escalated" and rasha["health_case_id"]
    out = f.post(
        f"/emergency/assessments/{rasha['id']}/close", {"outcome": "Vet visited, treated for pneumonia"}, 200
    )
    assert out["status"] == "resolved" and "Outcome: Vet visited" in out["explanation"]
    case = f.get(f"/health/cases/{rasha['health_case_id']}")
    assert case["status"] == "resolved" and "pneumonia" in case["outcome"]
    # A case a person opened is theirs: an escalation joins it, raises its
    # severity, and closing the emergency leaves it open.
    manual = f.post(
        "/health/cases",
        {
            "subject_type": "animal",
            "subject_id": f.clover["id"],
            "severity_code": "low",
            "notes": "Watching appetite",
        },
    )
    a = f.assess("animal", f.clover["id"], [{"code": "temperature_c", "value_numeric": 41.9}])
    assert a["status"] == "escalated" and a["health_case_id"] == manual["id"]
    assert f.get(f"/health/cases/{manual['id']}")["severity_code"] == "critical"
    f.post(f"/emergency/assessments/{a['id']}/close", {"outcome": "False alarm"}, 200)
    assert f.get(f"/health/cases/{manual['id']}")["status"] == "open"
    # Cross-tenant: another farm sees none of it.
    other = Farm(client, control_db, "HC-OTHER")
    assert other.get("/health/cases", {"status": "all"}) == [] and other.get("/emergency/assessments") == []
    assert client.get(f"{API}/health/cases/{manual['id']}", headers=other.h).status_code == 404
