from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .modbus_service import ModbusService
from .plc_reader import address_4x_to_pymodbus

logger = logging.getLogger(__name__)


class PLCWriter:
    """Writes to PLCs via the shared ModbusService.

    This class no longer creates/owns ModbusTcpClient sockets. All writes go through ModbusService,
    which ensures a single connection per PLC and serializes concurrent requests per PLC.
    
    Write datapoint configuration is now managed entirely through the database.
    The PLCWriter only performs low-level Modbus write operations.
    """

    def __init__(self, modbus: ModbusService) -> None:
        self.modbus = modbus

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
