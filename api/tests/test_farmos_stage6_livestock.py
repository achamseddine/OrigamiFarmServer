"""Stage 6 of the FarmOS tablet contract: the generic animal model —
species configuration, the capability resolver, identifiers, animal
groups and the animal care record (conditions, weights). See
docs/FARMOS_API.md and OrigamiFarmOS docs/GENERIC-ANIMAL-CAPABILITY-MODEL.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.common.tenant_router import TenantDataRouter
from app.tenant_api.models import Animal
from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant


def _owner(client, control_db, prefix: str, email: str):
    tenant = create_tenant(control_db, company_code=unique_code(prefix))
    add_farmos_user(control_db, tenant, email, role="owner")
    control_db.commit()
    token = farmos_login(client, email, FARMOS_DEMO_PASSWORD)
    return tenant, token


def _post_animal(client, token, **fields):
    body = {"name": "Bessie", "species": "cow", **fields}
    return client.post("/api/v1/animals", json=body, headers=farmos_headers(token))


# --- Species configuration & capability resolver --------------------------


def test_species_catalog_and_configuration(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-SP", "sp-owner@origami-demo.com")

    species = client.get("/api/v1/species", headers=farmos_headers(token))
    assert species.status_code == 200, species.text
    codes = [s["code"] for s in species.json()]
    assert codes[:4] == ["cow", "sheep", "goat", "horse"]
    assert "layer_hen" in codes and codes[-1] == "other"
    cow = next(s for s in species.json() if s["code"] == "cow")
    assert cow["terminology_json"]["birth_en"] == "Calving"
    assert cow["profiles_json"] == ["dairy", "meat", "breeding", "dual_purpose"]

    config = client.get("/api/v1/species/horse/configuration", headers=farmos_headers(token))
    assert config.status_code == 200, config.text
    body = config.json()
    assert body["species"]["reproduction_mode"] == "viviparous"
    assert [b["name"] for b in body["breeds"]] == ["Anglo-Arab", "Arabian"]
    assert [s["code"] for s in body["life_stages"]] == ["newborn", "young", "adult", "senior"]
    # A horse is never offered "broiler" or "dairy".
    assert {p["code"] for p in body["management_profiles"]} == {"breeding", "work", "companion"}
    rules = {(r["capability_code"], r["enabled"], r["required"]) for r in body["rules"]}
    assert ("MICROCHIP", True, True) in rules
    assert ("EAR_TAG", False, False) in rules
    assert all(r["id"] for r in body["rules"])

    # The same request twice gives the same rule ids — the tablet caches them.
    again = client.get("/api/v1/species/horse/configuration", headers=farmos_headers(token))
    assert [r["id"] for r in again.json()["rules"]] == [r["id"] for r in body["rules"]]

    unknown = client.get("/api/v1/species/dragon/configuration", headers=farmos_headers(token))
    assert unknown.status_code == 422
    assert "dragon" in unknown.json()["detail"]

    caps = client.get("/api/v1/capabilities", headers=farmos_headers(token))
    assert caps.status_code == 200
    assert {c["code"] for c in caps.json()} >= {"EAR_TAG", "MILK_PRODUCTION", "EGG_PRODUCTION", "HEALTH"}


def test_resolver_applies_configuration_then_biology(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-RS", "rs-owner@origami-demo.com")

    def resolve(**body):
        resp = client.post("/api/v1/animal-capabilities/resolve", json=body, headers=farmos_headers(token))
        assert resp.status_code == 200, resp.text
        return resp.json()

    dairy_cow = resolve(species="cow", sex="FEMALE", management_profile="dairy")
    dairy_codes = {c["code"] for c in dairy_cow["capabilities"]}
    assert {"MILK_PRODUCTION", "LACTATION", "PREGNANCY", "EAR_TAG"} <= dairy_codes
    assert dairy_cow["sex"] == "F" and dairy_cow["life_stage"] == "adult"
    assert dairy_cow["required_identifier_types"] == ["EAR_TAG"]
    assert dairy_cow["subject_kind"] == "individual"
    assert "EGG_PRODUCTION" in dairy_cow["suppressed"]

    # A meat cow is configured not to milk; a bull cannot, whatever the profile.
    meat_cow = resolve(species="cow", sex="F", management_profile="meat")
    assert "MILK_PRODUCTION" not in {c["code"] for c in meat_cow["capabilities"]}
    bull = resolve(species="cow", sex="M", management_profile="dairy")
    assert "MILK_PRODUCTION" in bull["suppressed"] and "PREGNANCY" in bull["suppressed"]

    # Hens lay; a broiler flock is vetoed from laying by configuration.
    hen = resolve(species="layer_hen", sex="F", management_profile="layer")
    assert "EGG_PRODUCTION" in {c["code"] for c in hen["capabilities"]}
    assert "PREGNANCY" in hen["suppressed"]
    assert hen["subject_kind"] == "either"
    assert "LEG_BAND" in hen["allowed_identifier_types"]
    broiler = resolve(species="layer_hen", sex="F", management_profile="broiler")
    assert "EGG_PRODUCTION" not in {c["code"] for c in broiler["capabilities"]}

    bad_sex = client.post(
        "/api/v1/animal-capabilities/resolve",
        json={"species": "cow", "sex": "X"},
        headers=farmos_headers(token),
    )
    assert bad_sex.status_code == 422


# --- Registration through the resolver -------------------------------------


def test_registration_enforces_required_and_allowed_identifiers(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-ID", "id-owner@origami-demo.com")

    # A horse must carry a microchip; a bare tag is not one.
    missing = _post_animal(client, token, name="Shadow", species="horse", sex="F", tag="H-1")
    assert missing.status_code == 422, missing.text
    assert "microchip" in missing.json()["detail"].lower()

    # An ear tag is not used on a horse.
    wrong_type = _post_animal(
        client,
        token,
        name="Shadow",
        species="horse",
        sex="F",
        identifiers=[{"identifier_type": "EAR_TAG", "identifier_value": "E-1"}],
    )
    assert wrong_type.status_code == 422
    assert "ear tag" in wrong_type.json()["detail"].lower()

    created = _post_animal(
        client,
        token,
        name="Shadow",
        species="horse",
        sex="F",
        management_profile="work",
        identifiers=[
            {
                "identifier_type": "MICROCHIP",
                "identifier_value": "985000012345678",
                "issuing_authority": "ISO",
            },
            {"identifier_type": "PASSPORT", "identifier_value": "LB-2024-77", "is_primary": True},
        ],
    )
    assert created.status_code == 201, created.text
    body = created.json()
    # The primary identifier's value is mirrored into `tag`.
    assert body["tag"] == "LB-2024-77"
    assert body["management_profile"] == "work"
    assert sorted(i["identifier_type"] for i in body["identifiers"]) == ["MICROCHIP", "PASSPORT"]
    assert [i["identifier_type"] for i in body["identifiers"] if i["is_primary"]] == ["PASSPORT"]
    assert {c["code"] for c in body["capabilities"]["capabilities"]} >= {
        "MICROCHIP",
        "HOOF_CARE",
        "PREGNANCY",
    }
    # The care record keys are present from day one, empty-safe.
    assert body["recent_administrations"] == []
    assert body["active_conditions"] == []
    assert body["latest_weight"] is None
    assert body["withdrawals"]["active"] is False
    assert body["open_health_case"] is None and body["emergency"] is None

    # A species the catalog does not know is refused with the list.
    unknown = _post_animal(client, token, name="Puff", species="dragon", tag="D-1")
    assert unknown.status_code == 422
    assert "cow" in unknown.json()["detail"]


def test_legacy_tag_becomes_primary_identifier_and_is_unique_per_farm(client, control_db):
    tenant_a, token_a = _owner(client, control_db, "FARM-UQA", "uqa-owner@origami-demo.com")
    _tenant_b, token_b = _owner(client, control_db, "FARM-UQB", "uqb-owner@origami-demo.com")

    first = _post_animal(client, token_a, tag="COW-7", sex="F", management_profile="dairy")
    assert first.status_code == 201, first.text
    assert first.json()["identifiers"][0] == {
        **first.json()["identifiers"][0],
        "identifier_type": "EAR_TAG",
        "identifier_value": "COW-7",
        "is_primary": True,
        "status": "active",
    }

    # Ear tag COW-7 is already worn by Bessie on this farm...
    dup = _post_animal(client, token_a, name="Daisy", tag="COW-7")
    assert dup.status_code == 409
    assert "Bessie" in dup.json()["detail"]
    # ...but another farm may use the same farm-issued number.
    other_farm = _post_animal(client, token_b, name="Daisy", tag="COW-7")
    assert other_farm.status_code == 201, other_farm.text

    # Listing carries each animal's active identifiers.
    listing = client.get(
        "/api/v1/animals", params={"farm_id": str(tenant_a.id)}, headers=farmos_headers(token_a)
    )
    assert listing.status_code == 200
    assert [i["identifier_value"] for a in listing.json() for i in a["identifiers"]] == ["COW-7"]


def test_seeded_animal_with_only_a_tag_is_materialised_on_first_read(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-LEG", "leg-owner@origami-demo.com")
    # The way scripts/seed.py and the sync protocol create animals: a row
    # with a tag and no identifier rows at all.
    with TenantDataRouter.session_for(tenant.id) as db:
        animal = Animal(tenant_id=tenant.id, tag="SEED-1", species="layer_hen", name="Henrietta", sex="F")
        db.add(animal)
        db.flush()
        animal_id = str(animal.id)

    idents = client.get(f"/api/v1/animals/{animal_id}/identifiers", headers=farmos_headers(token))
    assert idents.status_code == 200, idents.text
    assert [(i["identifier_type"], i["identifier_value"], i["is_primary"]) for i in idents.json()] == [
        ("LEG_BAND", "SEED-1", True)
    ]
    # Idempotent: a second read does not add a second row.
    again = client.get(f"/api/v1/animals/{animal_id}", headers=farmos_headers(token))
    assert len(again.json()["identifiers"]) == 1


def test_add_and_retire_identifiers(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-RET", "ret-owner@origami-demo.com")
    animal_id = _post_animal(client, token, tag="COW-1", sex="F").json()["id"]

    added = client.post(
        f"/api/v1/animals/{animal_id}/identifiers",
        json={"identifier_type": "RFID", "identifier_value": "982000111222333", "issuing_authority": "ICAR"},
        headers=farmos_headers(token),
    )
    assert added.status_code == 201, added.text
    assert added.json()["is_primary"] is False  # adding does not steal the primary
    rfid_id = added.json()["id"]

    # An identifier type the species does not wear is refused.
    bad = client.post(
        f"/api/v1/animals/{animal_id}/identifiers",
        json={"identifier_type": "LEG_BAND", "identifier_value": "LB-1"},
        headers=farmos_headers(token),
    )
    assert bad.status_code == 422

    # Promote the RFID to primary: `tag` follows it.
    promoted = client.post(
        f"/api/v1/animals/{animal_id}/identifiers",
        json={"identifier_type": "RFID", "identifier_value": "982000999888777", "is_primary": True},
        headers=farmos_headers(token),
    )
    assert promoted.status_code == 201, promoted.text
    detail = client.get(f"/api/v1/animals/{animal_id}", headers=farmos_headers(token)).json()
    assert detail["tag"] == "982000999888777"
    assert sum(1 for i in detail["identifiers"] if i["is_primary"]) == 1

    # The only ear tag cannot be retired: cattle must keep one.
    ear_tag_id = next(i["id"] for i in detail["identifiers"] if i["identifier_type"] == "EAR_TAG")
    kept = client.delete(
        f"/api/v1/animals/{animal_id}/identifiers/{ear_tag_id}", headers=farmos_headers(token)
    )
    assert kept.status_code == 422
    assert "ear tag" in kept.json()["detail"].lower()

    # Retiring the primary RFID hands the primary to the next active one.
    primary_id = next(i["id"] for i in detail["identifiers"] if i["is_primary"])
    retired = client.delete(
        f"/api/v1/animals/{animal_id}/identifiers/{primary_id}", headers=farmos_headers(token)
    )
    assert retired.status_code == 204, retired.text
    after = client.get(f"/api/v1/animals/{animal_id}/identifiers", headers=farmos_headers(token)).json()
    by_id = {i["id"]: i for i in after}
    assert by_id[primary_id]["status"] == "retired" and by_id[primary_id]["is_primary"] is False
    assert sum(1 for i in after if i["is_primary"] and i["status"] == "active") == 1
    assert by_id[rfid_id]["status"] == "active"

    missing = client.delete(
        f"/api/v1/animals/{animal_id}/identifiers/{uuid.uuid4()}", headers=farmos_headers(token)
    )
    assert missing.status_code == 404


def test_editing_tag_retires_the_old_primary(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-TAG", "tag-owner@origami-demo.com")
    animal_id = _post_animal(client, token, tag="OLD-1", sex="F").json()["id"]

    updated = client.put(f"/api/v1/animals/{animal_id}", json={"tag": "NEW-1"}, headers=farmos_headers(token))
    assert updated.status_code == 200, updated.text
    assert updated.json()["tag"] == "NEW-1"
    statuses = {(i["identifier_value"], i["status"]) for i in updated.json()["identifiers"]}
    assert statuses == {("OLD-1", "retired"), ("NEW-1", "active")}

    # The retired tag is still findable, so a treatment that names it resolves.
    found = client.get(
        "/api/v1/animals",
        params={"farm_id": updated.json()["farm_id"], "search": "NEW"},
        headers=farmos_headers(token),
    )
    assert [a["id"] for a in found.json()] == [animal_id]


def test_state_flags_follow_capabilities(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-ST", "st-owner@origami-demo.com")

    bull = _post_animal(client, token, name="Ferdinand", tag="BULL-1", sex="M", pregnant=True)
    assert bull.status_code == 422
    assert "pregnancy" in bull.json()["detail"].lower()

    hen = _post_animal(
        client, token, name="Henrietta", species="layer_hen", tag="H-1", sex="F", lactating=True
    )
    assert hen.status_code == 422

    cow = _post_animal(client, token, tag="COW-2", sex="F", management_profile="dairy", lactating=True)
    assert cow.status_code == 201, cow.text
    animal_id = cow.json()["id"]

    # Re-posting the whole record with the flags as they already are is fine…
    restated = client.put(
        f"/api/v1/animals/{animal_id}",
        json={"lactating": True, "name": "Bessie"},
        headers=farmos_headers(token),
    )
    assert restated.status_code == 200, restated.text
    # …but changing the profile re-judges them: a meat cow is not lactating.
    meat = client.put(
        f"/api/v1/animals/{animal_id}", json={"management_profile": "meat"}, headers=farmos_headers(token)
    )
    assert meat.status_code == 422
    assert "lactation" in meat.json()["detail"].lower()
    ok = client.put(
        f"/api/v1/animals/{animal_id}",
        json={"management_profile": "meat", "lactating": False},
        headers=farmos_headers(token),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["management_profile"] == "meat" and ok.json()["lactating"] is False


def test_species_cannot_change_once_history_exists(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-SPC", "spc-owner@origami-demo.com")
    animal_id = _post_animal(client, token, tag="COW-3", sex="F", management_profile="dairy").json()["id"]

    # No history yet: a correction is allowed.
    corrected = client.put(
        f"/api/v1/animals/{animal_id}", json={"species": "goat"}, headers=farmos_headers(token)
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["species"] == "goat"

    milk = client.post(
        "/api/v1/production/milk",
        json={"animal_id": animal_id, "session": "morning", "liters": 3.5, "destination": "stored"},
        headers=farmos_headers(token),
    )
    assert milk.status_code == 201, milk.text

    locked = client.put(
        f"/api/v1/animals/{animal_id}", json={"species": "sheep"}, headers=farmos_headers(token)
    )
    assert locked.status_code == 409
    assert "cannot be changed" in locked.json()["detail"]


def test_production_writes_respect_biology_and_configured_profile(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-PRD", "prd-owner@origami-demo.com")

    def milk(animal_id):
        return client.post(
            "/api/v1/production/milk",
            json={"animal_id": animal_id, "session": "morning", "liters": 4.0, "destination": "stored"},
            headers=farmos_headers(token),
        )

    # A legacy-style cow (no sex, no profile) is held to biology only.
    legacy = _post_animal(client, token, tag="COW-L").json()["id"]
    assert milk(legacy).status_code == 201
    # A bull never milks.
    bull = _post_animal(client, token, name="Ferdinand", tag="BULL-2", sex="M").json()["id"]
    refused = milk(bull)
    assert refused.status_code == 422
    assert "bull" in refused.json()["detail"].lower()
    # A cow configured for meat does not milk; the same cow as dairy does.
    meat = _post_animal(client, token, name="Rosa", tag="COW-M", sex="F", management_profile="meat").json()[
        "id"
    ]
    assert milk(meat).status_code == 422
    client.put(f"/api/v1/animals/{meat}", json={"management_profile": "dairy"}, headers=farmos_headers(token))
    assert milk(meat).status_code == 201

    # Eggs: a registered broiler flock cannot have an egg record; a layer
    # flock can; a free-text flock label is accepted as before.
    broilers = client.post(
        "/api/v1/animal-groups",
        json={"name": "Broilers B1", "species": "layer_hen", "count": 500, "management_profile": "broiler"},
        headers=farmos_headers(token),
    ).json()
    layers = client.post(
        "/api/v1/animal-groups",
        json={"name": "Layers L1", "species": "layer_hen", "count": 300, "management_profile": "layer"},
        headers=farmos_headers(token),
    ).json()

    def eggs(flock_id):
        return client.post(
            "/api/v1/production/eggs",
            json={"flock_id": flock_id, "total_eggs": 100, "sellable_eggs": 90},
            headers=farmos_headers(token),
        )

    assert eggs(broilers["id"]).status_code == 422
    assert eggs(layers["id"]).status_code == 201
    assert eggs("flock-legacy-label").status_code == 201


# --- Animal groups ---------------------------------------------------------


def test_animal_groups_round_trip_and_capabilities(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-GRP", "grp-owner@origami-demo.com")

    created = client.post(
        "/api/v1/animal-groups",
        json={"name": "Laying flock A", "species": "layer_hen", "count": 420, "management_profile": "layer"},
        headers=farmos_headers(token),
    )
    assert created.status_code == 201, created.text
    group = created.json()
    assert group["farm_id"] == str(tenant.id)
    assert group["group_type"] == "flock" and group["status"] == "healthy"
    group_id = group["id"]

    # Horses are tracked individually — a herd of them is not a group here.
    horses = client.post(
        "/api/v1/animal-groups",
        json={"name": "Stable", "species": "horse", "count": 4},
        headers=farmos_headers(token),
    )
    assert horses.status_code == 422
    assert "individually" in horses.json()["detail"]

    listing = client.get("/api/v1/animal-groups", headers=farmos_headers(token))
    assert [g["name"] for g in listing.json()] == ["Laying flock A"]

    got = client.get(f"/api/v1/animal-groups/{group_id}", headers=farmos_headers(token))
    assert got.status_code == 200 and got.json()["count"] == 420

    caps = client.get(f"/api/v1/animal-groups/{group_id}/capabilities", headers=farmos_headers(token))
    assert caps.status_code == 200
    assert "EGG_PRODUCTION" in {c["code"] for c in caps.json()["capabilities"]}
    assert caps.json()["sex"] == "F"  # an unstated composition resolves as a laying flock

    updated = client.patch(
        f"/api/v1/animal-groups/{group_id}",
        json={"count": 398, "sex_composition": "mixed", "location_label": "Coop 2"},
        headers=farmos_headers(token),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["count"] == 398 and updated.json()["location_label"] == "Coop 2"
    mixed = client.get(f"/api/v1/animal-groups/{group_id}/capabilities", headers=farmos_headers(token)).json()
    assert mixed["sex"] == "U"
    assert "EGG_PRODUCTION" not in {c["code"] for c in mixed["capabilities"]}

    missing = client.get(f"/api/v1/animal-groups/{uuid.uuid4()}", headers=farmos_headers(token))
    assert missing.status_code == 404


def test_animal_groups_are_tenant_isolated(client, control_db):
    _tenant_a, token_a = _owner(client, control_db, "FARM-GA", "ga-owner@origami-demo.com")
    _tenant_b, token_b = _owner(client, control_db, "FARM-GB", "gb-owner@origami-demo.com")

    group_id = client.post(
        "/api/v1/animal-groups",
        json={"name": "Flock A", "species": "layer_hen", "count": 10},
        headers=farmos_headers(token_a),
    ).json()["id"]

    assert client.get("/api/v1/animal-groups", headers=farmos_headers(token_b)).json() == []
    assert client.get(f"/api/v1/animal-groups/{group_id}", headers=farmos_headers(token_b)).status_code == 404
    assert (
        client.patch(
            f"/api/v1/animal-groups/{group_id}", json={"count": 0}, headers=farmos_headers(token_b)
        ).status_code
        == 404
    )
    # Still intact for its owner.
    assert (
        client.get(f"/api/v1/animal-groups/{group_id}", headers=farmos_headers(token_a)).json()["count"] == 10
    )


# --- Care record: conditions and weights -----------------------------------


def test_conditions_are_vet_recorded_and_ended_not_deleted(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-CND", "cnd-owner@origami-demo.com")
    add_farmos_user(
        control_db,
        tenant,
        "cnd-worker@origami-demo.com",
        role="worker",
        grid={"animals": ["view", "edit"], "animal_health": ["view", "create"]},
    )
    add_farmos_user(
        control_db,
        tenant,
        "cnd-vet@origami-demo.com",
        role="veterinarian",
        grid={"animals": ["view"], "animal_health": ["view", "create"]},
    )
    control_db.commit()
    worker = farmos_login(client, "cnd-worker@origami-demo.com", FARMOS_DEMO_PASSWORD)
    vet = farmos_login(client, "cnd-vet@origami-demo.com", FARMOS_DEMO_PASSWORD)
    animal_id = _post_animal(client, token, tag="COW-C", sex="F").json()["id"]

    payload = {
        "condition_type": "allergy",
        "code": "PENICILLIN",
        "label": "Penicillin allergy",
        "severity": "high",
    }
    # Workers record observations; workers do not diagnose.
    denied = client.post(
        f"/api/v1/animals/{animal_id}/conditions", json=payload, headers=farmos_headers(worker)
    )
    assert denied.status_code == 403

    recorded = client.post(
        f"/api/v1/animals/{animal_id}/conditions", json=payload, headers=farmos_headers(vet)
    )
    assert recorded.status_code == 201, recorded.text
    body = recorded.json()
    assert body["code"] == "penicillin" and body["active"] is True
    assert body["recorded_by_name"] == "cnd-vet@origami-demo.com"
    condition_id = body["id"]

    bad_type = client.post(
        f"/api/v1/animals/{animal_id}/conditions",
        json={**payload, "condition_type": "mood"},
        headers=farmos_headers(vet),
    )
    assert bad_type.status_code == 422

    # The worker can read it, and the animal's care record shows it.
    seen = client.get(f"/api/v1/animals/{animal_id}/conditions", headers=farmos_headers(worker))
    assert seen.status_code == 200 and [c["id"] for c in seen.json()] == [condition_id]
    twin = client.get(f"/api/v1/animals/{animal_id}", headers=farmos_headers(worker)).json()
    assert [c["code"] for c in twin["active_conditions"]] == ["penicillin"]

    ended = client.delete(
        f"/api/v1/animals/{animal_id}/conditions/{condition_id}", headers=farmos_headers(vet)
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["active"] is False and ended.json()["ended_at"] is not None
    assert client.get(f"/api/v1/animals/{animal_id}/conditions", headers=farmos_headers(vet)).json() == []
    history = client.get(
        f"/api/v1/animals/{animal_id}/conditions",
        params={"include_ended": "true"},
        headers=farmos_headers(vet),
    )
    assert [c["id"] for c in history.json()] == [condition_id]
    assert (
        client.get(f"/api/v1/animals/{animal_id}", headers=farmos_headers(vet)).json()["active_conditions"]
        == []
    )


def test_weights_are_dated_records_and_the_animal_carries_the_latest(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-WT", "wt-owner@origami-demo.com")
    add_farmos_user(
        control_db, tenant, "wt-viewer@origami-demo.com", role="worker", grid={"animals": ["view"]}
    )
    control_db.commit()
    viewer = farmos_login(client, "wt-viewer@origami-demo.com", FARMOS_DEMO_PASSWORD)

    # A weight given at registration is already a dated record.
    created = _post_animal(client, token, tag="COW-W", sex="F", weight_kg=512.5)
    assert created.status_code == 201, created.text
    animal_id = created.json()["id"]
    assert created.json()["latest_weight"]["weight_kg"] == 512.5
    assert created.json()["latest_weight"]["source_type"] == "animal_update"

    denied = client.post(
        f"/api/v1/animals/{animal_id}/weights", json={"weight_kg": 520}, headers=farmos_headers(viewer)
    )
    assert denied.status_code == 403

    recorded = client.post(
        f"/api/v1/animals/{animal_id}/weights",
        json={"weight_kg": 520, "method": "tape", "notes": "monthly"},
        headers=farmos_headers(token),
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["method"] == "tape"
    assert recorded.json()["recorded_by_name"] == "wt-owner@origami-demo.com"

    # A back-dated weight is kept in history but does not overwrite the latest.
    last_month = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    older = client.post(
        f"/api/v1/animals/{animal_id}/weights",
        json={"weight_kg": 498, "measured_at": last_month},
        headers=farmos_headers(token),
    )
    assert older.status_code == 201, older.text
    twin = client.get(f"/api/v1/animals/{animal_id}", headers=farmos_headers(token)).json()
    assert twin["weight_kg"] == 520.0
    assert twin["latest_weight"]["weight_kg"] == 520.0

    history = client.get(f"/api/v1/animals/{animal_id}/weights", headers=farmos_headers(viewer))
    assert history.status_code == 200
    assert [w["weight_kg"] for w in history.json()] == [520.0, 512.5, 498.0]  # newest first

    for bad in ({"weight_kg": 0}, {"weight_kg": 10, "method": "guess"}):
        assert (
            client.post(
                f"/api/v1/animals/{animal_id}/weights", json=bad, headers=farmos_headers(token)
            ).status_code
            == 422
        )

    # PUT weight_kg goes through the same ledger rather than overwriting.
    edited = client.put(
        f"/api/v1/animals/{animal_id}", json={"weight_kg": 530}, headers=farmos_headers(token)
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["weight_kg"] == 530.0
    assert len(client.get(f"/api/v1/animals/{animal_id}/weights", headers=farmos_headers(token)).json()) == 4
