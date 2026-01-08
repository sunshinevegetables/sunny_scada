from __future__ import annotations

import time

from fastapi.testclient import TestClient


def test_command_create_and_exec_success(client: TestClient, admin_token: str):
    h = {"Authorization": f"Bearer {admin_token}"}
    r = client.post(
        "/commands",
        headers=h,
        json={
            "plc_name": "Main PLC",
            "datapoint_id": "COMP_1_WR",
            "kind": "bit",
            "bit": 0,
            "value": 1,
        },
    )
    assert r.status_code == 200
    cmd_id = r.json()["command_id"]

    # wait for executor thread to process
    status = None
    for _ in range(30):
        g = client.get(f"/commands/{cmd_id}", headers=h)
        assert g.status_code == 200
        status = g.json()["status"]
        if status in ("success", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert status == "success"


def test_command_cancel(client: TestClient, admin_token: str):
    h = {"Authorization": f"Bearer {admin_token}"}
    r = client.post(
        "/commands",
        headers=h,
        json={
            "plc_name": "Main PLC",
            "datapoint_id": "COMP_1_WR",
            "kind": "bit",
            "bit": 1,
            "value": 1,
        },
    )
    assert r.status_code == 200
    cmd_id = r.json()["command_id"]

    c = client.post(f"/commands/{cmd_id}/cancel", headers=h)
    assert c.status_code == 200

    g = client.get(f"/commands/{cmd_id}", headers=h)
    assert g.status_code == 200
    assert g.json()["status"] in ("cancelled", "success")
