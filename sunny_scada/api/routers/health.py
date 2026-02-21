from __future__ import annotations

from fastapi import APIRouter, Depends

from sqlalchemy.orm import Session
from sqlalchemy import text

from sunny_scada.api.deps import get_modbus, get_db, require_permission

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    # DB ping
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    return {"status": "ok", "db": {"ok": db_ok}}


@router.get("/modbus/health")
def modbus_health(modbus=Depends(get_modbus), _perm=Depends(require_permission("plc:read"))):
    return {"plcs": modbus.health_snapshot()}


@router.get("/health/plcs")
def health_plcs(modbus=Depends(get_modbus), _perm=Depends(require_permission("plc:read"))):
    return {"plcs": modbus.health_snapshot()}
