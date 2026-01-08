from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from sunny_scada.db.models import Command, CommandEvent
from sunny_scada.modbus_service import ModbusService
from sunny_scada.services.audit_service import AuditService
from sunny_scada.services.data_points_service import DataPointsService
from sunny_scada.services.rate_limiter import RateLimiter
from sunny_scada.services.command_executor import CommandExecutor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateResult:
    command_id: str
    status: str


class CommandService:
    def __init__(
        self,
        *,
        modbus: ModbusService,
        data_points: DataPointsService,
        executor: CommandExecutor,
        rate_limiter: RateLimiter,
        audit: AuditService,
        rate_limit_per_minute: int = 30,
    ) -> None:
        self._modbus = modbus
        self._dp = data_points
        self._executor = executor
        self._limiter = rate_limiter
        self._audit = audit
        self._rpm = max(1, int(rate_limit_per_minute))

    def create(
        self,
        db: Session,
        *,
        plc_name: str,
        datapoint_id: str,
        kind: str,
        value: Any,
        bit: Optional[int],
        user_id: Optional[int],
        client_ip: Optional[str],
    ) -> CreateResult:
        plc_name = str(plc_name)
        datapoint_id = str(datapoint_id)
        kind = (kind or "").lower().strip()

        if plc_name not in set(self._modbus.plc_names()):
            raise HTTPException(status_code=400, detail=f"Unknown PLC '{plc_name}'")

        # Rate limit per user+plc+datapoint
        key = f"cmd:{user_id or 0}:{plc_name}:{datapoint_id}"
        limit = self._limiter.allow(key, limit=self._rpm, window_s=60)
        if not limit.allowed:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

        dp = self._dp.find_register(datapoint_id, direction="write")
        if not dp:
            raise HTTPException(status_code=400, detail=f"Datapoint '{datapoint_id}' is not configured as writable")

        addr = dp.get("address")
        if addr is None:
            raise HTTPException(status_code=400, detail="Writable datapoint missing 'address'")
        try:
            addr_i = int(addr)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid address")
        if addr_i < 40000:
            raise HTTPException(status_code=400, detail="Write address must be a 4xxxx holding register")

        typ = str(dp.get("type") or "").upper()
        payload: Dict[str, Any] = {"address": addr_i}

        if typ == "DIGITAL":
            if kind not in ("bit", ""):
                raise HTTPException(status_code=400, detail="DIGITAL points only support kind='bit'")
            if bit is None:
                raise HTTPException(status_code=400, detail="bit is required for DIGITAL writes")
            if int(bit) < 0 or int(bit) > 15:
                raise HTTPException(status_code=400, detail="bit must be 0..15")
            if int(value) not in (0, 1):
                raise HTTPException(status_code=400, detail="value must be 0 or 1")
            # enforce configured bit presence if bits mapping exists
            bits = dp.get("bits") or {}
            if isinstance(bits, dict) and bits and f"BIT {int(bit)}" not in bits:
                raise HTTPException(status_code=400, detail="bit not permitted for this datapoint")
            payload.update({"bit": int(bit), "value": int(value)})
            kind = "bit"

        elif typ == "INTEGER":
            if kind in ("", "register"):
                kind = "register"
            else:
                raise HTTPException(status_code=400, detail="INTEGER points only support kind='register'")
            try:
                v = int(value)
            except Exception:
                raise HTTPException(status_code=400, detail="value must be an integer")
            if "min" in dp and dp.get("min") is not None and v < int(dp.get("min")):
                raise HTTPException(status_code=400, detail="value below min")
            if "max" in dp and dp.get("max") is not None and v > int(dp.get("max")):
                raise HTTPException(status_code=400, detail="value above max")
            if v < 0 or v > 65535:
                raise HTTPException(status_code=400, detail="value out of 0..65535")
            payload.update({"value": v, "verify": True})

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported writable type '{typ}'")

        cmd = Command(
            plc_name=plc_name,
            datapoint_id=datapoint_id,
            kind=kind,
            payload=payload,
            status="queued",
            user_id=user_id,
            client_ip=client_ip,
        )
        db.add(cmd)
        db.flush()  # cmd.id
        db.add(CommandEvent(command_row_id=cmd.id, status="queued", message=None, meta={"rate_remaining": limit.remaining}))
        db.commit()

        # audit trail
        try:
            self._audit.log(
                db,
                action="command.create",
                user_id=user_id,
                client_ip=client_ip,
                resource=plc_name,
                metadata={"command_id": cmd.command_id, "datapoint_id": datapoint_id, "kind": kind},
            )
        except Exception:
            pass

        self._executor.enqueue(plc_name, cmd.id)
        return CreateResult(command_id=cmd.command_id, status=cmd.status)
