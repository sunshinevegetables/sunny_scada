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
    state: Optional[str] = Field(default=None, max_length=20)
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

    # Also write into unified alarm tables (restart-safe + websocket streaming)
    alarm_manager = getattr(request.app.state, "alarm_manager", None)
    broadcaster = getattr(request.app.state, "alarm_broadcaster", None)
    if alarm_manager:
        src = "plc" if (req.source or "").lower().startswith("plc") else "plc"
        # Prefer stable keys provided by the sender.
        raw_key = str((req.meta or {}).get("key") or (req.meta or {}).get("alarm_key") or "")
        if raw_key:
            key = f"{src}:{raw_key}"
        else:
            key = f"{src}:{alarm.alarm_id}"
        new_state = (req.state or "ALARM").upper()
        alarm_manager.set_state(
            db,
            source=src,
            key=key,
            new_state=new_state,
            severity=req.severity,
            message=req.message,
            ts=alarm.ts,
            meta={"legacy_alarm_id": alarm.alarm_id, **(req.meta or {})},
            broadcast_cb=broadcaster.broadcast if broadcaster else None,
        )

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
    # Support ack for unified alarms (ev_<event_id> or occ_<occurrence_id>)
    alarm_manager = getattr(request.app.state, "alarm_manager", None)
    if alarm_manager and (alarm_id.startswith("ev_") or alarm_id.startswith("occ_")):
        from sunny_scada.db.models import AlarmEvent, AlarmOccurrence

        occ_id: Optional[int] = None
        if alarm_id.startswith("occ_"):
            try:
                occ_id = int(alarm_id.split("_", 1)[1])
            except Exception:
                occ_id = None
        elif alarm_id.startswith("ev_"):
            try:
                ev_id = int(alarm_id.split("_", 1)[1])
            except Exception:
                ev_id = None
            if ev_id is not None:
                ev = db.query(AlarmEvent).filter(AlarmEvent.id == ev_id).one_or_none()
                occ_id = int(ev.occurrence_id) if ev else None

        if not occ_id:
            raise HTTPException(status_code=404, detail="Alarm not found")

        try:
            alarm_manager.acknowledge(
                db,
                occurrence_id=occ_id,
                acknowledged=True,
                user_id=user.id,
                client_ip=request.client.host if request.client else None,
                note=req.note,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Alarm not found")

        return {"alarm_id": alarm_id, "acked": True}

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
