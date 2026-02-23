from __future__ import annotations

import time

from fastapi.testclient import TestClient

from sunny_scada.db.models import CfgContainer, CfgDataPoint, CfgEquipment, CfgPLC


def _first_writable_digital(client: TestClient) -> tuple[str, int]:
    SessionLocal = client.app.state.db_sessionmaker
    with SessionLocal() as db:
        dp = (
            db.query(CfgDataPoint)
            .filter(CfgDataPoint.category == "write", CfgDataPoint.type == "DIGITAL")
            .order_by(CfgDataPoint.id.asc())
            .first()
        )
        if dp is None:
            dp = CfgDataPoint(
                owner_type="plc",
                owner_id=1,
                label="TEST_CMD_DIGITAL",
                category="write",
                type="DIGITAL",
                address="40001",
            )
            db.add(dp)
            db.commit()
            db.refresh(dp)
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


def test_bit_write_signal_duplicate_equipment_name_returns_400_and_supports_equipment_id(client: TestClient, admin_token: str):
    h = {"Authorization": f"Bearer {admin_token}"}

    plc_names = list(client.app.state.modbus.plc_names())
    assert plc_names
    plc_name = str(plc_names[0])

    SessionLocal = client.app.state.db_sessionmaker
    with SessionLocal() as db:
        plc = db.query(CfgPLC).filter(CfgPLC.name == plc_name).one_or_none()
        if plc is None:
            plc = CfgPLC(name=plc_name, ip="127.0.0.1", port=502)
            db.add(plc)
            db.flush()

        c1 = CfgContainer(plc_id=int(plc.id), name="DupContainer-1", type="Skid")
        c2 = CfgContainer(plc_id=int(plc.id), name="DupContainer-2", type="Skid")
        db.add_all([c1, c2])
        db.flush()

        eq_name = "Duplicate Equipment"
        e1 = CfgEquipment(container_id=int(c1.id), name=eq_name, type="Valve")
        e2 = CfgEquipment(container_id=int(c2.id), name=eq_name, type="Valve")
        db.add_all([e1, e2])
        db.flush()

        cmd_tag = "DUP_CMD"
        dp1 = CfgDataPoint(
            owner_type="equipment",
            owner_id=int(e1.id),
            label=cmd_tag,
            category="write",
            type="DIGITAL",
            address="40021",
        )
        dp2 = CfgDataPoint(
            owner_type="equipment",
            owner_id=int(e2.id),
            label=cmd_tag,
            category="write",
            type="DIGITAL",
            address="40022",
        )
        db.add_all([dp1, dp2])
        db.commit()

        eq1_id = int(e1.id)

    ambiguous = client.post(
        "/bit_write_signal",
        headers=h,
        json={
            "plc": plc_name,
            "equipmentLabel": "Duplicate Equipment",
            "commandTag": "DUP_CMD",
            "bit": 0,
            "value": 1,
        },
    )
    assert ambiguous.status_code == 400
    assert "Multiple equipment matched" in ambiguous.text

    disambiguated = client.post(
        "/bit_write_signal",
        headers=h,
        json={
            "plc": plc_name,
            "equipmentLabel": "Duplicate Equipment",
            "equipmentId": str(eq1_id),
            "commandTag": "DUP_CMD",
            "bit": 0,
            "value": 1,
        },
    )
    assert disambiguated.status_code == 200
