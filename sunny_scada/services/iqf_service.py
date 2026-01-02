from __future__ import annotations

import logging
import time
from typing import Any, Dict

from sunny_scada.data_storage import DataStorage
from sunny_scada.plc_reader import PLCReader
from sunny_scada.plc_writer import PLCWriter
from sunny_scada.services.mappers import map_condensers_to_control_status, map_compressors_to_status

logger = logging.getLogger(__name__)


class IQFService:
    def __init__(self, *, storage: DataStorage, reader: PLCReader, writer: PLCWriter, plc_name: str = "Main PLC") -> None:
        self.storage = storage
        self.reader = reader
        self.writer = writer
        self.plc_name = plc_name

    def start_iqf(self) -> None:
        storage_data = self.storage.get_data()
        condenser_map = map_condensers_to_control_status(storage_data)
        comp_status_map = map_compressors_to_status(storage_data)

        if not condenser_map:
            raise ValueError("No condenser control status data available (storage is empty or mapping failed).")

        condenser_on = any(v.get("Pump On") for v in condenser_map.values())
        if not condenser_on:
            logger.info("No condenser ON. Starting condenser 1 (toggle bit 0 @ 42022).")

            if not self.writer.bit_write_signal(self.plc_name, 42022, 0, 1):
                raise RuntimeError("Failed to start Condenser 1 (set bit 0).")
            time.sleep(0.2)
            if not self.writer.bit_write_signal(self.plc_name, 42022, 0, 0):
                raise RuntimeError("Failed to reset Condenser 1 (clear bit 0).")

            time.sleep(2)

            pump_on = self.reader.read_single_bit(self.plc_name, 42022, 9)
            if not pump_on:
                raise RuntimeError("Condenser 1 failed to turn on (BIT 9 not true).")

        # Give the system time to update status
        time.sleep(2)

        # Start Screw Compressor 2 if not running
        if not self._is_comp_running(comp_status_map, comp_no=2):
            logger.info("Compressor 2 OFF. Starting COMP_2 (toggle bit 0 @ 41340).")

            if not self.writer.bit_write_signal(self.plc_name, 41340, 0, 1):
                raise RuntimeError("Failed to start Compressor 2 (set bit 0).")
            time.sleep(0.2)
            if not self.writer.bit_write_signal(self.plc_name, 41340, 0, 0):
                raise RuntimeError("Failed to reset Compressor 2 (clear bit 0).")

            # Bring on load (bit 3)
            time.sleep(0.5)
            if not self.writer.bit_write_signal(self.plc_name, 41340, 3, 1):
                raise RuntimeError("Failed to bring Compressor 2 on load (set bit 3).")
            time.sleep(0.5)
            if not self.writer.bit_write_signal(self.plc_name, 41340, 3, 0):
                raise RuntimeError("Failed to reset Compressor 2 load command (clear bit 3).")

        # Refresh status map (optional)
        time.sleep(1)

        # Start Screw Compressor 4 if not running
        storage_data = self.storage.get_data()
        comp_status_map = map_compressors_to_status(storage_data)

        if not self._is_comp_running(comp_status_map, comp_no=4):
            logger.info("Compressor 4 OFF. Starting COMP_4 (toggle bit 0 @ 41348).")

            if not self.writer.bit_write_signal(self.plc_name, 41348, 0, 1):
                raise RuntimeError("Failed to start Compressor 4 (set bit 0).")
            time.sleep(0.2)
            if not self.writer.bit_write_signal(self.plc_name, 41348, 0, 0):
                raise RuntimeError("Failed to reset Compressor 4 (clear bit 0).")

    def _is_comp_running(self, status_map: Dict[str, Dict[str, Any]], comp_no: int) -> bool:
        target = f"COMP_{comp_no}_STATUS_2"
        for k, v in (status_map or {}).items():
            if k.endswith(target) and bool(v.get("Running")):
                return True
        return False
