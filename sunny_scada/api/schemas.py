from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


class BitWriteSignalRequest(BaseModel):
    # DB-driven mode only
    plc: str
    equipmentLabel: str
    equipmentId: Optional[str] = None
    commandTag: str
    receiverId: Optional[str] = None
    
    bit: int = Field(ge=0, le=15)
    value: int = Field(ge=0, le=1)


class BitReadSignalRequest(BaseModel):
    plc_type: str = Field(default="plc")
    plc_name: str
    register: str
    bit: int = Field(ge=0, le=15)


class UpdateDataPointRequest(BaseModel):
    # Same shape as your existing monolith for compatibility
    path: str
    name: str
    type: str
    description: str
    address: int
    bits: Optional[Dict[str, Any]] = None
