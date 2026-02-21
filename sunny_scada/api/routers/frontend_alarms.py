from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sunny_scada.api.deps import get_db, get_current_principal, require_permission
from sunny_scada.db.models import AlarmRule, CfgDataPoint


router = APIRouter(prefix="/api/frontend", tags=["alarms"])


def _parse_ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


class FrontendAlarmEvent(BaseModel):
    external_rule_id: str = Field(min_length=1, max_length=200)
    state: str = Field(min_length=1, max_length=20)
    severity: str = Field(default="info", max_length=30)
    message: str = Field(default="", max_length=500)
    datapoint_id: Optional[int] = None
    value: Optional[float] = None
    ts: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)


@router.post("/alarm-events")
def ingest_frontend_alarm_event(
    req: FrontendAlarmEvent,
    request: Request,
    db: Session = Depends(get_db),
    principal=Depends(get_current_principal),
    _perm=Depends(require_permission("alarms:write")),
):
    # This endpoint is intended for trusted HMI clients.
    alarm_manager = getattr(request.app.state, "alarm_manager", None)
    broadcaster = getattr(request.app.state, "alarm_broadcaster", None)
    if not alarm_manager:
        raise HTTPException(status_code=503, detail="Alarm manager not available")

    ts = _parse_ts(req.ts) if req.ts else dt.datetime.now(dt.timezone.utc)
    key = f"frontend:{req.external_rule_id}"

    res = alarm_manager.set_state(
        db,
        source="frontend_rule",
        key=key,
        new_state=req.state,
        severity=req.severity,
        message=req.message or f"Frontend rule {req.external_rule_id} -> {req.state}",
        ts=ts,
        datapoint_id=req.datapoint_id,
        external_rule_id=req.external_rule_id,
        value=req.value,
        meta=req.meta,
        broadcast_cb=broadcaster.broadcast if broadcaster else None,
    )
    return {"status": "ok", **res}


class FrontendAlarmRuleIn(BaseModel):
    external_rule_id: str = Field(min_length=1, max_length=200)
    datapoint_id: int
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    severity: str = Field(default="info", max_length=30)
    comparison: str = Field(default="above", max_length=30)

    warning_enabled: bool = False
    warning_threshold: Optional[float] = None
    alarm_threshold: Optional[float] = None
    warning_threshold_low: Optional[float] = None
    warning_threshold_high: Optional[float] = None
    alarm_threshold_low: Optional[float] = None
    alarm_threshold_high: Optional[float] = None

    schedule_enabled: bool = False
    schedule_start_time: Optional[dt.time] = None
    schedule_end_time: Optional[dt.time] = None
    schedule_timezone: Optional[str] = Field(default=None, max_length=64)


class FrontendAlarmRulesSync(BaseModel):
    rules: list[FrontendAlarmRuleIn] = Field(default_factory=list)


@router.put("/alarm-rules/sync")
def sync_frontend_alarm_rules(
    req: FrontendAlarmRulesSync,
    request: Request,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("alarms:write")),
):
    """Upsert frontend-origin rule definitions.

    This enables the backend to evaluate frontend rules during polling if desired.
    """

    # Validate datapoints exist first
    for r in req.rules:
        if not db.query(CfgDataPoint.id).filter(CfgDataPoint.id == int(r.datapoint_id)).first():
            raise HTTPException(status_code=404, detail=f"Datapoint not found: {r.datapoint_id}")

    upserted = 0
    for r in req.rules:
        existing = (
            db.query(AlarmRule)
            .filter(AlarmRule.rule_source == "frontend")
            .filter(AlarmRule.external_rule_id == r.external_rule_id)
            .one_or_none()
        )
        if not existing:
            existing = AlarmRule(
                datapoint_id=r.datapoint_id,
                rule_source="frontend",
                external_rule_id=r.external_rule_id,
                name=r.name,
                enabled=bool(r.enabled),
                severity=r.severity,
                comparison=r.comparison,
                warning_enabled=bool(r.warning_enabled),
                warning_threshold=r.warning_threshold,
                alarm_threshold=r.alarm_threshold,
                warning_threshold_low=r.warning_threshold_low,
                warning_threshold_high=r.warning_threshold_high,
                alarm_threshold_low=r.alarm_threshold_low,
                alarm_threshold_high=r.alarm_threshold_high,
                schedule_enabled=bool(r.schedule_enabled),
                schedule_start_time=r.schedule_start_time,
                schedule_end_time=r.schedule_end_time,
                schedule_timezone=r.schedule_timezone,
            )
            db.add(existing)
        else:
            existing.datapoint_id = r.datapoint_id
            existing.name = r.name
            existing.enabled = bool(r.enabled)
            existing.severity = r.severity
            existing.comparison = r.comparison
            existing.warning_enabled = bool(r.warning_enabled)
            existing.warning_threshold = r.warning_threshold
            existing.alarm_threshold = r.alarm_threshold
            existing.warning_threshold_low = r.warning_threshold_low
            existing.warning_threshold_high = r.warning_threshold_high
            existing.alarm_threshold_low = r.alarm_threshold_low
            existing.alarm_threshold_high = r.alarm_threshold_high
            existing.schedule_enabled = bool(r.schedule_enabled)
            existing.schedule_start_time = r.schedule_start_time
            existing.schedule_end_time = r.schedule_end_time
            existing.schedule_timezone = r.schedule_timezone

        upserted += 1

    db.commit()

    # refresh caches so changes take effect immediately
    mon = getattr(request.app.state, "alarm_monitor", None)
    if mon:
        try:
            mon.invalidate_cache()
        except Exception:
            pass

    return {"status": "ok", "upserted": upserted}
