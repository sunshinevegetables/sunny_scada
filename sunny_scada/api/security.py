from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Literal, Optional

from sunny_scada.db.models import AppClient, User


@dataclass(slots=True)
class Principal:
    """Authenticated principal for a request.

    This unifies identity for both human users and non-interactive application clients.
    """

    type: Literal["user", "app"]
    subject: str
    permissions: set[str] = field(default_factory=set)
    role_ids: list[int] = field(default_factory=list)

    user: Optional[User] = None
    app_client: Optional[AppClient] = None

    username: Optional[str] = None
    client_name: Optional[str] = None

    @property
    def actor_key(self) -> str:
        # Stable identifier used for rate limits/auditing.
        return f"{self.type}:{self.subject}"


def ip_in_cidrs(ip: str, cidrs: list[str]) -> bool:
    """Return True if `ip` is contained in any CIDR/IP string in `cidrs`."""
    try:
        ip_obj = ipaddress.ip_address(ip)
    except Exception:
        return False

    for raw in cidrs or []:
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            if "/" in raw:
                net = ipaddress.ip_network(raw, strict=False)
                if ip_obj in net:
                    return True
            else:
                if ip_obj == ipaddress.ip_address(raw):
                    return True
        except Exception:
            # Ignore malformed entries.
            continue
    return False


def is_path_allowlisted(path: str, *, env: str) -> bool:
    """Decide if the path should bypass auth enforcement.

    Keep this list minimal. Static assets are intentionally allowed because browsers
    don't send Authorization headers for them by default.
    """

    path = (path or "/").strip() or "/"

    # Static / UI
    if path == "/" or path.startswith("/static") or path.startswith("/frontend"):
        return True
    if path.startswith("/scripts") or path.startswith("/styles") or path.startswith("/images") or path.startswith("/sounds") or path.startswith("/pages"):
        return True
    if path.startswith("/admin-panel"):
        # /admin-panel + /admin-panel/login are UI entrypoints
        return True
    if path == "/favicon.ico":
        return True

    # Auth/token issuance
    if path == "/auth/login" or path == "/auth/refresh" or path == "/oauth/token":
        return True

    # Health endpoint (minimal)
    if path == "/health":
        return True

    # WebSocket endpoints (auth is done inside the connection after upgrade)
    if path.startswith("/ws/"):
        return True

    # Swagger docs only in dev
    if env.lower() in ("dev", "development", "local"):
        if path in ("/docs", "/openapi.json", "/redoc"):
            return True

    return False
