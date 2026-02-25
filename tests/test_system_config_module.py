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
            "bitPositions": {
                "0": {"label": "Ready", "class": "status"},
                "1": {"label": "Run", "class": "run-state"},
                "7": {"label": "Trip", "class": "trip-state"},
                "9": {"label": "Pump On", "class": "pump-state"},
            },
        },
    )
    assert r.status_code == 200, r.text
    dp = r.json()
    assert dp["type"] == "DIGITAL"
    assert dp["bitLabels"]["9"] == "Pump On" or dp["bitLabels"][9] == "Pump On"
    assert dp["bitPositions"]["9"]["class"] == "pump-state" or dp["bitPositions"][9]["class"] == "pump-state"
    dp_id = dp["id"]

    # Patch datapoint
    r = client.patch(f"/api/config/data-points/{dp_id}", headers=h, json={"description": "Updated"})
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "Updated"

    # Patch DIGITAL bit definitions (replace-on-write) should not violate unique constraints
    r = client.patch(
        f"/api/config/data-points/{dp_id}",
        headers=h,
        json={
            "bitPositions": {
                "2": {"label": "Auto", "class": "mode"},
                "9": {"label": "Pump On", "class": "pump-state"},
            }
        },
    )
    assert r.status_code == 200, r.text
    dp2 = r.json()
    bit2 = dp2["bitPositions"].get("2") or dp2["bitPositions"].get(2)
    assert bit2 and bit2["label"] == "Auto"

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


def test_meta_container_type_and_group_hierarchy_inheritance(client, admin_token):
    h = _auth_headers(admin_token)

    # Shared group used across hierarchy
    r = client.post(
        "/api/config/datapoint-groups",
        headers=h,
        json={"name": "HIER-GROUP", "description": "shared"},
    )
    assert r.status_code == 200, r.text
    gid = r.json()["id"]

    # Container type meta CRUD
    r = client.post(
        "/api/config/container-types",
        headers=h,
        json={"name": "COND", "description": "Condenser"},
    )
    assert r.status_code == 200, r.text
    container_type_id = r.json()["id"]

    r = client.patch(
        f"/api/config/container-types/{container_type_id}",
        headers=h,
        json={"name": "COND-UPDATED"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "COND-UPDATED"

    # PLC gets group directly
    r = client.post(
        "/api/config/plcs",
        headers=h,
        json={"name": "hier-plc", "ip": "192.168.1.11", "port": 502, "groupId": gid},
    )
    assert r.status_code == 200, r.text
    plc = r.json()
    plc_id = plc["id"]
    assert plc["groupId"] == gid

    # Container inherits PLC group when omitted
    r = client.post(
        f"/api/config/plcs/{plc_id}/containers",
        headers=h,
        json={"name": "COND-01", "type": "COND-UPDATED"},
    )
    assert r.status_code == 200, r.text
    container = r.json()
    container_id = container["id"]
    assert container["groupId"] == gid

    # Equipment inherits Container group when omitted
    r = client.post(
        f"/api/config/containers/{container_id}/equipment",
        headers=h,
        json={"name": "EQ-01", "type": "EVAP"},
    )
    assert r.status_code == 200, r.text
    equipment = r.json()
    equipment_id = equipment["id"]
    assert equipment["groupId"] == gid

    # PLC datapoint inherits PLC group when omitted
    r = client.post(
        f"/api/config/plcs/{plc_id}/data-points",
        headers=h,
        json={
            "label": "PLC_DP",
            "description": "plc-level dp",
            "category": "read",
            "type": "INTEGER",
            "address": "DB1.DBW0",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["groupId"] == gid

    # Equipment datapoint inherits Equipment->Container->PLC group chain when omitted
    r = client.post(
        f"/api/config/equipment/{equipment_id}/data-points",
        headers=h,
        json={
            "label": "EQ_DP",
            "description": "equipment-level dp",
            "category": "read",
            "type": "INTEGER",
            "address": "DB2.DBW0",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["groupId"] == gid

    # Shared group cannot be deleted while used by hierarchy resources/datapoints
    r = client.delete(f"/api/config/datapoint-groups/{gid}", headers=h)
    assert r.status_code == 400, r.text


def test_meta_equipment_type_crud(client, admin_token):
    h = _auth_headers(admin_token)

    r = client.post(
        "/api/config/equipment-types",
        headers=h,
        json={"name": "EVAP", "description": "Evaporator"},
    )
    assert r.status_code == 200, r.text
    equipment_type_id = r.json()["id"]

    r = client.patch(
        f"/api/config/equipment-types/{equipment_type_id}",
        headers=h,
        json={"name": "EVAP-UPDATED"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "EVAP-UPDATED"

    r = client.post(
        "/api/config/plcs",
        headers=h,
        json={"name": "eqtype-plc", "ip": "192.168.1.12", "port": 502},
    )
    assert r.status_code == 200, r.text
    plc_id = r.json()["id"]

    r = client.post(
        f"/api/config/plcs/{plc_id}/containers",
        headers=h,
        json={"name": "C-01", "type": "COND"},
    )
    assert r.status_code == 200, r.text
    container_id = r.json()["id"]

    r = client.post(
        f"/api/config/containers/{container_id}/equipment",
        headers=h,
        json={"name": "E-01", "type": "EVAP-UPDATED"},
    )
    assert r.status_code == 200, r.text

    r = client.delete(f"/api/config/equipment-types/{equipment_type_id}", headers=h)
    assert r.status_code == 400, r.text
