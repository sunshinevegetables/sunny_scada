from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from sunny_scada.db.models import CfgContainer, CfgDataPoint, CfgEquipment, CfgPLC


def test_historian_rollup_and_trends_endpoint(client: TestClient, admin_token: str):
    now = dt.datetime.now(dt.timezone.utc)
    with client.app.state.db_sessionmaker() as db:
        plc = db.query(CfgPLC).filter(CfgPLC.name == "Main PLC").one_or_none()
        if plc is None:
            plc = CfgPLC(name="Main PLC", ip="127.0.0.1", port=502)
            db.add(plc)
            db.flush()

        dp = (
            db.query(CfgDataPoint)
            .filter(
                CfgDataPoint.owner_type == "plc",
                CfgDataPoint.owner_id == int(plc.id),
                CfgDataPoint.label == "DP_X",
            )
            .one_or_none()
        )
        if dp is None:
            dp = CfgDataPoint(
                owner_type="plc",
                owner_id=int(plc.id),
                label="DP_X",
                category="read",
                type="INTEGER",
                address="40001",
            )
            db.add(dp)
            db.flush()

        canonical_key = f"db-dp:{int(dp.id)}"
        snap = {
            "Main PLC": {
                "data": {
                    canonical_key: {"id": int(dp.id), "label": dp.label, "type": "INTEGER", "value": 10}
                }
            }
        }
        client.app.state.historian_service.sample_from_storage(db, storage_snapshot=snap)
        client.app.state.historian_service.rollup_hourly(db)

    h = {"Authorization": f"Bearer {admin_token}"}
    frm = (now - dt.timedelta(hours=1)).isoformat()
    to = (now + dt.timedelta(hours=1)).isoformat()

    r = client.get(
        "/trends",
        headers=h,
        params={"plc_id": "Main PLC", "datapoint_id": canonical_key, "from": frm, "to": to, "bucket": "hour"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["bucket"] == "hour"
    assert isinstance(data["points"], list)

    latest = client.get(
        "/trends/latest",
        headers=h,
        params={"plc_id": "Main PLC", "datapoint_id": canonical_key},
    )
    assert latest.status_code == 200
    assert latest.json()["value"] == 10.0


def test_historian_canonical_datapoint_id_roundtrip(client: TestClient, admin_token: str):
    with client.app.state.db_sessionmaker() as db:
        plc = CfgPLC(name="Main PLC CANON", ip="127.0.0.1", port=502)
        db.add(plc)
        db.flush()
        dp = CfgDataPoint(
            owner_type="plc",
            owner_id=int(plc.id),
            label="DP_CANON",
            category="read",
            type="INTEGER",
            address="40001",
        )
        db.add(dp)
        db.commit()
        db.refresh(dp)
        dp_id = int(dp.id)

        canonical_key = f"db-dp:{dp_id}"
        snap = {"Main PLC CANON": {"data": {canonical_key: {"id": dp_id, "label": dp.label, "type": "INTEGER", "value": 42}}}}
        client.app.state.historian_service.sample_from_storage(db, storage_snapshot=snap)
        client.app.state.historian_service.rollup_hourly(db)

    h = {"Authorization": f"Bearer {admin_token}"}
    now = dt.datetime.now(dt.timezone.utc)
    frm = (now - dt.timedelta(hours=1)).isoformat()
    to = (now + dt.timedelta(hours=1)).isoformat()

    latest = client.get(
        "/trends/latest",
        headers=h,
        params={"plc_id": "Main PLC CANON", "datapoint_id": f"db-dp:{dp_id}"},
    )
    assert latest.status_code == 200
    body = latest.json()
    assert body["value"] == 42.0
    assert int(body["cfg_data_point_id"]) == dp_id

    trends = client.get(
        "/trends",
        headers=h,
        params={"plc_id": "Main PLC CANON", "cfg_data_point_id": dp_id, "from": frm, "to": to, "bucket": "hour"},
    )
    assert trends.status_code == 200
    t_body = trends.json()
    assert int(t_body["cfg_data_point_id"]) == dp_id
    assert isinstance(t_body["points"], list)


def test_trends_legacy_label_ambiguous_returns_409(client: TestClient, admin_token: str):
    with client.app.state.db_sessionmaker() as db:
        plc = CfgPLC(name="Amb PLC", ip="127.0.0.1", port=503)
        db.add(plc)
        db.flush()

        container = CfgContainer(plc_id=int(plc.id), name="C1", type="container")
        db.add(container)
        db.flush()

        equipment = CfgEquipment(container_id=int(container.id), name="E1", type="equipment")
        db.add(equipment)
        db.flush()

        db.add(
            CfgDataPoint(
                owner_type="container",
                owner_id=int(container.id),
                label="DUP_LABEL",
                category="read",
                type="INTEGER",
                address="40100",
            )
        )
        db.add(
            CfgDataPoint(
                owner_type="equipment",
                owner_id=int(equipment.id),
                label="DUP_LABEL",
                category="read",
                type="INTEGER",
                address="40101",
            )
        )
        db.commit()

    h = {"Authorization": f"Bearer {admin_token}"}
    now = dt.datetime.now(dt.timezone.utc)
    frm = (now - dt.timedelta(hours=1)).isoformat()
    to = (now + dt.timedelta(hours=1)).isoformat()

    res = client.get(
        "/trends",
        headers=h,
        params={"plc_id": "Amb PLC", "datapoint_id": "DUP_LABEL", "from": frm, "to": to, "bucket": "hour"},
    )
    assert res.status_code == 409
    detail = res.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("datapoint_id") == "DUP_LABEL"
    assert isinstance(detail.get("candidates"), list)
    assert len(detail.get("candidates")) >= 2
