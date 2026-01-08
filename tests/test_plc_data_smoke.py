from __future__ import annotations

from fastapi.testclient import TestClient


def test_plc_data_smoke(client: TestClient):
    r = client.get("/plc_data")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
