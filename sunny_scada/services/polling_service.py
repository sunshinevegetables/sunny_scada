from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from sunny_scada.plc_reader import PLCReader

logger = logging.getLogger(__name__)


class PollingService:
    """Background PLC polling loop (safe start/stop)."""

    def __init__(self, plc_reader: PLCReader, interval_s: float, enable: bool = True) -> None:
        self._reader = plc_reader
        self._interval_s = max(0.1, float(interval_s))
        self._enable = enable

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self._enable:
            logger.info("PollingService disabled (ENABLE_PLC_POLLING=0).")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="plc-poller", daemon=True)
        self._thread.start()
        logger.info("PollingService started.")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("PollingService stopped.")

    def _sleep_interruptible(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if self._stop.is_set():
                return
            time.sleep(0.1)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._reader.read_plcs_from_config()
            except Exception as e:
                logger.error("PollingService error: %s", e)
            self._sleep_interruptible(self._interval_s)
