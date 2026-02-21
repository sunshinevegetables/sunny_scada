from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from sunny_scada.api.deps import get_db, get_current_user, require_permission, get_audit_service
from sunny_scada.db.models import AlarmEvent, AlarmOccurrence


router = APIRouter(prefix="/admin/alarm-log", tags=["admin"])

def _parse_ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


@router.get("")
def query_alarm_log(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("alarms:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    source: Optional[str] = None,
    state: Optional[str] = None,
    severity: Optional[str] = None,
    active_only: bool = False,
    datapoint_id: Optional[int] = None,
    rule_id: Optional[int] = None,
    q: Optional[str] = None,
):
    offset = (page - 1) * page_size

    if active_only:
        qry = db.query(AlarmOccurrence)
        qry = qry.filter(AlarmOccurrence.is_active == True)  # noqa: E712
        if source:
            qry = qry.filter(AlarmOccurrence.source == source)
        if state:
            qry = qry.filter(AlarmOccurrence.state == state.upper())
        if severity:
            qry = qry.filter(AlarmOccurrence.severity == severity)
        if datapoint_id is not None:
            qry = qry.filter(AlarmOccurrence.datapoint_id == int(datapoint_id))
        if rule_id is not None:
            qry = qry.filter(AlarmOccurrence.rule_id == int(rule_id))
        if q:
            like = f"%{q}%"
            qry = qry.filter(or_(AlarmOccurrence.message.ilike(like), AlarmOccurrence.key.ilike(like)))

        total = qry.count()
        rows = qry.order_by(AlarmOccurrence.last_seen_at.desc()).offset(offset).limit(page_size).all()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "occurrence_id": r.id,
                    "source": r.source,
                    "key": r.key,
                    "datapoint_id": r.datapoint_id,
                    "rule_id": r.rule_id,
                    "external_rule_id": r.external_rule_id,
                    "state": r.state,
                    "severity": r.severity,
                    "message": r.message,
                    "value": r.value,
                    "warning_threshold": r.warning_threshold,
                    "alarm_threshold": r.alarm_threshold,
                    "first_seen_at": r.first_seen_at,
                    "last_seen_at": r.last_seen_at,
                    "acknowledged": r.acknowledged,
                    "acknowledged_at": r.acknowledged_at,
                    "meta": r.meta,
                }
                for r in rows
            ],
        }

    # Event log
    qry = db.query(AlarmEvent, AlarmOccurrence).join(
        AlarmOccurrence, AlarmEvent.occurrence_id == AlarmOccurrence.id
    )

    if from_ts:
        qry = qry.filter(AlarmEvent.ts >= _parse_ts(from_ts))
    if to_ts:
        qry = qry.filter(AlarmEvent.ts <= _parse_ts(to_ts))
    if source:
        qry = qry.filter(AlarmEvent.source == source)
    if state:
        qry = qry.filter(AlarmEvent.new_state == state.upper())
    if severity:
        qry = qry.filter(AlarmEvent.severity == severity)
    if datapoint_id is not None:
        qry = qry.filter(AlarmEvent.datapoint_id == int(datapoint_id))
    if rule_id is not None:
        qry = qry.filter(AlarmEvent.rule_id == int(rule_id))
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(AlarmEvent.message.ilike(like), AlarmEvent.key.ilike(like)))

    total = qry.with_entities(func.count()).scalar() or 0
    rows = qry.order_by(AlarmEvent.ts.desc()).offset(offset).limit(page_size).all()

    items = []
    for evt, occ in rows:
        items.append(
            {
                "event_id": evt.id,
                "occurrence_id": evt.occurrence_id,
                "ts": evt.ts,
                "source": evt.source,
                "key": evt.key,
                "datapoint_id": evt.datapoint_id,
                "rule_id": evt.rule_id,
                "external_rule_id": evt.external_rule_id,
                "prev_state": evt.prev_state,
                "state": evt.new_state,
                "severity": evt.severity,
                "message": evt.message,
                "value": evt.value,
                "acknowledged": occ.acknowledged,
                "acknowledged_at": occ.acknowledged_at,
                "meta": evt.meta,
            }
        )

    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/{occurrence_id}")
def get_alarm_occurrence(
    occurrence_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("alarms:read")),
    limit: int = Query(100, ge=1, le=500),
):
    occ = db.query(AlarmOccurrence).filter(AlarmOccurrence.id == int(occurrence_id)).one_or_none()
    if not occ:
        raise HTTPException(status_code=404, detail="Occurrence not found")

    evts = (
        db.query(AlarmEvent)
        .filter(AlarmEvent.occurrence_id == occ.id)
        .order_by(AlarmEvent.ts.desc())
        .limit(limit)
        .all()
    )

    return {
        "occurrence": {
            "occurrence_id": occ.id,
            "source": occ.source,
            "key": occ.key,
            "datapoint_id": occ.datapoint_id,
            "rule_id": occ.rule_id,
            "external_rule_id": occ.external_rule_id,
            "state": occ.state,
            "severity": occ.severity,
            "message": occ.message,
            "value": occ.value,
            "warning_threshold": occ.warning_threshold,
            "alarm_threshold": occ.alarm_threshold,
            "first_seen_at": occ.first_seen_at,
            "last_seen_at": occ.last_seen_at,
            "cleared_at": occ.cleared_at,
            "is_active": occ.is_active,
            "acknowledged": occ.acknowledged,
            "acknowledged_at": occ.acknowledged_at,
            "acknowledged_by_user_id": occ.acknowledged_by_user_id,
            "meta": occ.meta,
        },
        "events": [
            {
                "event_id": e.id,
                "ts": e.ts,
                "prev_state": e.prev_state,
                "state": e.new_state,
                "severity": e.severity,
                "message": e.message,
                "value": e.value,
                "meta": e.meta,
            }
            for e in evts
        ],
    }


class AckBody(BaseModel):
    acknowledged: bool = True
    note: Optional[str] = Field(default=None, max_length=500)


@router.post("/{occurrence_id}/ack")
def ack_alarm_occurrence(
    occurrence_id: int,
    body: AckBody,
    request: Request,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("alarms:write")),
):
    alarm_manager = getattr(request.app.state, "alarm_manager", None)
    if not alarm_manager:
        raise HTTPException(status_code=503, detail="Alarm manager not available")

    try:
        occ = alarm_manager.acknowledge(
            db,
            occurrence_id=int(occurrence_id),
            acknowledged=bool(body.acknowledged),
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            note=body.note,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Occurrence not found")

    try:
        audit.log(
            db,
            action="alarm.ack",
            user_id=me.id,
            client_ip=request.client.host if request.client else None,
            resource=f"occurrence:{occ.id}",
            metadata={"acknowledged": body.acknowledged, "note": body.note, "source": occ.source},
        )
    except Exception:
        pass

    return {
        "occurrence_id": occ.id,
        "acknowledged": occ.acknowledged,
        "acknowledged_at": occ.acknowledged_at,
    }
