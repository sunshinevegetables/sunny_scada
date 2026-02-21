from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from zoneinfo import ZoneInfo


ALARM_STATES = ("OK", "WARNING", "ALARM")


@dataclass(frozen=True)
class Evaluated:
    state: str
    message: str


def _to_time(v: Optional[dt.time]) -> Optional[dt.time]:
    return v if isinstance(v, dt.time) else None


def is_rule_active(
    *,
    now_utc: dt.datetime,
    schedule_enabled: bool,
    start: Optional[dt.time],
    end: Optional[dt.time],
    timezone: Optional[str],
) -> bool:
    """Return True if rule should be evaluated at now_utc."""

    if not schedule_enabled:
        return True

    start_t = _to_time(start)
    end_t = _to_time(end)
    if not start_t or not end_t:
        # If schedule enabled but incomplete, treat as always active.
        return True

    tz = None
    try:
        tz = ZoneInfo(timezone) if timezone else dt.timezone.utc
    except Exception:
        tz = dt.timezone.utc

    local = now_utc.astimezone(tz)
    t = local.timetz().replace(tzinfo=None)

    if start_t == end_t:
        return True  # 24h

    if start_t < end_t:
        return start_t <= t < end_t

    # Cross-midnight: active if after start OR before end
    return t >= start_t or t < end_t


def evaluate_rule(
    *,
    comparison: str,
    value: float,
    warning_enabled: bool,
    warning_threshold: Optional[float],
    alarm_threshold: Optional[float],
    warning_low: Optional[float],
    warning_high: Optional[float],
    alarm_low: Optional[float],
    alarm_high: Optional[float],
    name: str,
) -> Evaluated:
    """Evaluate thresholds and return an alarm state.

    This function is intentionally deterministic and side-effect free so it can be
    unit-tested and reused.
    """

    cmp = (comparison or "above").strip().lower()

    # One-sided comparisons
    if cmp in ("above", "below"):
        if alarm_threshold is None:
            return Evaluated("OK", f"Rule {name}: no alarm threshold")

        if cmp == "above":
            if value >= alarm_threshold:
                return Evaluated("ALARM", f"Rule {name} -> ALARM")
            if warning_enabled and warning_threshold is not None and value >= warning_threshold:
                return Evaluated("WARNING", f"Rule {name} -> WARNING")
            return Evaluated("OK", f"Rule {name} -> OK")

        # below
        if value <= alarm_threshold:
            return Evaluated("ALARM", f"Rule {name} -> ALARM")
        if warning_enabled and warning_threshold is not None and value <= warning_threshold:
            return Evaluated("WARNING", f"Rule {name} -> WARNING")
        return Evaluated("OK", f"Rule {name} -> OK")

    # Range comparisons
    if cmp in ("outside_range", "inside_range"):
        # alarm bounds required
        if alarm_low is None or alarm_high is None:
            return Evaluated("OK", f"Rule {name}: no alarm range")

        def _inside(lo: float, hi: float) -> bool:
            return lo <= value <= hi

        def _outside(lo: float, hi: float) -> bool:
            return value < lo or value > hi

        alarm_hit = _outside(alarm_low, alarm_high) if cmp == "outside_range" else _inside(alarm_low, alarm_high)
        if alarm_hit:
            return Evaluated("ALARM", f"Rule {name} -> ALARM")

        if warning_enabled and warning_low is not None and warning_high is not None:
            warn_hit = _outside(warning_low, warning_high) if cmp == "outside_range" else _inside(warning_low, warning_high)
            if warn_hit:
                return Evaluated("WARNING", f"Rule {name} -> WARNING")

        return Evaluated("OK", f"Rule {name} -> OK")

    return Evaluated("OK", f"Rule {name}: unsupported comparison")
