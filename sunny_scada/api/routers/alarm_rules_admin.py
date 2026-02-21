from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from sunny_scada.api.deps import (
    get_audit_service,
    get_current_user,
    get_db,
    require_permission,
)
from sunny_scada.db.models import AlarmRule, CfgDataPoint
from sunny_scada.services.alarm_rules_logic import normalize_timezone, validate_alarm_rule


router = APIRouter(prefix="/admin/alarm-rules", tags=["admin-alarms"])


Severity = Literal["critical", "major", "minor", "info"]
Comparison = Literal["above", "below", "outside_range", "inside_range"]


class AlarmRuleCreate(BaseModel):
    datapoint_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    severity: Severity = "info"
    comparison: Comparison = "above"

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
    schedule_timezone: Optional[str] = None

    @model_validator(mode="after")
    def _validate(self):
        validate_alarm_rule(self.model_dump())
        return self


class AlarmRuleUpdate(BaseModel):
    # All optional; we merge with existing rule then validate.
    datapoint_id: Optional[int] = Field(default=None, ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    enabled: Optional[bool] = None
    severity: Optional[Severity] = None
    comparison: Optional[Comparison] = None

    warning_enabled: Optional[bool] = None
    warning_threshold: Optional[float] = None
    alarm_threshold: Optional[float] = None

    warning_threshold_low: Optional[float] = None
    warning_threshold_high: Optional[float] = None
    alarm_threshold_low: Optional[float] = None
    alarm_threshold_high: Optional[float] = None

    schedule_enabled: Optional[bool] = None
    schedule_start_time: Optional[dt.time] = None
    schedule_end_time: Optional[dt.time] = None
    schedule_timezone: Optional[str] = None


class AlarmRuleOut(BaseModel):
    id: int
    datapoint_id: int
    datapoint_label: str
    enabled: bool
    name: str
    severity: str
    comparison: str

    warning_enabled: bool
    warning_threshold: Optional[float]
    alarm_threshold: Optional[float]
    warning_threshold_low: Optional[float]
    warning_threshold_high: Optional[float]
    alarm_threshold_low: Optional[float]
    alarm_threshold_high: Optional[float]

    schedule_enabled: bool
    schedule_start_time: Optional[dt.time]
    schedule_end_time: Optional[dt.time]
    schedule_timezone: str

    created_at: dt.datetime
    updated_at: dt.datetime


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _out(rule: AlarmRule) -> AlarmRuleOut:
    dp_label = ""
    try:
        dp_label = rule.data_point.label if rule.data_point else ""
    except Exception:
        dp_label = ""

    return AlarmRuleOut(
        id=rule.id,
        datapoint_id=rule.datapoint_id,
        datapoint_label=dp_label,
        enabled=bool(rule.enabled),
        name=rule.name,
        severity=rule.severity,
        comparison=rule.comparison,
        warning_enabled=bool(rule.warning_enabled),
        warning_threshold=rule.warning_threshold,
        alarm_threshold=rule.alarm_threshold,
        warning_threshold_low=rule.warning_threshold_low,
        warning_threshold_high=rule.warning_threshold_high,
        alarm_threshold_low=rule.alarm_threshold_low,
        alarm_threshold_high=rule.alarm_threshold_high,
        schedule_enabled=bool(rule.schedule_enabled),
        schedule_start_time=rule.schedule_start_time,
        schedule_end_time=rule.schedule_end_time,
        schedule_timezone=str(rule.schedule_timezone or "UTC"),
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


@router.get("/datapoints")
def list_alarm_datapoints(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("alarms:admin")),
):
    """Return a flat list of datapoints to attach alarm rules to."""
    dps = db.query(CfgDataPoint).order_by(CfgDataPoint.id.asc()).all()
    return [
        {
            "id": dp.id,
            "label": dp.label,
            "owner_type": dp.owner_type,
            "owner_id": dp.owner_id,
            "category": dp.category,
            "type": dp.type,
            "address": dp.address,
        }
        for dp in dps
    ]


@router.get("")
def list_alarm_rules(
    datapoint_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("alarms:admin")),
):
    q = db.query(AlarmRule).order_by(AlarmRule.id.asc())
    if datapoint_id is not None:
        q = q.filter(AlarmRule.datapoint_id == int(datapoint_id))
    items = q.all()
    return [_out(r).model_dump() for r in items]


@router.get("/{rule_id}")
def get_alarm_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("alarms:admin")),
):
    rule = db.query(AlarmRule).filter(AlarmRule.id == rule_id).one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Alarm rule not found")
    return _out(rule).model_dump()


@router.post("")
def create_alarm_rule(
    req: AlarmRuleCreate,
    request: Request,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm_admin=Depends(require_permission("alarms:admin")),
    _perm_write=Depends(require_permission("alarms:write")),
):
    dp = db.query(CfgDataPoint).filter(CfgDataPoint.id == req.datapoint_id).one_or_none()
    if not dp:
        raise HTTPException(status_code=404, detail="Datapoint not found")

    data = req.model_dump()
    tz = normalize_timezone(data.get("schedule_timezone"))

    rule = AlarmRule(
        datapoint_id=req.datapoint_id,
        name=req.name.strip(),
        enabled=bool(req.enabled),
        severity=req.severity,
        comparison=req.comparison,
        warning_enabled=bool(req.warning_enabled),
        warning_threshold=req.warning_threshold,
        alarm_threshold=req.alarm_threshold,
        warning_threshold_low=req.warning_threshold_low,
        warning_threshold_high=req.warning_threshold_high,
        alarm_threshold_low=req.alarm_threshold_low,
        alarm_threshold_high=req.alarm_threshold_high,
        schedule_enabled=bool(req.schedule_enabled),
        schedule_start_time=req.schedule_start_time,
        schedule_end_time=req.schedule_end_time,
        schedule_timezone=tz,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    try:
        audit.log(
            db,
            action="admin.alarm_rule.create",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=str(req.datapoint_id),
            metadata={"rule_id": rule.id, "severity": rule.severity, "comparison": rule.comparison},
        )
    except Exception:
        pass

    return _out(rule).model_dump()


@router.put("/{rule_id}")
def update_alarm_rule(
    rule_id: int,
    req: AlarmRuleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm_admin=Depends(require_permission("alarms:admin")),
    _perm_write=Depends(require_permission("alarms:write")),
):
    rule = db.query(AlarmRule).filter(AlarmRule.id == rule_id).one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Alarm rule not found")

    data = req.model_dump(exclude_unset=True)
    # Merge with existing for validation
    merged = _out(rule).model_dump()
    merged.update(data)
    validate_alarm_rule(merged)

    if "datapoint_id" in data and data["datapoint_id"] is not None:
        dp = db.query(CfgDataPoint).filter(CfgDataPoint.id == int(data["datapoint_id"])).one_or_none()
        if not dp:
            raise HTTPException(status_code=404, detail="Datapoint not found")
        rule.datapoint_id = int(data["datapoint_id"])

    if "name" in data and data["name"] is not None:
        rule.name = str(data["name"]).strip()

    for k in (
        "enabled",
        "severity",
        "comparison",
        "warning_enabled",
        "warning_threshold",
        "alarm_threshold",
        "warning_threshold_low",
        "warning_threshold_high",
        "alarm_threshold_low",
        "alarm_threshold_high",
        "schedule_enabled",
        "schedule_start_time",
        "schedule_end_time",
        "schedule_timezone",
    ):
        if k not in data:
            continue
        v = data.get(k)
        if k == "schedule_timezone":
            v = normalize_timezone(v)
        setattr(rule, k, v)

    db.add(rule)
    db.commit()
    db.refresh(rule)

    try:
        audit.log(
            db,
            action="admin.alarm_rule.update",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=str(rule.datapoint_id),
            metadata={"rule_id": rule.id},
        )
    except Exception:
        pass

    return _out(rule).model_dump()


@router.delete("/{rule_id}")
def delete_alarm_rule(
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm_admin=Depends(require_permission("alarms:admin")),
    _perm_write=Depends(require_permission("alarms:write")),
):
    rule = db.query(AlarmRule).filter(AlarmRule.id == rule_id).one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Alarm rule not found")
    dp_id = int(rule.datapoint_id)
    rid = int(rule.id)
    db.delete(rule)
    db.commit()

    try:
        audit.log(
            db,
            action="admin.alarm_rule.delete",
            user_id=me.id,
            client_ip=_client_ip(request),
            resource=str(dp_id),
            metadata={"rule_id": rid},
        )
    except Exception:
        pass

    return {"status": "ok"}
