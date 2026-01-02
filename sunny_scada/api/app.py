from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from sunny_scada.core.settings import Settings
from sunny_scada.data_storage import DataStorage

# Central Modbus + upgraded reader/writer (from your earlier upgrade)
from sunny_scada.modbus_service import ModbusService, load_plc_configs
from sunny_scada.plc_reader import PLCReader
from sunny_scada.plc_writer import PLCWriter

from sunny_scada.services.polling_service import PollingService
from sunny_scada.services.monitoring_service import MonitoringService
from sunny_scada.services.data_points_service import DataPointsService
from sunny_scada.services.alarm_service import AlarmService
from sunny_scada.services.iqf_service import IQFService

from sunny_scada.api.routers import processes, data_points, plc, iqf, health

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    # .../repo_root/sunny_scada/api/app.py -> parents[2] == repo_root
    return Path(__file__).resolve().parents[2]


def _resolve(p: str) -> str:
    path = Path(p)
    if path.is_absolute():
        return str(path)
    return str((_repo_root() / path).resolve())


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    # Resolve file/dir paths safely
    static_dir = _resolve(settings.static_dir)
    config_dir = _resolve(settings.config_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Starting Sunny SCADA app...")

        # Core shared runtime objects
        app.state.settings = settings
        app.state.storage = DataStorage()

        # Modbus service (one connection per PLC, with retries + locks)
        plc_configs = load_plc_configs(_resolve(settings.plc_config_file))
        app.state.modbus = ModbusService(
            plc_configs,
            timeout_s=settings.modbus_timeout_s,
            retries=settings.modbus_retries,
            backoff_s=settings.modbus_backoff_s,
        )

        app.state.plc_reader = PLCReader(
            modbus=app.state.modbus,
            storage=app.state.storage,
            config_file=_resolve(settings.plc_config_file),
            points_file=_resolve(settings.data_points_file),
        )

        app.state.plc_writer = PLCWriter(
            modbus=app.state.modbus,
            data_points_file=_resolve(settings.data_points_file),
        )

        # YAML registry/service for read/write-point lookups and edits
        app.state.data_points_service = DataPointsService(_resolve(settings.data_points_file))

        # Alarm/audio service
        app.state.alarm_service = AlarmService(
            enable_audio=settings.enable_alarm_audio,
            default_alarm_wav=_resolve(settings.alarm_default_wav),
            generate_tts=settings.alarm_generate_tts,
            sounds_dir=_resolve("static/sounds"),

            # ✅ TTS
            enable_tts=settings.enable_alarm_tts,
            tts_rate=settings.alarm_tts_rate,
            tts_volume=settings.alarm_tts_volume,
            tts_voice_contains=settings.alarm_tts_voice_contains,
            tts_prefix=settings.alarm_tts_prefix,
        )
        app.state.alarm_service.start()

        # Monitoring + polling services
        app.state.poller = PollingService(
            plc_reader=app.state.plc_reader,
            interval_s=settings.polling_interval_plc_s,
            enable=settings.enable_plc_polling,
        )

        app.state.monitoring = MonitoringService(
            storage=app.state.storage,
            alarm_service=app.state.alarm_service,
            enable_frozen=settings.enable_frozen_monitor,
            frozen_interval_s=settings.frozen_monitor_interval_s,
            enable_cold=settings.enable_cold_monitor,
            cold_interval_s=settings.cold_monitor_interval_s,
            enable_data_monitor=settings.enable_data_monitor,
            data_monitor_interval_s=settings.data_monitor_interval_s,

            # ✅ NEW
            repeat_interval_s=settings.alarm_repeat_interval_s,
        )

        # IQF sequence service (wraps your current /start_iqf logic cleanly)
        app.state.iqf_service = IQFService(
            storage=app.state.storage,
            reader=app.state.plc_reader,
            writer=app.state.plc_writer,
        )

        # Start background services
        app.state.poller.start()
        app.state.monitoring.start()

        try:
            yield
        finally:
            logger.info("Shutting down Sunny SCADA app...")
            app.state.poller.stop()
            app.state.monitoring.stop()
            app.state.alarm_service.stop()
            app.state.modbus.close()

    app = FastAPI(lifespan=lifespan)

    # Static mounts (keep compatible with your current paths)
    app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")
    app.mount("/frontend", StaticFiles(directory=static_dir, html=True), name="frontend")
    app.mount("/config", StaticFiles(directory=config_dir), name="config")

    # Root route: serve your actual index
    @app.get("/")
    def serve_index():
        index_path = Path(static_dir) / "pages" / "index.html"
        return FileResponse(str(index_path))

    # CORS (tighten in production)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )

    # Routers
    app.include_router(health.router)
    app.include_router(processes.router)
    app.include_router(data_points.router)
    app.include_router(plc.router)
    app.include_router(iqf.router)

    return app
