from __future__ import annotations

import datetime as dt
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from sunny_scada.api.security import Principal
from sunny_scada.db.models import AppClient, Command, CommandEvent, User
from sunny_scada.services.auth_service import InvalidToken
from sunny_scada.services.command_log_payload import build_command_log_payload

logger = logging.getLogger(__name__)


router = APIRouter(tags=["commands"])


def _principal_from_token(*, token: str, request_app) -> Principal:
    auth = request_app.state.auth_service
    SessionLocal = request_app.state.db_sessionmaker

    payload = auth.decode_access_token_payload(token)
    prt = str(payload.get("prt") or "user")

    with SessionLocal() as db:
        if prt == "user":
            user_id = int(payload.get("sub"))
            user = db.query(User).filter(User.id == user_id).one_or_none()
            if not user or not user.is_active:
                raise InvalidToken("invalid user")
            perms = auth.user_permissions(db, user)
            return Principal(
                type="user",
                subject=str(user.id),
                user=user,
                username=user.username,
                permissions=perms,
                role_ids=[r.id for r in (user.roles or [])],
            )

        if prt == "app":
            client_id = str(payload.get("sub") or "").strip()
            client = db.query(AppClient).filter(AppClient.id == client_id).one_or_none()
            if not client or not client.is_active:
                raise InvalidToken("invalid client")
            tok_ver = int(payload.get("ver") or 0)
            if tok_ver != int(client.token_version or 0):
                raise InvalidToken("token version mismatch")
            perms = auth.role_permissions(client.role)
            return Principal(
                type="app",
                subject=client.id,
                app_client=client,
                client_name=client.name,
                permissions=perms,
                role_ids=[client.role_id] if client.role_id else [],
            )

    raise InvalidToken("unsupported principal")


@router.websocket("/ws/commands")
async def ws_commands(websocket: WebSocket):
    await websocket.accept()

    try:
        first = await websocket.receive_text()
        msg = json.loads(first)
    except Exception:
        await websocket.close(code=4401)
        return

    if not isinstance(msg, dict) or msg.get("type") != "auth" or not msg.get("access_token"):
        await websocket.close(code=4401)
        return

    token = str(msg.get("access_token"))
    try:
        principal = _principal_from_token(token=token, request_app=websocket.app)
    except Exception:
        await websocket.close(code=4401)
        return

    perms = principal.permissions or set()
    if (
        ("command:read" not in perms)
        and ("command:write" not in perms)
        and ("command:*" not in perms)
    ):
        await websocket.close(code=4403)
        return

    broadcaster = getattr(websocket.app.state, "command_broadcaster", None)
    if not broadcaster:
        await websocket.close(code=1011)
        return

    principal_key = principal.actor_key
    await broadcaster.add(websocket, principal_key=principal_key)

    try:
        SessionLocal = websocket.app.state.db_sessionmaker
        with SessionLocal() as db:
            rows = (
                db.query(CommandEvent, Command)
                .join(Command, CommandEvent.command_row_id == Command.id)
                .order_by(CommandEvent.ts.desc())
                .limit(100)
                .all()
            )
            items = [build_command_log_payload(cmd, evt) for evt, cmd in reversed(rows)]

        await websocket.send_json(
            {
                "type": "snapshot",
                "channel": "commands",
                "items": items,
                "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
        )
    except Exception:
        logger.exception("Failed to send command websocket snapshot")

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await broadcaster.remove(websocket)
        except Exception:
            pass
