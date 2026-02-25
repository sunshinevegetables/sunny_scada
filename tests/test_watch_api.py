from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from sunny_scada.db.models import CfgContainer, CfgDataPoint, CfgDataPointUnit, CfgEquipment, CfgPLC


def _watch_auth(client: TestClient, username: str, password: str) -> str:
    r = client.post("/api/watch/token", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _seed_cfg(client: TestClient) -> tuple[int, int, int]:
    SessionLocal = client.app.state.db_sessionmaker
    with SessionLocal() as db:
        unit = CfgDataPointUnit(name="degC", description="C")
        db.add(unit)
        db.flush()

        plc = CfgPLC(name="PLC-WATCH", ip="127.0.0.1", port=502)
        db.add(plc)
        db.flush()

        container = CfgContainer(plc_id=plc.id, name="Cold Room", type="ROOM")
        db.add(container)
        db.flush()

        equipment = CfgEquipment(container_id=container.id, name="Cold Room 1", type="EVAP")
        db.add(equipment)
        db.flush()

        dp1 = CfgDataPoint(
            owner_type="equipment",
            owner_id=equipment.id,
            label="Chamber 1 Temperature Sensor A",
            description="Temp",
            category="read",
            type="REAL",
            address="40001",
            unit_id=unit.id,
            multiplier=1.0,
        )
        dp2 = CfgDataPoint(
            owner_type="equipment",
            owner_id=equipment.id,
            label="Chamber 2 Pressure",
            description="Pressure",
            category="read",
            type="REAL",
            address="40002",
            unit_id=unit.id,
            multiplier=1.0,
        )
        db.add(dp1)
        db.add(dp2)
        db.commit()
        return int(dp1.id), int(dp2.id), int(equipment.id)


def test_watch_token_scope_and_ttl(client: TestClient):
    r = client.post("/api/watch/token", json={"username": "admin", "password": "TestPassword!12345"})
    assert r.status_code == 200
    body = r.json()

    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_at"].endswith("Z")

    payload = client.app.state.auth_service.decode_access_token_payload(body["access_token"])
    assert payload.get("scope") == "watch"
    assert int(payload["exp"]) > int(payload["iat"])

    ttl_h = (int(payload["exp"]) - int(payload["iat"])) / 3600.0
    assert 24.0 <= ttl_h <= 72.0


def test_watch_datapoints_search_and_scope_reject(client: TestClient, admin_token: str):
    dp1_id, _, equipment_id = _seed_cfg(client)
    watch_token = _watch_auth(client, "admin", "TestPassword!12345")

    r = client.get(
        f"/api/watch/datapoints?q=chamber&equipment_id={equipment_id}&limit=50",
        headers={"Authorization": f"Bearer {watch_token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["results"], list)
    assert data["results"]
    first = data["results"][0]
    assert first["id"] == dp1_id
    assert len(first["label"]) <= 32
    assert "owner_type" not in first

    non_watch = client.get(
        "/api/watch/datapoints",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert non_watch.status_code == 401


def test_watch_latest_strict_shape_and_quality(client: TestClient):
    dp1_id, dp2_id, _ = _seed_cfg(client)
    watch_token = _watch_auth(client, "admin", "TestPassword!12345")

    old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    client.app.state.storage.update_data(
        "PLC-WATCH",
        {
            f"cfg_dp_{dp1_id}": {"id": dp1_id, "scaled_value": 4.6, "timestamp": old_ts},
            f"cfg_dp_{dp2_id}": {"id": dp2_id, "scaled_value": 9.9, "fault": True},
        },
    )

    r = client.get(
        f"/api/watch/datapoints/latest?ids={dp1_id},{dp2_id},999999",
        headers={"Authorization": f"Bearer {watch_token}"},
    )
    assert r.status_code == 200
    body = r.json()

    assert isinstance(body["values"], dict)
    assert body["ts"].endswith("Z")
    assert str(999999) not in body["values"]

    v1 = body["values"][str(dp1_id)]
    assert v1["quality"] == "stale"
    assert "timestamp" in v1
    assert v1["unit"]

    v2 = body["values"][str(dp2_id)]
    assert v2["quality"] == "error"
    assert "timestamp" in v2


def test_watch_latest_enforces_id_limit(client: TestClient):
    watch_token = _watch_auth(client, "admin", "TestPassword!12345")
    r = client.get(
        "/api/watch/datapoints/latest?ids=1,2,3,4,5,6,7",
        headers={"Authorization": f"Bearer {watch_token}"},
    )
    assert r.status_code == 400


def test_watch_omits_unauthorized_datapoints(client: TestClient, admin_token: str):
    dp1_id, _, equipment_id = _seed_cfg(client)

    create_user = client.post(
        "/admin/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "watch_user", "password": "LongEnoughPassword!234", "roles": []},
    )
    assert create_user.status_code == 200

    user_watch_token = _watch_auth(client, "watch_user", "LongEnoughPassword!234")

    list_resp = client.get(
        f"/api/watch/datapoints?q=chamber&equipment_id={equipment_id}",
        headers={"Authorization": f"Bearer {user_watch_token}"},
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["results"] == []

    latest_resp = client.get(
        f"/api/watch/datapoints/latest?ids={dp1_id}",
        headers={"Authorization": f"Bearer {user_watch_token}"},
    )
    assert latest_resp.status_code == 200
    assert latest_resp.json()["values"] == {}
