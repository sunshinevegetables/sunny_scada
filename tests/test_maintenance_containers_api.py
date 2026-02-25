from __future__ import annotations

from fastapi.testclient import TestClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_maintenance_containers_crud_and_tree(client: TestClient, admin_token: str):
    create_resp = client.post(
        "/maintenance/containers",
        headers=_auth(admin_token),
        json={
            "name": "Refrigeration Hall",
            "location": "Plant A",
            "description": "Main refrigeration container",
            "parent_id": None,
            "asset_category": "refrigeration",
            "asset_type": "refrigeration_system",
            "criticality": "A",
            "duty_cycle_hours_per_day": 24,
            "spares_class": "fast_moving",
            "safety_classification": ["pressure_vessel", "ammonia_exposure"],
            "meta": {},
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    container_id = int(created["id"])
    assert created["container_code"].startswith("MC-")
    assert created["criticality"] == "A"

    list_resp = client.get("/maintenance/containers", headers=_auth(admin_token))
    assert list_resp.status_code == 200, list_resp.text
    rows = list_resp.json()
    assert any(int(r["id"]) == container_id for r in rows)

    tree_resp = client.get("/maintenance/containers/tree", headers=_auth(admin_token))
    assert tree_resp.status_code == 200, tree_resp.text
    tree = tree_resp.json()
    assert isinstance(tree, list)
    assert any(int(node.get("id", 0)) == container_id for node in tree)

    update_resp = client.put(
        f"/maintenance/containers/{container_id}",
        headers=_auth(admin_token),
        json={
            "name": "Refrigeration Hall Updated",
            "location": "Plant A",
            "description": "Updated",
            "parent_id": None,
            "asset_category": "refrigeration",
            "asset_type": "pack",
            "criticality": "B",
            "duty_cycle_hours_per_day": 20,
            "spares_class": "standard",
            "safety_classification": ["rotating"],
            "meta": {"note": "updated"},
        },
    )
    assert update_resp.status_code == 200, update_resp.text
    updated = update_resp.json().get("container") or {}
    assert updated.get("asset_type") == "pack"
    assert updated.get("criticality") == "B"


def test_container_parent_assumptions_equipment_and_instruments(client: TestClient, admin_token: str):
    container_resp = client.post(
        "/maintenance/containers",
        headers=_auth(admin_token),
        json={
            "name": "Container B",
            "location": "Plant B",
            "description": "Container for line B",
            "parent_id": None,
            "asset_category": "processing",
            "asset_type": "line",
            "criticality": "B",
            "duty_cycle_hours_per_day": 16,
            "spares_class": "standard",
            "safety_classification": [],
            "meta": {},
        },
    )
    assert container_resp.status_code == 200, container_resp.text
    container_id = int(container_resp.json()["id"])

    equipment_resp = client.post(
        "/maintenance/equipment",
        headers=_auth(admin_token),
        json={
            "name": "Machine B1",
            "location": "Line B",
            "description": "Machine under container B",
            "vendor_id": None,
            "container_id": container_id,
            "parent_id": None,
            "asset_category": "processing",
            "asset_type": "machine",
            "criticality": "B",
            "duty_cycle_hours_per_day": 12,
            "spares_class": "standard",
            "safety_classification": ["rotating"],
            "meta": {},
        },
    )
    assert equipment_resp.status_code == 200, equipment_resp.text
    equipment = equipment_resp.json()
    equipment_id = int(equipment["id"])
    assert int(equipment["container_id"]) == container_id

    equipment_list_resp = client.get("/maintenance/equipment", headers=_auth(admin_token))
    assert equipment_list_resp.status_code == 200, equipment_list_resp.text
    eq_rows = equipment_list_resp.json()
    target = next((row for row in eq_rows if int(row["id"]) == equipment_id), None)
    assert target is not None
    assert int(target["container_id"]) == container_id

    instrument_resp = client.post(
        "/maintenance/instruments",
        headers=_auth(admin_token),
        json={
            "label": "TT-B1",
            "status": "active",
            "equipment_id": equipment_id,
            "instrument_type": "temperature",
            "model": "TX-1",
            "serial_number": "SN-TTB1",
            "location": "Machine B1",
            "meta": {},
        },
    )
    assert instrument_resp.status_code == 200, instrument_resp.text
    instrument = instrument_resp.json()
    assert int(instrument["equipment_id"]) == equipment_id

    delete_container_with_equipment = client.delete(
        f"/maintenance/containers/{container_id}", headers=_auth(admin_token)
    )
    assert delete_container_with_equipment.status_code == 400

    delete_equipment_resp = client.delete(
        f"/maintenance/equipment/{equipment_id}", headers=_auth(admin_token)
    )
    assert delete_equipment_resp.status_code == 200, delete_equipment_resp.text

    delete_container_resp = client.delete(
        f"/maintenance/containers/{container_id}", headers=_auth(admin_token)
    )
    assert delete_container_resp.status_code == 200, delete_container_resp.text
