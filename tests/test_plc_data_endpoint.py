import sys
from pathlib import Path

# Ensure repo root is on sys.path when pytest chooses a different rootdir.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sunny_scada.data_storage import DataStorage
from sunny_scada.db.base import Base
from sunny_scada.db.models import (
    AppClient,
    CfgAccessGrant,
    CfgContainer,
    CfgDataPoint,
    CfgDataPointBit,
    CfgEquipment,
    CfgPLC,
    Role,
    RolePermission,
    User,
)
from sunny_scada.db.session import create_engine_and_sessionmaker
from sunny_scada.plc_reader import address_4x_to_pymodbus
from sunny_scada.services.access_control_service import AccessControlService
from sunny_scada.services.auth_service import AuthService


def _seed_digital_leaf(bit0: bool, bit1: bool) -> dict:
    # Legacy PLCReader-style digital payload: {"BIT 0": {"value": bool, ...}, ...}
    bits = {}
    for i in range(16):
        v = False
        if i == 0:
            v = bool(bit0)
        if i == 1:
            v = bool(bit1)
        bits[f"BIT {i}"] = {"value": v, "description": f"bit {i}"}
    return bits


@pytest.fixture
def ctx(tmp_path: Path):
    """Build a minimal FastAPI app + SQLite file DB + seeded config/auth."""

    db_path = tmp_path / "test_scada.db"
    runtime = create_engine_and_sessionmaker(f"sqlite:///{db_path}")
    Base.metadata.create_all(runtime.engine)

    app = FastAPI()

    class _ReaderStub:
        def __init__(self) -> None:
            self.config_data = {}

        def read_plcs_from_config(self) -> None:
            return None

    app.state.db_sessionmaker = runtime.SessionLocal
    app.state.storage = DataStorage()
    app.state.plc_reader = _ReaderStub()
    app.state.auth_service = AuthService(jwt_secret_key="test-secret")
    app.state.access_control_service = AccessControlService()

    # Only include the PLC router; tests focus on GET /plc_data.
    from sunny_scada.api.routers.plc import router as plc_router

    app.include_router(plc_router)

    # --- Seed DB ---
    SessionLocal = runtime.SessionLocal
    auth: AuthService = app.state.auth_service

    with SessionLocal() as db:
        # RBAC
        role = Role(name="operator", description="Test operator")
        role.permissions = [RolePermission(permission="plc:read")]
        user = User(username="alice", password_hash=auth.hash_password("pw"), is_active=True)
        user.roles = [role]
        db.add_all([role, user])
        db.commit()
        db.refresh(role)
        db.refresh(user)

        # Config graph (2 PLCs, 1 container + 1 equipment each)
        plc_a = CfgPLC(name="PLC A", ip="10.0.0.1", port=502)
        plc_b = CfgPLC(name="PLC B", ip="10.0.0.2", port=502)
        db.add_all([plc_a, plc_b])
        db.commit()
        db.refresh(plc_a)
        db.refresh(plc_b)

        cont_a1 = CfgContainer(plc_id=plc_a.id, name="Container A1", type="Tank")
        cont_b1 = CfgContainer(plc_id=plc_b.id, name="Container B1", type="Skid")
        db.add_all([cont_a1, cont_b1])
        db.commit()
        db.refresh(cont_a1)
        db.refresh(cont_b1)

        eq_a1 = CfgEquipment(container_id=cont_a1.id, name="Equipment A1", type="Pump")
        eq_b1 = CfgEquipment(container_id=cont_b1.id, name="Equipment B1", type="Valve")
        db.add_all([eq_a1, eq_b1])
        db.commit()
        db.refresh(eq_a1)
        db.refresh(eq_b1)

        # Datapoints (include BOTH read and write; write must be excluded by endpoint)
        dp_plc_a_read = CfgDataPoint(
            owner_type="plc",
            owner_id=plc_a.id,
            label="PLC A INT",
            description="PLC-level integer",
            category="read",
            type="INTEGER",
            address="40001",
        )
        dp_plc_a_write = CfgDataPoint(
            owner_type="plc",
            owner_id=plc_a.id,
            label="PLC A WRITE",
            description="Should not appear",
            category="write",
            type="INTEGER",
            address="40002",
        )
        dp_cont_a_read = CfgDataPoint(
            owner_type="container",
            owner_id=cont_a1.id,
            label="CONT A REAL",
            description="Container-level real",
            category="read",
            type="REAL",
            address="40003",
        )
        dp_eq_a_read = CfgDataPoint(
            owner_type="equipment",
            owner_id=eq_a1.id,
            label="EQ A DIG",
            description="Equipment-level digital",
            category="read",
            type="DIGITAL",
            address="40005",
        )
        dp_eq_a_read.bits = [
            CfgDataPointBit(bit=0, label="Run"),
            CfgDataPointBit(bit=1, label="Fault"),
        ]

        dp_plc_b_read = CfgDataPoint(
            owner_type="plc",
            owner_id=plc_b.id,
            label="PLC B INT",
            description="PLC B integer",
            category="read",
            type="INTEGER",
            address="40011",
        )
        dp_plc_b_write = CfgDataPoint(
            owner_type="plc",
            owner_id=plc_b.id,
            label="PLC B WRITE",
            description="Should not appear",
            category="write",
            type="INTEGER",
            address="40012",
        )
        dp_cont_b_read = CfgDataPoint(
            owner_type="container",
            owner_id=cont_b1.id,
            label="CONT B REAL",
            description=None,
            category="read",
            type="REAL",
            address="40013",
        )

        db.add_all(
            [
                dp_plc_a_read,
                dp_plc_a_write,
                dp_cont_a_read,
                dp_eq_a_read,
                dp_plc_b_read,
                dp_plc_b_write,
                dp_cont_b_read,
            ]
        )
        db.commit()
        db.refresh(dp_plc_a_read)
        db.refresh(dp_plc_a_write)
        db.refresh(dp_cont_a_read)
        db.refresh(dp_eq_a_read)
        db.refresh(dp_plc_b_read)
        db.refresh(dp_plc_b_write)
        db.refresh(dp_cont_b_read)

        # App principal (bound to same role)
        app_client = AppClient(
            id="app1",
            name="Test App",
            role_id=role.id,
            secret_hash=auth.hash_password("secret"),
            is_active=True,
            token_version=0,
            allowed_ips=[],
        )
        db.add(app_client)
        db.commit()
        db.refresh(app_client)

        # User token
        user_token = auth.authenticate(db, username="alice", password="pw").access_token

        # App token
        app_token, _ = auth.issue_app_access_token(
            client_id=app_client.id,
            client_name=app_client.name,
            role_id=app_client.role_id,
            token_version=app_client.token_version,
        )

        ids = {
            "role_id": role.id,
            "user_id": user.id,
            "plc_a_id": plc_a.id,
            "plc_b_id": plc_b.id,
            "dp_plc_a_write_id": dp_plc_a_write.id,
            "app_client_id": app_client.id,
        }

    # --- Seed DataStorage snapshot trees (legacy-shaped) ---
    storage: DataStorage = app.state.storage

    # PLC A
    base_40001 = address_4x_to_pymodbus(40001)
    base_40002 = address_4x_to_pymodbus(40002)
    base_40003 = address_4x_to_pymodbus(40003)
    base_40005 = address_4x_to_pymodbus(40005)
    storage.update_data(
        "PLC A",
        {
            "read": {
                "plc": {"PLC A INT": {"register_address": base_40001, "value": 101}},
                "container": {"CONT A REAL": {"register_address": base_40003, "scaled_value": 12.34, "raw_value": 123.4}},
                "equipment": {"EQ A DIG": {"register_address": base_40005, "value": _seed_digital_leaf(True, False)}},
            },
            "write": {"PLC A WRITE": {"register_address": base_40002, "value": 999}},
        },
    )

    # PLC B
    base_40011 = address_4x_to_pymodbus(40011)
    base_40012 = address_4x_to_pymodbus(40012)
    base_40013 = address_4x_to_pymodbus(40013)
    storage.update_data(
        "PLC B",
        {
            "read": {
                "plc": {"PLC B INT": {"register_address": base_40011, "value": 201}},
                "container": {"CONT B REAL": {"register_address": base_40013, "scaled_value": 56.78, "raw_value": 567.8}},
            },
            "write": {"PLC B WRITE": {"register_address": base_40012, "value": 888}},
        },
    )

    client = TestClient(app)

    return {
        "app": app,
        "client": client,
        "SessionLocal": SessionLocal,
        "auth": auth,
        "ids": ids,
        "tokens": {"user": user_token, "app": app_token},
    }


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _all_datapoint_names(resp_json: dict) -> list[str]:
    names: list[str] = []
    for plc in resp_json.get("plcs", []) or []:
        for dp in plc.get("datapoints", []) or []:
            names.append(dp.get("label"))
        for c in plc.get("containers", []) or []:
            for dp in c.get("datapoints", []) or []:
                names.append(dp.get("label"))
            for e in c.get("equipment", []) or []:
                for dp in e.get("datapoints", []) or []:
                    names.append(dp.get("label"))
    return [n for n in names if n is not None]


def test_plc_data_no_auth_returns_401(ctx):
    client: TestClient = ctx["client"]
    r = client.get("/plc_data")
    assert r.status_code == 401


def test_plc_data_user_with_plc_read_but_no_grants_gets_empty_list(ctx):
    client: TestClient = ctx["client"]
    r = client.get("/plc_data", headers=_auth_header(ctx["tokens"]["user"]))
    assert r.status_code == 200
    assert r.json() == {"plcs": []}


def test_plc_data_role_grant_plc_a_only_returns_plc_a(ctx):
    client: TestClient = ctx["client"]
    SessionLocal = ctx["SessionLocal"]
    ids = ctx["ids"]

    with SessionLocal() as db:
        db.add(
            CfgAccessGrant(
                role_id=ids["role_id"],
                user_id=None,
                resource_type="plc",
                resource_id=ids["plc_a_id"],
                access_level="read",
                include_descendants=True,
            )
        )
        db.commit()

    r = client.get("/plc_data", headers=_auth_header(ctx["tokens"]["user"]))
    assert r.status_code == 200

    payload = r.json()
    assert [p["name"] for p in payload["plcs"]] == ["PLC A"]

    # Spot-check value resolution
    plc_a = payload["plcs"][0]
    assert plc_a["timestamp"] is not None
    assert {dp["label"]: dp["value"] for dp in plc_a["datapoints"]} == {"PLC A INT": 101}

    # REAL prefers scaled_value
    cont = plc_a["containers"][0]
    assert {dp["label"]: dp["value"] for dp in cont["datapoints"]} == {"CONT A REAL": 12.34}

    # DIGITAL with configured bits -> {label: bool}
    eq = cont["equipment"][0]
    assert {dp["label"]: dp["value"] for dp in eq["datapoints"]} == {"EQ A DIG": {"Run": True, "Fault": False}}


def test_plc_data_role_grant_plc_a_plus_user_grant_plc_b_returns_both(ctx):
    client: TestClient = ctx["client"]
    SessionLocal = ctx["SessionLocal"]
    ids = ctx["ids"]

    with SessionLocal() as db:
        db.add(
            CfgAccessGrant(
                role_id=ids["role_id"],
                user_id=None,
                resource_type="plc",
                resource_id=ids["plc_a_id"],
                access_level="read",
                include_descendants=True,
            )
        )
        db.add(
            CfgAccessGrant(
                role_id=None,
                user_id=ids["user_id"],
                resource_type="plc",
                resource_id=ids["plc_b_id"],
                access_level="read",
                include_descendants=True,
            )
        )
        db.commit()

    r = client.get("/plc_data", headers=_auth_header(ctx["tokens"]["user"]))
    assert r.status_code == 200

    payload = r.json()
    assert [p["name"] for p in payload["plcs"]] == ["PLC A", "PLC B"]


def test_plc_data_write_datapoints_never_appear(ctx):
    client: TestClient = ctx["client"]
    SessionLocal = ctx["SessionLocal"]
    ids = ctx["ids"]

    # Grant both PLCs so we can inspect full output.
    with SessionLocal() as db:
        db.add(
            CfgAccessGrant(
                role_id=ids["role_id"],
                user_id=None,
                resource_type="plc",
                resource_id=ids["plc_a_id"],
                access_level="read",
                include_descendants=True,
            )
        )
        db.add(
            CfgAccessGrant(
                role_id=None,
                user_id=ids["user_id"],
                resource_type="plc",
                resource_id=ids["plc_b_id"],
                access_level="read",
                include_descendants=True,
            )
        )
        db.commit()

    r = client.get("/plc_data", headers=_auth_header(ctx["tokens"]["user"]))
    assert r.status_code == 200

    names = _all_datapoint_names(r.json())
    assert "PLC A WRITE" not in names
    assert "PLC B WRITE" not in names


def test_plc_data_app_principal_uses_role_grants_only(ctx):
    client: TestClient = ctx["client"]
    SessionLocal = ctx["SessionLocal"]
    ids = ctx["ids"]

    # Role can read PLC A only, user can read PLC B.
    with SessionLocal() as db:
        db.add(
            CfgAccessGrant(
                role_id=ids["role_id"],
                user_id=None,
                resource_type="plc",
                resource_id=ids["plc_a_id"],
                access_level="read",
                include_descendants=True,
            )
        )
        db.add(
            CfgAccessGrant(
                role_id=None,
                user_id=ids["user_id"],
                resource_type="plc",
                resource_id=ids["plc_b_id"],
                access_level="read",
                include_descendants=True,
            )
        )
        db.commit()

    r = client.get("/plc_data", headers=_auth_header(ctx["tokens"]["app"]))
    assert r.status_code == 200

    payload = r.json()
    assert [p["name"] for p in payload["plcs"]] == ["PLC A"]
