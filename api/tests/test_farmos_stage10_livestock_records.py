"""Stage 10 of the FarmOS tablet contract: livestock records and
reproduction — effective-dated state history, first-class group membership
and the head-count ledger, breeding / pregnancy / birth / lactation /
incubation, the animal's exit, the farm's calendar settings, and the
duplicate guards on milk, breedings and registrations. Ports the reference
backend's test_livestock_records.py, test_livestock_backfill.py and the
animal / milk / breeding parts of test_duplicates_and_approvals.py and
test_registration_status_and_standard_feeds.py (OrigamiFarmOS
database/LIVESTOCK-SCHEMA.md, REPRODUCTION-SCHEMA.md, PRODUCTION-SCHEMA.md
§8, behavioural model §2–§3, §14).

Farm facts the tests lean on (the `farm` fixture): Bella (ear tag 744) is
pregnant 120 days from a recorded insemination, in her 2nd lactation, in
the Dairy Herd with Luna and Clover; Luna calved 90 days ago and her heifer
calf is not yet registered; Daisy (sheep) was served 35 days ago with no
check; the Layer Flock is hybrid (2450 head, two registered hens); a
layer-hen batch is in the incubator.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app.audit.models import AuditEvent
from app.common.tenant_router import TenantDataRouter
from app.farmos import feed_models as fm
from app.farmos import feeding_program_service
from app.farmos import reproduction_models as rm
from app.farmos.livestock_models import AnimalGroup
from app.tenant_api.models import Animal
from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant

API = "/api/v1"
API_ROOT = pathlib.Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "8de412824ec8"


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Farm:
    """One tenant with an owner, a worker (Animals view/create/edit, Egg and
    Milk create — no approve), a veterinarian and an accountant."""

    def __init__(self, client, control_db, prefix: str):
        self.client = client
        self.tenant = create_tenant(control_db, company_code=unique_code(prefix))
        self.farm_id = str(self.tenant.id)
        key = prefix.lower()
        add_farmos_user(control_db, self.tenant, f"{key}-owner@origami-demo.com", role="owner")
        add_farmos_user(
            control_db,
            self.tenant,
            f"{key}-worker@origami-demo.com",
            role="worker",
            grid={
                "animals": ["view", "create", "edit"],
                "egg_production": ["view", "create"],
                "milk_production": ["view", "create"],
            },
        )
        add_farmos_user(
            control_db,
            self.tenant,
            f"{key}-vet@origami-demo.com",
            role="veterinarian",
            grid={"animal_health": ["view", "create", "edit"], "animals": ["view"]},
        )
        add_farmos_user(
            control_db,
            self.tenant,
            f"{key}-acct@origami-demo.com",
            role="accountant",
            grid={"finance": ["view"]},
        )
        control_db.commit()
        self.h = farmos_headers(farmos_login(client, f"{key}-owner@origami-demo.com", FARMOS_DEMO_PASSWORD))
        self.worker = farmos_headers(
            farmos_login(client, f"{key}-worker@origami-demo.com", FARMOS_DEMO_PASSWORD)
        )
        self.vet = farmos_headers(farmos_login(client, f"{key}-vet@origami-demo.com", FARMOS_DEMO_PASSWORD))
        self.acct = farmos_headers(farmos_login(client, f"{key}-acct@origami-demo.com", FARMOS_DEMO_PASSWORD))

    # -- http
    def post(self, path, body, expect=201, h=None):
        r = self.client.post(f"{API}{path}", json=body, headers=h or self.h)
        assert r.status_code == expect, f"POST {path}: {r.status_code} {r.text}"
        return r.json()

    def get(self, path, params=None, h=None, expect=200):
        r = self.client.get(f"{API}{path}", params=params, headers=h or self.h)
        assert r.status_code == expect, f"GET {path}: {r.status_code} {r.text}"
        return r.json()

    def put(self, path, body, expect=200, h=None):
        r = self.client.put(f"{API}{path}", json=body, headers=h or self.h)
        assert r.status_code == expect, f"PUT {path}: {r.status_code} {r.text}"
        return r.json()

    def patch(self, path, body, expect=200, h=None):
        r = self.client.patch(f"{API}{path}", json=body, headers=h or self.h)
        assert r.status_code == expect, f"PATCH {path}: {r.status_code} {r.text}"
        return r.json()

    def raw_post(self, path, body, h=None):
        return self.client.post(f"{API}{path}", json=body, headers=h or self.h)

    def raw_put(self, path, body, h=None):
        return self.client.put(f"{API}{path}", json=body, headers=h or self.h)

    # -- domain shortcuts
    def animal(self, name, **fields):
        return self.post("/animals", {"name": name, "species": "cow", **fields})

    def cow(self, name, tag, **extra):
        return self.animal(
            name,
            sex="F",
            life_stage="adult",
            management_profile="dairy",
            identifiers=[{"identifier_type": "EAR_TAG", "identifier_value": tag, "is_primary": True}],
            **extra,
        )

    def twin(self, animal_id, h=None):
        return self.get(f"/animals/{animal_id}", h=h)

    def group(self, group_id):
        return self.get(f"/animal-groups/{group_id}")

    def members(self, group_id, **params):
        return self.get(f"/animal-groups/{group_id}/members", params=params)

    def session(self):
        return TenantDataRouter.session_for(self.tenant.id)


@pytest.fixture()
def farm(client, control_db):
    f = Farm(client, control_db, "FARM-S10")
    f.herd = f.post("/animal-groups", {"name": "Dairy Herd", "species": "cow", "group_type": "herd"})
    f.goats = f.post(
        "/animal-groups", {"name": "Goat Herd B", "species": "goat", "group_type": "herd", "count": 5}
    )
    f.layers = f.post(
        "/animal-groups",
        {
            "name": "Layer Flock",
            "species": "layer_hen",
            "count": 2450,
            "sex_composition": "female",
            "management_profile": "layer",
        },
    )
    f.pregnant_cows = f.post(
        "/animal-groups", {"name": "Pregnant cows", "species": "cow", "membership_rule": "PREGNANT"}
    )
    f.bella = f.cow(
        "Bella",
        "744",
        lactating=True,
        lactation_cycle=2,
        lactation_started_at=_days_ago(200),
        group_name="Dairy Herd",
        location_label="Barn A",
    )
    f.post(
        f"/animals/{f.bella['id']}/breedings",
        {
            "event_type": "ARTIFICIAL_INSEMINATION",
            "occurred_at": _days_ago(120),
            "sire_reference": "Holstein AI bull Atlas",
        },
    )
    f.post(
        f"/animals/{f.bella['id']}/pregnancy-checks",
        {"result_code": "POSITIVE", "assessment_type": "ULTRASOUND", "assessed_at": _days_ago(80)},
    )
    f.luna = f.cow("Luna", "214", group_name="Dairy Herd", location_label="Barn A")
    f.post(
        f"/animals/{f.luna['id']}/births",
        {
            "occurred_at": _days_ago(90),
            "offspring": [{"outcome_code": "LIVE", "sex": "F", "birth_weight_kg": 38}],
        },
    )
    f.clover = f.cow("Clover", "CLV", lactating=True, lactation_cycle=1, group_name="Dairy Herd")
    f.daisy = f.animal(
        "Daisy",
        species="sheep",
        sex="F",
        life_stage="adult",
        tag="S045",
        service_date=_days_ago(35),
        service_type="NATURAL_MATING",
    )
    f.thunder = f.animal(
        "Thunder",
        species="horse",
        sex="M",
        life_stage="adult",
        management_profile="work",
        identifiers=[{"identifier_type": "MICROCHIP", "identifier_value": "985000000000747"}],
    )
    f.hen = f.animal(
        "Hen 247",
        species="layer_hen",
        sex="F",
        life_stage="adult",
        management_profile="layer",
        tag="247",
        group_name="Layer Flock",
    )
    f.animal(
        "Hen 248",
        species="layer_hen",
        sex="F",
        life_stage="adult",
        management_profile="layer",
        tag="248",
        group_name="Layer Flock",
    )
    f.batch = f.post("/incubations", {"species_code": "layer_hen", "eggs_set": 150, "set_at": _days_ago(12)})
    f.post(
        "/production/milk",
        {"animal_id": f.bella["id"], "session": "morning", "liters": 12, "destination": "stored"},
    )
    return f


# ------------------------------------------------------------------ catalog
def test_species_carry_gestation_and_group_types_are_served(farm):
    species = {s["code"]: s for s in farm.get("/species")}
    assert species["cow"]["gestation_days"] == 283 and species["sheep"]["gestation_days"] == 147
    assert species["duck"]["incubation_days"] == 28 and species["cow"]["incubation_days"] is None
    types = {t["group_type"]: t for t in farm.get("/animal-group-types")}
    assert set(types) == {"herd", "flock", "pen", "batch", "cohort"}
    assert types["cohort"]["exclusive_membership"] is False and types["pen"]["exclusive_membership"] is True
    # A senior dairy cow keeps what she had as an adult.
    senior = farm.post(
        "/animal-capabilities/resolve",
        {"species": "cow", "sex": "F", "life_stage": "senior", "management_profile": "dairy"},
        expect=200,
    )
    assert {"LACTATION", "PREGNANCY", "BREEDING"} <= {c["code"] for c in senior["capabilities"]}


# ------------------------------------------------------------------ state history
class TestStateHistory:
    def test_registration_opens_the_history(self, farm):
        cow = farm.cow("Rima", "T-501", location_label="Calf Barn")
        rows = farm.get(f"/animals/{cow['id']}/history")
        assert {(r["state_type"], r["value"]) for r in rows} == {
            ("STATUS", "active"),
            ("LIFE_STAGE", "adult"),
            ("MANAGEMENT_PROFILE", "dairy"),
            ("LOCATION", "Calf Barn"),
        }
        assert all(r["effective_to"] is None for r in rows)
        assert farm.twin(cow["id"])["recent_history"]

    def test_a_move_closes_the_old_location_and_opens_the_new(self, farm):
        bella = farm.bella["id"]
        before = farm.get(f"/animals/{bella}/history", params={"state_type": "location"})
        assert len(before) == 1 and before[0]["effective_to"] is None
        moved = farm.patch(f"/animals/{bella}", {"location_label": "Isolation Pen"})
        assert moved["location_label"] == "Isolation Pen"
        after = farm.get(f"/animals/{bella}/history", params={"state_type": "LOCATION"})
        assert [r["value"] for r in after] == ["Isolation Pen", "Barn A"]
        assert after[0]["effective_to"] is None and after[1]["effective_to"] is not None

    def test_an_edit_writes_stage_and_profile_history(self, farm):
        thunder = farm.thunder["id"]
        farm.put(f"/animals/{thunder}", {"life_stage": "senior", "management_profile": "work"})
        types = [
            (x["state_type"], x["value"])
            for x in farm.get(f"/animals/{thunder}/history")
            if x["effective_to"] is None
        ]
        assert ("LIFE_STAGE", "senior") in types and ("MANAGEMENT_PROFILE", "work") in types
        assert (
            farm.client.get(
                f"{API}/animals/{thunder}/history", params={"state_type": "colour"}, headers=farm.h
            ).status_code
            == 422
        )


# ------------------------------------------------------------------ pregnancy / lactation are records
class TestNoEditableFlags:
    def test_an_edit_cannot_set_pregnancy_or_lactation(self, farm):
        clover = farm.clover["id"]
        r = farm.raw_put(f"/animals/{clover}", {"pregnant": True})
        assert r.status_code == 422 and "pregnancy check" in r.json()["detail"]
        r = farm.raw_put(f"/animals/{clover}", {"lactating": False})
        assert r.status_code == 422 and "dry-off" in r.json()["detail"]

    def test_resending_the_current_state_is_harmless(self, farm):
        bella = farm.twin(farm.bella["id"])
        body = farm.put(
            f"/animals/{bella['id']}",
            {"name": "Bella", "pregnant": True, "lactating": True, "pregnancy_days": 3},
        )
        assert (
            body["pregnancy_days"] == bella["pregnancy_days"] == 120
        ), "pregnancy_days is derived, never written"

    def test_registration_turns_opening_state_into_episodes(self, farm):
        cow = farm.cow("Hala", "T-502", pregnant=True, pregnancy_days=60, lactating=True, lactation_cycle=2)
        assert (
            cow["pregnant"]
            and cow["pregnancy_days"] == 60
            and cow["lactating"]
            and cow["lactation_cycle"] == 2
        )
        assert cow["expected_due_date"] is not None and cow["pregnancy_started_at"] is not None
        with farm.session() as db:
            episodes = db.scalars(
                select(rm.PregnancyEpisode).where(rm.PregnancyEpisode.animal_id == uuid.UUID(cow["id"]))
            ).all()
            assert [(e.status, e.source) for e in episodes] == [("active", "registration")]
            lactation = db.scalars(
                select(rm.LactationEpisode).where(rm.LactationEpisode.animal_id == uuid.UUID(cow["id"]))
            ).one()
            assert lactation.lactation_number == 2 and lactation.status == "active"

    def test_profile_change_waits_for_the_dry_off(self, farm):
        clover = farm.clover["id"]
        assert farm.raw_put(f"/animals/{clover}", {"management_profile": "meat"}).status_code == 422
        farm.post(f"/animals/{clover}/dry-off", {}, expect=200)
        body = farm.put(f"/animals/{clover}", {"management_profile": "meat"})
        assert body["management_profile"] == "meat" and body["lactating"] is False


# ------------------------------------------------------------------ groups
class TestGroups:
    def test_a_herd_counts_its_members_and_names_the_animal_s_group(self, farm):
        herd = farm.herd["id"]
        assert farm.group(herd)["count"] == 3 and farm.group(herd)["tracking_mode"] == "MEMBERSHIP_DERIVED"
        assert {m["animal_id"] for m in farm.members(herd)} == {
            farm.bella["id"],
            farm.luna["id"],
            farm.clover["id"],
        }
        cow = farm.cow("Nour", "T-503", group_name="dairy herd")
        assert cow["group_name"] == "Dairy Herd" and farm.group(herd)["count"] == 4

    def test_joining_another_exclusive_group_is_a_move(self, farm):
        luna, herd = farm.luna["id"], farm.herd["id"]
        pen = farm.post("/animal-groups", {"name": "Dry Cow Pen", "species": "cow", "group_type": "pen"})
        assert pen["tracking_mode"] == "MEMBERSHIP_DERIVED"
        farm.post(f"/animal-groups/{pen['id']}/members", {"animal_id": luna, "reason": "drying off"})
        assert farm.twin(luna)["group_name"] == "Dry Cow Pen"
        assert farm.group(herd)["count"] == 2 and farm.group(pen["id"])["count"] == 1
        past = [m for m in farm.members(herd, include_past=True) if m["animal_id"] == luna]
        assert (
            len(past) == 1 and past[0]["current"] is False and past[0]["end_reason"].startswith("moved_to:")
        )
        assert farm.raw_post(f"/animal-groups/{pen['id']}/members", {"animal_id": luna}).status_code == 409

    def test_the_feeding_resolver_follows_the_membership(self, farm):
        clover = farm.clover["id"]
        pen = farm.post("/animal-groups", {"name": "Heifer Pen", "species": "cow", "group_type": "pen"})
        farm.post(f"/animal-groups/{pen['id']}/members", {"animal_id": clover})
        with farm.session() as db:
            animal = db.get(Animal, uuid.UUID(clover))
            group = feeding_program_service.group_for_animal(db, animal)
            assert group is not None and str(group.id) == pen["id"]
            herd = db.get(AnimalGroup, uuid.UUID(farm.herd["id"]))
            assert clover not in {str(a.id) for a in feeding_program_service.animals_in_group(db, herd)}

    def test_a_cohort_overlaps_without_moving_anyone(self, farm):
        bella = farm.bella["id"]
        cohort = farm.post(
            "/animal-groups", {"name": "Spring scan list", "species": "cow", "group_type": "cohort"}
        )
        farm.post(f"/animal-groups/{cohort['id']}/members", {"animal_id": bella})
        twin = farm.twin(bella)
        assert twin["group_name"] == "Dairy Herd"
        # Her herd, the cohort, and the rule group her pregnancy puts her in.
        assert {g["group_name"] for g in twin["groups"]} == {
            "Dairy Herd",
            "Spring scan list",
            "Pregnant cows",
        }

    def test_rule_groups_follow_the_records(self, farm):
        pregnant = farm.pregnant_cows["id"]
        assert farm.group(pregnant)["group_type"] == "cohort"
        assert {m["animal_id"] for m in farm.members(pregnant)} == {farm.bella["id"]}
        open_cows = farm.post(
            "/animal-groups", {"name": "Open cows", "species": "cow", "membership_rule": "not_pregnant"}
        )
        assert {m["animal_id"] for m in farm.members(open_cows["id"])} == {farm.luna["id"], farm.clover["id"]}
        farm.post(f"/animals/{farm.luna['id']}/pregnancy-checks", {"result_code": "POSITIVE"})
        assert {m["animal_id"] for m in farm.members(pregnant)} == {farm.bella["id"], farm.luna["id"]}
        assert {m["animal_id"] for m in farm.members(open_cows["id"])} == {farm.clover["id"]}
        # Rule groups count members; their count is never typed.
        assert farm.group(pregnant)["count"] == 2
        assert (
            farm.raw_post(
                f"/animal-groups/{pregnant}/quantity-events", {"event_type": "ADDITION", "quantity_delta": 1}
            ).status_code
            == 422
        )

    def test_a_group_holds_one_species_and_one_farm(self, farm):
        r = farm.raw_post(f"/animal-groups/{farm.goats['id']}/members", {"animal_id": farm.bella["id"]})
        assert r.status_code == 422 and "one species" in r.json()["detail"]
        assert (
            farm.raw_post(
                f"/animal-groups/{farm.goats['id']}/members", {"animal_id": "no-such-animal"}
            ).status_code
            == 404
        )

    def test_an_edit_moves_the_herd_by_name_and_refuses_a_typo(self, farm):
        clover = farm.clover["id"]
        r = farm.raw_put(f"/animals/{clover}", {"group_name": "Dairy Hrd"})
        assert r.status_code == 422 and "Dairy Herd" in r.json()["detail"]
        body = farm.put(f"/animals/{clover}", {"group_name": ""})
        assert body["group_name"] is None
        assert farm.group(farm.herd["id"])["count"] == 2

    def test_a_flock_count_is_a_ledger(self, farm):
        layers = farm.layers["id"]
        assert farm.group(layers)["tracking_mode"] == "HYBRID", "two registered hens in a counted flock"
        ledger = farm.get(f"/animal-groups/{layers}/quantity-events")
        assert ledger[0]["resulting_quantity"] == 2450 and sum(e["quantity_delta"] for e in ledger) == 2450
        death = farm.post(
            f"/animal-groups/{layers}/quantity-events",
            {"event_type": "death", "quantity_delta": -5, "reason": "Predator"},
        )
        assert death["resulting_quantity"] == 2445 and farm.group(layers)["count"] == 2445
        assert (
            farm.raw_post(
                f"/animal-groups/{layers}/quantity-events", {"event_type": "DEATH", "quantity_delta": 5}
            ).status_code
            == 422
        )
        assert (
            farm.raw_post(
                f"/animal-groups/{layers}/quantity-events", {"event_type": "SALE", "quantity_delta": -99999}
            ).status_code
            == 422
        )
        # A recount typed as a number becomes an adjustment row, not an overwrite.
        farm.patch(f"/animal-groups/{layers}", {"count": 2440})
        latest = farm.get(f"/animal-groups/{layers}/quantity-events")[0]
        assert (latest["event_type"], latest["quantity_delta"], latest["resulting_quantity"]) == (
            "COUNT_ADJUSTMENT",
            -5,
            2440,
        )

    def test_a_herd_that_counts_members_takes_no_head_count_events(self, farm):
        r = farm.raw_post(
            f"/animal-groups/{farm.herd['id']}/quantity-events",
            {"event_type": "ADDITION", "quantity_delta": 1},
        )
        assert r.status_code == 422
        r = farm.raw_post(
            "/animal-groups", {"name": "Odd herd", "species": "cow", "tracking_mode": "SOMETHING"}
        )
        assert r.status_code == 422

    def test_group_names_stay_unique_per_species(self, farm):
        farm.post("/animal-groups", {"name": "Show heifers", "species": "cow"})
        assert farm.raw_post("/animal-groups", {"name": "show HEIFERS", "species": "cow"}).status_code == 409


# ------------------------------------------------------------------ reproduction
class TestBreedingAndPregnancy:
    def test_daisy_is_waiting_for_her_check(self, farm):
        overview = farm.get("/reproduction/overview")
        assert farm.daisy["id"] in {a["animal_id"] for a in overview["awaiting_pregnancy_check"]}
        bella = next(p for p in overview["pregnant"] if p["animal_id"] == farm.bella["id"])
        assert bella["days_pregnant"] == 120 and 150 < bella["days_to_due"] < 170
        assert overview["in_milk_count"] == 3 and len(overview["incubations"]) == 1
        assert overview["calendar"]["heat_cycle_days"] == 21

    def test_a_positive_check_dates_the_pregnancy_from_the_service(self, farm):
        daisy = farm.daisy["id"]
        body = farm.post(
            f"/animals/{daisy}/pregnancy-checks", {"assessment_type": "ultrasound", "result_code": "positive"}
        )
        assert body["pregnancy"]["originating_breeding_event_id"] is not None
        conceived = _dt(body["pregnancy"]["estimated_conception_at"])
        due = _dt(body["pregnancy"]["expected_due_date"])
        assert (due - conceived).days == 147, "a ewe's configured gestation"
        assert body["summary"]["pregnant"] and not body["summary"]["awaiting_pregnancy_check"]
        twin = farm.twin(daisy)
        assert twin["pregnant"] and twin["pregnancy_days"] == 35

    def test_a_negative_check_ends_an_open_pregnancy(self, farm):
        bella = farm.bella["id"]
        body = farm.post(
            f"/animals/{bella}/pregnancy-checks", {"result_code": "NEGATIVE", "assessment_type": "PALPATION"}
        )
        assert body["pregnancy"] is None
        assert farm.twin(bella)["pregnant"] is False
        timeline = farm.get(f"/animals/{bella}/reproduction")
        assert timeline["pregnancies"][0]["outcome_code"] == "NOT_PREGNANT"

    def test_a_pregnant_animal_is_not_bred_again(self, farm):
        assert (
            farm.raw_post(
                f"/animals/{farm.bella['id']}/breedings", {"event_type": "ARTIFICIAL_INSEMINATION"}
            ).status_code
            == 409
        )

    def test_the_sire_must_be_a_male_of_the_species(self, farm):
        r = farm.raw_post(
            f"/animals/{farm.daisy['id']}/breedings",
            {"event_type": "NATURAL_MATING", "male_animal_id": farm.thunder["id"]},
        )
        assert r.status_code == 422 and "horse" in r.json()["detail"]
        r = farm.raw_post(f"/animals/{farm.thunder['id']}/breedings", {"event_type": "HEAT_OBSERVED"})
        assert r.status_code == 422 and "female" in r.json()["detail"]
        r = farm.raw_post(
            f"/animals/{farm.daisy['id']}/breedings",
            {"event_type": "HEAT_OBSERVED", "sire_reference": "Ram 9"},
        )
        assert r.status_code == 422 and "heat" in r.json()["detail"].lower()

    def test_a_hen_is_never_pregnancy_checked(self, farm):
        r = farm.raw_post(f"/animals/{farm.hen['id']}/pregnancy-checks", {"result_code": "POSITIVE"})
        assert r.status_code == 422 and "pregnancy" in r.json()["detail"].lower()

    def test_a_loss_ends_the_pregnancy(self, farm):
        bella = farm.bella["id"]
        body = farm.post(
            f"/animals/{bella}/pregnancy-loss",
            {"outcome_code": "abortion", "notes": "Found at 4 months"},
            expect=200,
        )
        assert body["pregnancy"]["outcome_code"] == "ABORTION"
        assert farm.raw_post(f"/animals/{bella}/pregnancy-loss", {}).status_code == 404

    def test_a_mistaken_breeding_is_voided_not_deleted(self, farm):
        daisy, bella = farm.daisy["id"], farm.bella["id"]
        breeding_id = farm.get(f"/animals/{daisy}/reproduction")["breedings"][0]["id"]
        farm.post(f"/animals/{daisy}/breedings/{breeding_id}/void", {"reason": "Wrong ewe"}, expect=200)
        timeline = farm.get(f"/animals/{daisy}/reproduction")
        assert (
            timeline["breedings"][0]["status"] == "voided"
            and not timeline["summary"]["awaiting_pregnancy_check"]
        )
        # A breeding a pregnancy rests on cannot be voided.
        bella_breeding = farm.get(f"/animals/{bella}/reproduction")["breedings"][0]["id"]
        assert (
            farm.raw_post(f"/animals/{bella}/breedings/{bella_breeding}/void", {"reason": "x"}).status_code
            == 409
        )

    def test_the_technician_must_be_on_the_farm(self, farm):
        r = farm.raw_post(
            f"/animals/{farm.daisy['id']}/breedings",
            {"event_type": "NATURAL_MATING", "technician_user_id": str(uuid.uuid4())},
        )
        assert r.status_code == 422 and "technician" in r.json()["detail"]


class TestBirth:
    CALF = {
        "outcome_code": "LIVE",
        "sex": "F",
        "birth_weight_kg": 40,
        "register_now": True,
        "name": "Bella's calf",
        "identifiers": [{"identifier_type": "EAR_TAG", "identifier_value": "C-1001", "is_primary": True}],
    }

    def test_a_calving_closes_the_pregnancy_registers_the_calf_and_starts_milking(self, farm, control_db):
        bella = farm.bella["id"]
        body = farm.post(f"/animals/{bella}/births", {"assistance_code": "easy", "offspring": [self.CALF]})
        calf_id = body["registered_offspring_ids"][0]
        assert body["birth"]["live_count"] == 1 and body["birth"]["sire_name"] is None
        assert body["birth"]["sire_reference"].startswith("Holstein AI")
        assert body["summary"]["pregnant"] is False and body["summary"]["lactation"]["lactation_number"] == 3
        calf = farm.twin(calf_id)
        assert calf["species"] == "cow" and calf["life_stage"] == "newborn" and calf["dam_tag"] == "744"
        assert calf["relationships"]["dam"]["animal_id"] == bella and calf["tag"] == "C-1001"
        assert calf["acquisition_source"] == "born on farm" and calf["weight_kg"] == 40
        assert farm.twin(bella)["relationships"]["offspring"][0]["animal_id"] == calf_id
        with farm.session() as db:
            lactations = db.scalars(
                select(rm.LactationEpisode)
                .where(rm.LactationEpisode.animal_id == uuid.UUID(bella))
                .order_by(rm.LactationEpisode.started_at)
            ).all()
            assert [(lac.lactation_number, lac.status, lac.end_reason_code) for lac in lactations] == [
                (2, "ended", "NEW_LACTATION"),
                (3, "active", None),
            ]
        # The calf's registration and the birth are both on the audit trail.
        actions = set(
            control_db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.tenant_id == farm.tenant.id, AuditEvent.entity_id.in_((bella, calf_id))
                )
            )
        )
        assert {"animal.birth_recorded", "animal.created"} <= actions

    def test_a_newborn_the_rules_refuse_refuses_the_whole_birth(self, farm):
        bella = farm.bella["id"]
        # A microchip is not an identifier cattle carry: the registration
        # rules refuse it, so the whole birth is refused.
        calf = {
            **self.CALF,
            "identifiers": [{"identifier_type": "MICROCHIP", "identifier_value": "985000111"}],
        }
        r = farm.raw_post(f"/animals/{bella}/births", {"offspring": [calf]})
        assert r.status_code == 422 and r.json()["detail"].startswith("Newborn 1:")
        assert farm.twin(bella)["pregnant"] is True, "nothing half-recorded"
        assert farm.get(f"/animals/{bella}/reproduction")["births"] == []

    def test_a_calf_born_untagged_is_registered_later_from_its_birth(self, farm):
        luna = farm.luna["id"]
        birth = farm.get(f"/animals/{luna}/reproduction")["births"][0]
        assert birth["offspring"][0]["registrable"] is True
        body = {
            "name": "Luna's heifer",
            "identifiers": [{"identifier_type": "EAR_TAG", "identifier_value": "C-0990", "is_primary": True}],
        }
        out = farm.post(f"/births/{birth['id']}/offspring/1/register", body)
        heifer = farm.twin(out["offspring_animal_id"])
        assert (
            heifer["sex"] == "F"
            and heifer["weight_kg"] == 38
            and heifer["relationships"]["dam"]["animal_id"] == luna
        )
        assert farm.raw_post(f"/births/{birth['id']}/offspring/1/register", body).status_code == 409

    def test_stillborn_is_recorded_not_registered(self, farm):
        bella = farm.bella["id"]
        assert (
            farm.raw_post(
                f"/animals/{bella}/births",
                {"offspring": [{"outcome_code": "STILLBORN", "register_now": True}]},
            ).status_code
            == 422
        )
        body = farm.post(
            f"/animals/{bella}/births", {"offspring": [{"outcome_code": "STILLBORN", "sex": "M"}]}
        )
        assert body["birth"]["stillborn_count"] == 1 and body["registered_offspring_ids"] == []

    def test_only_a_live_bearer_gives_birth(self, farm):
        assert (
            farm.raw_post(
                f"/animals/{farm.hen['id']}/births", {"offspring": [{"outcome_code": "LIVE"}]}
            ).status_code
            == 422
        )
        assert (
            farm.raw_post(
                f"/animals/{farm.thunder['id']}/births", {"offspring": [{"outcome_code": "LIVE"}]}
            ).status_code
            == 422
        )

    def test_lambs_can_be_counted_into_a_flock(self, farm):
        flock = farm.post(
            "/animal-groups", {"name": "Lamb Pen", "species": "sheep", "group_type": "pen", "count": 10}
        )
        assert flock["tracking_mode"] == "QUANTITY_MANAGED"
        twins = [{"outcome_code": "LIVE", "destination_group_id": flock["id"]}] * 2
        body = farm.post(f"/animals/{farm.daisy['id']}/births", {"offspring": twins})
        assert body["counted_into_groups"] == 2 and farm.group(flock["id"])["count"] == 12
        assert (
            body["lactation_not_started"] is not None
        ), "a meat-unprofiled ewe has no LACTATION; the birth says so"

    def test_a_second_birth_within_a_month_is_a_possible_duplicate(self, farm):
        bella = farm.bella["id"]
        farm.post(f"/animals/{bella}/births", {"offspring": [{"outcome_code": "LIVE"}]})
        again = farm.raw_post(f"/animals/{bella}/births", {"offspring": [{"outcome_code": "LIVE"}]})
        assert again.status_code == 409 and again.json()["detail"].startswith("Possible duplicate:")
        assert farm.post(
            f"/animals/{bella}/births", {"offspring": [{"outcome_code": "LIVE"}], "confirm_duplicate": True}
        )


class TestLactation:
    def test_dry_off_then_a_new_lactation_counts_up(self, farm):
        clover = farm.clover["id"]
        assert farm.raw_post(f"/animals/{clover}/lactations", {}).status_code == 409
        body = farm.post(f"/animals/{clover}/dry-off", {}, expect=200)
        assert body["summary"]["lactation"] is None
        twin = farm.twin(clover)
        assert twin["lactating"] is False and twin["dried_off_at"] is not None
        body = farm.post(f"/animals/{clover}/lactations", {})
        assert body["lactation"]["lactation_number"] == 2

    def test_only_a_lactating_kind_of_animal_milks(self, farm):
        assert farm.raw_post(f"/animals/{farm.thunder['id']}/lactations", {}).status_code == 422
        assert farm.raw_post(f"/animals/{farm.thunder['id']}/dry-off", {}).status_code == 404

    def test_the_calving_date_of_a_lactation_can_be_corrected(self, farm, control_db):
        clover = farm.clover["id"]
        before = farm.twin(clover)["lactation_started_at"]
        r = farm.client.patch(
            f"{API}/animals/{clover}/lactation",
            json={"started_at": _days_ago(30), "reason": "Date known now"},
            headers=farm.h,
        )
        assert r.status_code == 200, r.text
        assert (
            r.json()["lactation"]["days_in_milk"] == 30
            and farm.twin(clover)["lactation_started_at"] != before
        )
        # A lactation a recorded birth started is dated by that birth.
        r = farm.client.patch(
            f"{API}/animals/{farm.luna['id']}/lactation", json={"started_at": _days_ago(10)}, headers=farm.h
        )
        assert r.status_code == 409
        corrected = control_db.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == farm.tenant.id, AuditEvent.action == "animal.lactation_corrected"
            )
        ).one()
        assert corrected.changes_json["lactation_started_at"]["from"] is not None


# ------------------------------------------------------------------ exit
class TestExit:
    def test_a_sale_closes_what_cannot_continue_and_keeps_the_record(self, farm, control_db):
        bella = farm.bella["id"]
        hay = farm.post(
            "/feed-products",
            {"name": "Alfalfa Hay", "unit_cost": 0.3, "opening_quantity": 500, "is_feedable": True},
        )
        program = farm.post(
            "/feeding-programs",
            {
                "name": "Milkers",
                "code": "MILK-1",
                "components": [{"feed_product_id": hay["id"], "quantity_per_head": 5}],
            },
        )
        farm.post(f"/livestock-subjects/{bella}/feeding-assignments", {"program_id": program["id"]})

        body = farm.post(
            f"/animals/{bella}/exit", {"exit_type": "SOLD", "reason": "Sold to a neighbour"}, expect=200
        )
        assert (
            body["lifecycle_status"] == "sold" and body["active"] is False and body["exited_at"] is not None
        )
        twin = farm.twin(bella)
        assert twin["active"] is False and twin["pregnant"] is False and twin["lactating"] is False
        assert twin["groups"] == [] and twin["lifecycle_status"] == "sold"
        assert farm.group(farm.herd["id"])["count"] == 2
        assert farm.group(farm.pregnant_cows["id"])["count"] == 0
        with farm.session() as db:
            active = db.scalars(
                select(fm.FeedingAssignment).where(
                    fm.FeedingAssignment.subject_id == uuid.UUID(bella),
                    fm.FeedingAssignment.status == "active",
                )
            ).all()
            assert active == []
            assert (
                db.scalar(
                    select(rm.PregnancyEpisode).where(rm.PregnancyEpisode.animal_id == uuid.UUID(bella))
                ).outcome_code
                == "ANIMAL_EXITED"
            )
        # Afterwards: history stays, no milk, no second exit, not in the herd list.
        assert farm.get("/production/milk", params={"farm_id": farm.farm_id})
        r = farm.raw_post(
            "/production/milk",
            {"animal_id": bella, "session": "morning", "liters": 10, "destination": "stored"},
        )
        assert r.status_code == 409 and "no longer on the farm" in r.json()["detail"]
        assert farm.raw_post(f"/animals/{bella}/exit", {"exit_type": "dead"}).status_code == 409
        herd = farm.get("/animals", params={"farm_id": farm.farm_id})
        assert bella not in {a["id"] for a in herd}
        everyone = farm.get("/animals", params={"farm_id": farm.farm_id, "include_exited": True})
        assert bella in {a["id"] for a in everyone}
        exited = control_db.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == farm.tenant.id, AuditEvent.action == "animal.exited"
            )
        ).one()
        assert exited.changes_json == {"lifecycle_status": {"from": "active", "to": "sold"}}

    def test_a_hen_s_death_comes_off_her_flock_s_count(self, farm):
        hen, layers = farm.hen["id"], farm.layers["id"]
        farm.post(f"/animals/{hen}/exit", {"exit_type": "dead"}, expect=200, h=farm.worker)
        assert farm.group(layers)["count"] == 2449
        latest = farm.get(f"/animal-groups/{layers}/quantity-events")[0]
        assert latest["event_type"] == "DEATH" and latest["source_id"] == hen

    def test_a_death_is_anyone_s_to_record_a_sale_is_a_manager_s(self, farm):
        r = farm.raw_post(f"/animals/{farm.thunder['id']}/exit", {"exit_type": "sold"}, h=farm.worker)
        assert r.status_code == 403 and "approve" in r.json()["detail"]
        assert farm.raw_post(f"/animals/{farm.thunder['id']}/exit", {"exit_type": "eaten"}).status_code == 422

    def test_only_an_archived_animal_comes_back(self, farm):
        thunder = farm.thunder["id"]
        farm.put(f"/animals/{thunder}", {"active": False})
        assert farm.twin(thunder)["lifecycle_status"] == "archived"
        body = farm.post(f"/animals/{thunder}/restore", {}, expect=200)
        assert body["active"] is True and farm.twin(thunder)["lifecycle_status"] == "active"
        farm.post(f"/animals/{thunder}/exit", {"exit_type": "sold"}, expect=200)
        assert farm.raw_post(f"/animals/{thunder}/restore", {}).status_code == 409
        # Archiving needs Animals delete; the worker has edit only.
        assert (
            farm.raw_put(f"/animals/{farm.daisy['id']}", {"active": False}, h=farm.worker).status_code == 403
        )


# ------------------------------------------------------------------ incubation
class TestIncubation:
    def test_eggs_hatch_into_a_flock_s_count(self, farm):
        layers = farm.layers["id"]
        batch = farm.get("/incubations")[0]
        assert batch["status"] == "incubating" and batch["days_to_hatch"] == 9
        body = farm.post(
            f"/incubations/{batch['id']}/hatch",
            {"hatched_live": 120, "unhatched": 25, "culled": 5, "destination_group_id": layers},
            expect=200,
        )
        assert body["hatch"]["hatch_rate_percent"] == 80.0 and body["status"] == "hatched"
        assert farm.group(layers)["count"] == 2570
        assert farm.raw_post(f"/incubations/{batch['id']}/hatch", {"hatched_live": 1}).status_code == 409

    def test_the_counts_cannot_exceed_the_eggs_set(self, farm):
        batch = farm.post("/incubations", {"species_code": "duck", "eggs_set": 40})
        assert batch["code"].startswith("INC-")
        assert (_dt(batch["expected_hatch_date"]) - _dt(batch["set_at"])).days == 28
        assert farm.raw_post(f"/incubations/{batch['id']}/hatch", {"hatched_live": 41}).status_code == 422
        body = farm.post(f"/incubations/{batch['id']}/cancel", {"reason": "Power cut"}, expect=200)
        assert body["status"] == "failed"

    def test_incubation_follows_configuration(self, farm):
        assert farm.raw_post("/incubations", {"species_code": "cow", "eggs_set": 5}).status_code == 422
        r = farm.raw_post(
            "/incubations", {"species_code": "layer_hen", "eggs_set": 5, "source_group_id": farm.layers["id"]}
        )
        assert r.status_code == 422, "a laying flock is not a breeding flock"


# ------------------------------------------------------------------ lookup, twin, permissions
class TestLookupAndTwin:
    def test_any_identifier_finds_the_animal(self, farm):
        luna, bella = farm.luna["id"], farm.bella["id"]
        farm.post(
            f"/animals/{luna}/identifiers", {"identifier_type": "RFID", "identifier_value": "982000411223344"}
        )
        found = farm.get("/animals/lookup", params={"value": "982000411223344"})
        assert [(f["animal_id"], f["matched_type"]) for f in found] == [(luna, "RFID")]
        listed = farm.get("/animals", params={"farm_id": farm.farm_id, "search": "9820004112"})
        assert [a["id"] for a in listed] == [luna]
        assert farm.get("/animals/lookup", params={"value": "bella"})[0]["animal_id"] == bella

    def test_a_retired_tag_still_resolves_flagged(self, farm):
        luna = farm.luna["id"]
        farm.post(
            f"/animals/{luna}/identifiers",
            {"identifier_type": "EAR_TAG", "identifier_value": "214-B", "is_primary": True},
        )
        old = next(i for i in farm.twin(luna)["identifiers"] if i["identifier_value"] == "214")
        assert (
            farm.client.delete(f"{API}/animals/{luna}/identifiers/{old['id']}", headers=farm.h).status_code
            == 204
        )
        found = farm.get("/animals/lookup", params={"value": "214"})
        assert found[0]["animal_id"] == luna and found[0]["retired"] is True

    def test_the_twin_carries_the_livestock_record(self, farm):
        twin = farm.twin(farm.luna["id"])
        assert twin["reproduction"]["applies"] == {
            "breeding": True,
            "pregnancy": True,
            "live_birth": True,
            "lactation": True,
        }
        assert twin["reproduction"]["terminology"]["birth_en"] == "Calving"
        assert (
            twin["reproduction"]["last_birth"]["live_count"] == 1
            and twin["reproduction"]["births_count"] == 1
        )
        assert (
            twin["reproduction"]["days_in_milk"] == 90 and twin["reproduction"]["ready_for_breeding"] is True
        )
        assert "Dairy Herd" in {g["group_name"] for g in twin["groups"]} and twin["recent_history"]
        hen = farm.twin(farm.hen["id"])
        assert (
            hen["reproduction"]["applies"]["pregnancy"] is False
            and hen["reproduction"]["applies"]["live_birth"] is False
        )

    def test_who_may_read_and_record(self, farm):
        assert farm.client.get(f"{API}/animals/{farm.bella['id']}", headers=farm.acct).status_code == 403
        assert (
            farm.raw_post(
                f"/animals/{farm.daisy['id']}/breedings", {"event_type": "HEAT_OBSERVED"}, h=farm.acct
            ).status_code
            == 403
        )
        farm.post(
            f"/animals/{farm.daisy['id']}/pregnancy-checks", {"result_code": "INCONCLUSIVE"}, h=farm.vet
        )
        assert farm.get("/reproduction/overview", h=farm.vet)


def test_another_farm_cannot_touch_this_herd(farm, client, control_db):
    other = Farm(client, control_db, "FARM-S10B")
    bella, daisy, herd = farm.bella["id"], farm.daisy["id"], farm.herd["id"]
    assert other.raw_post(f"/animals/{daisy}/breedings", {"event_type": "HEAT_OBSERVED"}).status_code == 404
    assert other.raw_post(f"/animals/{bella}/exit", {"exit_type": "dead"}).status_code == 404
    assert other.client.get(f"{API}/animals/{bella}/reproduction", headers=other.h).status_code == 404
    assert other.client.get(f"{API}/animals/{bella}/history", headers=other.h).status_code == 404
    assert other.raw_post(f"/animal-groups/{herd}/members", {"animal_id": bella}).status_code == 404
    assert other.client.get(f"{API}/animal-groups/{herd}/members", headers=other.h).status_code == 404
    assert other.get("/animals/lookup", params={"value": "744"}) == []
    assert other.get("/incubations") == [] and other.get("/reproduction/overview")["pregnant"] == []
    # Every farm has its own settings row.
    assert other.get("/farms/me")["dry_off_days_before_birth"] == 60
    farm.patch("/farms/me", {"dry_off_days_before_birth": 45})
    assert other.get("/farms/me")["dry_off_days_before_birth"] == 60


# ------------------------------------------------------------------ registration
class TestRegistration:
    def _register(self, farm, h=None, **body):
        payload = {
            "name": "New cow",
            "species": "cow",
            "sex": "F",
            "life_stage": "adult",
            "management_profile": "dairy",
            **body,
        }
        return farm.raw_post("/animals", payload, h=h)

    def _identifiers(self, farm, animal_id):
        return farm.get(f"/animals/{animal_id}/identifiers")

    def test_a_cow_registers_with_nothing_scanned_and_gets_a_farm_number(self, farm):
        r = self._register(farm)
        assert r.status_code == 201, r.text
        body = r.json()
        ids = self._identifiers(farm, body["id"])
        assert [i["identifier_type"] for i in ids] == ["FARM_NUMBER"]
        assert ids[0]["is_primary"] is True
        assert ids[0]["identifier_value"].isdigit() and len(ids[0]["identifier_value"]) >= 4
        assert body["tag"] == ids[0]["identifier_value"]

    def test_farm_numbers_are_one_sequence_and_a_typed_tag_stays_primary(self, farm):
        first = self._register(farm, name="One").json()
        second = self._register(
            farm, name="Two", identifiers=[{"identifier_type": "EAR_TAG", "identifier_value": "LB-9911"}]
        ).json()
        goat = farm.animal("Kid", species="goat", sex="F")
        n1 = int(
            next(i for i in self._identifiers(farm, first["id"]) if i["identifier_type"] == "FARM_NUMBER")[
                "identifier_value"
            ]
        )
        ids2 = self._identifiers(farm, second["id"])
        n2 = int(next(i for i in ids2 if i["identifier_type"] == "FARM_NUMBER")["identifier_value"])
        n3 = int(
            next(i for i in self._identifiers(farm, goat["id"]) if i["identifier_type"] == "FARM_NUMBER")[
                "identifier_value"
            ]
        )
        assert (n2, n3) == (n1 + 1, n1 + 2)  # one farm-level sequence, every species
        assert next(i for i in ids2 if i["is_primary"])["identifier_type"] == "EAR_TAG"
        assert second["tag"] == "LB-9911"

    def test_a_given_farm_number_is_kept_and_a_duplicate_is_still_refused(self, farm):
        r = self._register(farm, identifiers=[{"identifier_type": "FARM_NUMBER", "identifier_value": "0500"}])
        assert r.status_code == 201
        assert [i["identifier_value"] for i in self._identifiers(farm, r.json()["id"])] == ["0500"]
        clash = self._register(
            farm, name="Copy", identifiers=[{"identifier_type": "FARM_NUMBER", "identifier_value": "0500"}]
        )
        assert clash.status_code == 409
        # The next generated number follows the highest one in use.
        nxt = self._register(farm, name="Next").json()
        assert (
            next(i for i in self._identifiers(farm, nxt["id"]) if i["identifier_type"] == "FARM_NUMBER")[
                "identifier_value"
            ]
            == "0501"
        )

    def test_a_horse_registers_without_its_microchip(self, farm):
        foal = farm.animal("Foal", species="horse", sex="F")
        caps = farm.get(f"/animals/{foal['id']}/capabilities")
        assert "MICROCHIP" in caps["required_identifier_types"]  # still expected — shown as missing

    def test_registered_pregnant_from_an_insemination_date(self, farm):
        served = datetime.now(timezone.utc) - timedelta(days=100)
        r = self._register(
            farm,
            pregnant=True,
            service_date=served.isoformat(),
            service_type="ARTIFICIAL_INSEMINATION",
            lactating=True,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["pregnant"] is True and body["lactating"] is True and body["pregnancy_days"] == 100
        assert (_dt(body["expected_due_date"]).date() - served.date()).days == 283
        with farm.session() as db:
            breeding = db.scalar(
                select(rm.BreedingEvent).where(rm.BreedingEvent.female_animal_id == uuid.UUID(body["id"]))
            )
            episode = db.scalar(
                select(rm.PregnancyEpisode).where(rm.PregnancyEpisode.animal_id == uuid.UUID(body["id"]))
            )
            assert breeding.event_type == "ARTIFICIAL_INSEMINATION"
            assert episode.originating_breeding_event_id == breeding.id

    def test_registered_inseminated_but_not_confirmed(self, farm):
        r = self._register(farm, service_date=_days_ago(10), service_type="natural_mating")
        assert r.status_code == 201, r.text
        repro = farm.get(f"/animals/{r.json()['id']}/reproduction")
        assert repro["summary"]["pregnant"] is False
        assert repro["summary"]["last_breeding"]["event_type"] == "NATURAL_MATING"
        assert repro["summary"]["heat_watch_from"] is not None

    def test_an_insemination_past_term_is_refused(self, farm):
        r = self._register(farm, pregnant=True, service_date=_days_ago(400))
        assert r.status_code == 422 and "insemination date" in r.json()["detail"].lower()

    def test_a_senior_dairy_cow_can_be_pregnant_and_in_milk(self, farm):
        r = self._register(farm, life_stage="senior", pregnant=True, lactating=True)
        assert r.status_code == 201, r.text
        assert r.json()["pregnant"] is True and r.json()["lactating"] is True

    def test_an_animal_with_the_same_name_is_asked(self, farm, control_db):
        body = {"name": "Noor", "species": "cow", "sex": "F", "management_profile": "dairy"}
        first = farm.post("/animals", body)
        again = farm.raw_post("/animals", body)
        assert again.status_code == 409 and again.json()["detail"].startswith("Possible duplicate:")
        assert "Noor" in again.json()["detail"]
        confirmed = farm.post("/animals", {**body, "confirm_duplicate": True})
        logged = control_db.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == farm.tenant.id, AuditEvent.action == "duplicate.confirmed"
            )
        ).all()
        assert [(e.entity_id, e.metadata_json["resembles"]) for e in logged] == [
            (confirmed["id"], first["id"])
        ]

    def test_money_on_the_record_is_finance_data(self, farm):
        cow = farm.animal("Priced", sex="F", purchase_cost=1200, h=farm.worker) if False else None  # noqa: F841
        r = self._register(farm, h=farm.worker, name="Priced", purchase_cost=1200)
        assert r.status_code == 201, r.text
        assert r.json()["purchase_cost"] is None, "a worker without Finance cannot store a cost"
        assert self._register(farm, name="Priced owner", purchase_cost=1200).json()["purchase_cost"] == 1200


# ------------------------------------------------------------------ farm settings
class TestFarmSettings:
    def test_the_manager_sets_dry_off_days_and_the_herd_follows(self, farm, control_db):
        assert farm.get("/farms/me")["dry_off_days_before_birth"] == 60
        assert (
            farm.client.patch(
                f"{API}/farms/me", json={"dry_off_days_before_birth": 300}, headers=farm.h
            ).status_code
            == 422
        )
        assert (
            farm.client.patch(
                f"{API}/farms/me", json={"milk_peak_start_days": 80}, headers=farm.h
            ).status_code
            == 422
        )
        body = farm.patch("/farms/me", {"dry_off_days_before_birth": 45, "heat_cycle_days": 20})
        assert body["dry_off_days_before_birth"] == 45 and body["heat_cycle_days"] == 20 and body["name"]

        overview = farm.get("/reproduction/overview")
        assert overview["dry_off_days_before_birth"] == 45 and overview["calendar"]["heat_cycle_days"] == 20
        bella = next(p for p in overview["pregnant"] if p["animal_id"] == farm.bella["id"])
        assert (_dt(bella["expected_due_date"]) - _dt(bella["dry_off_from"])).days == 45
        assert bella["lactating"] is True
        assert bella["dry_off_due"] is (bella["days_to_dry_off"] <= 0)

        summary = farm.get(f"/animals/{farm.bella['id']}/reproduction")["summary"]
        assert summary["dry_off_days_before_birth"] == 45 and summary["dry_off_from"] is not None
        changed = control_db.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == farm.tenant.id, AuditEvent.action == "farm.settings_changed"
            )
        ).one()
        assert changed.changes_json["dry_off_days_before_birth"] == {"from": 60, "to": 45}

    def test_the_farm_calendar_needs_settings_configure(self, farm, client, control_db):
        assert (
            farm.client.patch(
                f"{API}/farms/me", json={"dry_off_days_before_birth": 30}, headers=farm.worker
            ).status_code
            == 403
        )
        add_farmos_user(
            control_db,
            farm.tenant,
            "farm-s10-settings@origami-demo.com",
            role="worker",
            grid={"settings": ["view", "create", "edit"]},
        )
        control_db.commit()
        granted = farmos_headers(
            farmos_login(client, "farm-s10-settings@origami-demo.com", FARMOS_DEMO_PASSWORD)
        )
        assert (
            farm.client.patch(
                f"{API}/farms/me", json={"dry_off_days_before_birth": 30}, headers=granted
            ).status_code
            == 403
        )
        assert (
            farm.client.patch(
                f"{API}/farms/me", json={"dry_off_days_before_birth": 30}, headers=farm.h
            ).status_code
            == 200
        )


# ------------------------------------------------------------------ milk
class TestMilk:
    def test_the_same_milking_twice_in_a_day_is_asked_then_recorded_and_logged(self, farm, control_db):
        luna = farm.luna["id"]
        body = {
            "animal_id": luna,
            "session": "evening",
            "liters": 7.5,
            "destination": "stored",
            "confirm_duplicate": True,
        }
        farm.post("/production/milk", body)
        again = farm.raw_post("/production/milk", {**body, "confirm_duplicate": False, "liters": 7.0})
        assert again.status_code == 409 and again.json()["detail"].startswith("Possible duplicate:")
        assert "evening" in again.json()["detail"]
        farm.post("/production/milk", {**body, "liters": 7.0})
        confirmed = control_db.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == farm.tenant.id,
                AuditEvent.action == "duplicate.confirmed",
                AuditEvent.entity_id == luna,
            )
        ).all()
        assert len(confirmed) == 1 and confirmed[0].metadata_json["resembles"]
        # Another session the same day is simply another milking.
        farm.post("/production/milk", {**body, "session": "morning", "confirm_duplicate": False})

    def test_milk_under_withdrawal_is_withheld_or_not_recorded(self, farm):
        luna = farm.luna["id"]
        farm.post(
            "/health/treatments",
            {
                "entity_type": "animal",
                "entity_id": luna,
                "medication": "Amoxicillin",
                "dose": "20 ml",
                "route": "intramuscular",
                "responsible_user_id": luna,
                "withdrawal_until": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
            },
        )
        for destination in ("sale", "stored", "processed"):
            r = farm.raw_post(
                "/production/milk",
                {"animal_id": luna, "session": "morning", "liters": 9, "destination": destination},
            )
            assert r.status_code == 422 and "withheld" in r.json()["detail"], destination
        withheld = farm.post(
            "/production/milk",
            {"animal_id": luna, "session": "morning", "liters": 9, "destination": "withheld"},
        )
        assert withheld["under_withdrawal_warning"] is True and withheld["destination"] == "withheld"


# ------------------------------------------------------------------ migration backfill
def _alembic(action: str, revision: str) -> None:
    cfg = Config(str(API_ROOT / "alembic_tenant.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations" / "tenant"))
    getattr(command, action)(cfg, revision)


def test_existing_flags_and_herds_become_records(_clean_slate):
    """Migration a1f0c2d4e6b8 on a database that already holds a farm: the
    flags and free-text herd names the old model used come out of the
    upgrade as the records those flags stood for — the old values unchanged."""
    tenant_id = uuid.uuid4()
    a1, a2, a3, a4 = (uuid.uuid4() for _ in range(4))
    fl1, fl2 = uuid.uuid4(), uuid.uuid4()
    _alembic("downgrade", PREVIOUS_REVISION)
    try:
        with TenantDataRouter.session_for(tenant_id) as db:
            db.execute(
                text(
                    "INSERT INTO animal_group (id, tenant_id, version, name, species, group_type, count, "
                    "status, sex_composition) "
                    "VALUES (:id, :t, 1, :name, :species, 'flock', :count, 'healthy', :sex)"
                ),
                [
                    {
                        "id": fl1,
                        "t": tenant_id,
                        "name": "Hens",
                        "species": "layer_hen",
                        "count": 300,
                        "sex": "female",
                    },
                    {"id": fl2, "t": tenant_id, "name": "Ducks", "species": "duck", "count": 0, "sex": None},
                ],
            )
            db.execute(
                text(
                    "INSERT INTO animal (id, tenant_id, version, name, species, sex, status, health_score, "
                    "life_stage, management_profile, location_label, group_name, pregnant, pregnancy_days, "
                    "lactating, lactation_cycle, active, birth_date_estimated) "
                    "VALUES (:id, :t, 1, :name, :species, 'F', 'healthy', 100, 'adult', :profile, :location, "
                    ":group_name, :pregnant, :pregnancy_days, :lactating, :lactation_cycle, :active, false)"
                ),
                [
                    {
                        "id": a1,
                        "t": tenant_id,
                        "name": "Sara",
                        "species": "cow",
                        "profile": "dairy",
                        "location": "Barn",
                        "group_name": "Old Herd",
                        "pregnant": True,
                        "pregnancy_days": 100,
                        "lactating": True,
                        "lactation_cycle": 2,
                        "active": True,
                    },
                    {
                        "id": a2,
                        "t": tenant_id,
                        "name": "Mona",
                        "species": "cow",
                        "profile": None,
                        "location": None,
                        "group_name": "old herd",
                        "pregnant": False,
                        "pregnancy_days": None,
                        "lactating": False,
                        "lactation_cycle": None,
                        "active": True,
                    },
                    {
                        "id": a3,
                        "t": tenant_id,
                        "name": "Hen 1",
                        "species": "layer_hen",
                        "profile": None,
                        "location": None,
                        "group_name": "Hens",
                        "pregnant": False,
                        "pregnancy_days": None,
                        "lactating": False,
                        "lactation_cycle": None,
                        "active": True,
                    },
                    {
                        "id": a4,
                        "t": tenant_id,
                        "name": "Gone",
                        "species": "goat",
                        "profile": None,
                        "location": None,
                        "group_name": "Old Herd",
                        "pregnant": False,
                        "pregnancy_days": None,
                        "lactating": False,
                        "lactation_cycle": None,
                        "active": False,
                    },
                ],
            )
    finally:
        _alembic("upgrade", "head")

    with TenantDataRouter.session_for(tenant_id) as db:

        def q(sql, **params):
            return db.execute(text(sql), params).fetchall()

        # Opening history for every animal; an archived one opens archived.
        assert {
            r[0] for r in q("SELECT state_type FROM animal_state_history WHERE animal_id = :a", a=a1)
        } == {"STATUS", "LIFE_STAGE", "MANAGEMENT_PROFILE", "LOCATION"}
        assert q("SELECT lifecycle_status FROM animal WHERE id = :a", a=a4)[0][0] == "archived"
        assert (
            q(
                "SELECT value_code FROM animal_state_history WHERE animal_id = :a AND state_type = 'STATUS'",
                a=a4,
            )[0][0]
            == "archived"
        )
        assert q("SELECT lifecycle_status FROM animal WHERE id = :a", a=a1)[0][0] == "active"

        # The free-text herd is a group of its species, the existing flock hybrid.
        herds = q("SELECT id, tracking_mode, count, group_type FROM animal_group WHERE name = 'Old Herd'")
        assert len(herds) == 1 and herds[0][1:] == (
            "MEMBERSHIP_DERIVED",
            2,
            "herd",
        ), "both cows, not the archived goat"
        members = {
            r[0]
            for r in q(
                "SELECT animal_id FROM animal_group_membership "
                "WHERE animal_group_id = :g AND valid_to IS NULL",
                g=herds[0][0],
            )
        }
        assert members == {a1, a2}
        assert q("SELECT tracking_mode FROM animal_group WHERE id = :g", g=fl1)[0][0] == "HYBRID"
        assert q("SELECT tracking_mode FROM animal_group WHERE id = :g", g=fl2)[0][0] == "QUANTITY_MANAGED"
        assert q(
            "SELECT event_type, resulting_quantity FROM animal_group_quantity_event "
            "WHERE animal_group_id = :g",
            g=fl1,
        ) == [("INITIAL_COUNT", 300)]
        assert (
            q("SELECT count(*) FROM animal_group_quantity_event WHERE animal_group_id = :g", g=fl2)[0][0] == 0
        )
        assert q("SELECT animal_id FROM animal_group_membership WHERE animal_group_id = :g", g=fl1) == [(a3,)]

        # The flags became the episodes they stood for, the flags untouched.
        pregnancy = q(
            "SELECT status, source, estimated_conception_at, expected_due_date "
            "FROM pregnancy_episode WHERE animal_id = :a",
            a=a1,
        )
        assert len(pregnancy) == 1 and pregnancy[0][:2] == ("active", "backfill")
        assert pregnancy[0][2] is not None and pregnancy[0][3] is not None
        assert q("SELECT status, lactation_number FROM lactation_episode WHERE animal_id = :a", a=a1) == [
            ("active", 2)
        ]
        assert q(
            "SELECT pregnant, lactating, group_name, expected_due_date IS NOT NULL FROM animal WHERE id = :a",
            a=a1,
        )[0] == (True, True, "Old Herd", True)
        assert q("SELECT count(*) FROM pregnancy_episode WHERE animal_id = :a", a=a4)[0][0] == 0
        # The farm's settings row, with the defaults.
        assert q("SELECT dry_off_days_before_birth, heat_cycle_days FROM farm_settings") == [(60, 21)]
