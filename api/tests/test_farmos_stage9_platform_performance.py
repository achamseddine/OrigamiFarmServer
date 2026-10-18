"""Stage 9 of the FarmOS tablet contract: the platform masters (units of
measure as references, farm assets, notification routes, the scheduled
cycles with their run log) and feed-performance intelligence (exposure
windows with lineage, explicit baselines, explained assessments with a
likelihood and a confidence, deduplicated alerts that acknowledge without
resolving and resolve when the evidence clears, mix and supplier scores,
the feed signals on the bell). See docs/FARMOS_API.md and OrigamiFarmOS
database/FEED-PERFORMANCE-INTELLIGENCE.md, FOUNDATION-SCHEMA.md,
ASSET-MAINTENANCE-SCHEMA.md §2, MEDICINE-NOTIFICATION-RULES.md.

Every test builds its own dairy-goat farm through the API: purchased
ingredients from three suppliers, a farm-produced goat mix with a
formula, a herd of two does in a group, and Gigi — a lactating doe
outside the herd whose milk the scenarios move.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.common.tenant_router import TenantDataRouter
from app.farmos import scheduler_service
from app.farmos.production_models import MilkRecord
from app.tenant_api.models import Task
from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant

API = "/api/v1"
ALL = ["view", "create", "edit", "delete", "approve", "export", "assign", "configure"]


def _at(days: float) -> datetime:
    """07:00 UTC, `days` from today (negative = past)."""
    base = datetime.now(timezone.utc).replace(hour=7, minute=0, second=0, microsecond=0)
    return base + timedelta(days=days)


def _post(client, h, path, body=None, expect=201):
    r = client.post(f"{API}{path}", json=body, headers=h)
    assert r.status_code == expect, f"POST {path}: {r.status_code} {r.text}"
    return r.json()


def _get(client, h, path, params=None):
    r = client.get(f"{API}{path}", params=params, headers=h)
    assert r.status_code == 200, f"GET {path}: {r.status_code} {r.text}"
    return r.json()


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
            grid={
                "feed_nutrition": ["view", "create"],
                "inventory": ["view"],
                "animals": ["view"],
                "settings": ["view"],
            },
        )
        control_db.commit()
        self.h = farmos_headers(farmos_login(client, self.owner.email, FARMOS_DEMO_PASSWORD))
        self.worker_h = farmos_headers(farmos_login(client, self.worker.email, FARMOS_DEMO_PASSWORD))
        self.vet_h = farmos_headers(farmos_login(client, self.vet.email, FARMOS_DEMO_PASSWORD))
        self.farm_id = str(tenant.id)

    # -- helpers --------------------------------------------------------
    def post(self, path, body=None, expect=201, h=None):
        return _post(self.client, h or self.h, path, body, expect)

    def get(self, path, params=None, h=None):
        return _get(self.client, h or self.h, path, params)

    def put(self, path, body, expect=200, h=None):
        r = self.client.put(f"{API}{path}", json=body, headers=h or self.h)
        assert r.status_code == expect, f"PUT {path}: {r.status_code} {r.text}"
        return r.json()

    def patch(self, path, body, expect=200, h=None):
        r = self.client.patch(f"{API}{path}", json=body, headers=h or self.h)
        assert r.status_code == expect, f"PATCH {path}: {r.status_code} {r.text}"
        return r.json()

    def tenant_session(self):
        return TenantDataRouter.session_for(self.tenant.id)

    def bell(self, source_type: str | None = None) -> list[dict]:
        rows = self.get("/notifications", {"unread_only": True, "limit": 200})["notifications"]
        return [n for n in rows if source_type is None or n["source_type"] == source_type]

    def alerts(self, status: str = "open") -> list[dict]:
        return self.get("/feed-performance/alerts", {"status": status})

    # -- the farm -------------------------------------------------------
    def goat_farm(self) -> "Farm":
        """Purchased ingredients (each from its own supplier, no opening
        stock, so every mix traces to a supplier lot), a farm-produced goat
        mix with a formula, a herd of two does and Gigi outside it."""
        self.barley = self.post("/feed-products", {"name": "Barley", "unit_cost": 0.4, "is_feedable": True})
        self.alfalfa = self.post(
            "/feed-products", {"name": "Alfalfa Hay", "unit_cost": 0.3, "is_feedable": True}
        )
        self.premix = self.post(
            "/feed-products", {"name": "Dairy Premix", "unit_cost": 3.0, "category": "premix"}
        )
        self.mix = self.post(
            "/feed-products",
            {
                "name": "Goat Mix 16",
                "code": "GOAT-MIX-16",
                "source_type": "farm_produced",
                "is_ingredient": False,
                "is_feedable": True,
                "category": "complete_feed",
            },
        )
        for product, supplier, qty, cost, code in (
            (self.barley, "Bekaa Hay Co.", 1000, 0.4, "BAR-01"),
            (self.alfalfa, "Al Mashreq", 1000, 0.3, "ALF-01"),
            (self.premix, "NutriPlus", 100, 3.0, "PMX-01"),
        ):
            self.post(
                "/feed-lots/receive",
                {
                    "feed_product_id": product["id"],
                    "quantity": qty,
                    "unit_cost": cost,
                    "supplier_label": supplier,
                    "lot_code": code,
                },
            )
        self.formula = self.post(
            "/feed-formulas",
            {
                "name": "Goat Mix 16",
                "code": "GOAT-MIX-16",
                "feed_product_id": self.mix["id"],
                "species_code": "goat",
                "batch_size": 1000,
                "unit": "kg",
                "components": [
                    {"feed_product_id": self.barley["id"], "target_quantity": 600},
                    {"feed_product_id": self.alfalfa["id"], "target_quantity": 390},
                    {"feed_product_id": self.premix["id"], "target_quantity": 10},
                ],
            },
        )
        self.herd = self.post(
            "/animal-groups",
            {
                "name": "Dairy Does",
                "species": "goat",
                "count": 2,
                "management_profile": "dairy",
                "sex_composition": "female",
            },
        )
        self.does = [
            self.post(
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
            for i, name in enumerate(("Bella", "Luna"))
        ]
        self.gigi = self.post(
            "/animals",
            {
                "name": "Gigi",
                "species": "goat",
                "sex": "F",
                "management_profile": "dairy",
                "lactating": True,
                "tag": "GOAT-9",
            },
        )
        return self

    def complete_mix(
        self, *, target: float = 300, bump: float = 1.0, mixer_asset_id: str | None = None
    ) -> dict:
        """A numbered mix from the formula; `bump` scales the largest
        component's actual so a mix can be made to deviate on purpose."""
        body: dict = {"formula_id": self.formula["id"], "target_quantity": target}
        if mixer_asset_id:
            body["mixer_asset_id"] = mixer_asset_id
        batch = self.post("/feed-batches", body)
        biggest = max(batch["components"], key=lambda c: c["target_quantity"])["feed_product_id"]
        actuals = [
            {
                "feed_product_id": c["feed_product_id"],
                "actual_quantity": round(
                    c["target_quantity"] * (bump if c["feed_product_id"] == biggest else 1), 2
                ),
            }
            for c in batch["components"]
        ]
        return self.post(
            f"/feed-batches/{batch['id']}/complete",
            {"actual_quantity": target, "actuals": actuals},
            expect=200,
        )

    def feed(self, subject_type, subject_id, product_id, qty, days_ago, event_type="delivered", lot_id=None):
        comp = {"feed_product_id": product_id, "quantity_offered": qty}
        if lot_id:
            comp["lot_id"] = lot_id
        return self.post(
            "/feeding-events",
            {
                "subject_type": subject_type,
                "subject_id": subject_id,
                "event_type": event_type,
                "occurred_at": _at(-days_ago).isoformat(),
                "components": [comp],
            },
        )

    def milk(self, animal_id: str, liters_by_day_offset: dict[int, float]) -> None:
        """Milk records straight into the production table for days that
        may lie in the future (the recovery scenario); the API path is
        exercised separately."""
        with self.tenant_session() as db:
            for offset, liters in liters_by_day_offset.items():
                db.add(
                    MilkRecord(
                        tenant_id=self.tenant.id,
                        animal_id=animal_id,
                        session="morning",
                        liters=liters,
                        quality_status="normal",
                        destination="stored",
                        recorded_at=_at(offset),
                        recorded_by=self.owner.id,
                    )
                )


# =============================================================== units
def test_units_are_a_reference_table_the_pharmacy_honours(client, control_db):
    farm = Farm(client, control_db, "S9U")
    other = Farm(client, control_db, "S9UB")
    units = {u["code"]: u for u in farm.get("/units-of-measure")}
    assert {"kg", "g", "ml", "l", "dose", "sachet", "mg/ml", "%", "head"} <= set(units)
    assert units["g"]["base_unit_code"] == "kg" and units["g"]["factor_to_base"] == 0.001
    assert units["dose"]["base_unit_code"] is None and units["kg"]["built_in"]

    # A medicine in an unknown unit is refused until the unit exists.
    body = {
        "name": "Vitamin B complex",
        "unit": "flask",
        "dosage_form": "injectable",
        "administration_routes": ["IM"],
    }
    r = client.post(f"{API}/pharmacy/medicines", json=body, headers=farm.h)
    assert r.status_code == 422 and "Unknown stock unit 'flask'" in r.json()["detail"], r.text
    assert (
        client.post(
            f"{API}/units-of-measure",
            json={"code": "flask", "name_en": "Flask", "dimension": "count"},
            headers=farm.worker_h,
        ).status_code
        == 403
    ), "units are configuration"
    created = farm.post("/units-of-measure", {"code": "Flask", "name_en": "Flask", "dimension": "count"})
    assert created["code"] == "flask" and created["built_in"] is False
    medicine = farm.post("/pharmacy/medicines", body)
    assert medicine["unit"] == "flask"
    assert (
        client.post(
            f"{API}/units-of-measure", json={"code": "kg", "name_en": "Kilo"}, headers=farm.h
        ).status_code
        == 422
    )

    # Conversions follow the dimension: a derived unit converts, a count unit never silently becomes another.
    farm.post(
        "/units-of-measure",
        {
            "code": "quintal",
            "name_en": "Quintal",
            "dimension": "mass",
            "base_unit_code": "kg",
            "factor_to_base": 100,
        },
    )
    farm.post(
        "/units-of-measure",
        {
            "code": "carton",
            "name_en": "Carton",
            "dimension": "count",
            "base_unit_code": "dose",
            "factor_to_base": 12,
        },
    )
    r = client.post(
        f"{API}/units-of-measure",
        json={
            "code": "weird",
            "name_en": "Weird",
            "dimension": "mass",
            "base_unit_code": "l",
            "factor_to_base": 1,
        },
        headers=farm.h,
    )
    assert r.status_code == 422 and "same dimension" in r.json()["detail"]
    lime = farm.post(
        "/pharmacy/medicines",
        {
            "name": "Hydrated lime",
            "unit": "kg",
            "dosage_form": "powder",
            "administration_routes": ["topical"],
        },
    )
    lot = farm.post(
        "/pharmacy/lots/receive",
        {
            "inventory_item_id": lime["inventory_item_id"],
            "quantity": 2,
            "unit": "quintal",
            "lot_code": "LIME-01",
            "expiry_date": _at(365).isoformat(),
        },
    )
    assert lot["quantity_on_hand"] == 200, "two quintals are 200 kg of stock"

    # A unit one farm invents is that farm's: the other farm never sees it.
    assert "flask" not in {u["code"] for u in other.get("/units-of-measure")}
    assert client.post(f"{API}/pharmacy/medicines", json=body, headers=other.h).status_code == 422


# ============================================================== assets
def test_assets_are_registered_and_named_by_mixes(client, control_db):
    farm = Farm(client, control_db, "S9A").goat_farm()
    other = Farm(client, control_db, "S9AB")
    mixer = farm.post("/assets", {"name": "Mixer wagon 1", "asset_type": "mixer", "manufacturer": "Trioliet"})
    assert (
        mixer["code"] == "mixer-wagon-1" and mixer["status"] == "active" and mixer["farm_id"] == farm.farm_id
    )
    tank = farm.post("/assets", {"name": "Cooling tank 1", "asset_type": "cooling_tank"})
    assert (
        client.post(
            f"{API}/assets", json={"name": "Spaceship", "asset_type": "rocket"}, headers=farm.h
        ).status_code
        == 422
    )
    assert (
        client.post(f"{API}/assets", json={"name": "Shovel"}, headers=farm.vet_h).status_code == 403
    ), "assets are stock-keeping records: inventory or feed write rights"
    assert farm.post("/assets", {"name": "Shovel"}, h=farm.worker_h)["asset_type"] == "other"
    assert [a["code"] for a in farm.get("/assets", {"asset_type": "mixer"})] == ["mixer-wagon-1"]
    assert farm.get(f"/assets/{mixer['id']}")["manufacturer"] == "Trioliet"

    # A mix names the mixer it was made on; the usage view names it back.
    done = farm.complete_mix(mixer_asset_id=mixer["id"])
    assert done["mixer_asset_id"] == mixer["id"] and done["mixer_asset_name"] == "Mixer wagon 1"
    usage = farm.get(f"/feed-mixes/{done['mix_number']}")
    assert usage["mixer_asset_name"] == "Mixer wagon 1"

    # Only a registered, in-service mixer: not a tank, not a stranger's id, not one out of service.
    r = client.post(
        f"{API}/feed-batches",
        json={"formula_id": farm.formula["id"], "target_quantity": 100, "mixer_asset_id": tank["id"]},
        headers=farm.h,
    )
    assert r.status_code == 422 and "not a mixer" in r.json()["detail"], r.text
    r = client.post(
        f"{API}/feed-batches",
        json={
            "formula_id": farm.formula["id"],
            "target_quantity": 100,
            "mixer_asset_id": str(farm.tenant.id),
        },
        headers=farm.h,
    )
    assert r.status_code == 404 and "register the mixer under Assets" in r.json()["detail"]
    patched = farm.patch(f"/assets/{mixer['id']}", {"status": "out_of_service", "notes": "Gearbox"})
    assert patched["status"] == "out_of_service" and patched["notes"] == "Gearbox"
    r = client.post(
        f"{API}/feed-batches",
        json={"formula_id": farm.formula["id"], "target_quantity": 100, "mixer_asset_id": mixer["id"]},
        headers=farm.h,
    )
    assert r.status_code == 422 and "out of service" in r.json()["detail"], r.text
    assert (
        client.patch(f"{API}/assets/{mixer['id']}", json={"status": "broken"}, headers=farm.h).status_code
        == 422
    )

    # Another farm sees none of it.
    assert other.get("/assets") == []
    assert client.get(f"{API}/assets/{mixer['id']}", headers=other.h).status_code == 404


# ================================================== notification routes
def test_notification_routes_decide_recipients_and_default_to_managers(client, control_db):
    farm = Farm(client, control_db, "S9R")
    routes = {x["route_code"]: x for x in farm.get("/notification-routes")}
    assert set(routes) >= {
        "pharmacy_stock",
        "emergency_veterinarian",
        "feed_performance",
        "procurement_requisition",
    }
    assert routes["pharmacy_stock"]["using_default"] and routes["pharmacy_stock"]["default_roles"] == [
        "manager",
        "owner",
    ]
    assert [u["name"] for u in routes["pharmacy_stock"]["effective_recipients"]] == ["Rami Owner"]
    assert [u["role"] for u in routes["emergency_veterinarian"]["effective_recipients"]] == ["veterinarian"]

    # Route feed performance to the worker personally: the worker is the recipient, the owner no longer.
    out = farm.put("/notification-routes/feed_performance", {"entries": [{"user_id": str(farm.worker.id)}]})
    assert not out["using_default"] and [u["id"] for u in out["effective_recipients"]] == [
        str(farm.worker.id)
    ]
    assert out["configured"][0]["channel"] == "in_app"
    # By role, with an explicit person first.
    out = farm.put(
        "/notification-routes/feed_performance",
        {"entries": [{"role": "veterinarian"}, {"user_id": str(farm.worker.id)}]},
    )
    assert [u["name"] for u in out["effective_recipients"]] == ["Karim Worker", "Dr. Layla Haddad"]
    assert len(out["configured"]) == 2, "the replaced rows stay inactive, the new ones are the configuration"

    # Refusals: configuration right, a known route, a member of this farm, a role or a user.
    assert (
        client.put(
            f"{API}/notification-routes/feed_performance", json={"entries": []}, headers=farm.worker_h
        ).status_code
        == 403
    )
    assert (
        client.put(f"{API}/notification-routes/nonsense", json={"entries": []}, headers=farm.h).status_code
        == 422
    )
    assert (
        client.put(
            f"{API}/notification-routes/pharmacy_stock", json={"entries": [{}]}, headers=farm.h
        ).status_code
        == 422
    )
    stranger = Farm(client, control_db, "S9RB")
    r = client.put(
        f"{API}/notification-routes/pharmacy_stock",
        json={"entries": [{"user_id": str(stranger.owner.id)}]},
        headers=farm.h,
    )
    assert r.status_code == 404
    assert (
        client.put(
            f"{API}/notification-routes/pharmacy_stock", json={"entries": [{"role": "ceo"}]}, headers=farm.h
        ).status_code
        == 422
    )
    # Back to default.
    assert farm.put("/notification-routes/feed_performance", {"entries": []})["using_default"]
    assert stranger.get("/notification-routes")[0]["using_default"]


# =========================================================== scheduler
def test_the_cycles_run_on_demand_and_are_logged(client, control_db, monkeypatch):
    farm = Farm(client, control_db, "S9S").goat_farm()
    jobs = {j["job_code"]: j for j in farm.get("/scheduler/jobs")}
    assert set(jobs) == {"feed_performance_cycle", "pharmacy_scan", "emergency_reassessment_check"}
    assert all(
        j["due"] and j["last_run_at"] is None for j in jobs.values()
    ), "nothing has run yet on a fresh farm"
    assert jobs["feed_performance_cycle"]["cadence_minutes"] == 1440
    assert jobs["emergency_reassessment_check"]["cadence_minutes"] == 15

    # The background loop: one pass runs every due job for every live farm, on the owner's authority.
    access = scheduler_service.system_access_for(farm.tenant.id)
    assert access is not None and access.role == "owner" and access.user_id == farm.owner.id
    assert scheduler_service.tick() == 3
    jobs = {j["job_code"]: j for j in farm.get("/scheduler/jobs")}
    assert all(not j["due"] and j["last_status"] == "succeeded" for j in jobs.values()), jobs
    runs = farm.get("/scheduler/runs")
    assert (
        len(runs) == 3
        and {r["trigger"] for r in runs} == {"scheduler"}
        and all(r["finished_at"] for r in runs)
    )

    # On demand: an owner's or manager's action; `force=false` runs only what is due.
    assert client.post(f"{API}/scheduler/run", json={}, headers=farm.worker_h).status_code == 403
    assert (
        client.post(f"{API}/scheduler/run", json={"job_codes": ["nope"]}, headers=farm.h).status_code == 422
    )
    assert farm.post("/scheduler/run", {"force": False}, expect=200)["ran"] == 0
    out = farm.post("/scheduler/run", {}, expect=200)
    assert out["ran"] == 3 and all(x["status"] == "succeeded" for x in out["runs"]), out
    by_code = {x["job_code"]: x for x in out["runs"]}
    assert "monitors" in by_code["feed_performance_cycle"]["summary"]
    assert by_code["feed_performance_cycle"]["summary"]["feed_bell_signals"] == {"reorder": 0, "variance": 0}
    assert by_code["pharmacy_scan"]["summary"]["medicines"] == 0
    assert by_code["emergency_reassessment_check"]["summary"]["awaiting_reassessment"] == 0
    assert len(farm.get("/scheduler/runs", {"job_code": "pharmacy_scan"})) == 2
    assert farm.get("/scheduler/runs", {"job_code": "pharmacy_scan"})[0]["trigger"] == "manual"

    # A failing job is logged and does not stop the others; a failed run does not count as done.
    def boom(_db, _access):
        raise RuntimeError("analytics exploded")

    monkeypatch.setitem(
        scheduler_service.JOBS, "pharmacy_scan", (scheduler_service.JOBS["pharmacy_scan"][0], boom)
    )
    out = farm.post("/scheduler/run", {}, expect=200)
    statuses = {x["job_code"]: x["status"] for x in out["runs"]}
    assert statuses == {
        "feed_performance_cycle": "succeeded",
        "pharmacy_scan": "failed",
        "emergency_reassessment_check": "succeeded",
    }
    failed = next(x for x in out["runs"] if x["job_code"] == "pharmacy_scan")
    assert "analytics exploded" in failed["error"]
    jobs = {j["job_code"]: j for j in out["jobs"]}
    scan = jobs["pharmacy_scan"]
    assert scan["last_status"] == "failed" and "analytics exploded" in scan["last_error"]
    assert scan["last_success_at"] < scan["last_run_at"], "the failed run does not count as the last success"
    assert not scan["due"], "the earlier success still stands within the cadence"
    assert not jobs["feed_performance_cycle"]["due"]
    assert farm.post("/scheduler/run", {"force": False}, expect=200)["ran"] == 0
    # On a farm that has never succeeded, the failed run leaves the job due.
    fresh = Farm(client, control_db, "S9S2")
    out = fresh.post("/scheduler/run", {"job_codes": ["pharmacy_scan"]}, expect=200)
    assert out["runs"][0]["status"] == "failed"
    assert {j["job_code"]: j for j in out["jobs"]}["pharmacy_scan"]["due"]


# ===================================================== feed performance
def test_a_persistent_decline_after_a_feed_change_is_explained_alerted_acknowledged_and_recovers(
    client, control_db
):
    farm = Farm(client, control_db, "S9P").goat_farm()
    done = farm.complete_mix()
    gigi = farm.gigi["id"]
    # Seven steady days, then the goat mix is introduced and milk drops a fifth for three days.
    farm.milk(gigi, {-d: 3.0 for d in range(3, 10)})
    farm.milk(gigi, {-2: 2.4, -1: 2.3, 0: 2.5})
    for d in (4, 3, 2, 1, 0):
        farm.feed("animal", gigi, farm.mix["id"], 1.2, days_ago=d, lot_id=done["output_lot_id"])
    # The farm routes feed-performance reviews to the worker.
    farm.put("/notification-routes/feed_performance", {"entries": [{"user_id": str(farm.worker.id)}]})

    r = client.post(
        f"{API}/feed-performance/monitors",
        json={"subject_type": "animal", "subject_id": gigi, "production_metric_code": "milk_l_per_day"},
        headers=farm.h,
    )
    assert r.status_code == 201, r.text
    monitor = r.json()
    assert monitor["baseline_method_code"] == "rolling_subject" and monitor["latest_assessment"] is None
    assert monitor["subject_name"] == "Gigi" and monitor["species"] == "goat"

    a = farm.post(f"/feed-performance/monitors/{monitor['id']}/evaluate", expect=200)
    assert a["status"] == "anomaly" and a["feed_related_likelihood"] == "HIGH", a["explanation"]
    assert -21 < a["variance_percent"] < -19 and a["baseline_value"] == 3.0 and a["observed_value"] == 2.4
    assert a["model_reference"] == "origami.feed_performance.deterministic" and a["model_version"]
    assert (
        0.45 <= a["confidence_score"] <= 0.7
    ), "confidence is lowered for every piece of context the farm does not record"
    missing = set(a["confounders"]["missing_context"])
    assert {"environment", "milk_components", "weight_change", "group_membership", "days_in_milk"} <= missing
    assert a["confounders"]["health_events"] == 0 and a["confounders"]["refusal_events"] == 0
    change = a["evidence"]["feed_changes"][0]
    assert change["feed_product_id"] == farm.mix["id"] and change["days_of_exposure"] >= 2
    assert change["lot_code"] == done["output_lot_code"] and change["mix_code"] == done["mix_code"]
    assert {i["product_name"] for i in change["ingredient_lots"]} == {"Barley", "Alfalfa Hay", "Dairy Premix"}
    assert a["evidence"]["baseline_window"]["observation_days"] == 7
    assert a["evidence"]["evaluation_window"]["observation_days"] == 3
    assert "20.0% below the rolling subject baseline" in a["explanation"]
    assert (
        "Relevant change: Goat Mix 16" in a["explanation"]
        and "an association, not a cause" in a["explanation"]
    )
    codes = [x["code"] for x in a["suggested_actions"]]
    assert codes[0] == "inspect_mix" and "review_ration" in codes and "sample_lab_analysis" in codes

    # The exposure projection carries the mix and its ingredient lots with their suppliers.
    windows = farm.get("/feed-performance/exposures", {"subject_type": "animal", "subject_id": gigi})
    window = next(w for w in windows if w["lot_id"] == done["output_lot_id"])
    assert (
        window["feed_batch_id"] == done["id"]
        and window["feeding_event_count"] == 5
        and window["offered_quantity"] == 6
    )
    assert window["exposure_end"] is None, "fed today, so the exposure is ongoing"
    lineage = window["lineage"]
    assert lineage["mix"]["mix_code"] == done["mix_code"] and lineage["source_type"] == "farm_produced"
    premix = next(i for i in lineage["ingredient_lots"] if i["product_name"] == "Dairy Premix")
    assert premix["supplier_label"] == "NutriPlus" and premix["lot_code"] == "PMX-01"

    # One alert with a review task (to the routed worker) and a bell entry; a repeat refreshes, never twins.
    alerts = [x for x in farm.alerts() if x["subject_id"] == gigi]
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["alert_type"] == "PRODUCTION_DECLINE_AFTER_FEED_CHANGE" and alert["severity"] == "high"
    assert alert["status"] == "open" and alert["feed_product_id"] == farm.mix["id"]
    assert (
        alert["assessment_id"] == a["id"]
        and alert["subject_name"] == "Gigi"
        and alert["mix_code"] == done["mix_code"]
    )
    with farm.tenant_session() as db:
        tasks = db.scalars(
            select(Task).where(Task.source_type == "feed_performance_review", Task.source_id == alert["id"])
        ).all()
        assert len(tasks) == 1 and tasks[0].status == "open" and tasks[0].priority == "high"
        assert tasks[0].assigned_to == farm.worker.id, "the farm's route decides who reviews"
        task_id = tasks[0].id
    bell = [n for n in farm.bell("feed_performance_alert") if n["source_id"] == alert["id"]]
    # The entry navigates to the mix behind the change (the alert names the animal).
    assert len(bell) == 1 and bell[0]["entity_type"] == "feed_batch" and bell[0]["entity_id"] == done["id"]
    assert bell[0]["module_code"] == "feed_nutrition" and bell[0]["priority"] == "high"
    assert bell[0]["notification_type"] == "feed_performance_production_decline_after_feed_change"
    farm.post(f"/feed-performance/monitors/{monitor['id']}/evaluate", expect=200)
    assert len([x for x in farm.alerts() if x["subject_id"] == gigi]) == 1
    assert (
        len(farm.get("/feed-performance/assessments", {"monitor_id": monitor["id"]})) == 2
    ), "every evaluation is kept"
    assert farm.get("/feed-performance/monitors")[0]["latest_assessment"]["id"] != a["id"]

    # Acknowledging records that a person saw it. The condition stands, so the alert stays open.
    ack = farm.post(f"/feed-performance/alerts/{alert['id']}/acknowledge", expect=200)
    assert ack["status"] == "acknowledged" and ack["acknowledged_by"] == str(farm.owner.id)
    assert ack["acknowledged_by_name"] == "Rami Owner"
    assert [x["status"] for x in farm.alerts() if x["subject_id"] == gigi] == ["acknowledged"]
    assert not [
        n for n in farm.bell("feed_performance_alert") if n["source_id"] == alert["id"]
    ], "acknowledged alerts leave the bell"
    assert farm.get("/feed-performance/summary")["open_alerts"] == 1

    # Milk comes back within the threshold: the alert resolves itself and its task closes.
    farm.milk(gigi, {1: 2.9, 2: 2.9, 3: 2.9})
    r = client.post(
        f"{API}/feed-performance/monitors/{monitor['id']}/evaluate",
        params={"as_of": _at(3).isoformat()},
        headers=farm.h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "normal" and r.json()["feed_related_likelihood"] == "LOW"
    assert not [x for x in farm.alerts() if x["subject_id"] == gigi]
    resolved = next(x for x in farm.alerts("all") if x["id"] == alert["id"])
    assert resolved["status"] == "resolved" and resolved["resolved_at"]
    assert "within the threshold" in resolved["resolution_note"]
    with farm.tenant_session() as db:
        assert db.get(Task, task_id).status == "done"

    # A milk record through the API re-evaluates the monitor between cycles (§12).
    before = len(farm.get("/feed-performance/assessments", {"subject_id": gigi}))
    farm.post("/production/milk", {"animal_id": gigi, "session": "evening", "liters": 1.4})
    assert len(farm.get("/feed-performance/assessments", {"subject_id": gigi})) == before + 1
    # The worker reads but cannot evaluate or acknowledge; a stranger sees nothing.
    assert client.post(f"{API}/feed-performance/evaluate", headers=farm.worker_h).status_code == 403
    assert len(farm.get("/feed-performance/alerts", {"status": "all"}, h=farm.worker_h)) == 1
    other = Farm(client, control_db, "S9PB")
    assert other.get("/feed-performance/monitors") == [] and other.alerts("all") == []
    assert (
        client.get(
            f"{API}/feed-performance/assessments", params={"monitor_id": monitor["id"]}, headers=other.h
        ).json()
        == []
    )


def test_monitor_rules_and_insufficient_evidence(client, control_db):
    farm = Farm(client, control_db, "S9M").goat_farm()
    bella = farm.does[0]["id"]
    r = client.post(
        f"{API}/feed-performance/monitors",
        json={"subject_type": "animal", "subject_id": bella, "production_metric_code": "eggs_per_day"},
        headers=farm.h,
    )
    assert r.status_code == 422 and "flock" in r.json()["detail"]
    r = client.post(
        f"{API}/feed-performance/monitors",
        json={"subject_type": "animal", "subject_id": bella, "baseline_method_code": "guesswork"},
        headers=farm.h,
    )
    assert r.status_code == 422
    r = client.post(
        f"{API}/feed-performance/monitors",
        json={"subject_type": "animal", "subject_id": str(farm.tenant.id)},
        headers=farm.h,
    )
    assert r.status_code == 404
    first = farm.post("/feed-performance/monitors", {"subject_type": "animal", "subject_id": bella})
    again = farm.post("/feed-performance/monitors", {"subject_type": "animal", "subject_id": bella})
    assert first["id"] == again["id"], "one monitor per subject and metric"
    patched = farm.patch(
        f"/feed-performance/monitors/{first['id']}",
        {"alert_threshold_percent": 10, "baseline_method_code": "previous_period"},
    )
    assert patched["alert_threshold_percent"] == 10 and patched["baseline_method_code"] == "previous_period"

    # No milk yet: insufficient evidence is named, never read as "no change".
    herd = farm.post(
        "/feed-performance/monitors",
        {"subject_type": "group", "subject_id": farm.herd["id"], "production_metric_code": "milk_l_per_day"},
    )
    assert herd["subject_name"] == "Dairy Does"
    out = farm.post("/feed-performance/evaluate", expect=200)
    assert out["monitors"] == 2 and out["insufficient"] == 2 and out["anomalies"] == 0
    latest = next(m for m in farm.get("/feed-performance/monitors") if m["id"] == herd["id"])[
        "latest_assessment"
    ]
    assert latest["status"] == "insufficient" and latest["feed_related_likelihood"] == "INSUFFICIENT_EVIDENCE"
    assert latest["variance_percent"] is None and latest["confidence_score"] <= 0.2
    assert "not enough evidence" in latest["explanation"] and "not as no change" in latest["explanation"]
    assert latest["suggested_actions"][0]["code"] == "record_more_observations"
    assert farm.alerts() == []
    # A lactation-adjusted baseline needs days in milk the record does not carry:
    # the fallback is said, not hidden.
    farm.patch(f"/feed-performance/monitors/{herd['id']}", {"baseline_method_code": "lactation_adjusted"})
    a = farm.post(f"/feed-performance/monitors/{herd['id']}/evaluate", expect=200)
    assert a["evidence"]["baseline_method_applied"] == "rolling_subject"
    assert "days in milk unknown" in a["evidence"]["baseline_details"]["fallback_reason"]


def test_mix_scores_supplier_rows_intake_alerts_and_the_summary(client, control_db):
    farm = Farm(client, control_db, "S9X").goat_farm()
    clean = farm.complete_mix()
    # The mix is scored the moment it is completed; a mix off its formula is a
    # manufacturing deviation with its own alert.
    score = farm.get(f"/feed-performance/batches/{clean['id']}")
    assert score["mix_code"] == clean["mix_code"] and score["formula_compliance_score"] == 1
    assert (
        score["production_response_score"] is None
    ), "a dimension without evidence is null, never scored as perfect"
    assert score["open_alerts"] == []
    off = farm.complete_mix(bump=1.12)
    score = farm.get(f"/feed-performance/batches/{off['id']}")
    assert score["mix_number"] == off["mix_number"] and score["formula_compliance_score"] < 1
    assert score["evidence"]["worst_component_deviation_pct"] > 5
    assert "formula_compliance" in score["evidence"]["dimensions_available"]
    assert [x["alert_type"] for x in score["open_alerts"]] == ["FORMULA_COMPLIANCE_DEVIATION"]
    alert = next(x for x in farm.alerts() if x["alert_type"] == "FORMULA_COMPLIANCE_DEVIATION")
    assert alert["mix_code"] == off["mix_code"] and alert["severity"] == "high"
    assert "manufacturing deviation" in alert["explanation"] and alert["evidence"]["tolerance_pct"] == 5
    assert alert["suggested_actions"][0]["target_id"] == off["id"]
    usage = farm.get(f"/feed-mixes/{off['mix_number']}")
    assert usage["performance"]["open_alerts"][0]["alert_type"] == "FORMULA_COMPLIANCE_DEVIATION"
    assert usage["performance"]["formula_compliance_score"] < 1 and usage["worst_variance_pct"] > 5
    assert farm.get(f"/feed-mixes/{clean['mix_number']}")["performance"]["open_alerts"] == []
    bell = [n for n in farm.bell("feed_performance_alert") if n["source_id"] == alert["id"]]
    assert len(bell) == 1 and bell[0]["entity_type"] == "feed_batch" and bell[0]["entity_id"] == off["id"]

    # Closing it by hand needs approve and a reason; the evidence has not changed.
    assert (
        client.post(
            f"{API}/feed-performance/alerts/{alert['id']}/resolve", json={"note": "x"}, headers=farm.worker_h
        ).status_code
        == 403
    )
    r = client.post(
        f"{API}/feed-performance/alerts/{alert['id']}/resolve", json={"note": "  "}, headers=farm.h
    )
    assert r.status_code == 422
    closed = farm.post(
        f"/feed-performance/alerts/{alert['id']}/resolve",
        {"note": "Authorised substitution of barley for the day"},
        expect=200,
    )
    assert closed["status"] == "resolved" and closed["resolution_note"].startswith("Authorised")
    assert closed["resolved_by"] == str(farm.owner.id) and closed["resolved_by_name"] == "Rami Owner"
    assert (
        client.post(f"{API}/feed-performance/alerts/{alert['id']}/acknowledge", headers=farm.h).status_code
        == 422
    )
    assert not [n for n in farm.bell("feed_performance_alert") if n["source_id"] == alert["id"]]
    farm.post("/feed-performance/evaluate", expect=200)
    assert not [
        x for x in farm.alerts() if x["alert_type"] == "FORMULA_COMPLIANCE_DEVIATION"
    ], "a condition a person explained is not raised again by the next re-score"

    # Repeated refusals raise an intake alert and count as a confounder.
    herd = farm.herd["id"]
    farm.feed(
        "group", herd, farm.mix["id"], 3, days_ago=1, event_type="refusal", lot_id=clean["output_lot_id"]
    )
    farm.feed(
        "group", herd, farm.mix["id"], 2, days_ago=0, event_type="refusal", lot_id=clean["output_lot_id"]
    )
    out = farm.post("/feed-performance/evaluate", expect=200)
    assert out["intake_alerts"] == 1 and out["batches_scored"] == 2 and out["suppliers"] == 3
    intake = next(x for x in farm.alerts() if x["alert_type"] == "INTAKE_OR_REFUSAL_ANOMALY")
    assert intake["subject_id"] == herd and intake["subject_name"] == "Dairy Does"
    assert "refused feed 2 times" in intake["explanation"]

    # Supplier rows are per supplier × ingredient, from the lots the mixes actually used.
    rows = {(x["supplier_label"], x["product_name"]): x for x in farm.get("/feed-performance/suppliers")}
    assert set(rows) == {
        ("Bekaa Hay Co.", "Barley"),
        ("Al Mashreq", "Alfalfa Hay"),
        ("NutriPlus", "Dairy Premix"),
    }
    barley = rows[("Bekaa Hay Co.", "Barley")]
    assert barley["lot_count"] == 1 and barley["feed_batch_count"] == 2 and barley["average_unit_cost"] == 0.4
    assert barley["unit"] == "kg" and barley["incident_count"] == 0 and barley["purchase_quantity"] == 1000
    assert barley["methodology_code"] == "deterministic_lot_lineage" and 0 <= barley["confidence_score"] <= 1
    assert barley["evidence"]["lots"][0]["lot_code"] == "BAR-01" and len(barley["evidence"]["batches"]) == 2
    lots = farm.get("/feed-performance/ingredient-lots")
    assert {x["lot_code"] for x in lots} == {"BAR-01", "ALF-01", "PMX-01"}
    assert all(
        x["production_response_score"] is None and not x["weak"] for x in lots
    ), "no mix has a production reading yet, so no lot gets a verdict"
    assert sorted(next(x for x in lots if x["lot_code"] == "BAR-01")["mixes"]) == sorted(
        [clean["mix_code"], off["mix_code"]]
    )
    # Quarantining a purchased lot is a supplier incident, re-scored on the spot.
    lot_id = barley["evidence"]["lots"][0]["lot_id"]
    farm.patch(f"/feed-lots/{lot_id}/status", {"status": "quarantined", "reason": "mould"})
    rows = {(x["supplier_label"], x["product_name"]): x for x in farm.get("/feed-performance/suppliers")}
    assert rows[("Bekaa Hay Co.", "Barley")]["incident_count"] == 1

    scores = farm.get("/feed-performance/batches")
    assert [x["mix_code"] for x in scores] == [off["mix_code"], clean["mix_code"]]
    assert all(0 <= x["confidence_score"] <= 1 for x in scores)
    s = farm.get("/feed-performance/summary")
    assert s["batches_scored"] == 2 and s["suppliers_scored"] == 3 and s["open_alerts"] == 1
    assert s["alerts_by_type"] == {"INTAKE_OR_REFUSAL_ANOMALY": 1}
    assert s["model_reference"] == "origami.feed_performance.deterministic" and s["last_evaluated_at"] is None
    value = farm.get("/feed-performance/effective-feed-value", {"days": 7})
    assert value["window_days"] == 7 and "milk volume" in value["missing"]


# ============================================================ bell signals
def test_feed_reorder_and_variance_signals_reach_the_bell(client, control_db):
    farm = Farm(client, control_db, "S9B").goat_farm()
    # A reorder point above the stock: the cycle puts the product on the bell until a task covers it.
    farm.put(
        "/feed-reorder-policies",
        {
            "feed_product_id": farm.premix["id"],
            "reorder_point": 500,
            "lead_time_days": 5,
            "preferred_reorder_quantity": 200,
        },
    )
    assert farm.bell("feed_reorder") == []
    out = farm.post("/scheduler/run", {"job_codes": ["feed_performance_cycle"]}, expect=200)
    assert out["runs"][0]["summary"]["feed_bell_signals"]["reorder"] == 1
    signal = farm.bell("feed_reorder")
    assert (
        len(signal) == 1
        and signal[0]["entity_type"] == "feed_product"
        and signal[0]["entity_id"] == farm.premix["id"]
    )
    assert signal[0]["notification_type"] == "feed_reorder_required" and "Dairy Premix" in signal[0]["title"]
    assert "suggest" in signal[0]["description"] and signal[0]["module_code"] == "feed_nutrition"
    farm.post(f"/feed-inventory/reorder-recommendations/{farm.premix['id']}/acknowledge", {"quantity": 200})
    assert farm.bell("feed_reorder") == [], "a replenishment task covers it"

    # A count off by more than the threshold: a variance signal until the reconciliation is closed.
    now = datetime.now(timezone.utc)
    rec = farm.post(
        "/feed-reconciliations",
        {
            "feed_product_id": farm.barley["id"],
            "period_from": (now - timedelta(days=30)).isoformat(),
            "period_to": (now + timedelta(minutes=1)).isoformat(),
            "counted_closing_quantity": 850,
        },
    )
    assert rec["variance_quantity"] == -150 and rec["status"] == "open"
    signal = farm.bell("feed_reconciliation")
    assert (
        len(signal) == 1
        and signal[0]["notification_type"] == "feed_variance"
        and signal[0]["entity_id"] == rec["id"]
    )
    assert signal[0]["priority"] == "high" and "Barley" in signal[0]["title"]
    farm.post(
        f"/feed-reconciliations/{rec['id']}/close",
        {"post_adjustment": False, "explanation": "Counted the wrong bay"},
        expect=200,
    )
    assert farm.bell("feed_reconciliation") == []
    assert len(farm.get("/notifications", {"limit": 200})["notifications"]) >= 2, "the entries stay, read"
