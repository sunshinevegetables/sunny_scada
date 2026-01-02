from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

import yaml


class DataPointsService:
    """Thread-safe YAML read/update/add + register lookup."""

    def __init__(self, yaml_path: str):
        self.path = Path(yaml_path)
        self._lock = RLock()

    def _read_all(self) -> Dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            with self.path.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}

    def _write_all(self, data: Dict[str, Any]) -> None:
        with self._lock:
            with self.path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)

    def get_by_path(self, path: str) -> Optional[Any]:
        """Get any node by slash-separated path. Example: 'data_points/plcs/comp/screw/comp_1/read'."""
        data = self._read_all()
        node: Any = data
        for key in (path or "").split("/"):
            if not key:
                continue
            if not isinstance(node, dict):
                return None
            node = node.get(key)
            if node is None:
                return None
        return node

    def update_point_at_path(self, path: str, point_data: Dict[str, Any]) -> bool:
        """Update an existing leaf key at full path (path includes the key)."""
        data = self._read_all()
        keys = [k for k in (path or "").split("/") if k]
        if not keys:
            return False

        parent = data
        for k in keys[:-1]:
            if not isinstance(parent, dict) or k not in parent:
                return False
            parent = parent[k]

        if not isinstance(parent, dict) or keys[-1] not in parent:
            return False

        parent[keys[-1]] = point_data
        self._write_all(data)
        return True

    def add_point(self, parent_path: str, name: str, point_data: Dict[str, Any]) -> bool:
        """Add a new key under parent_path."""
        data = self._read_all()
        keys = [k for k in (parent_path or "").split("/") if k]
        parent = data
        for k in keys:
            if not isinstance(parent, dict):
                return False
            if k not in parent or not isinstance(parent[k], dict):
                parent[k] = {}
            parent = parent[k]

        if not isinstance(parent, dict):
            return False
        parent[name] = point_data
        self._write_all(data)
        return True

    def find_register(self, register_name: str, direction: str = "read") -> Optional[Dict[str, Any]]:
        """Find first occurrence of register_name under any `{direction: {...}}` block."""
        root = (self._read_all().get("data_points") or {})
        return _find_in_tree(root, register_name, direction)


def _find_in_tree(node: Any, register_name: str, direction: str) -> Optional[Dict[str, Any]]:
    if isinstance(node, dict):
        block = node.get(direction)
        if isinstance(block, dict) and register_name in block and isinstance(block[register_name], dict):
            return block[register_name]

        for v in node.values():
            found = _find_in_tree(v, register_name, direction)
            if found is not None:
                return found

    if isinstance(node, list):
        for item in node:
            found = _find_in_tree(item, register_name, direction)
            if found is not None:
                return found

    return None
