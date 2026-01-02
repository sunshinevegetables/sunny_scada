from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import yaml

from .modbus_service import ModbusService
from .plc_reader import address_4x_to_pymodbus

logger = logging.getLogger(__name__)


class PLCWriter:
    """Writes to PLCs via the shared ModbusService.

    This class no longer creates/owns ModbusTcpClient sockets. All writes go through ModbusService,
    which ensures a single connection per PLC and serializes concurrent requests per PLC.
    """

    def __init__(self, modbus: ModbusService, *, data_points_file: str = "config/data_points.yaml") -> None:
        self.modbus = modbus
        self.data_points_file = data_points_file
        self.write_signals: Dict[str, Any] = self.load_write_points(self.data_points_file)

    def load_write_points(self, points_file: str) -> Dict[str, Any]:
        """Load write points from the unified data_points.yaml."""
        with open(points_file, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict):
            raise ValueError("data_points.yaml must contain a dictionary structure.")
        return data.get("data_points", {}) or {}

    # -------------------------
    # Core write operations
    # -------------------------

    def bit_write_signal(
        self,
        plc_name: str,
        register_address_4x: int,
        bit_position: int,
        value: int,
        *,
        verify: bool = True,
    ) -> bool:
        """Write a single bit in a 4xxxx holding register (read-modify-write)."""
        adjusted = address_4x_to_pymodbus(int(register_address_4x))
        return self.modbus.write_bit_in_holding_register(
            plc_name,
            adjusted,
            int(bit_position),
            int(value),
            verify=verify,
        )

    def write_register(
        self,
        plc_name: str,
        register_address_4x: int,
        value: int,
        *,
        verify: bool = False,
    ) -> bool:
        """Write a full register value (not bitwise)."""
        adjusted = address_4x_to_pymodbus(int(register_address_4x))
        ok = self.modbus.write_register(plc_name, adjusted, int(value))
        if not ok or not verify:
            return ok
        after = self.modbus.read_register(plc_name, adjusted)
        return after == int(value)

    # -------------------------
    # Optional helpers (reduce endpoint code)
    # -------------------------

    def find_write_register(self, register_key: str) -> Optional[Dict[str, Any]]:
        """Find a write-tag definition by name inside data_points.yaml."""
        return _find_point_in_tree(self.write_signals, register_key, direction="write")


def _find_point_in_tree(tree: Dict[str, Any], key: str, *, direction: str) -> Optional[Dict[str, Any]]:
    """Recursively search the YAML tree for a register key inside {direction: {...}} blocks."""
    if not isinstance(tree, dict):
        return None

    # If this node has a direction block, check it.
    block = tree.get(direction)
    if isinstance(block, dict) and key in block:
        value = block[key]
        if isinstance(value, dict):
            return value
        return {"value": value}

    # Recurse into children
    for _, v in tree.items():
        if isinstance(v, dict):
            found = _find_point_in_tree(v, key, direction=direction)
            if found is not None:
                return found
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    found = _find_point_in_tree(item, key, direction=direction)
                    if found is not None:
                        return found
    return None
