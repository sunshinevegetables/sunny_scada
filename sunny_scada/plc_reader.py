from __future__ import annotations

import logging
import os
import struct
from typing import Any, Dict, Optional

import yaml

from .data_storage import DataStorage
from .modbus_service import ModbusService, PLCConfig
from .scan_plan import TagSpec, build_blocks, build_tag_specs

logger = logging.getLogger(__name__)


def address_4x_to_pymodbus(address_4x: int) -> int:
    """Convert a 4xxxx address (e.g., 40001) into the address expected by PyModbus.

    Your original project used:
        pymodbus_addr = address_4x - 40001 + 1

    That mapping is preserved by default for backward compatibility, but can be tuned via env vars:

    - MODBUS_4X_BASE (default 40001)
    - MODBUS_PYMODBUS_OFFSET (default 1)
    """
    base = int(os.getenv("MODBUS_4X_BASE", "40001"))
    offset = int(os.getenv("MODBUS_PYMODBUS_OFFSET", "1"))
    return int(address_4x) - base + offset


def real_extra_offset() -> int:
    """Extra offset used when reading REAL (float) values.

    The original code read REAL values with:
        read_holding_registers(register_address + 1, 2)

    Where register_address was already (address_4x - 40001 + 1).

    Default: 1 (matches original behavior).
    """
    return int(os.getenv("MODBUS_REAL_EXTRA_OFFSET", "1"))


def use_block_reads() -> bool:
    return os.getenv("USE_BLOCK_READS", "1").strip() not in ("0", "false", "False", "no", "NO")


class PLCReader:
    """Reads tags from PLCs using a shared ModbusService.

    Key upgrades
    ------------
    1) **Connection management** is centralized in ModbusService (one connection per PLC + per-PLC lock).
    2) **Polling efficiency** can be upgraded via block reads (enabled by default via USE_BLOCK_READS=1):
       - A scan plan is built from data_points.yaml
       - Registers are read in contiguous blocks
       - Values are decoded locally

    If you need to temporarily fall back to the legacy per-tag reads, set USE_BLOCK_READS=0.
    """

    def __init__(
        self,
        modbus: ModbusService,
        storage: Optional[DataStorage] = None,
        *,
        config_file: str = "config/config.yaml",
        points_file: str = "config/data_points.yaml",
    ) -> None:
        self.modbus = modbus
        self.storage = storage

        self.config_file = config_file
        self.points_file = points_file

        self.config_data: Dict[str, Any] = self.load_config(self.config_file)
        self.data_points: Dict[str, Any] = self.load_data_points(self.points_file)

        # Ensure PLCs from config are registered in the ModbusService
        self._register_plcs_from_config()

        # Build scan plans per section (for block reads)
        self._scan_plans: Dict[str, Dict[str, Any]] = {}
        self._build_scan_plans()

        logger.info(
            "PLCReader initialized (sections=%d, block_reads=%s).",
            len(self.config_data),
            use_block_reads(),
        )

    # -------------------------
    # Config / points loading
    # -------------------------

    def load_config(self, config_file: str) -> Dict[str, Any]:
        """Load PLC configuration from YAML."""
        with open(config_file, "r", encoding="utf-8") as file:
            cfg = yaml.safe_load(file) or {}
        if not isinstance(cfg, dict):
            raise ValueError("Configuration file must contain a dictionary structure.")

        cleaned: Dict[str, Any] = {k: v for k, v in cfg.items() if isinstance(v, list)}
        if not cleaned:
            raise ValueError("Configuration file contains no PLC lists.")
        return cleaned

    def load_data_points(self, points_file: str) -> Dict[str, Any]:
        """Load data points from YAML."""
        with open(points_file, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict):
            raise ValueError("Data points file must contain a dictionary structure.")
        return data.get("data_points", {}) or {}

    def reload(self, *, config_file: Optional[str] = None, points_file: Optional[str] = None) -> None:
        """Reload config and/or points at runtime."""
        if config_file:
            self.config_file = config_file
            self.config_data = self.load_config(config_file)
            self._register_plcs_from_config()

        if points_file:
            self.points_file = points_file
            self.data_points = self.load_data_points(points_file)

        self._build_scan_plans()

    def _register_plcs_from_config(self) -> None:
        plcs: list[PLCConfig] = []
        for _, devices in self.config_data.items():
            for dev in devices:
                if not isinstance(dev, dict):
                    continue
                name = str(dev.get("name") or "").strip()
                ip = str(dev.get("ip") or "").strip()
                if not name or not ip:
                    continue
                port = int(dev.get("port") or 502)
                unit_id = int(dev.get("unit_id") or dev.get("slave") or 1)
                plcs.append(PLCConfig(name=name, ip=ip, port=port, unit_id=unit_id))

        if plcs:
            self.modbus.register_plcs(plcs)

    # -------------------------
    # Scan plan (block reads)
    # -------------------------

    def _build_scan_plans(self) -> None:
        """Build (tags, blocks) per section for efficient polling."""
        max_block_regs = int(os.getenv("MODBUS_MAX_BLOCK_REGS", "100"))
        max_gap_regs = int(os.getenv("MODBUS_MAX_GAP_REGS", "2"))
        extra = real_extra_offset()

        plans: Dict[str, Dict[str, Any]] = {}
        for section, tree in (self.data_points or {}).items():
            if not isinstance(tree, dict):
                continue
            tags = build_tag_specs(tree, address_4x_to_pymodbus=address_4x_to_pymodbus, real_extra_offset=extra)
            blocks = build_blocks(tags, max_block_regs=max_block_regs, max_gap_regs=max_gap_regs)
            plans[section] = {"tags": tags, "blocks": blocks}

            logger.info(
                "Scan plan built for section '%s': %d tags -> %d blocks (max_block=%d, max_gap=%d).",
                section,
                len(tags),
                len(blocks),
                max_block_regs,
                max_gap_regs,
            )

        self._scan_plans = plans

    # -------------------------
    # Decoding helpers
    # -------------------------

    @staticmethod
    def scale_value(raw_value: float, raw_zero: Optional[float], raw_full: Optional[float], eng_zero: Optional[float], eng_full: Optional[float]) -> float:
        if all(v is not None for v in (raw_zero, raw_full, eng_zero, eng_full)):
            if float(raw_full) == float(raw_zero):
                return float(raw_value)
            return ((float(raw_value) - float(raw_zero)) / (float(raw_full) - float(raw_zero))) * (float(eng_full) - float(eng_zero)) + float(eng_zero)
        return float(raw_value)

    @staticmethod
    def convert_to_float(high_register: int, low_register: int) -> float:
        combined = (int(high_register) << 16) | int(low_register)
        return struct.unpack(">f", struct.pack(">I", combined))[0]

    @staticmethod
    def _set_nested(root: Dict[str, Any], path: tuple[str, ...], value: Any) -> None:
        d = root
        for key in path[:-1]:
            nxt = d.get(key)
            if not isinstance(nxt, dict):
                nxt = {}
                d[key] = nxt
            d = nxt
        d[path[-1]] = value

    def _decode_tag(self, tag: TagSpec, reg_map: Dict[int, int]) -> Optional[Dict[str, Any]]:
        d = tag.details
        monitor = d.get("monitor")
        process = d.get("process")
        min_value = d.get("min")
        min_audio = d.get("min_audio")
        max_value = d.get("max")
        max_audio = d.get("max_audio")

        if tag.typ == "INTEGER":
            v = reg_map.get(tag.read_addr)
            if v is None:
                return None
            return {
                "description": tag.description,
                "type": "INTEGER",
                "value": int(v),
                "monitor": monitor,
                "process": process,
                "min": min_value,
                "max": max_value,
                "max_audio": max_audio,
                "min_audio": min_audio,
                "register_address": tag.base_addr,
            }

        if tag.typ == "REAL":
            hi = reg_map.get(tag.read_addr)
            lo = reg_map.get(tag.read_addr + 1)
            if hi is None or lo is None:
                return None
            raw_value = self.convert_to_float(hi, lo)
            scaled_value = self.scale_value(
                raw_value,
                d.get("raw_zero_scale"),
                d.get("raw_full_scale"),
                d.get("eng_zero_scale"),
                d.get("eng_full_scale"),
            )
            return {
                "description": tag.description,
                "type": "REAL",
                "raw_value": raw_value,
                "scaled_value": scaled_value,
                "monitor": monitor,
                "process": process,
                "min": min_value,
                "max": max_value,
                "max_audio": max_audio,
                "min_audio": min_audio,
                "register_address": tag.base_addr,
                "high_register": int(hi),
                "low_register": int(lo),
            }

        if tag.typ == "DIGITAL":
            v = reg_map.get(tag.read_addr)
            if v is None:
                return None
            integer_value = int(v)
            bit_statuses: Dict[str, Dict[str, Any]] = {}
            bits_cfg: Dict[str, Any] = d.get("bits", {}) or {}

            for bit_position in range(16):
                bit_label = f"BIT {bit_position}"
                bit_desc = bits_cfg.get(bit_label, "UNKNOWN")
                bit_value = bool(integer_value & (1 << bit_position))
                bit_statuses[bit_label] = {"description": bit_desc, "value": bit_value}

            return {
                "description": tag.description,
                "type": "DIGITAL",
                "value": bit_statuses,
                "monitor": monitor,
                "process": process,
                "min": min_value,
                "max": max_value,
                "max_audio": max_audio,
                "min_audio": min_audio,
                "register_address": tag.base_addr,
            }

        # Unknown type
        return None

    # -------------------------
    # Public read API
    # -------------------------

    def read_single_bit(self, plc_name: str, register_address_4x: int, bit_position: int) -> Optional[bool]:
        adjusted = address_4x_to_pymodbus(int(register_address_4x))
        return self.modbus.read_bit_from_holding_register(plc_name, adjusted, int(bit_position))

    def read_data_point(self, plc_name: str, point_name: str, point_details: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Ad-hoc read for a single tag definition (used by control sequences)."""
        return self._read_leaf_legacy(plc_name, point_name, point_details)

    def read_plc_section(self, plc_name: str, section: str) -> Dict[str, Any]:
        """Read a whole section for a PLC."""
        tree = self.data_points.get(section, {})
        if not isinstance(tree, dict) or not tree:
            return {}

        if not use_block_reads():
            # Legacy per-tag read (kept for troubleshooting)
            return self._read_plc_legacy(plc_name, tree)

        plan = self._scan_plans.get(section)
        if not plan:
            return {}

        tags: list[TagSpec] = plan["tags"]
        blocks = plan["blocks"]

        reg_map: Dict[int, int] = {}

        # Hold the PLC lock for the entire scan so writes cannot interleave.
        with self.modbus.plc_lock(plc_name):
            for block in blocks:
                regs = self.modbus.read_holding_registers(plc_name, block.start, block.count)
                if regs is None or len(regs) != block.count:
                    continue
                for i, val in enumerate(regs):
                    reg_map[block.start + i] = int(val)

        # Decode and rebuild nested structure
        root: Dict[str, Any] = {}
        for tag in tags:
            decoded = self._decode_tag(tag, reg_map)
            if decoded is None:
                continue
            self._set_nested(root, tag.path, decoded)
        return root

    def _read_plc_legacy(self, plc_name: str, data_points: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback: read tags one-by-one (slow; use only for troubleshooting)."""
        plc_data: Dict[str, Any] = {}
        for point_name, point_details in (data_points or {}).items():
            if isinstance(point_details, dict) and "address" not in point_details:
                plc_data[point_name] = self._read_plc_legacy(plc_name, point_details)
                continue

            decoded = self._read_leaf_legacy(plc_name, point_name, point_details)
            if decoded is not None:
                plc_data[point_name] = decoded
        return plc_data

    def _read_leaf_legacy(self, plc_name: str, point_name: str, point_details: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(point_details, dict):
            return None

        address_4x = point_details.get("address")
        data_type = point_details.get("type")
        description = point_details.get("description")

        if not address_4x or not data_type:
            return None

        base_addr = address_4x_to_pymodbus(int(address_4x))
        monitor = point_details.get("monitor")
        process = point_details.get("process")
        min_value = point_details.get("min")
        min_audio = point_details.get("min_audio")
        max_value = point_details.get("max")
        max_audio = point_details.get("max_audio")

        if data_type == "INTEGER":
            regs = self.modbus.read_holding_registers(plc_name, base_addr, 1)
            if not regs:
                return None
            return {
                "description": description,
                "type": "INTEGER",
                "value": int(regs[0]),
                "monitor": monitor,
                "process": process,
                "min": min_value,
                "max": max_value,
                "max_audio": max_audio,
                "min_audio": min_audio,
                "register_address": base_addr,
            }

        if data_type == "REAL":
            start = base_addr + real_extra_offset()
            regs = self.modbus.read_holding_registers(plc_name, start, 2)
            if not regs or len(regs) != 2:
                return None
            raw_value = self.convert_to_float(regs[0], regs[1])
            scaled_value = self.scale_value(
                raw_value,
                point_details.get("raw_zero_scale"),
                point_details.get("raw_full_scale"),
                point_details.get("eng_zero_scale"),
                point_details.get("eng_full_scale"),
            )
            return {
                "description": description,
                "type": "REAL",
                "raw_value": raw_value,
                "scaled_value": scaled_value,
                "monitor": monitor,
                "process": process,
                "min": min_value,
                "max": max_value,
                "max_audio": max_audio,
                "min_audio": min_audio,
                "register_address": base_addr,
                "high_register": int(regs[0]),
                "low_register": int(regs[1]),
            }

        if data_type == "DIGITAL":
            regs = self.modbus.read_holding_registers(plc_name, base_addr, 1)
            if not regs:
                return None
            integer_value = int(regs[0])
            bit_statuses: Dict[str, Dict[str, Any]] = {}
            bits_cfg: Dict[str, Any] = point_details.get("bits", {}) or {}
            for bit_position in range(16):
                bit_label = f"BIT {bit_position}"
                bit_desc = bits_cfg.get(bit_label, "UNKNOWN")
                bit_value = bool(integer_value & (1 << bit_position))
                bit_statuses[bit_label] = {"description": bit_desc, "value": bit_value}
            return {
                "description": description,
                "type": "DIGITAL",
                "value": bit_statuses,
                "monitor": monitor,
                "process": process,
                "min": min_value,
                "max": max_value,
                "max_audio": max_audio,
                "min_audio": min_audio,
                "register_address": base_addr,
            }

        logger.warning("Unsupported data type '%s' for '%s'.", data_type, point_name)
        return None

    def read_plcs_from_config(self, config_file: Optional[str] = None, data_points_file: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Read all PLCs as defined in the configuration.

        Returns:
            {section: {plc_name: plc_data, ...}, ...}

        Also updates DataStorage (if provided) per PLC.
        """
        try:
            if config_file or data_points_file:
                self.reload(config_file=config_file, points_file=data_points_file)

            all_device_data: Dict[str, Any] = {}

            for section, devices in self.config_data.items():
                section_data: Dict[str, Any] = {}
                for device in devices:
                    if not isinstance(device, dict):
                        continue
                    plc_name = device.get("name")
                    if not plc_name:
                        continue

                    device_data = self.read_plc_section(str(plc_name), section)
                    section_data[str(plc_name)] = device_data

                    if self.storage:
                        self.storage.update_data(str(plc_name), device_data)

                all_device_data[section] = section_data

            return all_device_data

        except Exception as e:
            logger.error("Unexpected error while processing PLC reads: %s", e)
            return None
