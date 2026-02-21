from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Connection:
    websocket: WebSocket
    principal_key: str


class AlarmBroadcaster:
    """Thread-safe in-process WebSocket broadcaster.

    Polling threads are not running in the asyncio event loop. We therefore
    schedule sends via call_soon_threadsafe on the loop captured at startup.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._conns: Set[Connection] = set()
        self._lock = asyncio.Lock()

    async def add(self, websocket: WebSocket, *, principal_key: str) -> None:
        async with self._lock:
            self._conns.add(Connection(websocket=websocket, principal_key=principal_key))

    async def remove(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._conns = {c for c in self._conns if c.websocket is not websocket}

    def broadcast(self, payload: Dict[str, Any]) -> None:
        """Broadcast a JSON message to all connected clients."""

        async def _send_all() -> None:
            dead: list[Connection] = []
            async with self._lock:
                conns = list(self._conns)

            for c in conns:
                try:
                    await c.websocket.send_json(payload)
                except Exception:
                    dead.append(c)

            if dead:
                async with self._lock:
                    for d in dead:
                        self._conns.discard(d)

        try:
            self._loop.call_soon_threadsafe(lambda: asyncio.create_task(_send_all()))
        except Exception as e:
            logger.debug("Broadcast scheduling failed: %s", e)
