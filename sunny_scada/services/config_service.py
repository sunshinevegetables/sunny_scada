from __future__ import annotations

import difflib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

try:
    import portalocker  # type: ignore
except Exception:  # pragma: no cover
    portalocker = None
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


class ConfigError(RuntimeError):
    pass


class NotFound(ConfigError):
    pass


class Conflict(ConfigError):
    pass


class ConfigService:
    """Concurrency-safe, round-trip YAML editor for data_points.yaml.

    - Uses a file lock (fcntl) to protect read-modify-write.
    - Uses atomic write (temp + fsync + os.replace).
    - Uses ruamel.yaml round-trip loader/dumper to preserve ordering/comments.
    """

    def __init__(self, yaml_path: str) -> None:
        self.path = Path(yaml_path)
        self._yaml = YAML(typ="rt")
        self._yaml.preserve_quotes = True
        self._yaml.width = 4096
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _file_lock(self) -> Generator[None, None, None]:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Cross-platform file lock:
        # - Windows: uses portalocker (msvcrt under the hood)
        # - POSIX: portalocker if installed, else fcntl
        if portalocker is not None:
            with portalocker.Lock(str(self._lock_path), timeout=30):
                yield
            return

        try:
            import fcntl  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "File locking requires 'portalocker' on this platform. "
                "Please install it and retry."
            ) from e

        with open(self._lock_path, "w", encoding="utf-8") as lockf:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

    def load(self) -> CommentedMap:
        if not self.path.exists():
            raise NotFound(f"Config file not found: {self.path}")
        with self.path.open("r", encoding="utf-8") as f:
            data = self._yaml.load(f)  # type: ignore[no-any-return]
        if data is None:
            data = CommentedMap()
        if not isinstance(data, CommentedMap):
            raise ConfigError("Invalid YAML root shape (expected mapping)")
        return data

    def dump_to_string(self, data: CommentedMap) -> str:
        from io import StringIO

        buf = StringIO()
        self._yaml.dump(data, buf)
        return buf.getvalue()

    def atomic_write(self, data: CommentedMap) -> None:
        """Atomic write with fsync + os.replace."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmpf:
                self._yaml.dump(data, tmpf)
                tmpf.flush()
                os.fsync(tmpf.fileno())
            os.replace(tmp_path, self.path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    def read_snapshot(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")

    def compute_diff(self, before: str, after: str) -> str:
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=str(self.path),
                tofile=str(self.path),
            )
        )

    # ----------------------------
    # Helpers for data_points.yaml
    # ----------------------------
    def _root_plcs(self, root: CommentedMap) -> CommentedMap:
        data_points = root.get("data_points")
        if data_points is None:
            data_points = CommentedMap()
            root["data_points"] = data_points
        if not isinstance(data_points, CommentedMap):
            raise ConfigError("data_points must be a mapping")
        plcs = data_points.get("plcs")
        if plcs is None:
            plcs = CommentedMap()
            data_points["plcs"] = plcs
        if not isinstance(plcs, CommentedMap):
            raise ConfigError("data_points.plcs must be a mapping")
        return plcs

    def list_plcs(self) -> List[str]:
        with self._file_lock():
            root = self.load()
            plcs = self._root_plcs(root)
            return [str(k) for k in plcs.keys()]

    def get_plc(self, plc_id: str) -> CommentedMap:
        with self._file_lock():
            root = self.load()
            plcs = self._root_plcs(root)
            node = plcs.get(plc_id)
            if node is None:
                raise NotFound(f"PLC '{plc_id}' not found")
            if not isinstance(node, CommentedMap):
                raise ConfigError(f"PLC '{plc_id}' must be a mapping")
            return node

    def create_plc(self, plc_id: str, *, content: Optional[Dict[str, Any]] = None) -> Tuple[str, str, str]:
        with self._file_lock():
            before = self.read_snapshot()
            root = self.load()
            plcs = self._root_plcs(root)
            if plc_id in plcs:
                raise Conflict(f"PLC '{plc_id}' already exists")
            plcs[plc_id] = CommentedMap(content or {})
            self.atomic_write(root)
            after = self.read_snapshot()
            return before, after, self.compute_diff(before, after)

    def update_plc(
        self,
        plc_id: str,
        *,
        new_id: Optional[str] = None,
        content: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str, str]:
        with self._file_lock():
            before = self.read_snapshot()
            root = self.load()
            plcs = self._root_plcs(root)
            if plc_id not in plcs:
                raise NotFound(f"PLC '{plc_id}' not found")

            node = plcs[plc_id]
            if content is not None:
                plcs[plc_id] = CommentedMap(content)
            if new_id and new_id != plc_id:
                if new_id in plcs:
                    raise Conflict(f"PLC '{new_id}' already exists")
                plcs[new_id] = plcs.pop(plc_id)

            self.atomic_write(root)
            after = self.read_snapshot()
            return before, after, self.compute_diff(before, after)

    def delete_plc(self, plc_id: str) -> Tuple[str, str, str]:
        with self._file_lock():
            before = self.read_snapshot()
            root = self.load()
            plcs = self._root_plcs(root)
            if plc_id not in plcs:
                raise NotFound(f"PLC '{plc_id}' not found")
            plcs.pop(plc_id)
            self.atomic_write(root)
            after = self.read_snapshot()
            return before, after, self.compute_diff(before, after)

    # Datapoint traversal -------------------------------------------------
    def _iter_direction_blocks(
        self,
        node: Any,
        *,
        path: List[str],
    ) -> Generator[Tuple[List[str], str, CommentedMap], None, None]:
        """Yield (path_to_block, direction, block_map) for each read/write block."""
        if isinstance(node, CommentedMap):
            for direction in ("read", "write"):
                block = node.get(direction)
                if isinstance(block, CommentedMap):
                    yield (path + [direction], direction, block)
            for k, v in node.items():
                if k in ("read", "write"):
                    continue
                yield from self._iter_direction_blocks(v, path=path + [str(k)])

    def list_datapoints(self, plc_id: str) -> List[Dict[str, Any]]:
        with self._file_lock():
            root = self.load()
            plcs = self._root_plcs(root)
            plc = plcs.get(plc_id)
            if plc is None:
                raise NotFound(f"PLC '{plc_id}' not found")
            if not isinstance(plc, CommentedMap):
                raise ConfigError(f"PLC '{plc_id}' must be a mapping")

            results: List[Dict[str, Any]] = []
            for p, direction, block in self._iter_direction_blocks(plc, path=[]):
                for dp_id, dp in block.items():
                    if not isinstance(dp, CommentedMap):
                        continue
                    results.append(
                        {
                            "datapoint_id": str(dp_id),
                            "direction": direction,
                            "path": "/".join(p),
                            "data": dp,
                        }
                    )
            return results

    def find_datapoint(
        self,
        plc_id: str,
        dp_id: str,
        *,
        path: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> List[Tuple[List[str], CommentedMap]]:
        """Return list of matches (path_to_datapoint, datapoint_map)."""
        root = self.load()
        plcs = self._root_plcs(root)
        plc = plcs.get(plc_id)
        if plc is None:
            raise NotFound(f"PLC '{plc_id}' not found")
        if not isinstance(plc, CommentedMap):
            raise ConfigError(f"PLC '{plc_id}' must be a mapping")

        matches: List[Tuple[List[str], CommentedMap]] = []
        for p, dir_name, block in self._iter_direction_blocks(plc, path=[]):
            if direction and dir_name != direction:
                continue
            if path and "/".join(p) != path:
                continue
            if dp_id in block and isinstance(block[dp_id], CommentedMap):
                matches.append((p + [dp_id], block[dp_id]))
        return matches

    def _ensure_path(self, plc: CommentedMap, path_parts: List[str]) -> CommentedMap:
        node: CommentedMap = plc
        for part in path_parts:
            if part not in node or not isinstance(node.get(part), CommentedMap):
                node[part] = CommentedMap()
            node = node[part]
        return node

    def create_datapoint(
        self,
        plc_id: str,
        *,
        dp_id: str,
        direction: str,
        parent_path: str,
        data: Dict[str, Any],
    ) -> Tuple[str, str, str]:
        direction = direction.strip().lower()
        if direction not in ("read", "write"):
            raise ConfigError("direction must be 'read' or 'write'")

        with self._file_lock():
            before = self.read_snapshot()
            root = self.load()
            plcs = self._root_plcs(root)
            plc = plcs.get(plc_id)
            if plc is None:
                raise NotFound(f"PLC '{plc_id}' not found")
            if not isinstance(plc, CommentedMap):
                raise ConfigError(f"PLC '{plc_id}' must be a mapping")

            # Ensure parent path exists, then ensure direction block exists
            parts = [p for p in parent_path.split("/") if p]
            container = self._ensure_path(plc, parts)
            if direction not in container or not isinstance(container.get(direction), CommentedMap):
                container[direction] = CommentedMap()
            block: CommentedMap = container[direction]

            if dp_id in block:
                raise Conflict(f"Datapoint '{dp_id}' already exists at {parent_path}/{direction}")
            block[dp_id] = CommentedMap(data)

            self.atomic_write(root)
            after = self.read_snapshot()
            return before, after, self.compute_diff(before, after)

    def update_datapoint(
        self,
        plc_id: str,
        dp_id: str,
        *,
        data: Dict[str, Any],
        path: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        with self._file_lock():
            before = self.read_snapshot()
            root = self.load()
            plcs = self._root_plcs(root)
            plc = plcs.get(plc_id)
            if plc is None:
                raise NotFound(f"PLC '{plc_id}' not found")
            if not isinstance(plc, CommentedMap):
                raise ConfigError(f"PLC '{plc_id}' must be a mapping")

            matches = self.find_datapoint(plc_id, dp_id, path=path, direction=direction)
            if not matches:
                raise NotFound(f"Datapoint '{dp_id}' not found")
            if len(matches) > 1:
                raise Conflict(
                    f"Datapoint '{dp_id}' is ambiguous; matches: {['/'.join(p) for p,_ in matches]}"
                )

            dp_path, _ = matches[0]

            # Walk to parent of datapoint (block) and replace
            node: Any = plc
            for part in dp_path[:-1]:
                node = node[part]
            if not isinstance(node, CommentedMap):
                raise ConfigError("Invalid YAML structure")
            node[dp_path[-1]] = CommentedMap(data)

            self.atomic_write(root)
            after = self.read_snapshot()
            return before, after, self.compute_diff(before, after)

    def delete_datapoint(
        self,
        plc_id: str,
        dp_id: str,
        *,
        path: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        with self._file_lock():
            before = self.read_snapshot()
            root = self.load()
            plcs = self._root_plcs(root)
            plc = plcs.get(plc_id)
            if plc is None:
                raise NotFound(f"PLC '{plc_id}' not found")
            if not isinstance(plc, CommentedMap):
                raise ConfigError(f"PLC '{plc_id}' must be a mapping")

            matches = self.find_datapoint(plc_id, dp_id, path=path, direction=direction)
            if not matches:
                raise NotFound(f"Datapoint '{dp_id}' not found")
            if len(matches) > 1:
                raise Conflict(
                    f"Datapoint '{dp_id}' is ambiguous; matches: {['/'.join(p) for p,_ in matches]}"
                )

            dp_path, _ = matches[0]
            node: Any = plc
            for part in dp_path[:-1]:
                node = node[part]
            if not isinstance(node, CommentedMap):
                raise ConfigError("Invalid YAML structure")
            node.pop(dp_path[-1], None)

            self.atomic_write(root)
            after = self.read_snapshot()
            return before, after, self.compute_diff(before, after)

    def patch_datapoint_parameters(
        self,
        plc_id: str,
        dp_id: str,
        *,
        set_params: Dict[str, Any],
        delete_params: List[str],
        path: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        with self._file_lock():
            before = self.read_snapshot()
            root = self.load()
            plcs = self._root_plcs(root)
            plc = plcs.get(plc_id)
            if plc is None:
                raise NotFound(f"PLC '{plc_id}' not found")
            if not isinstance(plc, CommentedMap):
                raise ConfigError(f"PLC '{plc_id}' must be a mapping")

            matches = self.find_datapoint(plc_id, dp_id, path=path, direction=direction)
            if not matches:
                raise NotFound(f"Datapoint '{dp_id}' not found")
            if len(matches) > 1:
                raise Conflict(
                    f"Datapoint '{dp_id}' is ambiguous; matches: {['/'.join(p) for p,_ in matches]}"
                )

            _, dp = matches[0]
            for k, v in (set_params or {}).items():
                dp[k] = v
            for k in delete_params or []:
                if k in dp:
                    dp.pop(k)

            self.atomic_write(root)
            after = self.read_snapshot()
            return before, after, self.compute_diff(before, after)

    def validate(self) -> Dict[str, Any]:
        """Basic sanity checks for data_points.yaml."""
        errors: List[str] = []
        warnings: List[str] = []

        with self._file_lock():
            root = self.load()
            try:
                plcs = self._root_plcs(root)
            except Exception as e:
                return {"ok": False, "errors": [str(e)], "warnings": []}

            seen: Dict[str, List[str]] = {}
            addr_seen: Dict[str, List[str]] = {}

            for plc_id, plc in plcs.items():
                if not isinstance(plc, CommentedMap):
                    errors.append(f"PLC '{plc_id}' is not a mapping")
                    continue
                for p, direction, block in self._iter_direction_blocks(plc, path=[]):
                    block_path = f"{plc_id}/" + "/".join(p)
                    for dp_id, dp in block.items():
                        if not isinstance(dp, CommentedMap):
                            warnings.append(f"Datapoint '{dp_id}' at {block_path} is not a mapping")
                            continue
                        loc = f"{block_path}/{dp_id}"
                        seen.setdefault(str(dp_id), []).append(loc)

                        if "address" not in dp:
                            errors.append(f"Missing 'address' for datapoint {loc}")
                        else:
                            try:
                                addr = int(dp.get("address"))
                                addr_seen.setdefault(f"{plc_id}:{direction}:{addr}", []).append(loc)
                            except Exception:
                                errors.append(f"Invalid 'address' (must be int) for datapoint {loc}")

                        if "type" not in dp:
                            errors.append(f"Missing 'type' for datapoint {loc}")
                        else:
                            t = str(dp.get("type")).strip()
                            if not t:
                                errors.append(f"Invalid 'type' for datapoint {loc}")

            # Duplicate datapoint ids across file
            for dp_id, locs in seen.items():
                if len(locs) > 1:
                    warnings.append(f"Duplicate datapoint id '{dp_id}' found at: {locs}")

            # Overlapping addresses within same plc+direction
            for key, locs in addr_seen.items():
                if len(locs) > 1:
                    warnings.append(f"Overlapping address '{key}' used by: {locs}")

        return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}

    # ------------------------------------------------------------------
    # Legacy helpers (to keep existing /update_data_point + /add_data_point
    # endpoints concurrency-safe and round-trip friendly).
    # ------------------------------------------------------------------
    def update_leaf_by_absolute_path(self, path: str, value: Dict[str, Any]) -> Tuple[str, str, str]:
        """Update an existing mapping node at an absolute slash-separated path.

        Example path:
          data_points/plcs/comp/screw/comp_1/read/COMP_1_CAPACITY_CONTROL
        """
        keys = [k for k in (path or "").split("/") if k]
        if not keys:
            raise ConfigError("path is required")

        with self._file_lock():
            before = self.read_snapshot()
            root = self.load()
            node: Any = root
            for k in keys[:-1]:
                if not isinstance(node, CommentedMap) or k not in node:
                    raise NotFound(f"Path not found: {path}")
                node = node[k]
            if not isinstance(node, CommentedMap) or keys[-1] not in node:
                raise NotFound(f"Path not found: {path}")
            node[keys[-1]] = CommentedMap(value)
            self.atomic_write(root)
            after = self.read_snapshot()
            return before, after, self.compute_diff(before, after)

    def add_leaf_by_absolute_path(self, parent_path: str, name: str, value: Dict[str, Any]) -> Tuple[str, str, str]:
        """Add a new key under an absolute slash-separated parent path."""
        keys = [k for k in (parent_path or "").split("/") if k]
        if not name:
            raise ConfigError("name is required")

        with self._file_lock():
            before = self.read_snapshot()
            root = self.load()
            node: Any = root
            for k in keys:
                if not isinstance(node, CommentedMap):
                    raise ConfigError("Invalid YAML structure")
                if k not in node or not isinstance(node.get(k), CommentedMap):
                    node[k] = CommentedMap()
                node = node[k]
            if not isinstance(node, CommentedMap):
                raise ConfigError("Invalid YAML structure")
            if name in node:
                raise Conflict(f"Key '{name}' already exists at {parent_path}")
            node[name] = CommentedMap(value)
            self.atomic_write(root)
            after = self.read_snapshot()
            return before, after, self.compute_diff(before, after)
