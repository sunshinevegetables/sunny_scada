from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sunny_scada.api.deps import (
    get_db,
    get_current_user,
    get_command_service,
    require_permission,
)
from sunny_scada.db.models import Command, CommandEvent

router = APIRouter(prefix="/commands", tags=["commands"])


class CreateCommandRequest(BaseModel):
    plc_name: str = Field(min_length=1, max_length=200)
    datapoint_id: str = Field(min_length=1, max_length=200)
    kind: str | None = Field(default=None, description="bit or register; inferred if omitted")
    value: Any
    bit: Optional[int] = Field(default=None, ge=0, le=15)


class CreateCommandResponse(BaseModel):
    command_id: str
    status: str


@router.post("", response_model=CreateCommandResponse)
def create_command(
    req: CreateCommandRequest,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    svc=Depends(get_command_service),
    _perm=Depends(require_permission("command:write")),
):
    res = svc.create(
        db,
        plc_name=req.plc_name,
        datapoint_id=req.datapoint_id,
        kind=req.kind or "",
        value=req.value,
        bit=req.bit,
        user_id=user.id,
        client_ip=request.client.host if request.client else None,
    )
    return CreateCommandResponse(command_id=res.command_id, status=res.status)


@router.get("")
def list_commands(
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("command:read")),
    plc_name: str | None = None,
    datapoint_id: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = db.query(Command)
    if plc_name:
        q = q.filter(Command.plc_name == plc_name)
    if datapoint_id:
        q = q.filter(Command.datapoint_id == datapoint_id)
    if status:
        q = q.filter(Command.status == status)
    total = q.count()
    rows = q.order_by(Command.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "command_id": r.command_id,
                "plc_name": r.plc_name,
                "datapoint_id": r.datapoint_id,
                "kind": r.kind,
                "status": r.status,
                "attempts": r.attempts,
                "error_message": r.error_message,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ],
    }


@router.get("/{command_id}")
def get_command(
    command_id: str,
    db: Session = Depends(get_db),
    _perm=Depends(require_permission("command:read")),
):
    cmd = db.query(Command).filter(Command.command_id == command_id).one_or_none()
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")
    events = (
        db.query(CommandEvent)
        .filter(CommandEvent.command_row_id == cmd.id)
        .order_by(CommandEvent.ts.asc())
        .all()
    )
    return {
        "command_id": cmd.command_id,
        "plc_name": cmd.plc_name,
        "datapoint_id": cmd.datapoint_id,
        "kind": cmd.kind,
        "payload": cmd.payload,
        "status": cmd.status,
        "attempts": cmd.attempts,
        "error_message": cmd.error_message,
        "created_at": cmd.created_at,
        "updated_at": cmd.updated_at,
        "events": [
            {"ts": e.ts, "status": e.status, "message": e.message, "meta": e.meta}
            for e in events
        ],
    }


@router.post("/{command_id}/cancel")
def cancel_command(
    command_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    _perm=Depends(require_permission("command:read")),
):
    cmd = db.query(Command).filter(Command.command_id == command_id).one_or_none()
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")
    if cmd.status != "queued":
        return {"status": cmd.status}
    cmd.status = "cancelled"
    cmd.error_message = "cancelled"
    db.add(cmd)
    db.add(CommandEvent(command_row_id=cmd.id, status="cancelled", message="cancelled", meta={"by": user.username}))
    db.commit()
    return {"status": "cancelled"}
