from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List


def _env_bool(key: str, default: str = "1") -> bool:
    return os.getenv(key, default).strip().lower() not in ("0", "false", "no", "off")


def _env_int(key: str, default: str) -> int:
    raw = os.getenv(key, default)
    try:
        return int(str(raw).strip())
    except Exception:
        return int(default)


def _env_float(key: str, default: str) -> float:
    raw = os.getenv(key, default)
    try:
        return float(str(raw).strip())
    except Exception:
        return float(default)


def _env_list(key: str, default: str = "") -> List[str]:
    raw = os.getenv(key, default).strip()
    if not raw:
        return []
    if raw == "*":
        return ["*"]
    if raw.startswith("["):
        try:
            v = json.loads(raw)
            if isinstance(v, list):
                return [str(x) for x in v if str(x).strip()]
        except Exception:
            pass
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass(frozen=True, slots=True)
class Settings:
    # Files/dirs (relative to repo root unless absolute)
    plc_config_file: str = field(default_factory=lambda: os.getenv("PLC_CONFIG_FILE", "config/config.yaml"))
    data_points_file: str = field(default_factory=lambda: os.getenv("DATA_POINTS_FILE", "config/data_points.yaml"))
    processes_file: str = field(default_factory=lambda: os.getenv("PROCESSES_FILE", "config/processes.yaml"))

    static_dir: str = field(default_factory=lambda: os.getenv("STATIC_DIR", "static"))
    config_dir: str = field(default_factory=lambda: os.getenv("CONFIG_DIR", "config"))

    # Polling / monitoring
    enable_plc_polling: bool = field(default_factory=lambda: _env_bool("ENABLE_PLC_POLLING", "1"))
    polling_interval_plc_s: float = field(default_factory=lambda: _env_float("POLLING_INTERVAL_PLC", "1"))

    enable_frozen_monitor: bool = field(default_factory=lambda: _env_bool("ENABLE_FROZEN_MONITOR", "1"))
    frozen_monitor_interval_s: int = field(default_factory=lambda: _env_int("FROZEN_MONITOR_INTERVAL_S", "10"))

    enable_cold_monitor: bool = field(default_factory=lambda: _env_bool("ENABLE_COLD_MONITOR", "1"))
    cold_monitor_interval_s: int = field(default_factory=lambda: _env_int("COLD_MONITOR_INTERVAL_S", "10"))

    enable_data_monitor: bool = field(default_factory=lambda: _env_bool("ENABLE_DATA_MONITOR", "0"))
    data_monitor_interval_s: int = field(default_factory=lambda: _env_int("DATA_MONITOR_INTERVAL_S", "10"))

    # Alarm/audio
    enable_alarm_audio: bool = field(default_factory=lambda: _env_bool("ENABLE_ALARM_AUDIO", "1"))
    alarm_default_wav: str = field(default_factory=lambda: os.getenv("ALARM_DEFAULT_WAV", "static/sounds/alarm.wav"))
    alarm_generate_tts: bool = field(default_factory=lambda: _env_bool("ALARM_GENERATE_TTS", "0"))

    # Voice callouts + optional repeat
    enable_alarm_tts: bool = field(default_factory=lambda: _env_bool("ENABLE_ALARM_TTS", "1"))
    alarm_tts_rate: int = field(default_factory=lambda: _env_int("ALARM_TTS_RATE", "180"))
    alarm_tts_volume: float = field(default_factory=lambda: _env_float("ALARM_TTS_VOLUME", "1.0"))
    alarm_tts_voice_contains: str = field(default_factory=lambda: os.getenv("ALARM_TTS_VOICE_CONTAINS", "").strip())
    alarm_tts_prefix: str = field(default_factory=lambda: os.getenv("ALARM_TTS_PREFIX", "Alarm").strip())
    alarm_repeat_interval_s: float = field(default_factory=lambda: _env_float("ALARM_REPEAT_INTERVAL_S", "0"))

    # CORS defaults to locked-down (no cross-origin). Set CORS_ALLOW_ORIGINS to enable UI on another origin.
    cors_allow_origins: List[str] = field(default_factory=lambda: _env_list("CORS_ALLOW_ORIGINS", ""))
    cors_allow_methods: List[str] = field(default_factory=lambda: _env_list("CORS_ALLOW_METHODS", "GET,POST,PUT,PATCH,DELETE,OPTIONS"))
    cors_allow_headers: List[str] = field(default_factory=lambda: _env_list("CORS_ALLOW_HEADERS", "Authorization,Content-Type"))
    cors_allow_credentials: bool = field(default_factory=lambda: _env_bool("CORS_ALLOW_CREDENTIALS", "1"))

    # Modbus service tuning
    modbus_timeout_s: float = field(default_factory=lambda: _env_float("MODBUS_TIMEOUT_S", "3"))
    modbus_retries: int = field(default_factory=lambda: _env_int("MODBUS_RETRIES", "2"))
    modbus_backoff_s: float = field(default_factory=lambda: _env_float("MODBUS_BACKOFF_S", "0.2"))

    # Database
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./sunny_scada.db"))
    db_echo: bool = field(default_factory=lambda: _env_bool("DB_ECHO", "0"))
    # NOTE: In production, use Alembic migrations (alembic upgrade head). AUTO_CREATE_DB is a dev/test escape hatch.
    auto_create_db: bool = field(default_factory=lambda: _env_bool("AUTO_CREATE_DB", "0"))

    # Auth
    auth_enabled: bool = field(default_factory=lambda: _env_bool("AUTH_ENABLED", "1"))
    jwt_secret_key: str = field(default_factory=lambda: os.getenv("JWT_SECRET_KEY", "").strip())
    jwt_issuer: str = field(default_factory=lambda: os.getenv("JWT_ISSUER", "sunny_scada").strip())
    access_token_ttl_s: int = field(default_factory=lambda: _env_int("ACCESS_TOKEN_TTL_S", "900"))
    refresh_token_ttl_s: int = field(default_factory=lambda: _env_int("REFRESH_TOKEN_TTL_S", str(60 * 60 * 24 * 7)))
    auth_lockout_threshold: int = field(default_factory=lambda: _env_int("AUTH_LOCKOUT_THRESHOLD", "5"))
    auth_lockout_duration_s: int = field(default_factory=lambda: _env_int("AUTH_LOCKOUT_DURATION_S", "900"))

    # Bootstrap
    initial_admin_username: str = field(default_factory=lambda: os.getenv("INITIAL_ADMIN_USERNAME", "admin").strip())
    initial_admin_password: str = field(default_factory=lambda: os.getenv("INITIAL_ADMIN_PASSWORD", "").strip())

    # Client log ingestion token (optional). If set, clients can POST /logs/client with X-Client-Log-Token.
    client_log_token: str = field(default_factory=lambda: os.getenv("CLIENT_LOG_TOKEN", "").strip())

    # Request limits / hardening
    max_request_size_bytes: int = field(default_factory=lambda: _env_int("MAX_REQUEST_SIZE_BYTES", str(1024 * 1024)))

    # Background scheduler (retention, historian, maintenance schedules).
    enable_scheduler: bool = field(default_factory=lambda: _env_bool("ENABLE_SCHEDULER", "1"))

    # Rate limiting for commands
    command_rate_limit_per_minute: int = field(default_factory=lambda: _env_int("COMMAND_RATE_LIMIT_PER_MIN", "30"))

    # Digital datapoint bit range (system config module)
    digital_bit_max: int = field(default_factory=lambda: _env_int("DIGITAL_BIT_MAX", "15"))

    # Retention (days)
    retention_server_logs_days: int = field(default_factory=lambda: _env_int("RETENTION_SERVER_LOGS_DAYS", "30"))
    retention_audit_logs_days: int = field(default_factory=lambda: _env_int("RETENTION_AUDIT_LOGS_DAYS", "365"))
    retention_commands_days: int = field(default_factory=lambda: _env_int("RETENTION_COMMANDS_DAYS", "365"))
    retention_alarms_days: int = field(default_factory=lambda: _env_int("RETENTION_ALARMS_DAYS", "365"))
    retention_historian_raw_days: int = field(default_factory=lambda: _env_int("RETENTION_HISTORIAN_RAW_DAYS", "30"))
    retention_historian_rollup_days: int = field(default_factory=lambda: _env_int("RETENTION_HISTORIAN_ROLLUP_DAYS", "365"))
    retention_maintenance_days: int = field(default_factory=lambda: _env_int("RETENTION_MAINTENANCE_DAYS", "0"))

    # Historian
    enable_historian: bool = field(default_factory=lambda: _env_bool("ENABLE_HISTORIAN", "1"))
    historian_sample_interval_s: int = field(default_factory=lambda: _env_int("HISTORIAN_SAMPLE_INTERVAL_S", "10"))
    historian_rollup_interval_s: int = field(default_factory=lambda: _env_int("HISTORIAN_ROLLUP_INTERVAL_S", "60"))

    # Maintenance scheduler
    enable_maintenance_scheduler: bool = field(default_factory=lambda: _env_bool("ENABLE_MAINT_SCHEDULER", "1"))
    maintenance_scheduler_tick_s: int = field(default_factory=lambda: _env_int("MAINT_SCHEDULER_TICK_S", "60"))
