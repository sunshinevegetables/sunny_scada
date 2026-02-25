from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
import jwt

from sunny_scada.db.models import CfgDataPoint, CfgPLC, HistorianSample, InventoryTransaction


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_instruments_expired_token_returns_401(client: TestClient):
    auth = client.app.state.auth_service
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "iss": auth._jwt_issuer,
        "sub": "1",
        "prt": "user",
        "typ": "access",
        "iat": int((now - dt.timedelta(hours=1)).timestamp()),
        "exp": int((now - dt.timedelta(minutes=5)).timestamp()),
    }
    if auth._jwt_audience:
        payload["aud"] = auth._jwt_audience

    expired_token = jwt.encode(payload, auth._jwt_secret_key, algorithm="HS256")
    r = client.get("/maintenance/instruments", headers=_auth(expired_token))
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid token"


def _create_equipment(client: TestClient, admin_token: str, name: str = "EQ-INS-01") -> int:
    r = client.post(
        "/maintenance/equipment",
        headers=_auth(admin_token),
        json={
            "name": name,
            "location": "Plant Room",
            "description": "Instrument host equipment",
            "vendor_id": None,
            "meta": {},
        },
    )
    assert r.status_code == 200, r.text
    return int(r.json()["id"])


def _create_spare(client: TestClient, admin_token: str, name: str = "Sensor Probe") -> int:
    r = client.post(
        "/maintenance/spare_parts",
        headers=_auth(admin_token),
        json={
            "name": name,
            "vendor_id": None,
            "unit": "pcs",
            "quantity_on_hand": 5,
            "min_stock": 1,
            "meta": {},
        },
    )
    assert r.status_code == 200, r.text
    return int(r.json()["id"])


def _seed_cfg_datapoint(client: TestClient) -> int:
    SessionLocal = client.app.state.db_sessionmaker
    with SessionLocal() as db:
        plc = db.query(CfgPLC).filter(CfgPLC.name == "INST-PLC").one_or_none()
        if plc is None:
            plc = CfgPLC(name="INST-PLC", ip="127.0.0.1", port=502)
            db.add(plc)
            db.flush()

        dp = CfgDataPoint(
            owner_type="plc",
            owner_id=int(plc.id),
            label="INST_DP_1",
            category="read",
            type="INTEGER",
            address="40001",
        )
        db.add(dp)
        db.commit()
        db.refresh(dp)
        return int(dp.id)


def test_instrument_crud_and_list_filters(client: TestClient, admin_token: str):
    equipment_id = _create_equipment(client, admin_token)

    create_resp = client.post(
        "/maintenance/instruments",
        headers=_auth(admin_token),
        json={
            "label": "PT-101",
            "status": "active",
            "equipment_id": equipment_id,
            "vendor_id": None,
            "instrument_type": "pressure",
            "model": "PX-900",
            "serial_number": "SN-PT-101",
            "location": "Line 1",
            "notes": "Primary pressure transmitter",
            "meta": {"critical": True},
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    instrument_id = int(created["id"])
    assert created["equipment"]["id"] == equipment_id
    assert created["vendor"] is None

    get_resp = client.get(f"/maintenance/instruments/{instrument_id}", headers=_auth(admin_token))
    assert get_resp.status_code == 200
    assert get_resp.json()["label"] == "PT-101"

    list_resp = client.get(
        "/maintenance/instruments",
        headers=_auth(admin_token),
        params={"equipment_id": equipment_id, "q": "PT-101", "status": "active", "type": "pressure"},
    )
    assert list_resp.status_code == 200
    rows = list_resp.json()
    assert len(rows) >= 1
    assert any(int(r["id"]) == instrument_id for r in rows)

    upd_resp = client.put(
        f"/maintenance/instruments/{instrument_id}",
        headers=_auth(admin_token),
        json={"status": "inactive", "model": "PX-901"},
    )
    assert upd_resp.status_code == 200
    assert upd_resp.json()["status"] == "inactive"
    assert upd_resp.json()["model"] == "PX-901"

    del_resp = client.delete(f"/maintenance/instruments/{instrument_id}", headers=_auth(admin_token))
    assert del_resp.status_code == 200

    missing = client.get(f"/maintenance/instruments/{instrument_id}", headers=_auth(admin_token))
    assert missing.status_code == 404


def test_instrument_datapoint_mapping_and_calibration(client: TestClient, admin_token: str):
    equipment_id = _create_equipment(client, admin_token, name="EQ-INS-02")
    cfg_dp_id = _seed_cfg_datapoint(client)

    create_resp = client.post(
        "/maintenance/instruments",
        headers=_auth(admin_token),
        json={
            "label": "TT-201",
            "status": "active",
            "equipment_id": equipment_id,
            "instrument_type": "temperature",
            "model": "TX-200",
            "serial_number": "SN-TT-201",
            "location": "Tank 3",
            "meta": {},
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    instrument_id = int(create_resp.json()["id"])

    map_resp = client.post(
        f"/maintenance/instruments/{instrument_id}/datapoints",
        headers=_auth(admin_token),
        json={"cfg_data_point_id": cfg_dp_id, "role": "process_value"},
    )
    assert map_resp.status_code == 200, map_resp.text
    mapped = map_resp.json()
    assert int(mapped["cfg_data_point_id"]) == cfg_dp_id
    assert mapped["datapoint_key"] == f"db-dp:{cfg_dp_id}"
    assert mapped["label"] == "INST_DP_1"
    map_id = int(mapped["id"])

    list_map = client.get(f"/maintenance/instruments/{instrument_id}/datapoints", headers=_auth(admin_token))
    assert list_map.status_code == 200
    list_payload = list_map.json()
    assert len(list_payload) == 1
    assert int(list_payload[0]["id"]) == map_id

    cal_add = client.post(
        f"/maintenance/instruments/{instrument_id}/calibrations",
        headers=_auth(admin_token),
        json={
            "method": "bench",
            "result": "pass",
            "as_found": 10.1,
            "as_left": 10.0,
            "performed_by": "tech",
            "certificate_no": "CAL-001",
            "notes": "ok",
            "meta": {"ambient_c": 24},
        },
    )
    assert cal_add.status_code == 200, cal_add.text
    cal = cal_add.json()
    assert cal["method"] == "bench"

    cal_add_ui_strings = client.post(
        f"/maintenance/instruments/{instrument_id}/calibrations",
        headers=_auth(admin_token),
        json={
            "method": "6.5 Digit Precision Multimeter",
            "result": "Pass",
            "as_found": "+0.5%",
            "as_left": "",
            "performed_by": "Bharti Automation Pvt Ltd",
            "certificate_no": "BA/2025/04/04/4/24",
            "notes": "Additional notes...",
        },
    )
    assert cal_add_ui_strings.status_code == 200, cal_add_ui_strings.text
    cal_ui = cal_add_ui_strings.json()
    assert cal_ui["as_found"] == 0.5
    assert cal_ui["as_left"] is None

    cal_list = client.get(f"/maintenance/instruments/{instrument_id}/calibrations", headers=_auth(admin_token))
    assert cal_list.status_code == 200
    assert len(cal_list.json()) >= 1

    del_map = client.delete(
        f"/maintenance/instruments/{instrument_id}/datapoints/{map_id}",
        headers=_auth(admin_token),
    )
    assert del_map.status_code == 200


def test_instrument_spares_map_endpoints(client: TestClient, admin_token: str):
    equipment_id = _create_equipment(client, admin_token, name="EQ-INS-03")
    spare_part_id = _create_spare(client, admin_token)

    create_resp = client.post(
        "/maintenance/instruments",
        headers=_auth(admin_token),
        json={
            "label": "FT-301",
            "status": "active",
            "equipment_id": equipment_id,
            "instrument_type": "flow",
            "model": "FX-300",
            "serial_number": "SN-FT-301",
            "location": "Pump skid",
            "meta": {},
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    instrument_id = int(create_resp.json()["id"])

    add_spare = client.post(
        f"/maintenance/instruments/{instrument_id}/spares",
        headers=_auth(admin_token),
        json={"spare_part_id": spare_part_id, "qty_per_replacement": 2},
    )
    assert add_spare.status_code == 200, add_spare.text
    spare_map = add_spare.json()
    assert int(spare_map["part_id"]) == spare_part_id
    assert int(spare_map["qty_required"]) == 2

    list_spares = client.get(f"/maintenance/instruments/{instrument_id}/spares", headers=_auth(admin_token))
    assert list_spares.status_code == 200
    rows = list_spares.json()
    assert len(rows) == 1
    assert int(rows[0]["part_id"]) == spare_part_id

    inst_detail = client.get(f"/maintenance/instruments/{instrument_id}", headers=_auth(admin_token))
    assert inst_detail.status_code == 200, inst_detail.text
    detail = inst_detail.json()
    assert len(detail["recommended_spares"]) == 1
    assert int(detail["recommended_spares"][0]["spare_part_id"]) == spare_part_id
    assert "reorder_required" in detail["recommended_spares"][0]
    assert len(detail["current_stock_levels"]) == 1
    assert int(detail["current_stock_levels"][0]["spare_part_id"]) == spare_part_id

    delete_spare = client.delete(
        f"/maintenance/instruments/{instrument_id}/spares/{spare_part_id}",
        headers=_auth(admin_token),
    )
    assert delete_spare.status_code == 200


def test_instrument_health_uses_pv_cfg_datapoint(client: TestClient, admin_token: str):
    equipment_id = _create_equipment(client, admin_token, name="EQ-INS-HEALTH")
    cfg_dp_id = _seed_cfg_datapoint(client)

    create_resp = client.post(
        "/maintenance/instruments",
        headers=_auth(admin_token),
        json={
            "label": "LT-401",
            "status": "active",
            "equipment_id": equipment_id,
            "instrument_type": "temperature",
            "model": "LX-401",
            "serial_number": "SN-LT-401",
            "location": "Vessel 4",
            "meta": {},
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    instrument_id = int(create_resp.json()["id"])

    map_resp = client.post(
        f"/maintenance/instruments/{instrument_id}/datapoints",
        headers=_auth(admin_token),
        json={"cfg_data_point_id": cfg_dp_id, "role": "pv"},
    )
    assert map_resp.status_code == 200, map_resp.text

    now = dt.datetime.now(dt.timezone.utc)
    SessionLocal = client.app.state.db_sessionmaker
    with SessionLocal() as db:
        rows = [
            HistorianSample(
                ts=now - dt.timedelta(minutes=9),
                plc_id="INST-PLC",
                cfg_data_point_id=cfg_dp_id,
                datapoint_id=f"db-dp:{cfg_dp_id}",
                value=10.0,
                quality="good",
                meta={},
            ),
            HistorianSample(
                ts=now - dt.timedelta(minutes=8),
                plc_id="INST-PLC",
                cfg_data_point_id=cfg_dp_id,
                datapoint_id=f"db-dp:{cfg_dp_id}",
                value=10.0,
                quality="good",
                meta={},
            ),
            HistorianSample(
                ts=now - dt.timedelta(minutes=7),
                plc_id="INST-PLC",
                cfg_data_point_id=cfg_dp_id,
                datapoint_id=f"db-dp:{cfg_dp_id}",
                value=10.0,
                quality="good",
                meta={},
            ),
        ]
        db.add_all(rows)
        db.commit()

    h = client.get(
        f"/maintenance/instruments/{instrument_id}/health",
        headers=_auth(admin_token),
        params={"window_minutes": 10, "flatline_minutes": 10, "max_gap_seconds": 30},
    )
    assert h.status_code == 200, h.text
    payload = h.json()
    assert int(payload["instrument_id"]) == instrument_id
    assert int(payload["pv_cfg_data_point_id"]) == cfg_dp_id
    assert int(payload["window_minutes"]) == 10
    assert isinstance(payload["score_0_100"], int)
    assert isinstance(payload["flags"], list)
    assert "flatline_detected" in payload["flags"]
    assert int(payload["sample_count"]) >= 3
    assert payload["simple_stats"]["min"] == 10.0
    assert payload["simple_stats"]["max"] == 10.0


def test_work_order_instrument_integration_and_inventory_close_flow(client: TestClient, admin_token: str):
    equipment_id = _create_equipment(client, admin_token, name="EQ-WO-INS")
    spare_part_id = _create_spare(client, admin_token, name="WO Spare")

    inst_resp = client.post(
        "/maintenance/instruments",
        headers=_auth(admin_token),
        json={
            "label": "PT-WO-01",
            "status": "active",
            "equipment_id": equipment_id,
            "instrument_type": "pressure",
            "model": "P-100",
            "serial_number": "SN-P-100",
            "location": "Header",
            "meta": {},
        },
    )
    assert inst_resp.status_code == 200, inst_resp.text
    instrument_id = int(inst_resp.json()["id"])

    wo_create = client.post(
        "/maintenance/work_orders",
        headers=_auth(admin_token),
        json={
            "equipment_id": equipment_id,
            "instrument_id": instrument_id,
            "title": "Calibrate PT-WO-01",
            "description": "Routine calibration",
            "priority": "normal",
            "assigned_user_id": None,
            "assigned_role_id": None,
            "due_at": None,
            "meta": {},
        },
    )
    assert wo_create.status_code == 200, wo_create.text
    work_order_id = int(wo_create.json()["id"])

    wo_list = client.get(
        "/maintenance/work_orders",
        headers=_auth(admin_token),
        params={"instrument_id": instrument_id},
    )
    assert wo_list.status_code == 200, wo_list.text
    rows = wo_list.json()
    assert len(rows) >= 1
    row = next((r for r in rows if int(r["id"]) == work_order_id), None)
    assert row is not None
    assert int(row["instrument_id"]) == instrument_id
    assert row["instrument"]["id"] == instrument_id

    wo_detail = client.get(f"/maintenance/work_orders/{work_order_id}", headers=_auth(admin_token))
    assert wo_detail.status_code == 200, wo_detail.text
    detail = wo_detail.json()
    assert int(detail["instrument_id"]) == instrument_id
    assert detail["instrument"]["id"] == instrument_id

    move_resp = client.post(
        f"/maintenance/work_orders/{work_order_id}/status",
        headers=_auth(admin_token),
        json={"status": "in_progress"},
    )
    assert move_resp.status_code == 200, move_resp.text

    close_resp = client.post(
        f"/maintenance/work_orders/{work_order_id}/status",
        headers=_auth(admin_token),
        json={
            "status": "done",
            "parts_used": [{"part_id": spare_part_id, "qty_used": 2, "reason": "replacement"}],
        },
    )
    assert close_resp.status_code == 200, close_resp.text

    spare_get = client.get(f"/maintenance/spare_parts/{spare_part_id}", headers=_auth(admin_token))
    assert spare_get.status_code == 200, spare_get.text
    assert int(spare_get.json()["quantity_on_hand"]) == 3

    SessionLocal = client.app.state.db_sessionmaker
    with SessionLocal() as db:
        txn = (
            db.query(InventoryTransaction)
            .filter(
                InventoryTransaction.work_order_id == work_order_id,
                InventoryTransaction.part_id == spare_part_id,
                InventoryTransaction.qty_delta == -2,
            )
            .order_by(InventoryTransaction.id.desc())
            .first()
        )
        assert txn is not None
