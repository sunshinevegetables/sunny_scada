from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass(frozen=True)
class TagSpec:
    """A leaf tag from data_points.yaml, flattened with its path.

    path:
        Tuple of dictionary keys from the section root down to the tag name.
    read_addr:
        PyModbus address used for reading (already includes REAL extra offset if applicable).
    base_addr:
        PyModbus address corresponding to the configured 4xxxx address without any REAL extra offset.
        This is kept for backward-compatible output fields like `register_address`.
    length:
        Number of 16-bit holding registers to read (1 for INTEGER/DIGITAL, 2 for REAL).
    """
    path: Tuple[str, ...]
    typ: str
    description: str | None
    details: Dict[str, Any]
    address_4x: int
    base_addr: int
    read_addr: int
    length: int


@dataclass(frozen=True)
class Block:
    start: int
    count: int


def flatten_points(tree: Dict[str, Any], *, prefix: Tuple[str, ...] = ()) -> List[Tuple[Tuple[str, ...], Dict[str, Any]]]:
    """Flatten a nested data_points tree into leaf nodes.

    A leaf is any dict that has both 'address' and 'type'.
    """
    out: List[Tuple[Tuple[str, ...], Dict[str, Any]]] = []
    for k, v in (tree or {}).items():
        if isinstance(v, dict) and "address" in v and "type" in v:
            out.append((prefix + (str(k),), v))
        elif isinstance(v, dict):
            out.extend(flatten_points(v, prefix=prefix + (str(k),)))
    return out


def build_tag_specs(
    tree: Dict[str, Any],
    *,
    address_4x_to_pymodbus,
    real_extra_offset: int,
) -> List[TagSpec]:
    """Build TagSpec objects from a section tree."""
    tags: List[TagSpec] = []
    for path, details in flatten_points(tree):
        try:
            addr_4x = int(details["address"])
            typ = str(details["type"])
        except Exception:
            continue

        base_addr = int(address_4x_to_pymodbus(addr_4x))
        if typ == "REAL":
            read_addr = base_addr + int(real_extra_offset)
            length = 2
        else:
            read_addr = base_addr
            length = 1

        tags.append(
            TagSpec(
                path=path,
                typ=typ,
                description=details.get("description"),
                details=details,
                address_4x=addr_4x,
                base_addr=base_addr,
                read_addr=read_addr,
                length=length,
            )
        )

    # Sort by read address for deterministic block building
    tags.sort(key=lambda t: (t.read_addr, t.length, t.path))
    return tags


def build_blocks(
    tags: List[TagSpec],
    *,
    max_block_regs: int = 100,
    max_gap_regs: int = 2,
) -> List[Block]:
    """Build a list of contiguous register blocks to read.

    Blocks are merged as long as:
      - the gap between the end of the current block and the next tag start is <= max_gap_regs
      - the resulting block size stays <= max_block_regs
    """
    if not tags:
        return []

    blocks: List[Block] = []

    block_start = tags[0].read_addr
    block_end = tags[0].read_addr + tags[0].length - 1

    for tag in tags[1:]:
        tag_start = tag.read_addr
        tag_end = tag.read_addr + tag.length - 1

        gap = tag_start - block_end - 1
        new_end = max(block_end, tag_end)
        new_size = new_end - block_start + 1

        if gap <= max_gap_regs and new_size <= max_block_regs:
            block_end = new_end
        else:
            blocks.append(Block(start=block_start, count=block_end - block_start + 1))
            block_start = tag_start
            block_end = tag_end

    blocks.append(Block(start=block_start, count=block_end - block_start + 1))
    return blocks
