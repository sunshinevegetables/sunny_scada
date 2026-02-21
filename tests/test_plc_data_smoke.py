from __future__ import annotations

from fastapi.testclient import TestClient


def test_plc_data_smoke(client: TestClient, admin_token: str):
    r = client.get("/plc_data", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
