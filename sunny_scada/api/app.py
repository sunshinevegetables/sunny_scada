from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sunny_scada.api.errors import register_error_handlers
from sunny_scada.api.middleware import RequestSizeLimitMiddleware, SecurityHeadersMiddleware
from sunny_scada.core.settings import Settings
from sunny_scada.data_storage import DataStorage
from sunny_scada.db.base import Base
from sunny_scada.db.session import create_engine_and_sessionmaker
from sunny_scada.modbus_service import ModbusService, load_plc_configs
from sunny_scada.plc_reader import PLCReader
from sunny_scada.plc_writer import PLCWriter
from sunny_scada.services.alarm_service import AlarmService
from sunny_scada.services.audit_service import AuditService
from sunny_scada.services.auth_service import AuthService
from sunny_scada.services.command_executor import CommandExecutor
from sunny_scada.services.command_service import CommandService
from sunny_scada.services.config_service import ConfigService
from sunny_scada.services.data_points_service import DataPointsService
from sunny_scada.services.db_log_handler import DBLogHandler
from sunny_scada.services.historian_service import HistorianService
from sunny_scada.services.iqf_service import IQFService
from sunny_scada.services.maintenance_scheduler import MaintenanceScheduler
from sunny_scada.services.monitoring_service import MonitoringService
from sunny_scada.services.polling_service import PollingService
from sunny_scada.services.rate_limiter import RateLimiter
from sunny_scada.services.retention_service import RetentionService

from sunny_scada.api.routers import (
    health,
    processes,
    data_points,
    plc,
    iqf,
    auth,
    config_admin,
    commands,
    logs,
    alarms,
    admin,
    maintenance,
    trends,
    system_config,
)

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

    static_dir = _resolve(settings.static_dir)
    config_dir = _resolve(settings.config_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Starting Sunny SCADA app...")

        app.state.settings = settings
        app.state.storage = DataStorage()

        # --- DB ---
        db_rt = create_engine_and_sessionmaker(settings.database_url, echo=settings.db_echo)
        app.state.db_engine = db_rt.engine
        app.state.db_sessionmaker = db_rt.SessionLocal
        if settings.auto_create_db:
            Base.metadata.create_all(bind=db_rt.engine)

        # --- Auth/Audit ---
        if not settings.jwt_secret_key:
            raise RuntimeError("JWT_SECRET_KEY is required")

        app.state.auth_service = AuthService(
            jwt_secret_key=settings.jwt_secret_key,
            jwt_issuer=settings.jwt_issuer,
            access_ttl_s=settings.access_token_ttl_s,
            refresh_ttl_s=settings.refresh_token_ttl_s,
            lockout_threshold=settings.auth_lockout_threshold,
            lockout_duration_s=settings.auth_lockout_duration_s,
        )
        app.state.audit_service = AuditService()

        # Bootstrap initial admin if DB empty
        try:
            from sqlalchemy.exc import OperationalError
            from sunny_scada.db.models import User

            with db_rt.SessionLocal() as db:
                try:
                    user_count = db.query(User).count()
                except OperationalError as e:
                    raise RuntimeError(
                        "Database schema not initialized. Run `alembic upgrade head` (or set AUTO_CREATE_DB=1 for dev)."
                    ) from e

                if user_count == 0:
                    if not settings.initial_admin_password:
                        raise RuntimeError(
                            "INITIAL_ADMIN_PASSWORD is required on first run to bootstrap the admin user"
                        )
                    app.state.auth_service.ensure_initial_admin(
                        db,
                        username=settings.initial_admin_username,
                        password=settings.initial_admin_password,
                        permissions=[
                            "config:read",
                            "config:write",
                            "command:read",
                            "command:write",
                            "alarms:*",
                            "maintenance:*",
                            "inventory:write",
                            "users:admin",
                            "roles:admin",
                        ],
                    )
        except Exception:
            logger.exception("Failed to bootstrap initial admin")
            raise

        # --- Config editor (data_points.yaml) ---
        app.state.config_service = ConfigService(_resolve(settings.data_points_file))

        # --- Modbus + reader/writer ---
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

        app.state.data_points_service = DataPointsService(_resolve(settings.data_points_file))

        # --- Alarm service ---
        app.state.alarm_service = AlarmService(
            enable_audio=settings.enable_alarm_audio,
            default_alarm_wav=_resolve(settings.alarm_default_wav),
            generate_tts=settings.alarm_generate_tts,
            sounds_dir=_resolve("static/sounds"),
            enable_tts=settings.enable_alarm_tts,
            tts_rate=settings.alarm_tts_rate,
            tts_volume=settings.alarm_tts_volume,
            tts_voice_contains=settings.alarm_tts_voice_contains,
            tts_prefix=settings.alarm_tts_prefix,
        )
        app.state.alarm_service.start()

        # --- Polling/monitoring ---
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
            repeat_interval_s=settings.alarm_repeat_interval_s,
        )

        app.state.iqf_service = IQFService(
            storage=app.state.storage,
            reader=app.state.plc_reader,
            writer=app.state.plc_writer,
        )

        # --- Cycle 2 services ---
        app.state.rate_limiter = RateLimiter()
        app.state.command_executor = CommandExecutor(
            sessionmaker=db_rt.SessionLocal,
            writer=app.state.plc_writer,
            max_retries=settings.modbus_retries,
            backoff_s=settings.modbus_backoff_s,
        )
        app.state.command_executor.start()

        app.state.command_service = CommandService(
            modbus=app.state.modbus,
            data_points=app.state.data_points_service,
            executor=app.state.command_executor,
            rate_limiter=app.state.rate_limiter,
            audit=app.state.audit_service,
            rate_limit_per_minute=settings.command_rate_limit_per_minute,
        )

        app.state.historian_service = HistorianService()
        app.state.retention_service = RetentionService()
        app.state.maintenance_scheduler = MaintenanceScheduler()

        # --- DB log handler (WARNING+) ---
        try:
            db_handler = DBLogHandler(db_rt.SessionLocal)
            db_handler.setLevel(logging.WARNING)
            logging.getLogger("sunny_scada").addHandler(db_handler)
        except Exception:
            logger.exception("Failed to attach DB log handler")

        # --- Scheduler ---
        app.state.scheduler = None
        if settings.enable_scheduler:
            sched = BackgroundScheduler(timezone="UTC")

            def _run_retention():
                with db_rt.SessionLocal() as db:
                    app.state.retention_service.cleanup(
                        db,
                        server_logs_days=settings.retention_server_logs_days,
                        audit_logs_days=settings.retention_audit_logs_days,
                        commands_days=settings.retention_commands_days,
                        alarms_days=settings.retention_alarms_days,
                        historian_raw_days=settings.retention_historian_raw_days,
                        historian_rollup_days=settings.retention_historian_rollup_days,
                    )

            if settings.enable_historian:
                def _sample():
                    with db_rt.SessionLocal() as db:
                        app.state.historian_service.sample_from_storage(db, storage_snapshot=app.state.storage.get_data())

                def _rollup():
                    with db_rt.SessionLocal() as db:
                        app.state.historian_service.rollup_hourly(db)

                sched.add_job(_sample, "interval", seconds=max(2, int(settings.historian_sample_interval_s)), id="historian_sample")
                sched.add_job(_rollup, "interval", seconds=max(30, int(settings.historian_rollup_interval_s)), id="historian_rollup")

            if settings.enable_maintenance_scheduler:
                def _maint():
                    with db_rt.SessionLocal() as db:
                        app.state.maintenance_scheduler.tick(db)

                sched.add_job(_maint, "interval", seconds=max(30, int(settings.maintenance_scheduler_tick_s)), id="maint")

            # Run retention hourly
            sched.add_job(_run_retention, "interval", hours=1, id="retention")

            sched.start()
            app.state.scheduler = sched

        # start background services
        app.state.poller.start()
        app.state.monitoring.start()

        try:
            yield
        finally:
            logger.info("Shutting down Sunny SCADA app...")
            try:
                app.state.poller.stop()
                app.state.monitoring.stop()
                app.state.command_executor.stop()
                app.state.alarm_service.stop()
                if app.state.scheduler:
                    app.state.scheduler.shutdown(wait=False)
                app.state.modbus.close()
            finally:
                try:
                    app.state.db_engine.dispose()
                except Exception:
                    pass

    app = FastAPI(lifespan=lifespan)

    # Middleware
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.max_request_size_bytes)
    app.add_middleware(SecurityHeadersMiddleware)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins or [],
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )

    register_error_handlers(app)

    # Static
    app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")
    app.mount("/frontend", StaticFiles(directory=static_dir, html=True), name="frontend")

    @app.get("/")
    def serve_index():
        index_path = Path(static_dir) / "pages" / "index.html"
        return FileResponse(str(index_path))

    # Routers
    app.include_router(health.router)
    app.include_router(processes.router)
    app.include_router(data_points.router)
    app.include_router(plc.router)
    app.include_router(iqf.router)
    app.include_router(auth.router)
    app.include_router(admin.router)

    # Cycle2
    app.include_router(commands.router)
    app.include_router(logs.router)
    app.include_router(alarms.router)
    app.include_router(maintenance.router)
    app.include_router(trends.router)

    # System config (DB-backed PLC/Container/Equipment/Datapoints)
    app.include_router(system_config.router)

    # Config admin API
    app.include_router(config_admin.router)

    # Backward-compatible config file access: /config/<filename>
    @app.get("/config/{file_name}", tags=["config-files"], include_in_schema=False)
    def get_config_file(file_name: str):
        if "/" in file_name or "\\" in file_name or ".." in file_name or file_name.startswith("."):
            raise HTTPException(status_code=400, detail="Invalid file name")
        p = Path(config_dir) / file_name
        if not p.exists() or not p.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        if p.suffix.lower() not in (".yaml", ".yml", ".json"):
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(str(p))

    return app
