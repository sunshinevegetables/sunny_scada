from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from sunny_scada.api.deps import get_db, get_historian_service, require_permission

router = APIRouter(tags=["trends"])


@router.get("/trends")
def trends(
    plc_id: str,
    datapoint_id: str,
    from_ts: str = Query(..., alias="from"),
    to_ts: str = Query(..., alias="to"),
    bucket: str = Query("hour", pattern="^(hour|day|week|month|year)$"),
    db: Session = Depends(get_db),
    svc=Depends(get_historian_service),
    _perm=Depends(require_permission("config:read")),
):
    return svc.trends(db, plc_id=plc_id, datapoint_id=datapoint_id, from_ts=from_ts, to_ts=to_ts, bucket=bucket)


@router.get("/trends/latest")
def latest(
    plc_id: str,
    datapoint_id: str,
    db: Session = Depends(get_db),
    svc=Depends(get_historian_service),
    _perm=Depends(require_permission("config:read")),
):
    s = svc.latest(db, plc_id=plc_id, datapoint_id=datapoint_id)
    if not s:
        raise HTTPException(status_code=404, detail="No data")
    return {"ts": s.ts, "value": s.value, "quality": s.quality}
