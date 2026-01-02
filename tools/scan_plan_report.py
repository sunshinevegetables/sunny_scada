from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TagSpec:
    """A leaf tag definition extracted from the nested YAML tree."""

    path: Tuple[str, ...]  # hierarchical keys to this tag (including tag name)
    address_4x: int  # Modbus address from YAML (e.g., 40136)
    data_type: str  # INTEGER / REAL / DIGITAL

    # Read plan for pymodbus (0-based addresses as expected by pymodbus)
    read_address: int
    read_count: int

    # Optional metadata (passed through to API clients)
    description: Optional[str] = None
    monitor: Optional[bool] = None
    process: Optional[str] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_audio: Optional[str] = None
    max_audio: Optional[str] = None

    # REAL scaling parameters
    raw_zero_scale: Optional[float] = None
    raw_full_scale: Optional[float] = None
    eng_zero_scale: Optional[float] = None
    eng_full_scale: Optional[float] = None

    # DIGITAL
    bits: Optional[Dict[str, str]] = None


@dataclass(frozen=True)
class ReadBlock:
    """A single contiguous Modbus register read."""

    address: int  # start address (0-based)
    count: int


def address_4x_to_pymodbus(address_4x: int) -> int:
    """Convert a 4xxxx Modbus address (human) to pymodbus 0-based address.

    This project historically used an off-by-one mapping (address-40001+1).
    To avoid breaking existing deployments, we preserve that mapping here.

    If you want to migrate to strict 0-based addressing, change this function
    AND update the YAML addresses accordingly.
    """

    return int(address_4x) - 40001 + 1


def required_range_for_tag(address_4x: int, data_type: str) -> Tuple[int, int]:
    """Return (read_address, read_count) for a tag."""

    base = address_4x_to_pymodbus(address_4x)
    t = (data_type or "").upper()
    if t == "REAL":
        # Preserve existing behavior: float starts at base+1
        return base + 1, 2
    return base, 1


def flatten_tag_tree(
    node: Mapping[str, Any],
    *,
    prefix: Tuple[str, ...] = (),
) -> List[TagSpec]:
    """Flatten nested YAML tag tree into TagSpec items."""

    tags: List[TagSpec] = []
    for key, value in node.items():
        path = prefix + (str(key),)
        if isinstance(value, Mapping) and "address" in value:
            address = int(value.get("address"))
            dtype = str(value.get("type") or "").upper()
            read_address, read_count = required_range_for_tag(address, dtype)

            tags.append(
                TagSpec(
                    path=path,
                    address_4x=address,
                    data_type=dtype,
                    read_address=read_address,
                    read_count=read_count,
                    description=value.get("description"),
                    monitor=value.get("monitor"),
                    process=value.get("process"),
                    min_value=value.get("min"),
                    max_value=value.get("max"),
                    min_audio=value.get("min_audio"),
                    max_audio=value.get("max_audio"),
                    raw_zero_scale=value.get("raw_zero_scale"),
                    raw_full_scale=value.get("raw_full_scale"),
                    eng_zero_scale=value.get("eng_zero_scale"),
                    eng_full_scale=value.get("eng_full_scale"),
                    bits=(value.get("bits") or None),
                )
            )
        elif isinstance(value, Mapping):
            tags.extend(flatten_tag_tree(value, prefix=path))

    return tags


def build_blocks(
    tags: Iterable[TagSpec],
    *,
    max_block_size: int = 120,
    max_gap: int = 2,
) -> List[ReadBlock]:
    """Merge per-tag ranges into a list of contiguous blocks.

    max_block_size is capped to 125-ish for typical Modbus devices.
    max_gap allows small holes to reduce number of requests.
    """

    ranges: List[Tuple[int, int]] = []
    for t in tags:
        start = int(t.read_address)
        end = start + int(t.read_count) - 1
        ranges.append((start, end))

    if not ranges:
        return []

    ranges.sort(key=lambda r: (r[0], r[1]))

    blocks: List[ReadBlock] = []
    cur_start, cur_end = ranges[0]

    for start, end in ranges[1:]:
        proposed_start = cur_start
        proposed_end = max(cur_end, end)
        proposed_len = proposed_end - proposed_start + 1

        should_merge = (start <= (cur_end + 1 + max_gap)) and (proposed_len <= max_block_size)
        if should_merge:
            cur_end = proposed_end
        else:
            blocks.append(ReadBlock(address=cur_start, count=(cur_end - cur_start + 1)))
            cur_start, cur_end = start, end

    blocks.append(ReadBlock(address=cur_start, count=(cur_end - cur_start + 1)))

    logger.info("Built scan plan: %d tags -> %d block reads", len(ranges), len(blocks))
    return blocks


def set_in_tree(tree: MutableMapping[str, Any], path: Tuple[str, ...], value: Any) -> None:
    """Set a value in a nested dict given a path."""

    cur: MutableMapping[str, Any] = tree
    for key in path[:-1]:
        if key not in cur or not isinstance(cur[key], MutableMapping):
            cur[key] = {}
        cur = cur[key]
    cur[path[-1]] = value
