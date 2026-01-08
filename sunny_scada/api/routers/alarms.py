from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sunny_scada.api.deps import get_db, get_current_user, require_permission, get_audit_service
from sunny_scada.db.models import Alarm

router = APIRouter(prefix="/alarms", tags=["alarms"])


class CreateAlarmRequest(BaseModel):
    severity: str = Field(default="info", max_length=30)
    message: str = Field(min_length=1, max_length=500)
    source: Optional[str] = Field(default=None, max_length=200)
    meta: dict[str, Any] = Field(default_factory=dict)


@router.post("")
def create_alarm(
    req: CreateAlarmRequest,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("alarms:write")),
):
    ip = request.client.host if request.client else None
    alarm = Alarm(severity=req.severity, message=req.message, source=req.source, meta=req.meta)
    db.add(alarm)
    db.commit()

    try:
        audit.log(
            db,
            action="alarm.create",
            user_id=user.id,
            client_ip=ip,
            resource=req.source,
            metadata={"alarm_id": alarm.alarm_id, "severity": req.severity},
        )
    except Exception:
        pass

    return {"alarm_id": alarm.alarm_id, "status": "created"}


class AckRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)


@router.post("/{alarm_id}/ack")
def ack_alarm(
    alarm_id: str,
    req: AckRequest,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("alarms:write")),
):
    alarm = db.query(Alarm).filter(Alarm.alarm_id == alarm_id).one_or_none()
    if not alarm:
        raise HTTPException(status_code=404, detail="Alarm not found")

    if alarm.acked:
        return {"alarm_id": alarm.alarm_id, "acked": True}

    import datetime as dt

    alarm.acked = True
    alarm.acked_at = dt.datetime.now(dt.timezone.utc)
    alarm.acked_by_user_id = user.id
    alarm.acked_by_client_ip = request.client.host if request.client else None
    if req.note:
        meta = dict(alarm.meta or {})
        meta["ack_note"] = req.note
        alarm.meta = meta
    db.add(alarm)
    db.commit()

    try:
        audit.log(
            db,
            action="alarm.ack",
            user_id=user.id,
            client_ip=alarm.acked_by_client_ip,
            resource=alarm.source,
            metadata={"alarm_id": alarm.alarm_id, "note": req.note},
        )
    except Exception:
        pass

    return {"alarm_id": alarm.alarm_id, "acked": True}
