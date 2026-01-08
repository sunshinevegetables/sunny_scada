from __future__ import annotations

import shutil
from pathlib import Path
import sys

# Ensure repo root is importable
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


import pytest
from fastapi.testclient import TestClient

from sunny_scada.api.app import create_app
from sunny_scada.core.settings import Settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    # copy configuration files to temp to keep repo clean
    repo_root = Path(__file__).resolve().parents[1]
    cfg_src = repo_root / "config" / "config.yaml"
    dp_src = repo_root / "config" / "data_points.yaml"

    cfg_dst = tmp_path / "config.yaml"
    dp_dst = tmp_path / "data_points.yaml"
    shutil.copyfile(cfg_src, cfg_dst)
    shutil.copyfile(dp_src, dp_dst)

    db_path = tmp_path / "test.db"

    return Settings(
        plc_config_file=str(cfg_dst),
        data_points_file=str(dp_dst),
        processes_file=str(repo_root / "config" / "processes.yaml"),
        static_dir=str(repo_root / "static"),
        config_dir=str(repo_root / "config"),
        enable_plc_polling=False,
        enable_frozen_monitor=False,
        enable_cold_monitor=False,
        enable_data_monitor=False,
        enable_alarm_audio=False,
        enable_alarm_tts=False,
        alarm_generate_tts=False,
        enable_scheduler=False,
        enable_historian=False,
        enable_maintenance_scheduler=False,
        database_url=f"sqlite:///{db_path}",
        auto_create_db=True,
        jwt_secret_key="test_jwt_secret",
        initial_admin_username="admin",
        initial_admin_password="TestPassword!12345",
    )


@pytest.fixture()
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as c:
        # Patch PLC writer methods to avoid real Modbus I/O
        c.app.state.plc_writer.bit_write_signal = lambda *args, **kwargs: True
        c.app.state.plc_writer.write_register = lambda *args, **kwargs: True
        yield c


@pytest.fixture()
def admin_token(client: TestClient) -> str:
    resp = client.post("/auth/login", json={"username": "admin", "password": "TestPassword!12345"})
    assert resp.status_code == 200
    return resp.json()["access_token"]
