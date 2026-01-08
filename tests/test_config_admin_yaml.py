from __future__ import annotations

from fastapi.testclient import TestClient


def test_config_plc_crud_revision_and_rollback(client: TestClient, admin_token: str):
    h = {"Authorization": f"Bearer {admin_token}"}

    before = client.get("/config/plcs", headers=h)
    assert before.status_code == 200

    r = client.post("/config/plcs", headers=h, json={"plc_id": "test_plc", "content": {}})
    assert r.status_code == 200
    rev_id = r.json()["revision_id"]

    after = client.get("/config/plcs", headers=h).json()["plcs"]
    assert "test_plc" in after

    revs = client.get("/config/revisions", headers=h)
    assert revs.status_code == 200
    assert any(x["id"] == rev_id for x in revs.json()["revisions"])

    rb = client.post(f"/config/rollback/{rev_id}", headers=h)
    assert rb.status_code == 200

    final = client.get("/config/plcs", headers=h).json()["plcs"]
    assert "test_plc" not in final
