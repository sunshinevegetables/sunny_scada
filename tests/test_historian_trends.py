from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient


def test_historian_rollup_and_trends_endpoint(client: TestClient, admin_token: str):
    now = dt.datetime.now(dt.timezone.utc)
    snap = {"Main PLC": {"DP_X": {"type": "INTEGER", "value": 10}}}
    with client.app.state.db_sessionmaker() as db:
        client.app.state.historian_service.sample_from_storage(db, storage_snapshot=snap)
        client.app.state.historian_service.rollup_hourly(db)

    h = {"Authorization": f"Bearer {admin_token}"}
    frm = (now - dt.timedelta(hours=1)).isoformat()
    to = (now + dt.timedelta(hours=1)).isoformat()

    r = client.get(
        "/trends",
        headers=h,
        params={"plc_id": "Main PLC", "datapoint_id": "DP_X", "from": frm, "to": to, "bucket": "hour"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["bucket"] == "hour"
    assert len(data["points"]) >= 1

    latest = client.get(
        "/trends/latest",
        headers=h,
        params={"plc_id": "Main PLC", "datapoint_id": "DP_X"},
    )
    assert latest.status_code == 200
    assert latest.json()["value"] == 10.0
