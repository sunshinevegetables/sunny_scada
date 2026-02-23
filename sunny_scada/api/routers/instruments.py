from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sunny_scada.api.deps import get_audit_service, get_current_user, get_db, require_permission
from sunny_scada.services.instrument_health_service import InstrumentHealthService
from sunny_scada.services.instrument_service import InstrumentService


router = APIRouter(prefix="/maintenance/instruments", tags=["maintenance"])
_service = InstrumentService()
_health_service = InstrumentHealthService()


def _parse_iso_datetime(value: Optional[str]) -> Optional[dt.datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


class InstrumentIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    status: str = Field(default="active", min_length=1, max_length=30)
    equipment_id: Optional[int] = None
    vendor_id: Optional[int] = None
    instrument_type: Optional[str] = Field(default=None, max_length=100)
    model: Optional[str] = Field(default=None, max_length=120)
    serial_number: Optional[str] = Field(default=None, max_length=120)
    location: Optional[str] = Field(default=None, max_length=200)
    installed_at: Optional[str] = None
    notes: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class InstrumentUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=200)
    status: Optional[str] = Field(default=None, min_length=1, max_length=30)
    equipment_id: Optional[int] = None
    vendor_id: Optional[int] = None
    instrument_type: Optional[str] = Field(default=None, max_length=100)
    model: Optional[str] = Field(default=None, max_length=120)
    serial_number: Optional[str] = Field(default=None, max_length=120)
    location: Optional[str] = Field(default=None, max_length=200)
    installed_at: Optional[str] = None
    notes: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


class InstrumentDataPointMapIn(BaseModel):
    cfg_data_point_id: int
    role: str = Field(default="process", min_length=1, max_length=50)


class InstrumentCalibrationIn(BaseModel):
    ts: Optional[str] = None
    next_due_at: Optional[str] = None
    method: Optional[str] = Field(default=None, max_length=120)
    result: Optional[str] = Field(default=None, max_length=60)
    as_found: Optional[float] = None
    as_left: Optional[float] = None
    performed_by: Optional[str] = Field(default=None, max_length=200)
    certificate_no: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class InstrumentSpareIn(BaseModel):
    spare_part_id: int
    qty_per_replacement: int = Field(ge=1)


@router.post("")
def create_instrument(
    req: InstrumentIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    try:
        out = _service.create_instrument(
            db,
            label=req.label,
            status=req.status,
            equipment_id=req.equipment_id,
            vendor_id=req.vendor_id,
            instrument_type=req.instrument_type,
            model=req.model,
            serial_number=req.serial_number,
            location=req.location,
            installed_at=_parse_iso_datetime(req.installed_at),
            notes=req.notes,
            meta=req.meta,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        audit.log(
            db,
            action="maintenance.instrument.create",
            user_id=user.id,
            client_ip=request.client.host if request.client else None,
            resource=str(out.get("label") or out.get("id")),
            metadata={"id": out.get("id")},
        )
    except Exception:
        pass

    return out


@router.get("")
def list_instruments(
    equipment_id: Optional[int] = None,
    q: Optional[str] = None,
    status: Optional[str] = None,
    type: Optional[str] = Query(default=None, alias="type"),
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    return _service.list_instruments(
        db,
        equipment_id=equipment_id,
        status=status,
        instrument_type=type,
        q=q,
    )


@router.get("/{instrument_id}")
def get_instrument(
    instrument_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    out = _service.get_instrument(db, instrument_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Not found")
    return out


@router.put("/{instrument_id}")
def update_instrument(
    instrument_id: int,
    req: InstrumentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    patch = req.model_dump(exclude_unset=True)
    if "installed_at" in patch:
        patch["installed_at"] = _parse_iso_datetime(req.installed_at)

    try:
        out = _service.update_instrument(db, instrument_id, patch=patch)
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail="Not found")
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        audit.log(
            db,
            action="maintenance.instrument.update",
            user_id=user.id,
            client_ip=request.client.host if request.client else None,
            resource=str(out.get("label") or out.get("id")),
            metadata={"id": out.get("id")},
        )
    except Exception:
        pass

    return out


@router.delete("/{instrument_id}")
def delete_instrument(
    instrument_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    deleted = _service.delete_instrument(db, instrument_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        audit.log(
            db,
            action="maintenance.instrument.delete",
            user_id=user.id,
            client_ip=request.client.host if request.client else None,
            resource=str(instrument_id),
            metadata={"id": instrument_id},
        )
    except Exception:
        pass

    return {"status": "ok"}


@router.post("/{instrument_id}/datapoints")
def add_datapoint_mapping(
    instrument_id: int,
    req: InstrumentDataPointMapIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    try:
        out = _service.link_datapoint(
            db,
            instrument_id=instrument_id,
            cfg_data_point_id=req.cfg_data_point_id,
            role=req.role,
        )
    except ValueError as exc:
        msg = str(exc).lower()
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        audit.log(
            db,
            action="maintenance.instrument.mapping.add",
            user_id=user.id,
            client_ip=request.client.host if request.client else None,
            resource=str(instrument_id),
            metadata={"instrument_id": instrument_id, "mapping_id": out.get("id"), "cfg_data_point_id": out.get("cfg_data_point_id"), "role": out.get("role")},
        )
    except Exception:
        pass

    return out


@router.get("/{instrument_id}/datapoints")
def list_datapoint_mappings(
    instrument_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    return _service.list_datapoint_mappings(db, instrument_id=instrument_id)


@router.delete("/{instrument_id}/datapoints/{map_id}")
def delete_datapoint_mapping(
    instrument_id: int,
    map_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    deleted = _service.delete_datapoint_mapping(db, instrument_id=instrument_id, map_id=map_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        audit.log(
            db,
            action="maintenance.instrument.mapping.delete",
            user_id=user.id,
            client_ip=request.client.host if request.client else None,
            resource=str(instrument_id),
            metadata={"instrument_id": instrument_id, "mapping_id": map_id},
        )
    except Exception:
        pass

    return {"status": "ok"}


@router.post("/{instrument_id}/calibrations")
def add_calibration(
    instrument_id: int,
    req: InstrumentCalibrationIn,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    audit=Depends(get_audit_service),
    _perm=Depends(require_permission("maintenance:write")),
):
    try:
        out = _service.add_calibration(
            db,
            instrument_id=instrument_id,
            ts=_parse_iso_datetime(req.ts),
            next_due_at=_parse_iso_datetime(req.next_due_at),
            method=req.method,
            result=req.result,
            as_found=req.as_found,
            as_left=req.as_left,
            performed_by=req.performed_by,
            certificate_no=req.certificate_no,
            notes=req.notes,
            meta=req.meta,
        )
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        audit.log(
            db,
            action="maintenance.instrument.calibration.add",
            user_id=user.id,
            client_ip=request.client.host if request.client else None,
            resource=str(instrument_id),
            metadata={"instrument_id": instrument_id, "calibration_id": out.get("id")},
        )
    except Exception:
        pass

    return out


@router.get("/{instrument_id}/calibrations")
def list_calibrations(
    instrument_id: int,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    return _service.list_calibrations(db, instrument_id=instrument_id, limit=limit)


@router.post("/{instrument_id}/spares")
def add_spare_map(
    instrument_id: int,
    req: InstrumentSpareIn,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:write")),
):
    try:
        return _service.map_spare(
            db,
            instrument_id=instrument_id,
            part_id=req.spare_part_id,
            qty_required=req.qty_per_replacement,
        )
    except ValueError as exc:
        msg = str(exc).lower()
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{instrument_id}/spares")
def list_spare_map(
    instrument_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    return _service.list_spare_map(db, instrument_id=instrument_id)


@router.delete("/{instrument_id}/spares/{spare_part_id}")
def delete_spare_map(
    instrument_id: int,
    spare_part_id: int,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:write")),
):
    deleted = _service.unmap_spare(db, instrument_id=instrument_id, part_id=spare_part_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Not found")
    return {"status": "ok"}


@router.get("/{instrument_id}/health")
def get_instrument_health(
    instrument_id: int,
    window_minutes: int = Query(10, ge=1, le=24 * 60),
    flatline_minutes: int = Query(10, ge=1, le=24 * 60),
    max_gap_seconds: int = Query(30, ge=1, le=3600),
    noise_std_threshold: Optional[float] = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("maintenance:read")),
):
    try:
        return _health_service.get_health(
            db,
            instrument_id=instrument_id,
            window_minutes=window_minutes,
            flatline_minutes=flatline_minutes,
            max_gap_seconds=max_gap_seconds,
            noise_std_threshold=noise_std_threshold,
        )
    except ValueError as exc:
        msg = str(exc).lower()
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
