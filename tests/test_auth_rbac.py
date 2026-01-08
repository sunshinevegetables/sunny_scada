from __future__ import annotations

from fastapi.testclient import TestClient


def test_me_and_permissions(client: TestClient, admin_token: str):
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "admin"
    assert "config:read" in data["permissions"]


def test_rbac_forbidden_for_non_privileged_user(client: TestClient, admin_token: str):
    # create a user with no roles
    r = client.post(
        "/admin/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "bob", "password": "LongEnoughPassword!234", "roles": []},
    )
    assert r.status_code == 200

    # login as bob
    login = client.post("/auth/login", json={"username": "bob", "password": "LongEnoughPassword!234"})
    assert login.status_code == 200
    token = login.json()["access_token"]

    # config endpoint should be forbidden
    r2 = client.get("/config/plcs", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code in (401, 403)
