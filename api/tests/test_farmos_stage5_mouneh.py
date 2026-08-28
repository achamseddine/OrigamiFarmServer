"""Stage 5 (part 1) of the FarmOS tablet contract: module licensing
(GET /modules, POST /modules/{code}/activate) and the Mouneh production
module — recipe versioning, batch planning/consumption/completion, and
sales. See docs/FARMOS_API.md.
"""

from __future__ import annotations

from tests.conftest import farmos_headers, farmos_login, unique_code
from tests.helpers import FARMOS_DEMO_PASSWORD, add_farmos_user, create_tenant


def _owner(client, control_db, prefix: str, email: str):
    tenant = create_tenant(control_db, company_code=unique_code(prefix))
    add_farmos_user(control_db, tenant, email, role="owner")
    control_db.commit()
    token = farmos_login(client, email, FARMOS_DEMO_PASSWORD)
    return tenant, token


# --- Module licensing ------------------------------------------------------


def test_owner_can_activate_a_module_and_it_appears_in_catalog(client, control_db):
    tenant, token = _owner(client, control_db, "FARM-MOD", "modowner@origami-demo.com")

    before = client.get("/api/v1/modules", headers=farmos_headers(token))
    assert before.json() == []

    activated = client.post(
        "/api/v1/modules/mouneh/activate",
        json={"status": "active", "plan": "mouneh_addon"},
        headers=farmos_headers(token),
    )
    assert activated.status_code == 200, activated.text
    body = activated.json()
    assert body["farm_id"] == str(tenant.id)
    assert body["module_code"] == "mouneh"
    assert body["status"] == "active"

    after = client.get("/api/v1/modules", headers=farmos_headers(token))
    assert len(after.json()) == 1

    catalog = client.get("/api/v1/modules/catalog", headers=farmos_headers(token))
    by_code = {e["code"]: e for e in catalog.json()}
    assert by_code["mouneh_production"]["licensed_active"] is True


def test_worker_cannot_activate_a_module(client, control_db):
    tenant = create_tenant(control_db, company_code=unique_code("FARM-MOD2"))
    add_farmos_user(control_db, tenant, "worker@origami-demo.com", role="worker")
    control_db.commit()
    token = farmos_login(client, "worker@origami-demo.com", FARMOS_DEMO_PASSWORD)

    resp = client.post(
        "/api/v1/modules/mouneh/activate", json={}, headers=farmos_headers(token)
    )
    assert resp.status_code == 403


# --- Mouneh: end-to-end production flow -----------------------------------


def _activate_mouneh(client, token):
    resp = client.post(
        "/api/v1/modules/mouneh/activate", json={"status": "active"}, headers=farmos_headers(token)
    )
    assert resp.status_code == 200, resp.text


def test_mouneh_recipe_creation_versions_and_deactivates_the_previous_one(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-MOU", "mouowner@origami-demo.com")
    _activate_mouneh(client, token)

    material = client.post(
        "/api/v1/mouneh/raw-materials",
        json={"name": "Baby Eggplant", "unit": "kg", "default_unit_cost": 1.2, "current_stock": 320},
        headers=farmos_headers(token),
    ).json()

    product = client.post(
        "/api/v1/mouneh/products",
        json={"name": "Makdous", "output_unit": "jar", "default_batch_size": 100},
        headers=farmos_headers(token),
    ).json()

    recipe_v1 = client.post(
        f"/api/v1/mouneh/products/{product['id']}/recipes",
        json={
            "basis_quantity": 100,
            "basis_unit": "jar",
            "items": [
                {"material_id": material["id"], "quantity": 45, "unit": "kg", "loss_percent": 6}
            ],
        },
        headers=farmos_headers(token),
    )
    assert recipe_v1.status_code == 201, recipe_v1.text
    assert recipe_v1.json()["version"] == 1
    assert recipe_v1.json()["active"] is True

    detail = client.get(f"/api/v1/mouneh/products/{product['id']}", headers=farmos_headers(token))
    assert detail.json()["active_recipe"]["id"] == recipe_v1.json()["id"]

    recipe_v2 = client.post(
        f"/api/v1/mouneh/products/{product['id']}/recipes",
        json={
            "basis_quantity": 100,
            "basis_unit": "jar",
            "items": [
                {"material_id": material["id"], "quantity": 50, "unit": "kg", "loss_percent": 5}
            ],
        },
        headers=farmos_headers(token),
    )
    assert recipe_v2.json()["version"] == 2

    detail_after = client.get(f"/api/v1/mouneh/products/{product['id']}", headers=farmos_headers(token))
    assert detail_after.json()["active_recipe"]["id"] == recipe_v2.json()["id"]
    assert detail_after.json()["active_recipe"]["version"] == 2


def test_mouneh_batch_planning_and_completion_creates_finished_goods_and_deducts_stock(
    client, control_db
):
    tenant, token = _owner(client, control_db, "FARM-MOU2", "mouowner2@origami-demo.com")
    _activate_mouneh(client, token)

    material = client.post(
        "/api/v1/mouneh/raw-materials",
        json={"name": "Baby Eggplant", "unit": "kg", "default_unit_cost": 1.2, "current_stock": 320},
        headers=farmos_headers(token),
    ).json()
    product = client.post(
        "/api/v1/mouneh/products",
        json={"name": "Makdous", "output_unit": "jar", "default_batch_size": 100, "shelf_life_days": 365},
        headers=farmos_headers(token),
    ).json()
    client.post(
        f"/api/v1/mouneh/products/{product['id']}/recipes",
        json={
            "basis_quantity": 100,
            "basis_unit": "jar",
            "items": [
                {"material_id": material["id"], "quantity": 45, "unit": "kg", "loss_percent": 6}
            ],
            "cost_components": [
                {
                    "label": "Labor",
                    "cost_type": "labor",
                    "calculation_method": "quantity_x_rate",
                    "quantity": 10,
                    "unit_cost": 5,
                }
            ],
        },
        headers=farmos_headers(token),
    )

    # No recipe -> no batch, for a product that was never given one.
    other_product = client.post(
        "/api/v1/mouneh/products",
        json={"name": "Jam", "output_unit": "jar"},
        headers=farmos_headers(token),
    ).json()
    no_recipe = client.post(
        "/api/v1/mouneh/batches",
        json={"product_id": other_product["id"], "planned_qty": 10},
        headers=farmos_headers(token),
    )
    assert no_recipe.status_code == 422

    batch = client.post(
        "/api/v1/mouneh/batches",
        json={"product_id": product["id"], "planned_qty": 60},
        headers=farmos_headers(token),
    )
    assert batch.status_code == 201, batch.text
    body = batch.json()
    assert body["status"] == "in_progress"
    assert body["batch_code"].startswith("MOU-")
    consumption = body["consumptions"][0]
    assert round(consumption["planned_qty"], 2) == 28.62  # 45 * 0.6 * 1.06
    assert consumption["actual_qty"] is None
    # material_cost = 28.62 * 1.2 = 34.344; labor scaled 10*0.6*5 = 30 -> total 64.344, /60
    assert round(body["planned_total_cost"], 3) == round(34.344 + 30, 3)
    assert round(body["planned_unit_cost"], 4) == round((34.344 + 30) / 60, 4)
    batch_id = body["id"]

    completed = client.post(
        f"/api/v1/mouneh/batches/{batch_id}/complete",
        json={"actual_output_qty": 58, "waste_qty": 2},
        headers=farmos_headers(token),
    )
    assert completed.status_code == 200, completed.text
    completed_body = completed.json()
    assert completed_body["status"] == "completed"
    assert completed_body["actual_total_cost"] == body["planned_total_cost"]
    assert completed_body["consumptions"][0]["actual_qty"] == consumption["planned_qty"]
    assert completed_body["expiry_date"] is not None  # derived from shelf_life_days

    # Raw material stock was deducted by the (auto-filled) actual quantity.
    materials = client.get("/api/v1/mouneh/raw-materials", headers=farmos_headers(token))
    updated_material = next(m for m in materials.json() if m["id"] == material["id"])
    assert round(updated_material["current_stock"], 2) == round(320 - 28.62, 2)

    finished_goods = client.get(
        "/api/v1/mouneh/finished-goods", params={"product_id": product["id"]}, headers=farmos_headers(token)
    )
    assert finished_goods.status_code == 200
    assert finished_goods.json()[0]["quantity_available"] == 58.0
    stock_id = finished_goods.json()[0]["id"]

    # Completing twice is rejected.
    twice = client.post(
        f"/api/v1/mouneh/batches/{batch_id}/complete",
        json={"actual_output_qty": 58},
        headers=farmos_headers(token),
    )
    assert twice.status_code == 422

    sale = client.post(
        "/api/v1/mouneh/sales",
        json={
            "product_id": product["id"],
            "finished_goods_stock_id": stock_id,
            "quantity": 10,
            "unit_price": 6.5,
        },
        headers=farmos_headers(token),
    )
    assert sale.status_code == 201, sale.text
    sale_body = sale.json()
    assert sale_body["batch_id"] == batch_id
    assert sale_body["revenue"] == 65.0
    assert round(sale_body["cost_per_unit"], 4) == round(completed_body["actual_unit_cost"], 4)

    stock_after = client.get(
        "/api/v1/mouneh/finished-goods", params={"product_id": product["id"]}, headers=farmos_headers(token)
    ).json()[0]
    assert stock_after["quantity_available"] == 48.0
    assert stock_after["quantity_sold"] == 10.0

    over_sale = client.post(
        "/api/v1/mouneh/sales",
        json={
            "product_id": product["id"],
            "finished_goods_stock_id": stock_id,
            "quantity": 1000,
            "unit_price": 6.5,
        },
        headers=farmos_headers(token),
    )
    assert over_sale.status_code == 422


def test_mouneh_consume_blocks_negative_stock_unless_overridden(client, control_db):
    _tenant, token = _owner(client, control_db, "FARM-MOU3", "mouowner3@origami-demo.com")
    _activate_mouneh(client, token)

    material = client.post(
        "/api/v1/mouneh/raw-materials",
        json={"name": "Garlic", "unit": "kg", "default_unit_cost": 2.1, "current_stock": 5},
        headers=farmos_headers(token),
    ).json()
    product = client.post(
        "/api/v1/mouneh/products",
        json={"name": "Toum", "output_unit": "jar"},
        headers=farmos_headers(token),
    ).json()
    client.post(
        f"/api/v1/mouneh/products/{product['id']}/recipes",
        json={
            "basis_quantity": 10,
            "basis_unit": "jar",
            "items": [{"material_id": material["id"], "quantity": 2, "unit": "kg"}],
        },
        headers=farmos_headers(token),
    )
    batch = client.post(
        "/api/v1/mouneh/batches",
        json={"product_id": product["id"], "planned_qty": 10},
        headers=farmos_headers(token),
    ).json()

    blocked = client.post(
        f"/api/v1/mouneh/batches/{batch['id']}/consume",
        json={"lines": [{"material_id": material["id"], "actual_qty": 50}]},
        headers=farmos_headers(token),
    )
    assert blocked.status_code == 422

    forced = client.post(
        f"/api/v1/mouneh/batches/{batch['id']}/consume",
        json={"lines": [{"material_id": material["id"], "actual_qty": 50}], "allow_negative": True},
        headers=farmos_headers(token),
    )
    assert forced.status_code == 200, forced.text
    updated_consumption = forced.json()["consumptions"][0]
    assert updated_consumption["actual_qty"] == 50.0
    assert updated_consumption["total_cost"] == 50.0 * 2.1
