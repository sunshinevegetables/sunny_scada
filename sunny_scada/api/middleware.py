from __future__ import annotations

import hashlib

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from sunny_scada.api.security import Principal, ip_in_cidrs, is_path_allowlisted
from sunny_scada.db.models import AppClient, User
from sunny_scada.services.auth_service import InvalidToken


class AuthEnforcementMiddleware(BaseHTTPMiddleware):
    """Default-deny auth enforcement for the API.

    This middleware validates Bearer access tokens for all non-allowlisted paths.
    On success, it attaches a `Principal` to `request.state.principal` for reuse
    in FastAPI dependencies.
    """

    async def dispatch(self, request: Request, call_next):
        settings = getattr(request.app.state, "settings", None)
        if not settings or not getattr(settings, "auth_enabled", True):
            return await call_next(request)

        path = request.url.path
        # Allow CORS preflight to pass through (actual endpoints still require auth).
        if request.method.upper() == "OPTIONS":
            return await call_next(request)
        if is_path_allowlisted(path, env=getattr(settings, "env", "prod")):
            return await call_next(request)

        # Require Authorization: Bearer <token>
        raw = (request.headers.get("authorization") or "").strip()
        if not raw.lower().startswith("bearer "):
            from starlette.responses import JSONResponse

            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        token = raw.split(" ", 1)[1].strip()
        if not token:
            from starlette.responses import JSONResponse

            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        auth = request.app.state.auth_service
        SessionLocal = request.app.state.db_sessionmaker
        db = SessionLocal()  # type: ignore
        try:
            try:
                payload = auth.decode_access_token_payload(token)
            except InvalidToken:
                from starlette.responses import JSONResponse

                return JSONResponse({"detail": "Invalid token"}, status_code=401)
            prt = str(payload.get("prt") or "user")

            if prt == "user":
                try:
                    user_id = int(payload.get("sub"))
                except Exception:
                    from starlette.responses import JSONResponse

                    return JSONResponse({"detail": "Invalid token"}, status_code=401)
                user = db.query(User).filter(User.id == user_id).one_or_none()
                if not user or not user.is_active:
                    from starlette.responses import JSONResponse

                    return JSONResponse({"detail": "Invalid token"}, status_code=401)
                perms = auth.user_permissions(db, user)
                principal = Principal(
                    type="user",
                    subject=str(user.id),
                    user=user,
                    username=user.username,
                    permissions=perms,
                    role_ids=[r.id for r in (user.roles or [])],
                )

            elif prt == "app":
                client_id = str(payload.get("sub") or "").strip()
                if not client_id:
                    from starlette.responses import JSONResponse

                    return JSONResponse({"detail": "Invalid token"}, status_code=401)

                client = db.query(AppClient).filter(AppClient.id == client_id).one_or_none()
                if not client or not client.is_active:
                    from starlette.responses import JSONResponse

                    return JSONResponse({"detail": "Invalid token"}, status_code=401)

                # Fast revocation (token_version bump on rotate/disable)
                try:
                    tok_ver = int(payload.get("ver") or 0)
                except Exception:
                    tok_ver = -1
                if tok_ver != int(client.token_version or 0):
                    from starlette.responses import JSONResponse

                    return JSONResponse({"detail": "Invalid token"}, status_code=401)

                # Optional client IP allowlist
                if client.allowed_ips:
                    ip = request.client.host if request.client else ""
                    if not ip or not ip_in_cidrs(ip, list(client.allowed_ips or [])):
                        from starlette.responses import JSONResponse

                        return JSONResponse({"detail": "Forbidden"}, status_code=403)

                perms = auth.role_permissions(client.role)
                principal = Principal(
                    type="app",
                    subject=client.id,
                    app_client=client,
                    client_name=client.name,
                    permissions=perms,
                    role_ids=[client.role_id] if client.role_id else [],
                )

            else:
                from starlette.responses import JSONResponse

                return JSONResponse({"detail": "Invalid token"}, status_code=401)

            request.state.principal = principal
            return await call_next(request)
        finally:
            db.close()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        # HSTS only when the effective scheme is HTTPS.
        settings = getattr(request.app.state, "settings", None)
        trusted_proxies = list(getattr(settings, "trusted_proxies", []) or []) if settings else []
        is_https = request.url.scheme == "https"
        xf_proto = (request.headers.get("x-forwarded-proto") or "").strip().lower()
        if not is_https and xf_proto == "https" and trusted_proxies:
            # Only honor X-Forwarded-Proto when TRUSTED_PROXIES is configured.
            src_ip = request.client.host if request.client else ""
            if "*" in trusted_proxies or (src_ip and ip_in_cidrs(src_ip, trusted_proxies)):
                is_https = True

        if is_https:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")

        # Content Security Policy (keep it pragmatic to avoid breaking the built-in UI)
        if request.url.path.startswith("/admin-panel") or "/admin/instruments" in request.url.path:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; base-uri 'self'; frame-ancestors 'none'",
            )

        # cache control for auth/admin/config endpoints
        if (
            request.url.path.startswith("/auth")
            or request.url.path.startswith("/oauth")
            or request.url.path.startswith("/admin")
            or request.url.path.startswith("/config")
            or request.url.path.startswith("/api/watch")
        ):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max(1, int(max_bytes))

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl:
            try:
                if int(cl) > self.max_bytes:
                    from starlette.responses import JSONResponse

                    return JSONResponse({"detail": "Request too large"}, status_code=413)
            except Exception:
                pass
        return await call_next(request)


class WatchRateLimitMiddleware(BaseHTTPMiddleware):
    """Rate-limit /api/watch/* requests for watch-scoped tokens."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/watch/"):
            return await call_next(request)

        if path == "/api/watch/token":
            return await call_next(request)

        settings = getattr(request.app.state, "settings", None)
        limiter = getattr(request.app.state, "rate_limiter", None)
        auth = getattr(request.app.state, "auth_service", None)
        if not settings or limiter is None or auth is None:
            return await call_next(request)

        raw = (request.headers.get("authorization") or "").strip()
        if not raw.lower().startswith("bearer "):
            return await call_next(request)

        token = raw.split(" ", 1)[1].strip()
        if not token:
            return await call_next(request)

        try:
            payload = auth.decode_access_token_payload(token)
        except Exception:
            return await call_next(request)

        if str(payload.get("scope") or "").strip().lower() != "watch":
            return await call_next(request)

        rpm = max(50, min(100, int(getattr(settings, "watch_rate_limit_per_minute", 90))))
        key = "watch:" + hashlib.sha1(token.encode("utf-8")).hexdigest()
        limit = limiter.allow(key, limit=rpm, window_s=60)
        if not limit.allowed:
            from starlette.responses import JSONResponse

            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)

        response = await call_next(request)
        response.headers.setdefault("X-RateLimit-Remaining", str(limit.remaining))
        response.headers.setdefault("X-RateLimit-Reset", str(int(limit.reset_after_s)))
        return response
