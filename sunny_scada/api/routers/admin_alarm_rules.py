from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from sunny_scada.api.deps import get_db, get_current_user, require_permission, get_audit_service
from sunny_scada.db.models import (
    AlarmRule,
    CfgDataPoint,
    CfgDataPointGroup,
    CfgPLC,
)


router = APIRouter(prefix="/admin/alarm-rules", tags=["admin"])


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class AlarmRuleBase(BaseModel):
    datapoint_id: int
    rule_source: str = Field(default="backend", max_length=20)
    external_rule_id: Optional[str] = Field(default=None, max_length=200)

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

    @field_validator("rule_source")
    @classmethod
    def _v_src(cls, v: str) -> str:
        v = (v or "backend").strip().lower()
        if v not in ("backend", "frontend"):
            raise ValueError("rule_source must be 'backend' or 'frontend'")
        return v

    @field_validator("comparison")
    @classmethod
    def _v_cmp(cls, v: str) -> str:
        v = (v or "above").strip().lower()
        if v not in ("above", "below", "outside_range", "inside_range"):
            raise ValueError("comparison must be above|below|outside_range|inside_range")
        return v

    @model_validator(mode="after")
    def _validate_thresholds(self):
        cmp = self.comparison

        if cmp in ("above", "below"):
            if self.alarm_threshold is None:
                raise ValueError("alarm_threshold is required for above/below")
            if self.warning_enabled:
                if self.warning_threshold is None:
                    raise ValueError("warning_threshold is required when warning_enabled")
                if cmp == "above" and self.warning_threshold >= self.alarm_threshold:
                    raise ValueError("for 'above', warning_threshold must be < alarm_threshold")
                if cmp == "below" and self.warning_threshold <= self.alarm_threshold:
                    raise ValueError("for 'below', warning_threshold must be > alarm_threshold")

        if cmp in ("outside_range", "inside_range"):
            if self.alarm_threshold_low is None or self.alarm_threshold_high is None:
                raise ValueError("alarm_threshold_low and alarm_threshold_high are required for range comparisons")
            if self.alarm_threshold_low > self.alarm_threshold_high:
                raise ValueError("alarm_threshold_low must be <= alarm_threshold_high")
            if self.warning_enabled:
                if self.warning_threshold_low is None or self.warning_threshold_high is None:
                    raise ValueError("warning range thresholds required when warning_enabled")
                if self.warning_threshold_low > self.warning_threshold_high:
                    raise ValueError("warning_threshold_low must be <= warning_threshold_high")

        if self.schedule_enabled:
            # allow missing times (treated as always active), but keep it consistent if one is set
            if (self.schedule_start_time is None) ^ (self.schedule_end_time is None):
                raise ValueError("both schedule_start_time and schedule_end_time must be set")

        return self


class AlarmRuleCreate(AlarmRuleBase):
    pass


class AlarmRuleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    enabled: Optional[bool] = None
    severity: Optional[str] = Field(default=None, max_length=30)
    comparison: Optional[str] = Field(default=None, max_length=30)

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
    schedule_timezone: Optional[str] = Field(default=None, max_length=64)


def _invalidate_alarm_cache(request: Request) -> None:
    mon = getattr(request.app.state, "alarm_monitor", None)
    if mon:
        try:
            mon.invalidate_cache()
        except Exception:
            pass


@router.get("/datapoints")
def list_alarm_rule_datapoints(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("alarms:admin")),
    group_id: Optional[int] = None,
    q: Optional[str] = None,
    limit: int = Query(500, ge=1, le=5000),
):
    qry = db.query(CfgDataPoint)
    if group_id is not None:
        qry = qry.filter(CfgDataPoint.group_id == int(group_id))
    if q:
        like = f"%{q}%"
        qry = qry.filter(CfgDataPoint.label.ilike(like))

    rows = qry.order_by(CfgDataPoint.label.asc()).limit(limit).all()
    # pre-load group names
    groups = {g.id: g.name for g in db.query(CfgDataPointGroup).all()}
    # pre-load plc names
    plc_by_id = {p.id: p.name for p in db.query(CfgPLC).all()}

    items = []
    for r in rows:
        plc_name = plc_by_id.get(r.owner_id) if r.owner_type == "plc" else None
        items.append(
            {
                "id": r.id,
                "label": r.label,
                "owner_type": r.owner_type,
                "owner_id": r.owner_id,
                "plc_name": plc_name,
                "group_id": r.group_id,
                "group_name": groups.get(r.group_id),
                "type": r.type,
                "category": r.category,
            }
        )

    return {"items": items}


@router.get("")
def list_alarm_rules(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("alarms:admin")),
    datapoint_id: Optional[int] = None,
    rule_source: Optional[str] = None,
):
    q = db.query(AlarmRule)
    if datapoint_id is not None:
        q = q.filter(AlarmRule.datapoint_id == int(datapoint_id))
    if rule_source:
        q = q.filter(AlarmRule.rule_source == str(rule_source).strip().lower())
    rows = q.order_by(AlarmRule.id.desc()).all()
    return {"items": [serialize_rule(r) for r in rows]}


@router.post("")
def create_alarm_rule(
    req: AlarmRuleCreate,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("alarms:write")),
):
    dp = db.query(CfgDataPoint).filter(CfgDataPoint.id == int(req.datapoint_id)).one_or_none()
    if not dp:
        raise HTTPException(status_code=404, detail="Datapoint not found")

    rule = AlarmRule(
        datapoint_id=req.datapoint_id,
        rule_source=req.rule_source,
        external_rule_id=req.external_rule_id,
        name=req.name,
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
        schedule_timezone=req.schedule_timezone,
    )
    db.add(rule)
    db.commit()

    _invalidate_alarm_cache(request)

    try:
        audit.log(
            db,
            action="alarm_rule.create",
            user_id=user.id,
            client_ip=request.client.host if request.client else None,
            resource=str(rule.id),
            metadata={"datapoint_id": rule.datapoint_id, "rule_source": rule.rule_source},
        )
    except Exception:
        pass

    return serialize_rule(rule)


@router.get("/{rule_id}")
def get_alarm_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("alarms:admin")),
):
    rule = db.query(AlarmRule).filter(AlarmRule.id == int(rule_id)).one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return serialize_rule(rule)


@router.put("/{rule_id}")
def update_alarm_rule(
    rule_id: int,
    req: AlarmRuleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("alarms:write")),
):
    rule = db.query(AlarmRule).filter(AlarmRule.id == int(rule_id)).one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    data = req.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(rule, k, v)

    # revalidate using AlarmRuleBase rules by creating a merged object
    merged = serialize_rule(rule)
    try:
        AlarmRuleBase(**merged)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    rule.updated_at = _now()
    db.add(rule)
    db.commit()

    _invalidate_alarm_cache(request)

    try:
        audit.log(
            db,
            action="alarm_rule.update",
            user_id=user.id,
            client_ip=request.client.host if request.client else None,
            resource=str(rule.id),
            metadata={"fields": list(data.keys())},
        )
    except Exception:
        pass

    return serialize_rule(rule)


@router.delete("/{rule_id}")
def delete_alarm_rule(
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("alarms:write")),
):
    rule = db.query(AlarmRule).filter(AlarmRule.id == int(rule_id)).one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()

    _invalidate_alarm_cache(request)

    try:
        audit.log(
            db,
            action="alarm_rule.delete",
            user_id=user.id,
            client_ip=request.client.host if request.client else None,
            resource=str(rule.id),
            metadata={"datapoint_id": rule.datapoint_id},
        )
    except Exception:
        pass

    return {"status": "deleted"}


def serialize_rule(r: AlarmRule) -> dict:
    return {
        "id": r.id,
        "datapoint_id": r.datapoint_id,
        "rule_source": r.rule_source,
        "external_rule_id": r.external_rule_id,
        "name": r.name,
        "enabled": r.enabled,
        "severity": r.severity,
        "comparison": r.comparison,
        "warning_enabled": r.warning_enabled,
        "warning_threshold": r.warning_threshold,
        "alarm_threshold": r.alarm_threshold,
        "warning_threshold_low": r.warning_threshold_low,
        "warning_threshold_high": r.warning_threshold_high,
        "alarm_threshold_low": r.alarm_threshold_low,
        "alarm_threshold_high": r.alarm_threshold_high,
        "schedule_enabled": r.schedule_enabled,
        "schedule_start_time": r.schedule_start_time.isoformat() if r.schedule_start_time else None,
        "schedule_end_time": r.schedule_end_time.isoformat() if r.schedule_end_time else None,
        "schedule_timezone": r.schedule_timezone,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }
