"""sunny_scada.modbus_service

A centralized, thread-safe Modbus TCP driver/service.

Why you want this
-----------------
The original Sunny SCADA code created Modbus clients in *both* PLCReader and PLCWriter and
connected/closed sockets repeatedly. Polling (background thread) and write endpoints could
also use the same client concurrently, causing connect/close races and intermittent failures.

This module provides:
- One persistent ModbusTcpClient per PLC (lazy connect).
- Per-PLC re-entrant lock: reads/writes are serialized per PLC to avoid interleaving requests.
- Automatic reconnect + retry with exponential backoff.
- Lightweight health state per PLC for /health endpoints and diagnostics.

It is intentionally synchronous because:
- Your current codebase uses threads (polling/monitoring).
- FastAPI runs sync endpoints in a threadpool; synchronous Modbus is fine.
- Migrating to the PyModbus async client is a separate, optional step.

If you later want an asyncio-native version, this service can be wrapped or replaced by an
AsyncModbusService with the same public methods.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml

try:
    from pymodbus.client import ModbusTcpClient  # type: ignore
except Exception:  # pragma: no cover
    ModbusTcpClient = None  # type: ignore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PLCConfig:
    """A single Modbus TCP endpoint."""
    name: str
    ip: str
    port: int = 502
    unit_id: int = 1  # a.k.a. slave id


@dataclass
class PLCHealth:
    connected: bool = False
    last_ok_ts: Optional[float] = None
    last_error_ts: Optional[float] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "last_ok_ts": self.last_ok_ts,
            "last_error_ts": self.last_error_ts,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
        }


class ModbusServiceError(RuntimeError):
    """Base exception for ModbusService."""


class UnknownPLCError(ModbusServiceError):
    """Raised when a PLC name is not registered."""


class ModbusConnectError(ModbusServiceError):
    """Raised when we fail to connect (after retries) and raise_on_error=True."""


class ModbusRequestError(ModbusServiceError):
    """Raised when a Modbus request fails (after retries) and raise_on_error=True."""


def load_plc_configs(config_file: str) -> List[PLCConfig]:
    """Load PLC definitions from a YAML file.

    Supports files shaped like:
      plcs:
        - name: ...
          ip: ...
          port: 502

    …and multi-section configs like:
      screw_comp:
        - name: ...
          ip: ...
      plcs:
        - name: ...
          ip: ...

    Any top-level key whose value is a list of dicts with at least (name, ip) is treated as a PLC list.
    """
    with open(config_file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ValueError(f"Invalid config file shape (expected dict): {config_file}")

    plcs: List[PLCConfig] = []
    for _, value in cfg.items():
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            ip = str(item.get("ip") or "").strip()
            if not name or not ip:
                continue
            port = int(item.get("port") or 502)
            unit_id = int(item.get("unit_id") or item.get("slave") or 1)
            plcs.append(PLCConfig(name=name, ip=ip, port=port, unit_id=unit_id))

    # Deduplicate by PLC name (first wins)
    dedup: Dict[str, PLCConfig] = {}
    for plc in plcs:
        dedup.setdefault(plc.name, plc)
    return list(dedup.values())


class ModbusService:
    """Central Modbus I/O service (thread-safe)."""

    def __init__(
        self,
        plcs: Sequence[PLCConfig],
        *,
        timeout_s: float = 3.0,
        retries: int = 2,
        backoff_s: float = 0.2,
        max_backoff_s: float = 2.0,
    ) -> None:
        if ModbusTcpClient is None:  # pragma: no cover
            raise ImportError("pymodbus is not installed. Install pymodbus==3.x to use ModbusService.")

        if not plcs:
            raise ValueError("ModbusService requires at least one PLCConfig.")

        self._timeout_s = float(timeout_s)
        self._retries = int(retries)
        self._backoff_s = float(backoff_s)
        self._max_backoff_s = float(max_backoff_s)

        self._plcs: Dict[str, PLCConfig] = {p.name: p for p in plcs}
        self._locks: Dict[str, RLock] = {name: RLock() for name in self._plcs}
        self._health: Dict[str, PLCHealth] = {name: PLCHealth() for name in self._plcs}

        # Create clients eagerly, connect lazily.
        self._clients: Dict[str, ModbusTcpClient] = {}
        for name, plc in self._plcs.items():
            self._clients[name] = ModbusTcpClient(plc.ip, port=plc.port, timeout=self._timeout_s)

        logger.info("ModbusService initialized with %d PLC(s).", len(self._plcs))

    def register_plcs(self, plcs: Sequence[PLCConfig]) -> None:
        """Register additional PLCs at runtime (idempotent)."""
        if ModbusTcpClient is None:  # pragma: no cover
            raise ImportError("pymodbus is not installed.")

        for plc in plcs:
            if plc.name in self._plcs:
                continue
            self._plcs[plc.name] = plc
            self._locks[plc.name] = RLock()
            self._health[plc.name] = PLCHealth()
            self._clients[plc.name] = ModbusTcpClient(plc.ip, port=plc.port, timeout=self._timeout_s)
            logger.info("Registered PLC '%s' (%s:%s).", plc.name, plc.ip, plc.port)

    def plc_names(self) -> List[str]:
        return list(self._plcs.keys())

    def health_snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {name: h.to_dict() for name, h in self._health.items()}

    @contextmanager
    def plc_lock(self, plc_name: str) -> Iterable[None]:
        """Acquire the per-PLC lock.

        Useful when you need a read-modify-write sequence to be atomic, or want to prevent writes
        from interleaving with a multi-block scan.
        """
        lock = self._locks.get(plc_name)
        if lock is None:
            raise UnknownPLCError(f"PLC '{plc_name}' is not registered.")
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def close(self) -> None:
        """Close all Modbus sockets."""
        for name, client in self._clients.items():
            try:
                if getattr(client, "is_socket_open", None) and client.is_socket_open():
                    client.close()
            except Exception as e:  # pragma: no cover
                logger.debug("Error closing PLC '%s' socket: %s", name, e)
        logger.info("ModbusService sockets closed.")

    # ---- internal helpers ----

    def _get_client_locked(self, plc_name: str) -> ModbusTcpClient:
        client = self._clients.get(plc_name)
        if client is None:
            raise UnknownPLCError(f"PLC '{plc_name}' is not registered.")
        return client

    def _get_unit_id(self, plc_name: str, unit_id: Optional[int]) -> int:
        if unit_id is not None:
            return int(unit_id)
        return int(self._plcs[plc_name].unit_id)

    def _mark_ok(self, plc_name: str) -> None:
        h = self._health[plc_name]
        h.connected = True
        h.last_ok_ts = time.time()
        h.last_error = None
        h.last_error_ts = None
        h.consecutive_failures = 0

    def _mark_error(self, plc_name: str, err: str) -> None:
        h = self._health[plc_name]
        h.connected = False
        h.last_error = err
        h.last_error_ts = time.time()
        h.consecutive_failures += 1

    def _sleep_backoff(self, attempt: int) -> None:
        delay = min(self._max_backoff_s, self._backoff_s * (2 ** attempt))
        time.sleep(delay)

    def _ensure_connected_locked(self, plc_name: str, client: ModbusTcpClient) -> bool:
        # Throttle reconnect attempts a bit when repeatedly failing.
        h = self._health[plc_name]
        if h.last_error_ts is not None and h.consecutive_failures > 0:
            since = time.time() - h.last_error_ts
            min_wait = min(self._max_backoff_s, self._backoff_s * (2 ** min(h.consecutive_failures, 5)))
            if since < min_wait:
                return False

        try:
            if getattr(client, "is_socket_open", None) and client.is_socket_open():
                h.connected = True
                return True
            ok = bool(client.connect())
            h.connected = ok
            if not ok:
                self._mark_error(plc_name, "connect() returned False")
            return ok
        except Exception as e:
            self._mark_error(plc_name, f"connect exception: {e}")
            return False

    def _execute(
        self,
        plc_name: str,
        op_name: str,
        func,
        *,
        unit_id: Optional[int] = None,
        raise_on_error: bool = False,
    ) -> Any:
        """Run a Modbus operation with per-PLC locking, reconnect and retries.

        `func` must be a callable like: func(client, unit_id) -> response
        """
        lock = self._locks.get(plc_name)
        if lock is None:
            raise UnknownPLCError(f"PLC '{plc_name}' is not registered.")

        with lock:
            client = self._get_client_locked(plc_name)
            uid = self._get_unit_id(plc_name, unit_id)

            last_exc: Optional[Exception] = None
            for attempt in range(self._retries + 1):
                if not self._ensure_connected_locked(plc_name, client):
                    last_exc = ModbusConnectError(f"Failed to connect to PLC '{plc_name}'.")
                    if attempt < self._retries:
                        self._sleep_backoff(attempt)
                        continue
                    if raise_on_error:
                        raise last_exc
                    return None

                try:
                    resp = func(client, uid)
                    if resp is None:
                        raise ModbusRequestError(f"{op_name}: no response")
                    if hasattr(resp, "isError") and resp.isError():
                        raise ModbusRequestError(f"{op_name}: {resp}")

                    self._mark_ok(plc_name)
                    return resp

                except Exception as e:
                    last_exc = e
                    self._mark_error(plc_name, f"{op_name}: {e}")
                    try:
                        client.close()  # force reconnect next attempt
                    except Exception:
                        pass

                    if attempt < self._retries:
                        self._sleep_backoff(attempt)
                        continue

                    if raise_on_error:
                        raise ModbusRequestError(f"{op_name} failed for PLC '{plc_name}': {e}") from e
                    return None

            # Should never get here.
            if raise_on_error and last_exc is not None:
                raise ModbusRequestError(f"{op_name} failed for PLC '{plc_name}': {last_exc}") from last_exc
            return None

    # -----------------
    # Public operations
    # -----------------

    def read_holding_registers(
        self,
        plc_name: str,
        address: int,
        count: int,
        *,
        unit_id: Optional[int] = None,
    ) -> Optional[List[int]]:
        """Read holding registers.

        Note: `address` is the PyModbus address (what you pass to client.read_holding_registers).
        In your existing project, you convert 4xxxx addresses with: (addr_4x - 40001 + 1).
        """
        def op(client: ModbusTcpClient, uid: int):
            return client.read_holding_registers(int(address), int(count), slave=uid)

        resp = self._execute(plc_name, "read_holding_registers", op, unit_id=unit_id)
        if resp is None:
            return None
        regs = getattr(resp, "registers", None)
        if regs is None:
            return None
        return list(regs)

    def write_register(
        self,
        plc_name: str,
        address: int,
        value: int,
        *,
        unit_id: Optional[int] = None,
    ) -> bool:
        """Write a single holding register."""
        def op(client: ModbusTcpClient, uid: int):
            return client.write_register(int(address), int(value), slave=uid)

        resp = self._execute(plc_name, "write_register", op, unit_id=unit_id)
        return resp is not None

    def read_register(
        self,
        plc_name: str,
        address: int,
        *,
        unit_id: Optional[int] = None,
    ) -> Optional[int]:
        """Read one holding register."""
        regs = self.read_holding_registers(plc_name, address, 1, unit_id=unit_id)
        if not regs:
            return None
        return int(regs[0])

    def read_bit_from_holding_register(
        self,
        plc_name: str,
        address: int,
        bit: int,
        *,
        unit_id: Optional[int] = None,
    ) -> Optional[bool]:
        """Read a single bit from a holding register."""
        value = self.read_register(plc_name, address, unit_id=unit_id)
        if value is None:
            return None
        if bit < 0 or bit > 15:
            raise ValueError("bit must be in range 0..15")
        return bool(value & (1 << bit))

    def write_bit_in_holding_register(
        self,
        plc_name: str,
        address: int,
        bit: int,
        value: int,
        *,
        unit_id: Optional[int] = None,
        verify: bool = True,
    ) -> bool:
        """Read-modify-write a single bit in a holding register.

        This operation is serialized by the per-PLC lock, so it is safe with concurrent polling.
        """
        if bit < 0 or bit > 15:
            raise ValueError("bit must be in range 0..15")
        if value not in (0, 1):
            raise ValueError("value must be 0 or 1")

        with self.plc_lock(plc_name):
            current = self.read_register(plc_name, address, unit_id=unit_id)
            if current is None:
                return False

            if value == 1:
                new_value = current | (1 << bit)
            else:
                new_value = current & ~(1 << bit)

            if not self.write_register(plc_name, address, new_value, unit_id=unit_id):
                return False

            if not verify:
                return True

            after = self.read_register(plc_name, address, unit_id=unit_id)
            if after is None:
                return False
            return bool(after & (1 << bit)) == bool(value)
