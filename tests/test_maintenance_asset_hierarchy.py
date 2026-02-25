from __future__ import annotations

from fastapi.testclient import TestClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_equipment(client: TestClient, token: str, payload: dict) -> dict:
    base = {
        "name": "Asset",
        "location": None,
        "description": None,
        "vendor_id": None,
        "meta": {},
        "parent_id": None,
        "asset_category": None,
        "asset_type": None,
        "criticality": "B",
        "duty_cycle_hours_per_day": None,
        "spares_class": "standard",
        "safety_classification": [],
    }
    base.update(payload or {})
    resp = client.post("/maintenance/equipment", headers=_auth(token), json=base)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_maintenance_asset_tree_path_descendants(client: TestClient, admin_token: str):
    root = _create_equipment(
        client,
        admin_token,
        {
            "name": "Refrigeration System",
            "asset_category": "refrigeration",
            "asset_type": "refrigeration_system",
            "criticality": "A",
        },
    )
    pack = _create_equipment(
        client,
        admin_token,
        {
            "name": "Pack A",
            "parent_id": int(root["id"]),
            "asset_category": "refrigeration",
            "asset_type": "pack",
        },
    )
    child = _create_equipment(
        client,
        admin_token,
        {
            "name": "Compressor 1",
            "parent_id": int(pack["id"]),
            "asset_category": "refrigeration",
            "asset_type": "compressor",
            "safety_classification": ["rotating", "ammonia_exposure"],
        },
    )

    tree_resp = client.get("/maintenance/equipment/tree", headers=_auth(admin_token))
    assert tree_resp.status_code == 200, tree_resp.text
    tree = tree_resp.json()
    assert isinstance(tree, list)
    assert any(int(node.get("id", 0)) == int(root["id"]) for node in tree)

    path_resp = client.get(f"/maintenance/equipment/{int(child['id'])}/path", headers=_auth(admin_token))
    assert path_resp.status_code == 200, path_resp.text
    path = path_resp.json().get("path") or []
    assert [p.get("name") for p in path][-1] == "Compressor 1"

    desc_resp = client.get(f"/maintenance/equipment/{int(root['id'])}/descendants", headers=_auth(admin_token))
    assert desc_resp.status_code == 200, desc_resp.text
    descendants = desc_resp.json().get("descendants") or []
    assert int(pack["id"]) in descendants
    assert int(child["id"]) in descendants


def test_maintenance_asset_governance_validation(client: TestClient, admin_token: str):
    line = _create_equipment(
        client,
        admin_token,
        {
            "name": "Line 1",
            "asset_category": "processing",
            "asset_type": "line",
        },
    )

    invalid = client.post(
        "/maintenance/equipment",
        headers=_auth(admin_token),
        json={
            "name": "Compressor Invalid",
            "location": None,
            "description": None,
            "vendor_id": None,
            "meta": {},
            "parent_id": int(line["id"]),
            "asset_category": "refrigeration",
            "asset_type": "compressor",
            "criticality": "B",
            "duty_cycle_hours_per_day": None,
            "spares_class": "standard",
            "safety_classification": [],
        },
    )
    assert invalid.status_code == 400
    assert "invalid hierarchy" in str(invalid.json().get("detail", "")).lower()
