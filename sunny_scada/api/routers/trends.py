from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from sunny_scada.api.deps import get_db, get_historian_service, require_permission
from sunny_scada.services.datapoint_identity import AmbiguousDatapointIdentifierError

router = APIRouter(tags=["trends"])


@router.get("/trends")
def trends(
    plc_id: str,
    datapoint_id: str | None = None,
    cfg_data_point_id: int | None = None,
    owner_type: str | None = None,
    owner_id: int | None = None,
    from_ts: str = Query(..., alias="from"),
    to_ts: str = Query(..., alias="to"),
    bucket: str = Query("hour", pattern="^(hour|day|week|month|year)$"),
    db: Session = Depends(get_db),
    svc=Depends(get_historian_service),
    _perm=Depends(require_permission("config:read")),
):
    if not datapoint_id and cfg_data_point_id is None:
        raise HTTPException(status_code=422, detail="Provide datapoint_id or cfg_data_point_id")
    try:
        return svc.trends(
            db,
            plc_id=plc_id,
            datapoint_id=datapoint_id,
            cfg_data_point_id=cfg_data_point_id,
            owner_type=owner_type,
            owner_id=owner_id,
            from_ts=from_ts,
            to_ts=to_ts,
            bucket=bucket,
        )
    except AmbiguousDatapointIdentifierError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "datapoint_id": exc.datapoint_id,
                "candidates": exc.candidates,
            },
        )


@router.get("/trends/latest")
def latest(
    plc_id: str,
    datapoint_id: str | None = None,
    cfg_data_point_id: int | None = None,
    owner_type: str | None = None,
    owner_id: int | None = None,
    db: Session = Depends(get_db),
    svc=Depends(get_historian_service),
    _perm=Depends(require_permission("config:read")),
):
    if not datapoint_id and cfg_data_point_id is None:
        raise HTTPException(status_code=422, detail="Provide datapoint_id or cfg_data_point_id")
    try:
        s = svc.latest(
            db,
            plc_id=plc_id,
            datapoint_id=datapoint_id,
            cfg_data_point_id=cfg_data_point_id,
            owner_type=owner_type,
            owner_id=owner_id,
        )
    except AmbiguousDatapointIdentifierError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "datapoint_id": exc.datapoint_id,
                "candidates": exc.candidates,
            },
        )
    if not s:
        raise HTTPException(status_code=404, detail="No data")
    return {"ts": s.ts, "value": s.value, "quality": s.quality, "cfg_data_point_id": s.cfg_data_point_id}
