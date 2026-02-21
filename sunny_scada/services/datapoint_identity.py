from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.orm import Query, Session

from sunny_scada.db.models import CfgContainer, CfgDataPoint, CfgEquipment, CfgPLC


CANONICAL_DP_PREFIX = "db-dp:"


def make_canonical_datapoint_key(cfg_data_point_id: int) -> str:
    return f"{CANONICAL_DP_PREFIX}{int(cfg_data_point_id)}"


def parse_canonical_datapoint_key(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith(CANONICAL_DP_PREFIX):
        suffix = text.split(":", 1)[1].strip()
        if suffix.isdigit():
            return int(suffix)
        return None
    if text.isdigit():
        return int(text)
    return None


@dataclass
class DataPointIdentifierResolution:
    cfg_data_point_id: Optional[int]
    legacy_datapoint_id: str
    canonical_datapoint_key: Optional[str] = None
    used_scoped_label_fallback: bool = False
    ambiguous_candidates: list[dict[str, Any]] = field(default_factory=list)


class AmbiguousDatapointIdentifierError(ValueError):
    def __init__(self, *, datapoint_id: str, candidates: list[dict[str, Any]]):
        self.datapoint_id = datapoint_id
        self.candidates = candidates
        super().__init__(f"Ambiguous datapoint identifier '{datapoint_id}'")


def _scoped_query(
    db: Session,
    *,
    owner_type: Optional[str],
    owner_id: Optional[int],
    plc_name: Optional[str],
) -> Query:
    q = db.query(CfgDataPoint)

    if owner_type and owner_id is not None:
        return q.filter(CfgDataPoint.owner_type == str(owner_type), CfgDataPoint.owner_id == int(owner_id))

    if plc_name:
        plc_rows = db.query(CfgPLC.id).filter(CfgPLC.name == str(plc_name)).all()
        plc_ids = [int(r[0]) for r in plc_rows]
        if not plc_ids:
            return q.filter(CfgDataPoint.id == -1)

        container_ids = [
            int(r[0])
            for r in db.query(CfgContainer.id).filter(CfgContainer.plc_id.in_(plc_ids)).all()
        ]
        equipment_ids = [
            int(r[0])
            for r in db.query(CfgEquipment.id).filter(CfgEquipment.container_id.in_(container_ids)).all()
        ]

        filters = [
            (CfgDataPoint.owner_type == "plc") & (CfgDataPoint.owner_id.in_(plc_ids)),
        ]
        if container_ids:
            filters.append((CfgDataPoint.owner_type == "container") & (CfgDataPoint.owner_id.in_(container_ids)))
        if equipment_ids:
            filters.append((CfgDataPoint.owner_type == "equipment") & (CfgDataPoint.owner_id.in_(equipment_ids)))

        expr = filters[0]
        for f in filters[1:]:
            expr = expr | f
        return q.filter(expr)

    return q


def resolve_cfg_datapoint_identifier(
    db: Session,
    *,
    datapoint_id: Optional[str],
    cfg_data_point_id: Optional[int] = None,
    plc_name: Optional[str] = None,
    owner_type: Optional[str] = None,
    owner_id: Optional[int] = None,
    label: Optional[str] = None,
) -> DataPointIdentifierResolution:
    legacy = str(datapoint_id or label or "").strip()

    owner_id_int: Optional[int] = None
    if owner_id is not None:
        try:
            owner_id_int = int(owner_id)
        except Exception:
            owner_id_int = None

    resolved_cfg_id = int(cfg_data_point_id) if cfg_data_point_id is not None else None
    if resolved_cfg_id is None:
        parsed = parse_canonical_datapoint_key(datapoint_id)
        if parsed is not None:
            resolved_cfg_id = int(parsed)

    if resolved_cfg_id is not None:
        row = db.query(CfgDataPoint.id).filter(CfgDataPoint.id == int(resolved_cfg_id)).one_or_none()
        if row is None:
            resolved_cfg_id = None
        elif not legacy:
            legacy = make_canonical_datapoint_key(int(resolved_cfg_id))

    if resolved_cfg_id is not None:
        return DataPointIdentifierResolution(
            cfg_data_point_id=int(resolved_cfg_id),
            legacy_datapoint_id=legacy,
            canonical_datapoint_key=make_canonical_datapoint_key(int(resolved_cfg_id)),
        )

    candidate_label = str(label or datapoint_id or "").strip()
    if not candidate_label:
        return DataPointIdentifierResolution(cfg_data_point_id=None, legacy_datapoint_id=legacy)

    scoped_q = _scoped_query(
        db,
        owner_type=(str(owner_type).strip().lower() if owner_type else None),
        owner_id=owner_id_int,
        plc_name=(str(plc_name).strip() if plc_name else None),
    )

    scoped = scoped_q.filter(CfgDataPoint.label == candidate_label).all()
    if len(scoped) == 1:
        cfg_id = int(scoped[0].id)
        return DataPointIdentifierResolution(
            cfg_data_point_id=cfg_id,
            legacy_datapoint_id=legacy or candidate_label,
            canonical_datapoint_key=make_canonical_datapoint_key(cfg_id),
            used_scoped_label_fallback=True,
        )

    if len(scoped) > 1:
        candidates = [
            {
                "id": int(dp.id),
                "label": str(dp.label),
                "owner_type": str(dp.owner_type),
                "owner_id": int(dp.owner_id),
            }
            for dp in scoped
        ]
        raise AmbiguousDatapointIdentifierError(datapoint_id=candidate_label, candidates=candidates)

    return DataPointIdentifierResolution(cfg_data_point_id=None, legacy_datapoint_id=legacy or candidate_label)