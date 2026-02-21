from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sunny_scada.api.deps import get_audit_service, get_auth_service, get_db, get_rate_limiter
from sunny_scada.api.security import ip_in_cidrs
from sunny_scada.db.models import AppClient
from sunny_scada.services.audit_service import AuditService
from sunny_scada.services.auth_service import AuthService


router = APIRouter(prefix="/oauth", tags=["oauth"])

_basic = HTTPBasic(auto_error=False)


class OAuthTokenResponse(BaseModel):
    token_type: str = "bearer"
    access_token: str
    expires_in: int
    scope: str = ""


@router.post("/token", response_model=OAuthTokenResponse, summary="OAuth2 client credentials")
def oauth_token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    basic: HTTPBasicCredentials | None = Depends(_basic),
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    audit: AuditService = Depends(get_audit_service),
    limiter=Depends(get_rate_limiter),
):
    """Issue short-lived access tokens for trusted applications.

    Spec-ish behavior:
      - Accepts either HTTP Basic (client_id/client_secret) or form fields
      - grant_type must be client_credentials
    """

    if (grant_type or "").strip() != "client_credentials":
        raise HTTPException(status_code=400, detail="Unsupported grant_type")

    # Credentials can come from Basic auth or form body.
    if basic:
        client_id = basic.username
        client_secret = basic.password

    client_id = (client_id or "").strip()
    client_secret = (client_secret or "").strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=401, detail="Invalid client credentials")

    # Rate limit by IP + client_id
    ip = request.client.host if request.client else "unknown"
    lim = limiter.allow(f"oauth:{ip}:{client_id}", limit=30, window_s=60)
    if not lim.allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    client = db.query(AppClient).filter(AppClient.id == client_id).one_or_none()
    if not client or not client.is_active:
        try:
            audit.log(db, action="oauth.client_credentials.failure", user_id=None, client_ip=ip, resource=client_id)
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="Invalid client credentials")

    if client.allowed_ips:
        if not ip_in_cidrs(ip, list(client.allowed_ips or [])):
            raise HTTPException(status_code=403, detail="Forbidden")

    if not auth.verify_password(client_secret, client.secret_hash):
        try:
            audit.log(db, action="oauth.client_credentials.failure", user_id=None, client_ip=ip, resource=client_id)
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="Invalid client credentials")

    token, expires_at = auth.issue_app_access_token(
        client_id=client.id,
        client_name=client.name,
        role_id=client.role_id,
        token_version=int(client.token_version or 0),
    )

    # Update last_used_at at issuance time (avoid DB writes on every API request).
    client.last_used_at = dt.datetime.now(dt.timezone.utc)
    db.add(client)
    db.commit()

    perms = sorted(auth.role_permissions(client.role))
    try:
        audit.log(db, action="oauth.client_credentials.success", user_id=None, client_ip=ip, resource=client_id)
    except Exception:
        pass

    return OAuthTokenResponse(
        access_token=token,
        expires_in=max(0, int((expires_at - dt.datetime.now(dt.timezone.utc)).total_seconds())),
        scope=" ".join(perms),
    )
