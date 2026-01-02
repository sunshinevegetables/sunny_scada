from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Dict, Optional, TypeVar

from pymodbus.client import ModbusTcpClient

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class PLCConnectionInfo:
    """Connection details for a PLC."""

    name: str
    ip: str
    port: int = 502
    unit_id: int = 1


class ModbusManager:
    """Manages Modbus TCP clients and provides safe, retrying I/O methods.

    Create one ModbusManager for the whole application and share it across
    polling threads and API endpoints.
    """

    def __init__(
        self,
        plcs: list[PLCConnectionInfo],
        *,
        timeout_s: float = 3.0,
        retries: int = 2,
        backoff_s: float = 0.2,
    ) -> None:
        self._timeout_s = timeout_s
        self._retries = max(0, retries)
        self._backoff_s = max(0.0, backoff_s)

        self._plcs: Dict[str, PLCConnectionInfo] = {p.name: p for p in plcs}
        self._clients: Dict[str, ModbusTcpClient] = {
            p.name: ModbusTcpClient(p.ip, port=p.port, timeout=timeout_s) for p in plcs
        }
        self._locks: Dict[str, Lock] = {p.name: Lock() for p in plcs}

        logger.info("ModbusManager initialized for %d PLC(s)", len(plcs))

    def close(self) -> None:
        """Close all client sockets."""
        for name, client in self._clients.items():
            try:
                if client and client.is_socket_open():
                    logger.info("Closing Modbus socket for %s", name)
                    client.close()
            except Exception:
                logger.exception("Failed closing Modbus socket for %s", name)

    def _ensure_connected(self, plc_name: str, client: ModbusTcpClient) -> bool:
        """Ensure the client has an open socket."""
        try:
            if client.is_socket_open():
                return True
            # connect() returns bool
            ok = client.connect()
            if not ok:
                logger.warning("Modbus connect failed for %s", plc_name)
            return bool(ok)
        except Exception:
            logger.exception("Modbus connect error for %s", plc_name)
            return False

    def _with_lock(self, plc_name: str, fn: Callable[[ModbusTcpClient, int], T], unit_id: Optional[int]) -> Optional[T]:
        if plc_name not in self._clients:
            logger.error("Unknown PLC '%s'", plc_name)
            return None

        info = self._plcs[plc_name]
        client = self._clients[plc_name]
        lock = self._locks[plc_name]
        uid = int(unit_id if unit_id is not None else info.unit_id)

        with lock:
            if not self._ensure_connected(plc_name, client):
                return None

            last_exc: Optional[BaseException] = None
            for attempt in range(self._retries + 1):
                try:
                    return fn(client, uid)
                except Exception as exc:
                    last_exc = exc
                    # drop socket and retry
                    try:
                        client.close()
                    except Exception:
                        pass
                    if attempt < self._retries:
                        sleep_s = self._backoff_s * (2**attempt)
                        time.sleep(sleep_s)

            if last_exc:
                logger.exception("Modbus operation failed for %s", plc_name, exc_info=last_exc)
            return None

    def read_holding_registers(
        self,
        plc_name: str,
        address: int,
        count: int,
        *,
        unit_id: Optional[int] = None,
    ):
        """Read holding registers.

        Parameters
        ----------
        plc_name: PLC name from config
        address: 0-based register address as expected by pymodbus
        count: number of registers (max 125 typical)
        unit_id: optional Modbus unit id
        """

        if count <= 0:
            return None

        def _op(client: ModbusTcpClient, uid: int):
            # PyModbus 3.3+ uses the keyword `slave=` (older versions used `unit=`)
            resp = client.read_holding_registers(address, count, slave=uid)
            if resp is None or getattr(resp, "isError", lambda: True)():
                raise RuntimeError(f"read_holding_registers error (addr={address}, count={count})")
            return resp

        return self._with_lock(plc_name, _op, unit_id)

    def write_register(
        self,
        plc_name: str,
        address: int,
        value: int,
        *,
        unit_id: Optional[int] = None,
    ) -> bool:
        """Write a single holding register."""

        def _op(client: ModbusTcpClient, uid: int):
            resp = client.write_register(address, value, slave=uid)
            if resp is None or getattr(resp, "isError", lambda: True)():
                raise RuntimeError(f"write_register error (addr={address}, value={value})")
            return True

        return bool(self._with_lock(plc_name, _op, unit_id))

    def read_modify_write_bit(
        self,
        plc_name: str,
        address: int,
        bit: int,
        value: int,
        *,
        unit_id: Optional[int] = None,
        verify: bool = True,
    ) -> bool:
        """Safely modify a single bit in a holding register.

        This method serializes access per PLC, reads the register, modifies the bit,
        writes the result back, and optionally verifies the write.
        """

        if bit < 0 or bit > 15:
            raise ValueError("bit must be in [0, 15]")
        if value not in (0, 1):
            raise ValueError("value must be 0 or 1")

        def _op(client: ModbusTcpClient, uid: int):
            read_resp = client.read_holding_registers(address, 1, slave=uid)
            if read_resp is None or getattr(read_resp, "isError", lambda: True)():
                raise RuntimeError(f"read before write failed (addr={address})")
            current = int(read_resp.registers[0])
            if value == 1:
                new_val = current | (1 << bit)
            else:
                new_val = current & ~(1 << bit)

            write_resp = client.write_register(address, new_val, slave=uid)
            if write_resp is None or getattr(write_resp, "isError", lambda: True)():
                raise RuntimeError(f"write failed (addr={address}, value={new_val})")

            if not verify:
                return True

            verify_resp = client.read_holding_registers(address, 1, slave=uid)
            if verify_resp is None or getattr(verify_resp, "isError", lambda: True)():
                raise RuntimeError(f"verify read failed (addr={address})")
            actual = int(verify_resp.registers[0])
            expected_bit = bool(actual & (1 << bit))
            if expected_bit != bool(value):
                raise RuntimeError(
                    f"verify mismatch (addr={address}, bit={bit}, expected={value}, actual={int(expected_bit)})"
                )
            return True

        return bool(self._with_lock(plc_name, _op, unit_id))
