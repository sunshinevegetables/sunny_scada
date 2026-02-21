from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from sunny_scada.db.models import HistorianHourlyRollup, HistorianSample
from sunny_scada.services.datapoint_identity import (
    AmbiguousDatapointIdentifierError,
    resolve_cfg_datapoint_identifier,
)

logger = logging.getLogger(__name__)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _floor_to_hour(ts: dt.datetime) -> dt.datetime:
    ts = ts.astimezone(dt.timezone.utc)
    return ts.replace(minute=0, second=0, microsecond=0)


def _parse_iso(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def _extract_numeric_leaf(node: Dict[str, Any]) -> Optional[float]:
    # legacy outputs
    typ = str(node.get("type") or "").upper()
    if typ == "INTEGER":
        v = node.get("value")
        if isinstance(v, (int, float)):
            return float(v)
    if typ == "REAL":
        v = node.get("scaled_value")
        if isinstance(v, (int, float)):
            return float(v)
        v = node.get("raw_value")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _walk_points(tree: Any, *, prefix: Tuple[str, ...] = ()) -> Iterable[Tuple[Tuple[str, ...], Dict[str, Any]]]:
    if isinstance(tree, dict):
        if "type" in tree and ("value" in tree or "scaled_value" in tree or "raw_value" in tree):
            yield prefix, tree
            return
        for k, v in tree.items():
            yield from _walk_points(v, prefix=prefix + (str(k),))


class HistorianService:
    def _resolve_query_identifier(
        self,
        db: Session,
        *,
        plc_id: str,
        datapoint_id: Optional[str],
        cfg_data_point_id: Optional[int],
        owner_type: Optional[str],
        owner_id: Optional[int],
    ) -> tuple[Optional[int], Optional[str]]:
        resolution = resolve_cfg_datapoint_identifier(
            db,
            datapoint_id=datapoint_id,
            cfg_data_point_id=cfg_data_point_id,
            plc_name=plc_id,
            owner_type=owner_type,
            owner_id=owner_id,
            label=(None if datapoint_id is None else str(datapoint_id)),
        )
        return resolution.cfg_data_point_id, (resolution.legacy_datapoint_id or None)

    def sample_from_storage(self, db: Session, *, storage_snapshot: Dict[str, Any]) -> int:
        """Persist numeric datapoints from storage snapshot.

        storage_snapshot format: {plc_name: nested_dict, ...}
        """
        now = _utcnow()
        n = 0
        for plc_name, data in (storage_snapshot or {}).items():
            if not isinstance(data, dict):
                continue
            walk_root = data.get("data") if isinstance(data.get("data"), dict) else data
            for path, leaf in _walk_points(walk_root, prefix=()):
                leaf_key = path[-1] if path else ""
                if not leaf_key:
                    continue
                v = _extract_numeric_leaf(leaf)
                if v is None:
                    continue

                cfg_dp_id: Optional[int] = None
                try:
                    resolution = resolve_cfg_datapoint_identifier(
                        db,
                        datapoint_id=str(leaf_key),
                        cfg_data_point_id=(int(leaf.get("id")) if isinstance(leaf.get("id"), int) else None),
                        plc_name=str(plc_name),
                        owner_type=(leaf.get("owner_type") if isinstance(leaf, dict) else None),
                        owner_id=(leaf.get("owner_id") if isinstance(leaf, dict) and leaf.get("owner_id") is not None else None),
                        label=(leaf.get("label") if isinstance(leaf, dict) else None),
                    )
                    cfg_dp_id = resolution.cfg_data_point_id
                    legacy_dp_id = resolution.legacy_datapoint_id or str(leaf_key)
                except AmbiguousDatapointIdentifierError as exc:
                    logger.warning(
                        "Historian skipped ambiguous datapoint identifier plc=%s leaf=%s candidates=%s",
                        plc_name,
                        leaf_key,
                        exc.candidates,
                    )
                    continue

                if cfg_dp_id is None:
                    logger.warning(
                        "Historian canonical id unresolved plc=%s leaf=%s label=%s owner_type=%s owner_id=%s",
                        plc_name,
                        leaf_key,
                        leaf.get("label") if isinstance(leaf, dict) else None,
                        leaf.get("owner_type") if isinstance(leaf, dict) else None,
                        leaf.get("owner_id") if isinstance(leaf, dict) else None,
                    )

                db.add(
                    HistorianSample(
                        ts=now,
                        plc_id=str(plc_name),
                        cfg_data_point_id=cfg_dp_id,
                        datapoint_id=str(legacy_dp_id),
                        value=float(v),
                        quality="good",
                        meta={
                            "path": "/".join(path),
                            "label": (leaf.get("label") if isinstance(leaf, dict) else None),
                            "owner_type": (leaf.get("owner_type") if isinstance(leaf, dict) else None),
                            "owner_id": (leaf.get("owner_id") if isinstance(leaf, dict) else None),
                        },
                    )
                )
                n += 1
        db.commit()
        return n

    def rollup_hourly(self, db: Session, *, lookback_hours: int = 2) -> int:
        """Roll up raw samples into hourly buckets.

        Runs incrementally over a lookback window for safety.
        """
        now = _utcnow()
        start = now - dt.timedelta(hours=max(1, int(lookback_hours)))
        # Pull samples in window
        samples = (
            db.query(HistorianSample)
            .filter(HistorianSample.ts >= start)
            .all()
        )
        buckets: Dict[Tuple[dt.datetime, str, Optional[int], str], List[float]] = defaultdict(list)
        for s in samples:
            b = _floor_to_hour(s.ts)
            buckets[(b, s.plc_id, s.cfg_data_point_id, s.datapoint_id)].append(float(s.value))

        upserts = 0
        for (b, plc, cfg_dp, legacy_dp), vals in buckets.items():
            if not vals:
                continue
            avg = sum(vals) / len(vals)
            mn = min(vals)
            mx = max(vals)
            cnt = len(vals)

            existing_q = db.query(HistorianHourlyRollup).filter(
                HistorianHourlyRollup.bucket_start == b,
                HistorianHourlyRollup.plc_id == plc,
            )
            if cfg_dp is not None:
                existing_q = existing_q.filter(HistorianHourlyRollup.cfg_data_point_id == int(cfg_dp))
            else:
                existing_q = existing_q.filter(HistorianHourlyRollup.datapoint_id == legacy_dp)
            existing = existing_q.one_or_none()
            if existing:
                existing.avg_value = avg
                existing.min_value = mn
                existing.max_value = mx
                existing.sample_count = cnt
                if existing.cfg_data_point_id is None and cfg_dp is not None:
                    existing.cfg_data_point_id = int(cfg_dp)
                db.add(existing)
            else:
                db.add(
                    HistorianHourlyRollup(
                        bucket_start=b,
                        plc_id=plc,
                        cfg_data_point_id=cfg_dp,
                        datapoint_id=legacy_dp,
                        avg_value=avg,
                        min_value=mn,
                        max_value=mx,
                        sample_count=cnt,
                    )
                )
            upserts += 1
        db.commit()
        return upserts

    def latest(
        self,
        db: Session,
        *,
        plc_id: str,
        datapoint_id: Optional[str] = None,
        cfg_data_point_id: Optional[int] = None,
        owner_type: Optional[str] = None,
        owner_id: Optional[int] = None,
    ) -> Optional[HistorianSample]:
        resolved_cfg_id, resolved_legacy = self._resolve_query_identifier(
            db,
            plc_id=plc_id,
            datapoint_id=datapoint_id,
            cfg_data_point_id=cfg_data_point_id,
            owner_type=owner_type,
            owner_id=owner_id,
        )

        q = db.query(HistorianSample).filter(HistorianSample.plc_id == plc_id)
        if resolved_cfg_id is not None:
            q = q.filter(HistorianSample.cfg_data_point_id == int(resolved_cfg_id))
        elif resolved_legacy:
            q = q.filter(HistorianSample.datapoint_id == str(resolved_legacy))
        else:
            return None

        return q.order_by(HistorianSample.ts.desc()).first()

    def query_rollups(
        self,
        db: Session,
        *,
        plc_id: str,
        datapoint_id: Optional[str],
        cfg_data_point_id: Optional[int],
        from_ts: dt.datetime,
        to_ts: dt.datetime,
        owner_type: Optional[str] = None,
        owner_id: Optional[int] = None,
    ) -> List[HistorianHourlyRollup]:
        resolved_cfg_id, resolved_legacy = self._resolve_query_identifier(
            db,
            plc_id=plc_id,
            datapoint_id=datapoint_id,
            cfg_data_point_id=cfg_data_point_id,
            owner_type=owner_type,
            owner_id=owner_id,
        )

        q = db.query(HistorianHourlyRollup).filter(
            HistorianHourlyRollup.plc_id == plc_id,
            HistorianHourlyRollup.bucket_start >= from_ts,
            HistorianHourlyRollup.bucket_start <= to_ts,
        )
        if resolved_cfg_id is not None:
            q = q.filter(HistorianHourlyRollup.cfg_data_point_id == int(resolved_cfg_id))
        elif resolved_legacy:
            q = q.filter(HistorianHourlyRollup.datapoint_id == str(resolved_legacy))
        else:
            return []

        return q.order_by(HistorianHourlyRollup.bucket_start.asc()).all()

    def trends(
        self,
        db: Session,
        *,
        plc_id: str,
        datapoint_id: Optional[str] = None,
        cfg_data_point_id: Optional[int] = None,
        from_ts: str,
        to_ts: str,
        bucket: str,
        owner_type: Optional[str] = None,
        owner_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        start = _parse_iso(from_ts)
        end = _parse_iso(to_ts)
        if end < start:
            start, end = end, start

        bucket = (bucket or "hour").lower()
        resolved_cfg_id, _ = self._resolve_query_identifier(
            db,
            plc_id=plc_id,
            datapoint_id=datapoint_id,
            cfg_data_point_id=cfg_data_point_id,
            owner_type=owner_type,
            owner_id=owner_id,
        )
        rollups = self.query_rollups(
            db,
            plc_id=plc_id,
            datapoint_id=datapoint_id,
            cfg_data_point_id=cfg_data_point_id,
            from_ts=start,
            to_ts=end,
            owner_type=owner_type,
            owner_id=owner_id,
        )

        if bucket == "hour":
            return {
                "bucket": "hour",
                "cfg_data_point_id": resolved_cfg_id,
                "points": [
                    {
                        "ts": r.bucket_start.isoformat(),
                        "avg": r.avg_value,
                        "min": r.min_value,
                        "max": r.max_value,
                        "count": r.sample_count,
                        "cfg_data_point_id": r.cfg_data_point_id,
                    }
                    for r in rollups
                ],
            }

        def key_for(ts: dt.datetime) -> dt.datetime:
            ts = ts.astimezone(dt.timezone.utc)
            if bucket == "day":
                return ts.replace(hour=0, minute=0, second=0, microsecond=0)
            if bucket == "week":
                # ISO week starts Monday
                monday = ts - dt.timedelta(days=(ts.isoweekday() - 1))
                return monday.replace(hour=0, minute=0, second=0, microsecond=0)
            if bucket == "month":
                return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if bucket == "year":
                return ts.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            return _floor_to_hour(ts)

        grouped: Dict[dt.datetime, List[HistorianHourlyRollup]] = defaultdict(list)
        for r in rollups:
            grouped[key_for(r.bucket_start)].append(r)

        out = []
        for k in sorted(grouped.keys()):
            rs = grouped[k]
            vals = [float(x.avg_value) for x in rs]
            out.append(
                {
                    "ts": k.isoformat(),
                    "avg": sum(vals) / len(vals) if vals else None,
                    "min": min(float(x.min_value) for x in rs) if rs else None,
                    "max": max(float(x.max_value) for x in rs) if rs else None,
                    "count": sum(int(x.sample_count) for x in rs),
                    "cfg_data_point_id": resolved_cfg_id,
                }
            )
        return {"bucket": bucket, "cfg_data_point_id": resolved_cfg_id, "points": out}
