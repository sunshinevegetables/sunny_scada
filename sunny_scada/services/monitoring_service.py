from __future__ import annotations

import logging
import threading
import time

from sunny_scada.data_storage import DataStorage
from sunny_scada.services.alarm_service import AlarmService
from sunny_scada.services.mappers import map_temperature_points, map_monitored_data

logger = logging.getLogger(__name__)


class MonitoringService:
    """Monitoring loops with latching + optional repeat while still breached."""

    def __init__(
        self,
        *,
        storage: DataStorage,
        alarm_service: AlarmService,
        enable_frozen: bool,
        frozen_interval_s: int,
        enable_cold: bool,
        cold_interval_s: int,
        enable_data_monitor: bool,
        data_monitor_interval_s: int,
        repeat_interval_s: float = 0.0,  # ✅ NEW
    ) -> None:
        self.storage = storage
        self.alarms = alarm_service

        self.enable_frozen = enable_frozen
        self.frozen_interval_s = max(1, int(frozen_interval_s))

        self.enable_cold = enable_cold
        self.cold_interval_s = max(1, int(cold_interval_s))

        self.enable_data_monitor = enable_data_monitor
        self.data_monitor_interval_s = max(1, int(data_monitor_interval_s))

        self.repeat_interval_s = max(0.0, float(repeat_interval_s))

        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._stop.clear()
        self._threads = []

        if self.enable_frozen:
            self._threads.append(threading.Thread(target=self._run_temp, args=("FROZEN", self.frozen_interval_s), daemon=True))
        if self.enable_cold:
            self._threads.append(threading.Thread(target=self._run_temp, args=("COLD", self.cold_interval_s), daemon=True))
        if self.enable_data_monitor:
            self._threads.append(threading.Thread(target=self._run_all_monitored, args=(self.data_monitor_interval_s,), daemon=True))

        for t in self._threads:
            t.start()

        logger.info("MonitoringService started (%d thread(s)). repeat_interval_s=%s", len(self._threads), self.repeat_interval_s)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=5)
        logger.info("MonitoringService stopped.")

    def _sleep_interruptible(self, seconds: int) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if self._stop.is_set():
                return
            time.sleep(0.1)

    def _should_repeat(self, last_ts: float) -> bool:
        if self.repeat_interval_s <= 0:
            return False
        return (time.time() - last_ts) >= self.repeat_interval_s

    def _run_temp(self, process_name: str, interval_s: int) -> None:
        in_max_alarm: dict[str, bool] = {}
        in_min_alarm: dict[str, bool] = {}
        last_max_fire: dict[str, float] = {}
        last_min_fire: dict[str, float] = {}

        while not self._stop.is_set():
            try:
                storage_data = self.storage.get_data()
                points = map_temperature_points(storage_data, process_name)

                now = time.time()

                for point_name, v in points.items():
                    scaled = v.get("scaled_value")
                    vmax = v.get("max")
                    vmin = v.get("min")

                    if scaled is None:
                        in_max_alarm[point_name] = False
                        in_min_alarm[point_name] = False
                        continue

                    # MAX
                    breach_max = (vmax is not None) and (scaled > vmax)
                    prev_max = in_max_alarm.get(point_name, False)
                    if breach_max:
                        if not prev_max:
                            self.alarms.trigger_alarm(point_name, float(scaled), "max")
                            last_max_fire[point_name] = now
                        else:
                            if self.repeat_interval_s > 0 and self._should_repeat(last_max_fire.get(point_name, 0.0)):
                                self.alarms.trigger_alarm(point_name, float(scaled), "max")
                                last_max_fire[point_name] = now
                    in_max_alarm[point_name] = breach_max

                    # MIN
                    breach_min = (vmin is not None) and (scaled < vmin)
                    prev_min = in_min_alarm.get(point_name, False)
                    if breach_min:
                        if not prev_min:
                            self.alarms.trigger_alarm(point_name, float(scaled), "min")
                            last_min_fire[point_name] = now
                        else:
                            if self.repeat_interval_s > 0 and self._should_repeat(last_min_fire.get(point_name, 0.0)):
                                self.alarms.trigger_alarm(point_name, float(scaled), "min")
                                last_min_fire[point_name] = now
                    in_min_alarm[point_name] = breach_min

            except Exception as e:
                logger.error("Temperature monitor error (%s): %s", process_name, e)

            self._sleep_interruptible(interval_s)

    def _run_all_monitored(self, interval_s: int) -> None:
        in_max_alarm: dict[str, bool] = {}
        in_min_alarm: dict[str, bool] = {}
        last_max_fire: dict[str, float] = {}
        last_min_fire: dict[str, float] = {}

        while not self._stop.is_set():
            try:
                storage_data = self.storage.get_data()
                points = map_monitored_data(storage_data)
                now = time.time()

                for point_name, v in points.items():
                    scaled = v.get("scaled_value")
                    vmax = v.get("max")
                    vmin = v.get("min")

                    if scaled is None:
                        in_max_alarm[point_name] = False
                        in_min_alarm[point_name] = False
                        continue

                    breach_max = (vmax is not None) and (scaled > vmax)
                    prev_max = in_max_alarm.get(point_name, False)
                    if breach_max:
                        if not prev_max:
                            self.alarms.trigger_alarm(point_name, float(scaled), "max")
                            last_max_fire[point_name] = now
                        else:
                            if self.repeat_interval_s > 0 and self._should_repeat(last_max_fire.get(point_name, 0.0)):
                                self.alarms.trigger_alarm(point_name, float(scaled), "max")
                                last_max_fire[point_name] = now
                    in_max_alarm[point_name] = breach_max

                    breach_min = (vmin is not None) and (scaled < vmin)
                    prev_min = in_min_alarm.get(point_name, False)
                    if breach_min:
                        if not prev_min:
                            self.alarms.trigger_alarm(point_name, float(scaled), "min")
                            last_min_fire[point_name] = now
                        else:
                            if self.repeat_interval_s > 0 and self._should_repeat(last_min_fire.get(point_name, 0.0)):
                                self.alarms.trigger_alarm(point_name, float(scaled), "min")
                                last_min_fire[point_name] = now
                    in_min_alarm[point_name] = breach_min

            except Exception as e:
                logger.error("Data monitor error: %s", e)

            self._sleep_interruptible(interval_s)
