from __future__ import annotations

import time

from fastapi.testclient import TestClient

from sunny_scada.db.models import CfgDataPoint


def _first_writable_digital(client: TestClient) -> tuple[str, int]:
    SessionLocal = client.app.state.db_sessionmaker
    with SessionLocal() as db:
        dp = (
            db.query(CfgDataPoint)
            .filter(CfgDataPoint.category == "write", CfgDataPoint.type == "DIGITAL")
            .order_by(CfgDataPoint.id.asc())
            .first()
        )
        assert dp is not None, "Expected at least one writable DIGITAL datapoint in seeded DB"
        allowed = sorted({b.bit for b in (dp.bits or [])})
        bit = allowed[0] if allowed else 0
        return f"db-dp:{dp.id}", int(bit)


def test_command_create_and_exec_success(client: TestClient, admin_token: str):
    h = {"Authorization": f"Bearer {admin_token}"}
    datapoint_id, bit = _first_writable_digital(client)
    r = client.post(
        "/commands",
        headers=h,
        json={
            "plc_name": "Main PLC",
            "datapoint_id": datapoint_id,
            "kind": "bit",
            "bit": bit,
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
    datapoint_id, bit = _first_writable_digital(client)
    r = client.post(
        "/commands",
        headers=h,
        json={
            "plc_name": "Main PLC",
            "datapoint_id": datapoint_id,
            "kind": "bit",
            "bit": bit,
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


def test_ws_commands_streams_live_events(client: TestClient, admin_token: str):
    h = {"Authorization": f"Bearer {admin_token}"}
    datapoint_id, bit = _first_writable_digital(client)

    with client.websocket_connect("/ws/commands") as ws:
        ws.send_json({"type": "auth", "access_token": admin_token})
        snap = ws.receive_json()
        assert snap.get("type") == "snapshot"
        assert snap.get("channel") == "commands"

        r = client.post(
            "/commands",
            headers=h,
            json={
                "plc_name": "Main PLC",
                "datapoint_id": datapoint_id,
                "kind": "bit",
                "bit": bit,
                "value": 1,
            },
        )
        assert r.status_code == 200
        cmd_id = r.json()["command_id"]

        seen_statuses = set()
        for _ in range(8):
            msg = ws.receive_json()
            if msg.get("type") != "command_log":
                continue
            command = msg.get("command") or {}
            if command.get("command_id") != cmd_id:
                continue
            event = msg.get("event") or {}
            if event.get("status"):
                seen_statuses.add(event.get("status"))
            if command.get("status") in ("success", "failed", "cancelled"):
                break

        assert "queued" in seen_statuses
        assert any(s in seen_statuses for s in ("executing", "success", "failed", "cancelled"))
