from __future__ import annotations

from fastapi import APIRouter, Depends

from sunny_scada.api.deps import get_modbus

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/modbus/health")
def modbus_health(modbus=Depends(get_modbus)):
    return {"plcs": modbus.health_snapshot()}
