from __future__ import annotations

import datetime as dt
import logging
import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, selectinload

from sunny_scada.api.security import Principal
from sunny_scada.data_storage import DataStorage
from sunny_scada.db.models import CfgContainer, CfgDataPoint, CfgEquipment, CfgPLC
from sunny_scada.services.access_control_service import AccessControlService

logger = logging.getLogger(__name__)


def iso_utc(value: dt.datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    return aware.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


@dataclass(slots=True)
class WatchSnapshotPoint:
    value: Any
    timestamp: dt.datetime | None
    fault: bool


class WatchService:
    def __init__(
        self,
        *,
        storage: DataStorage,
        access_control: AccessControlService,
        stale_after_s: int = 120,
    ) -> None:
        self._storage = storage
        self._access_control = access_control
        self._stale_after_s = max(30, int(stale_after_s))

        self._snapshot_lock = threading.Lock()
        self._snapshot_cache_built_at: dt.datetime | None = None
        self._snapshot_cache: dict[int, WatchSnapshotPoint] = {}

    @property
    def stale_after_s(self) -> int:
        return self._stale_after_s

    def _is_admin_bypass(self, principal: Principal) -> bool:
        perms = principal.permissions or set()
        return ("users:admin" in perms) or ("roles:admin" in perms)

    def readable_datapoint_ids(self, db: Session, principal: Principal) -> set[int]:
        if self._is_admin_bypass(principal):
            rows = db.query(CfgDataPoint.id).all()
            return {int(r[0]) for r in rows}

        if principal.type == "user" and principal.user is not None:
            ea = self._access_control.effective_access(db, principal.user)
        else:
            ea = self._access_control.effective_access_for_role_ids(db, role_ids=principal.role_ids)
        return {int(x) for x in ea.read_datapoint_ids}

    def list_datapoints(
        self,
        db: Session,
        principal: Principal,
        *,
        q: str | None,
        equipment_id: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        readable = self.readable_datapoint_ids(db, principal)
        if not readable:
            return []

        q_limit = max(1, min(100, int(limit or 50)))

        query = (
            db.query(CfgDataPoint)
            .options(selectinload(CfgDataPoint.dp_unit))
            .filter(CfgDataPoint.category == "read")
            .filter(CfgDataPoint.id.in_(readable))
        )

        if q:
            query = query.filter(CfgDataPoint.label.ilike(f"%{str(q).strip()}%"))

        if equipment_id is not None:
            query = query.filter(
                CfgDataPoint.owner_type == "equipment",
                CfgDataPoint.owner_id == int(equipment_id),
            )

        rows = query.order_by(CfgDataPoint.label.asc(), CfgDataPoint.id.asc()).limit(q_limit).all()
        if not rows:
            return []

        equipment_ids = {int(dp.owner_id) for dp in rows if str(dp.owner_type) == "equipment"}
        container_ids = {int(dp.owner_id) for dp in rows if str(dp.owner_type) == "container"}
        plc_ids = {int(dp.owner_id) for dp in rows if str(dp.owner_type) == "plc"}

        equipment_names = {
            int(e.id): str(e.name)
            for e in db.query(CfgEquipment.id, CfgEquipment.name).filter(CfgEquipment.id.in_(equipment_ids)).all()
        } if equipment_ids else {}
        container_names = {
            int(c.id): str(c.name)
            for c in db.query(CfgContainer.id, CfgContainer.name).filter(CfgContainer.id.in_(container_ids)).all()
        } if container_ids else {}
        plc_names = {
            int(p.id): str(p.name)
            for p in db.query(CfgPLC.id, CfgPLC.name).filter(CfgPLC.id.in_(plc_ids)).all()
        } if plc_ids else {}

        out: list[dict[str, Any]] = []
        for dp in rows:
            owner_type = str(dp.owner_type)
            owner_id = int(dp.owner_id)
            if owner_type == "equipment":
                equipment_name = equipment_names.get(owner_id, "")
            elif owner_type == "container":
                equipment_name = container_names.get(owner_id, "")
            elif owner_type == "plc":
                equipment_name = plc_names.get(owner_id, "")
            else:
                equipment_name = ""

            out.append(
                {
                    "id": int(dp.id),
                    "label": str(dp.label or "")[:32],
                    "unit": str(dp.dp_unit.name) if dp.dp_unit is not None else "",
                    "equipment_name": equipment_name,
                }
            )
        return out

    def _leaf_fault(self, leaf: dict[str, Any]) -> bool:
        quality = str(leaf.get("quality") or "").strip().lower()
        if quality in {"bad", "error", "fault", "invalid"}:
            return True

        for key in (
            "bad_quality",
            "quality_bad",
            "is_bad",
            "fault",
            "is_fault",
            "device_fault",
            "error",
            "has_error",
        ):
            if bool(leaf.get(key)):
                return True

        status = str(leaf.get("status") or "").strip().lower()
        if status and any(x in status for x in ("fault", "error", "bad", "fail")):
            return True
        return False

    def _extract_numeric_value(self, leaf: dict[str, Any]) -> float | int | None:
        candidate = leaf.get("scaled_value")
        if candidate is None:
            candidate = leaf.get("value")
        if candidate is None:
            candidate = leaf.get("raw_value")

        if isinstance(candidate, bool):
            return int(candidate)
        if isinstance(candidate, (int, float)):
            return candidate
        return None

    def _extract_id_from_key(self, key: str) -> int | None:
        if not str(key).startswith("cfg_dp_"):
            return None
        try:
            return int(str(key).split("_")[-1])
        except Exception:
            return None

    def _rebuild_snapshot_cache(self) -> dict[int, WatchSnapshotPoint]:
        storage_data = self._storage.get_data() or {}
        by_id: dict[int, WatchSnapshotPoint] = {}

        for _, plc_snapshot in storage_data.items():
            if not isinstance(plc_snapshot, dict):
                continue
            plc_ts = parse_ts(plc_snapshot.get("timestamp"))
            tree = plc_snapshot.get("data")
            if not isinstance(tree, dict):
                continue

            for key, leaf in tree.items():
                if not isinstance(leaf, dict):
                    continue

                dp_id: int | None = None
                try:
                    if leaf.get("id") is not None:
                        dp_id = int(leaf.get("id"))
                except Exception:
                    dp_id = None
                if dp_id is None:
                    dp_id = self._extract_id_from_key(str(key))
                if dp_id is None:
                    continue

                ts = parse_ts(leaf.get("timestamp")) or plc_ts
                point = WatchSnapshotPoint(
                    value=self._extract_numeric_value(leaf),
                    timestamp=ts,
                    fault=self._leaf_fault(leaf),
                )

                prev = by_id.get(dp_id)
                if prev is None:
                    by_id[dp_id] = point
                    continue
                if prev.timestamp is None and point.timestamp is not None:
                    by_id[dp_id] = point
                    continue
                if prev.timestamp and point.timestamp and point.timestamp >= prev.timestamp:
                    by_id[dp_id] = point

        return by_id

    def snapshot_by_datapoint_id(self) -> dict[int, WatchSnapshotPoint]:
        now = dt.datetime.now(dt.timezone.utc)
        with self._snapshot_lock:
            should_rebuild = (
                self._snapshot_cache_built_at is None
                or (now - self._snapshot_cache_built_at).total_seconds() >= 1.0
            )
            if should_rebuild:
                self._snapshot_cache = self._rebuild_snapshot_cache()
                self._snapshot_cache_built_at = now
            return dict(self._snapshot_cache)

    def latest_values(
        self,
        db: Session,
        principal: Principal,
        *,
        ids: list[int],
    ) -> dict[str, Any]:
        readable = self.readable_datapoint_ids(db, principal)
        if not readable:
            return {"ts": iso_utc(dt.datetime.now(dt.timezone.utc)), "values": {}}

        requested = [int(x) for x in ids if int(x) in readable]
        if not requested:
            return {"ts": iso_utc(dt.datetime.now(dt.timezone.utc)), "values": {}}

        rows = (
            db.query(CfgDataPoint)
            .options(selectinload(CfgDataPoint.dp_unit))
            .filter(CfgDataPoint.id.in_(requested))
            .all()
        )
        by_id = {int(dp.id): dp for dp in rows}
        snapshot = self.snapshot_by_datapoint_id()
        now = dt.datetime.now(dt.timezone.utc)

        values: dict[str, Any] = {}
        for req_id in requested:
            dp = by_id.get(req_id)
            if dp is None:
                continue

            snap = snapshot.get(req_id)
            unit = str(dp.dp_unit.name) if dp.dp_unit is not None else ""
            if snap is None:
                values[str(req_id)] = {
                    "value": None,
                    "unit": unit,
                    "quality": "no_data",
                    "timestamp": None,
                }
                continue

            if snap.fault:
                quality = "error"
            elif snap.timestamp is None or snap.value is None:
                quality = "no_data"
            elif (now - snap.timestamp).total_seconds() > self._stale_after_s:
                quality = "stale"
            else:
                quality = "good"

            values[str(req_id)] = {
                "value": snap.value,
                "unit": unit,
                "quality": quality,
                "timestamp": iso_utc(snap.timestamp) if snap.timestamp is not None else None,
            }

        return {
            "ts": iso_utc(now),
            "values": values,
        }
