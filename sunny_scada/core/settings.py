from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import List


def _env_bool(key: str, default: str = "1") -> bool:
    return os.getenv(key, default).strip().lower() not in ("0", "false", "no", "off")


def _env_list(key: str, default: str = "*") -> List[str]:
    raw = os.getenv(key, default).strip()
    if raw == "*":
        return ["*"]
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
    polling_interval_plc_s: float = field(default_factory=lambda: float(os.getenv("POLLING_INTERVAL_PLC", "1")))

    enable_frozen_monitor: bool = field(default_factory=lambda: _env_bool("ENABLE_FROZEN_MONITOR", "1"))
    frozen_monitor_interval_s: int = field(default_factory=lambda: int(os.getenv("FROZEN_MONITOR_INTERVAL_S", "10")))

    enable_cold_monitor: bool = field(default_factory=lambda: _env_bool("ENABLE_COLD_MONITOR", "1"))
    cold_monitor_interval_s: int = field(default_factory=lambda: int(os.getenv("COLD_MONITOR_INTERVAL_S", "10")))

    enable_data_monitor: bool = field(default_factory=lambda: _env_bool("ENABLE_DATA_MONITOR", "0"))
    data_monitor_interval_s: int = field(default_factory=lambda: int(os.getenv("DATA_MONITOR_INTERVAL_S", "10")))

    # Alarm/audio
    enable_alarm_audio: bool = field(default_factory=lambda: _env_bool("ENABLE_ALARM_AUDIO", "1"))
    alarm_default_wav: str = field(default_factory=lambda: os.getenv("ALARM_DEFAULT_WAV", "static/sounds/alarm.wav"))
    alarm_generate_tts: bool = field(default_factory=lambda: _env_bool("ALARM_GENERATE_TTS", "0"))

     # ✅ NEW: Voice callouts + optional repeat
    enable_alarm_tts: bool = field(default_factory=lambda: _env_bool("ENABLE_ALARM_TTS", "1"))
    alarm_tts_rate: int = field(default_factory=lambda: int(os.getenv("ALARM_TTS_RATE", "180")))
    alarm_tts_volume: float = field(default_factory=lambda: float(os.getenv("ALARM_TTS_VOLUME", "1.0")))
    alarm_tts_voice_contains: str = field(default_factory=lambda: os.getenv("ALARM_TTS_VOICE_CONTAINS", "").strip())
    alarm_tts_prefix: str = field(default_factory=lambda: os.getenv("ALARM_TTS_PREFIX", "Alarm").strip())

    # Repeat callout while condition stays in alarm (0 = only once per transition)
    alarm_repeat_interval_s: float = field(default_factory=lambda: float(os.getenv("ALARM_REPEAT_INTERVAL_S", "0")))
    
    # CORS (✅ fixed with default_factory)
    cors_allow_origins: List[str] = field(default_factory=lambda: _env_list("CORS_ALLOW_ORIGINS", "*"))
    cors_allow_methods: List[str] = field(default_factory=lambda: _env_list("CORS_ALLOW_METHODS", "*"))
    cors_allow_headers: List[str] = field(default_factory=lambda: _env_list("CORS_ALLOW_HEADERS", "*"))
    cors_allow_credentials: bool = field(default_factory=lambda: _env_bool("CORS_ALLOW_CREDENTIALS", "1"))

    # Modbus service tuning
    modbus_timeout_s: float = field(default_factory=lambda: float(os.getenv("MODBUS_TIMEOUT_S", "3")))
    modbus_retries: int = field(default_factory=lambda: int(os.getenv("MODBUS_RETRIES", "2")))
    modbus_backoff_s: float = field(default_factory=lambda: float(os.getenv("MODBUS_BACKOFF_S", "0.2")))
