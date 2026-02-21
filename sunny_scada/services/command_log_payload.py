from __future__ import annotations

import datetime as dt
from typing import Optional

from sunny_scada.db.models import Command, CommandEvent


def _iso(ts: Optional[dt.datetime]) -> Optional[str]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.timezone.utc).isoformat()
    return ts.isoformat()


def build_command_log_payload(cmd: Command, event: Optional[CommandEvent] = None) -> dict:
    cmd_payload = cmd.payload or {}
    username = cmd.user.username if cmd.user else "System"
    
    payload = {
        "type": "command_log",
        "command": {
            "command_id": cmd.command_id,
            "time": _iso(cmd.created_at),
            "plc": cmd.plc_name,
            "container": cmd_payload.get("equipment_label", cmd.plc_name),
            "equipment": cmd_payload.get("equipment_label", "Unknown"),
            "data_point_label": cmd_payload.get("datapoint_label", cmd.datapoint_id),
            "bit_label": cmd_payload.get("bit_label", None),
            "bit": cmd_payload.get("bit"),
            "value": cmd_payload.get("value"),
            "status": cmd.status,
            "attempts": int(cmd.attempts or 0),
            "username": username,
            "client_ip": cmd.client_ip or "Unknown",
            "error_message": cmd.error_message,
        },
        "event": None,
    }

    if event is not None:
        payload["event"] = {
            "ts": _iso(event.ts),
            "status": event.status,
            "message": event.message,
        }

    return payload
