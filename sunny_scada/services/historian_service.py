from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from sunny_scada.db.models import HistorianHourlyRollup, HistorianSample

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
    def sample_from_storage(self, db: Session, *, storage_snapshot: Dict[str, Any]) -> int:
        """Persist numeric datapoints from storage snapshot.

        storage_snapshot format: {plc_name: nested_dict, ...}
        """
        now = _utcnow()
        n = 0
        for plc_name, data in (storage_snapshot or {}).items():
            if not isinstance(data, dict):
                continue
            for path, leaf in _walk_points(data, prefix=()):
                dp_id = path[-1] if path else ""
                if not dp_id:
                    continue
                v = _extract_numeric_leaf(leaf)
                if v is None:
                    continue
                db.add(
                    HistorianSample(
                        ts=now,
                        plc_id=str(plc_name),
                        datapoint_id=str(dp_id),
                        value=float(v),
                        quality="good",
                        meta={"path": "/".join(path)},
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
        buckets: Dict[Tuple[dt.datetime, str, str], List[float]] = defaultdict(list)
        for s in samples:
            b = _floor_to_hour(s.ts)
            buckets[(b, s.plc_id, s.datapoint_id)].append(float(s.value))

        upserts = 0
        for (b, plc, dp), vals in buckets.items():
            if not vals:
                continue
            avg = sum(vals) / len(vals)
            mn = min(vals)
            mx = max(vals)
            cnt = len(vals)

            existing = (
                db.query(HistorianHourlyRollup)
                .filter(
                    HistorianHourlyRollup.bucket_start == b,
                    HistorianHourlyRollup.plc_id == plc,
                    HistorianHourlyRollup.datapoint_id == dp,
                )
                .one_or_none()
            )
            if existing:
                existing.avg_value = avg
                existing.min_value = mn
                existing.max_value = mx
                existing.sample_count = cnt
                db.add(existing)
            else:
                db.add(
                    HistorianHourlyRollup(
                        bucket_start=b,
                        plc_id=plc,
                        datapoint_id=dp,
                        avg_value=avg,
                        min_value=mn,
                        max_value=mx,
                        sample_count=cnt,
                    )
                )
            upserts += 1
        db.commit()
        return upserts

    def latest(self, db: Session, *, plc_id: str, datapoint_id: str) -> Optional[HistorianSample]:
        return (
            db.query(HistorianSample)
            .filter(HistorianSample.plc_id == plc_id, HistorianSample.datapoint_id == datapoint_id)
            .order_by(HistorianSample.ts.desc())
            .first()
        )

    def query_rollups(
        self,
        db: Session,
        *,
        plc_id: str,
        datapoint_id: str,
        from_ts: dt.datetime,
        to_ts: dt.datetime,
    ) -> List[HistorianHourlyRollup]:
        return (
            db.query(HistorianHourlyRollup)
            .filter(
                HistorianHourlyRollup.plc_id == plc_id,
                HistorianHourlyRollup.datapoint_id == datapoint_id,
                HistorianHourlyRollup.bucket_start >= from_ts,
                HistorianHourlyRollup.bucket_start <= to_ts,
            )
            .order_by(HistorianHourlyRollup.bucket_start.asc())
            .all()
        )

    def trends(
        self,
        db: Session,
        *,
        plc_id: str,
        datapoint_id: str,
        from_ts: str,
        to_ts: str,
        bucket: str,
    ) -> Dict[str, Any]:
        start = _parse_iso(from_ts)
        end = _parse_iso(to_ts)
        if end < start:
            start, end = end, start

        bucket = (bucket or "hour").lower()
        rollups = self.query_rollups(db, plc_id=plc_id, datapoint_id=datapoint_id, from_ts=start, to_ts=end)

        if bucket == "hour":
            return {
                "bucket": "hour",
                "points": [
                    {
                        "ts": r.bucket_start.isoformat(),
                        "avg": r.avg_value,
                        "min": r.min_value,
                        "max": r.max_value,
                        "count": r.sample_count,
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
                }
            )
        return {"bucket": bucket, "points": out}
