from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from sunny_scada.db.models import Command, CommandEvent, CfgDataPoint
from sunny_scada.modbus_service import ModbusService
from sunny_scada.services.audit_service import AuditService
from sunny_scada.services.rate_limiter import RateLimiter
from sunny_scada.services.command_executor import CommandExecutor
from sunny_scada.services.command_log_payload import build_command_log_payload

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
        executor: CommandExecutor,
        rate_limiter: RateLimiter,
        audit: AuditService,
        broadcaster=None,
        rate_limit_per_minute: int = 30,
    ) -> None:
        self._modbus = modbus
        self._executor = executor
        self._limiter = rate_limiter
        self._audit = audit
        self._broadcaster = broadcaster
        self._rpm = max(1, int(rate_limit_per_minute))

    def _emit(self, payload: dict) -> None:
        if not self._broadcaster:
            return
        try:
            self._broadcaster(payload)
        except Exception:
            logger.debug("CommandService broadcast failed", exc_info=True)

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

        # DB-backed datapoints only (format: db-dp:123)
        if not datapoint_id.startswith("db-dp:"):
            raise HTTPException(status_code=400, detail="Only DB-backed datapoints are supported (format: db-dp:ID)")
        
        try:
            dp_db_id = int(datapoint_id.split(":", 1)[1])
        except (IndexError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid DB datapoint ID format")
        
        db_dp = db.query(CfgDataPoint).filter(CfgDataPoint.id == dp_db_id).one_or_none()
        if not db_dp or db_dp.category != "write":
            raise HTTPException(status_code=400, detail=f"Datapoint '{datapoint_id}' is not configured as writable")
        
        # Extract address and type from DB
        try:
            addr_i = int(db_dp.address)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid address format in DB: {db_dp.address}")
        
        if addr_i < 40000:
            raise HTTPException(status_code=400, detail="Write address must be a 4xxxx holding register")
        
        typ = db_dp.type.upper()
        allowed_bits = {b.bit for b in db_dp.bits} if db_dp.bits else set()

        # Resolve equipment/container label for logging
        equipment_label = "Unknown"
        if db_dp.owner_type and db_dp.owner_id:
            if db_dp.owner_type == "equipment":
                from sunny_scada.db.models import CfgEquipment
                eq = db.query(CfgEquipment).filter(CfgEquipment.id == db_dp.owner_id).one_or_none()
                equipment_label = (getattr(eq, "name", None) or "Unknown") if eq else "Unknown"
            elif db_dp.owner_type == "container":
                from sunny_scada.db.models import CfgContainer
                ct = db.query(CfgContainer).filter(CfgContainer.id == db_dp.owner_id).one_or_none()
                equipment_label = (getattr(ct, "name", None) or "Unknown") if ct else "Unknown"

        payload: Dict[str, Any] = {"address": addr_i, "datapoint_label": db_dp.label, "equipment_label": equipment_label}

        if typ == "DIGITAL":
            if kind not in ("bit", ""):
                raise HTTPException(status_code=400, detail="DIGITAL points only support kind='bit'")
            if bit is None:
                raise HTTPException(status_code=400, detail="bit is required for DIGITAL writes")
            if int(bit) < 0 or int(bit) > 15:
                raise HTTPException(status_code=400, detail="bit must be 0..15")
            if int(value) not in (0, 1):
                raise HTTPException(status_code=400, detail="value must be 0 or 1")
            
            # Enforce bit presence in DB
            if allowed_bits and int(bit) not in allowed_bits:
                raise HTTPException(status_code=400, detail=f"bit not permitted for this datapoint (allowed: {sorted(allowed_bits)})")
            
            # Extract bit label from database
            bit_label = "Unknown"
            if db_dp.bits:
                for b in db_dp.bits:
                    if b.bit == int(bit):
                        bit_label = b.label or f"Bit {int(bit)}"
                        break
            
            payload.update({"bit": int(bit), "bit_label": bit_label, "value": int(value)})
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
        evt = CommandEvent(command_row_id=cmd.id, status="queued", message=None, meta={"rate_remaining": limit.remaining})
        db.add(evt)
        db.commit()
        db.refresh(cmd)
        db.refresh(evt)
        self._emit(build_command_log_payload(cmd, evt))

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
