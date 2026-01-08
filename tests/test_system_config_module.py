from __future__ import annotations

import pytest


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_system_config_requires_auth(client):
    r = client.get("/api/config/plcs")
    assert r.status_code == 401


def test_plc_container_equipment_datapoint_crud(client, admin_token):
    h = _auth_headers(admin_token)

    # Create PLC
    r = client.post("/api/config/plcs", headers=h, json={"name": "cold_stores", "ip": "192.168.1.10", "port": 502})
    assert r.status_code == 200, r.text
    plc = r.json()
    assert plc["name"] == "cold_stores"

    plc_id = plc["id"]

    # Create container under PLC
    r = client.post(f"/api/config/plcs/{plc_id}/containers", headers=h, json={"name": "COND-01", "type": "COND"})
    assert r.status_code == 200, r.text
    container = r.json()
    assert container["plc_id"] == plc_id
    container_id = container["id"]

    # Create equipment under container
    r = client.post(f"/api/config/containers/{container_id}/equipment", headers=h, json={"name": "EVAP-01", "type": "EVAP"})
    assert r.status_code == 200, r.text
    equipment = r.json()
    assert equipment["container_id"] == container_id
    equipment_id = equipment["id"]

    # Create DIGITAL datapoint with bit labels
    r = client.post(
        f"/api/config/equipment/{equipment_id}/data-points",
        headers=h,
        json={
            "label": "CTRL_STS",
            "description": "Control status word",
            "category": "read",
            "type": "DIGITAL",
            "address": "DB10.DBW2",
            "bitLabels": {"0": "Ready", "1": "Run", "7": "Trip", "9": "Pump On"},
        },
    )
    assert r.status_code == 200, r.text
    dp = r.json()
    assert dp["type"] == "DIGITAL"
    assert dp["bitLabels"]["9"] == "Pump On" or dp["bitLabels"][9] == "Pump On"
    dp_id = dp["id"]

    # Patch datapoint
    r = client.patch(f"/api/config/data-points/{dp_id}", headers=h, json={"description": "Updated"})
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "Updated"

    # Reject bitLabels on non-DIGITAL
    r = client.post(
        f"/api/config/containers/{container_id}/data-points",
        headers=h,
        json={
            "label": "SP",
            "description": "Suction pressure",
            "category": "read",
            "type": "INTEGER",
            "address": "DB1.DBW0",
            "bitLabels": {"0": "x"},
        },
    )
    assert r.status_code == 400

    # Deletion is restricted by default
    r = client.delete(f"/api/config/plcs/{plc_id}", headers=h)
    assert r.status_code == 400

    # Force delete PLC (cascade)
    r = client.delete(f"/api/config/plcs/{plc_id}?force=true", headers=h)
    assert r.status_code == 200

    # PLC should be gone
    r = client.get(f"/api/config/plcs/{plc_id}", headers=h)
    assert r.status_code == 404
