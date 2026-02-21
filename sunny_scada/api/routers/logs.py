from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import cast, literal, select, union_all, String, func

from sunny_scada.api.deps import (
    get_db,
    get_current_principal,
    get_rate_limiter,
    require_permission,
)
from sunny_scada.api.security import Principal
from sunny_scada.db.models import Alarm, AlarmEvent, AlarmOccurrence, AuditLog, Command, ServerLog

router = APIRouter(prefix="/logs", tags=["logs"])


def _parse_ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


class ClientLogRequest(BaseModel):
    level: str = Field(default="info", max_length=20)
    message: str = Field(min_length=1, max_length=5000)
    meta: dict[str, Any] = Field(default_factory=dict)


@router.post("/client")
def ingest_client_log(
    req: ClientLogRequest,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
    limiter=Depends(get_rate_limiter),
):
    # rate limit by principal+ip
    ip = request.client.host if request.client else "unknown"
    lim = limiter.allow(f"clientlog:{principal.actor_key}:{ip}", limit=120, window_s=60)
    if not lim.allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    db.add(
        ServerLog(
            level=req.level.upper(),
            logger=None,
            message=req.message,
            source="client",
            user_id=principal.user.id if principal.user else None,
            client_ip=ip,
            meta={"actor": principal.actor_key, **(req.meta or {})},
        )
    )
    db.commit()
    return {"status": "ok"}


@router.get("/server")
def query_server_logs(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("users:admin")),
    level: Optional[str] = None,
    source: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = db.query(ServerLog)
    if level:
        q = q.filter(ServerLog.level == level.upper())
    if source:
        q = q.filter(ServerLog.source == source)
    if from_ts:
        q = q.filter(ServerLog.ts >= _parse_ts(from_ts))
    if to_ts:
        q = q.filter(ServerLog.ts <= _parse_ts(to_ts))

    total = q.count()
    rows = q.order_by(ServerLog.ts.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "ts": r.ts,
                "level": r.level,
                "source": r.source,
                "message": r.message,
                "client_ip": r.client_ip,
                "user_id": r.user_id,
                "meta": r.meta,
            }
            for r in rows
        ],
    }


@router.get("/commands")
def query_command_logs(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("command:read")),
    status: Optional[str] = None,
    plc_name: Optional[str] = None,
    datapoint_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = db.query(Command)
    if status:
        q = q.filter(Command.status == status)
    if plc_name:
        q = q.filter(Command.plc_name == plc_name)
    if datapoint_id:
        q = q.filter(Command.datapoint_id == datapoint_id)

    total = q.count()
    rows = q.order_by(Command.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "command_id": r.command_id,
                "time": r.created_at,
                "plc": r.plc_name,
                "container": r.plc_name,
                "equipment": (r.payload or {}).get("equipment_label", "Unknown"),
                "data_point_label": (r.payload or {}).get("datapoint_label", r.datapoint_id),
                "bit_label": (r.payload or {}).get("bit_label"),
                "bit": (r.payload or {}).get("bit"),
                "value": (r.payload or {}).get("value"),
                "status": r.status,
                "attempts": r.attempts,
                "username": r.user.username if r.user else "System",
                "client_ip": r.client_ip or "Unknown",
                "error_message": r.error_message,
                "payload": r.payload,
            }
            for r in rows
        ],
    }


@router.get("/alarms")
def query_alarm_logs(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("alarms:read")),
    acked: Optional[bool] = None,
    severity: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    # Legacy (alarms table) + unified alarm_events join.
    s1 = select(
        Alarm.ts.label("ts"),
        Alarm.severity.label("severity"),
        Alarm.message.label("message"),
        Alarm.source.label("source"),
        Alarm.alarm_id.label("alarm_id"),
        Alarm.acked.label("acked"),
        Alarm.acked_at.label("acked_at"),
        Alarm.acked_by_user_id.label("acked_by_user_id"),
        Alarm.meta.label("meta"),
    )
    if acked is not None:
        s1 = s1.where(Alarm.acked == bool(acked))
    if severity:
        s1 = s1.where(Alarm.severity == severity)

    s2 = (
        select(
            AlarmEvent.ts.label("ts"),
            AlarmEvent.severity.label("severity"),
            AlarmEvent.message.label("message"),
            AlarmEvent.source.label("source"),
            (literal("ev_") + cast(AlarmEvent.id, String)).label("alarm_id"),
            AlarmOccurrence.acknowledged.label("acked"),
            AlarmOccurrence.acknowledged_at.label("acked_at"),
            AlarmOccurrence.acknowledged_by_user_id.label("acked_by_user_id"),
            AlarmEvent.meta.label("meta"),
        )
        .select_from(AlarmEvent)
        .join(AlarmOccurrence, AlarmEvent.occurrence_id == AlarmOccurrence.id)
    )
    if acked is not None:
        s2 = s2.where(AlarmOccurrence.acknowledged == bool(acked))
    if severity:
        s2 = s2.where(AlarmEvent.severity == severity)

    u = union_all(s1, s2).subquery()
    total = db.execute(select(func.count()).select_from(u)).scalar() or 0
    rows = db.execute(select(u).order_by(u.c.ts.desc()).offset(offset).limit(limit)).all()

    return {
        "total": int(total),
        "items": [
            {
                "alarm_id": r.alarm_id,
                "ts": r.ts,
                "severity": r.severity,
                "message": r.message,
                "source": r.source,
                "acked": bool(r.acked),
                "acked_at": r.acked_at,
                "acked_by_user_id": r.acked_by_user_id,
                "meta": r.meta,
            }
            for r in rows
        ],
    }


@router.get("/audit")
def query_audit_logs(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("users:admin")),
    action: Optional[str] = None,
    user_id: Optional[int] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)

    total = q.count()
    rows = q.order_by(AuditLog.ts.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "ts": r.ts,
                "user_id": r.user_id,
                "client_ip": r.client_ip,
                "action": r.action,
                "resource": r.resource,
                "meta": r.meta,
                "config_revision_id": r.config_revision_id,
            }
            for r in rows
        ],
    }
