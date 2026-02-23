from __future__ import annotations

import datetime as dt
import math
from typing import Any, Optional

from sqlalchemy.orm import Session

from sunny_scada.db.models import Instrument, InstrumentDataPoint
from sunny_scada.services.historian_service import HistorianService


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _as_utc(ts: dt.datetime) -> dt.datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.timezone.utc)
    return ts.astimezone(dt.timezone.utc)


class InstrumentHealthService:
    def __init__(self, *, historian: Optional[HistorianService] = None) -> None:
        self._historian = historian or HistorianService()
        self._default_noise_std_threshold = 2.0
        self._noise_threshold_by_type: dict[str, float] = {
            "temperature": 0.8,
            "pressure": 1.5,
            "flow": 2.0,
        }

    def _resolve_pv_cfg_data_point_id(self, db: Session, instrument_id: int) -> Optional[int]:
        row = (
            db.query(InstrumentDataPoint)
            .filter(
                InstrumentDataPoint.instrument_id == int(instrument_id),
                InstrumentDataPoint.role == "pv",
            )
            .order_by(InstrumentDataPoint.id.asc())
            .one_or_none()
        )
        if row is None:
            return None
        return int(row.cfg_data_point_id)

    @staticmethod
    def _std(values: list[float]) -> float:
        n = len(values)
        if n <= 1:
            return 0.0
        avg = sum(values) / float(n)
        var = sum((v - avg) ** 2 for v in values) / float(n)
        return math.sqrt(max(var, 0.0))

    def get_health(
        self,
        db: Session,
        *,
        instrument_id: int,
        window_minutes: int = 10,
        flatline_minutes: int = 10,
        max_gap_seconds: int = 30,
        noise_std_threshold: Optional[float] = None,
    ) -> dict[str, Any]:
        instrument = db.query(Instrument).filter(Instrument.id == int(instrument_id)).one_or_none()
        if instrument is None:
            raise ValueError("instrument not found")

        pv_cfg_data_point_id = self._resolve_pv_cfg_data_point_id(db, int(instrument_id))
        if pv_cfg_data_point_id is None:
            raise ValueError("pv datapoint mapping not configured")

        now = _utcnow()
        window_m = max(1, int(window_minutes))
        flatline_m = max(1, int(flatline_minutes))
        gap_s = max(1, int(max_gap_seconds))

        start_ts = now - dt.timedelta(minutes=window_m)
        flatline_start = now - dt.timedelta(minutes=flatline_m)

        samples = self._historian.query_samples_by_cfg_data_point_id(
            db,
            cfg_data_point_id=int(pv_cfg_data_point_id),
            from_ts=start_ts,
            to_ts=now,
        )

        values = [float(s.value) for s in samples]
        sample_count = len(values)
        last_sample_ts = (_as_utc(samples[-1].ts) if samples else None)

        if sample_count > 0:
            min_v = min(values)
            max_v = max(values)
            avg_v = sum(values) / float(sample_count)
            std_v = self._std(values)
        else:
            min_v = None
            max_v = None
            avg_v = None
            std_v = None

        missing_data = False
        max_gap_seen = 0.0
        if sample_count <= 1:
            missing_data = True
        else:
            prev = samples[0].ts
            for s in samples[1:]:
                cur = _as_utc(s.ts)
                prev_u = _as_utc(prev)
                gap = (cur - prev_u).total_seconds()
                if gap > max_gap_seen:
                    max_gap_seen = gap
                prev = s.ts

            tail_gap = (now - _as_utc(samples[-1].ts)).total_seconds()
            if tail_gap > max_gap_seen:
                max_gap_seen = tail_gap

            missing_data = max_gap_seen > float(gap_s)

        flatline_detected = False
        flat_values = [float(s.value) for s in samples if _as_utc(s.ts) >= flatline_start]
        if len(flat_values) >= 2:
            flatline_detected = (max(flat_values) - min(flat_values)) == 0.0
        elif len(flat_values) == 1 and samples:
            age_s = (now - _as_utc(samples[-1].ts)).total_seconds()
            flatline_detected = age_s >= float(flatline_m * 60)

        threshold = float(noise_std_threshold) if noise_std_threshold is not None else None
        if threshold is None:
            typ = str(instrument.instrument_type or "").strip().lower()
            threshold = float(self._noise_threshold_by_type.get(typ, self._default_noise_std_threshold))

        noise_high = False
        if std_v is not None:
            noise_high = float(std_v) > float(threshold)

        flags: list[str] = []
        if flatline_detected:
            flags.append("flatline_detected")
        if missing_data:
            flags.append("missing_data")
        if noise_high:
            flags.append("noise_high")

        score = 100
        if flatline_detected:
            score -= 35
        if missing_data:
            score -= 40
        if noise_high:
            score -= 25
        score = max(0, min(100, int(score)))

        return {
            "instrument_id": int(instrument_id),
            "pv_cfg_data_point_id": int(pv_cfg_data_point_id),
            "window_minutes": int(window_m),
            "score_0_100": int(score),
            "flags": flags,
            "last_sample_ts": last_sample_ts,
            "sample_count": int(sample_count),
            "simple_stats": {
                "min": min_v,
                "max": max_v,
                "avg": avg_v,
                "std": std_v,
            },
        }
