from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from sunny_scada.api.security import Principal
from sunny_scada.services.auth_service import InvalidToken
from sunny_scada.db.models import AppClient, User

logger = logging.getLogger(__name__)


router = APIRouter(tags=["alarms"])


def _principal_from_token(*, token: str, request_app) -> Principal:
    """Validate JWT and build a Principal.

    Mirrors logic in get_current_principal(), but works for WebSocket handshake.
    """

    auth = request_app.state.auth_service
    SessionLocal = request_app.state.db_sessionmaker

    try:
        payload = auth.decode_access_token_payload(token)
    except InvalidToken:
        raise

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


@router.websocket("/ws/alarms")
async def ws_alarms(websocket: WebSocket):
    await websocket.accept()

    # Handshake: expect auth message first.
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

    perms = principal.permissions
    if ("alarms:read" not in perms) and ("alarms:admin" not in perms) and ("alarms:*" not in perms):
        await websocket.close(code=4403)
        return

    broadcaster = getattr(websocket.app.state, "alarm_broadcaster", None)
    alarm_manager = getattr(websocket.app.state, "alarm_manager", None)
    if not broadcaster or not alarm_manager:
        await websocket.close(code=1011)
        return

    principal_key = principal.actor_key
    await broadcaster.add(websocket, principal_key=principal_key)

    # Snapshot from DB-backed occurrences
    try:
        SessionLocal = websocket.app.state.db_sessionmaker
        with SessionLocal() as db:
            snap = alarm_manager.active_snapshot(db)
        await websocket.send_json({"type": "snapshot", "active": snap, "ts": ""})
    except Exception:
        logger.exception("Failed to send alarm snapshot")

    try:
        while True:
            # We don't currently support client messages beyond auth.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await broadcaster.remove(websocket)
        except Exception:
            pass
