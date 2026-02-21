from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sunny_scada.api.deps import (
    get_auth_service,
    get_audit_service,
    get_db,
    get_current_principal,
    get_current_user,
    get_rate_limiter,
)
from sunny_scada.api.security import Principal
from sunny_scada.services.audit_service import AuditService
from sunny_scada.services.auth_service import AuthService, InvalidCredentials, InvalidToken, UserLocked


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=300)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10)


class TokenResponse(BaseModel):
    token_type: str = "bearer"
    access_token: str
    refresh_token: str
    access_expires_at: dt.datetime
    refresh_expires_at: dt.datetime


class MeResponse(BaseModel):
    id: int
    username: str
    permissions: list[str]
    roles: list[str]


@router.post("/login", response_model=TokenResponse)
def login(
    req: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    audit: AuditService = Depends(get_audit_service),
    limiter=Depends(get_rate_limiter),
):
    ip = request.client.host if request.client else "unknown"
    # Basic brute-force throttle (in addition to DB lockout).
    lim = limiter.allow(f"login:{ip}:{req.username}", limit=20, window_s=60)
    if not lim.allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    try:
        tokens = auth.authenticate(db, username=req.username, password=req.password)
    except UserLocked as e:
        try:
            audit.log(db, action="auth.login.locked", user_id=None, client_ip=ip, resource=req.username)
        except Exception:
            pass
        # Keep response minimal (avoid disclosing too much)
        raise HTTPException(status_code=423, detail=str(e))
    except InvalidCredentials:
        try:
            audit.log(db, action="auth.login.failure", user_id=None, client_ip=ip, resource=req.username)
        except Exception:
            pass

        raise HTTPException(status_code=401, detail="Invalid username or password")

    try:
        audit.log(db, action="auth.login.success", user_id=int(auth.decode_access_token(tokens.access_token)), client_ip=ip, resource=req.username)
    except Exception:
        pass

    # Audit is handled in config/admin endpoints in Cycle 1; auth auditing is added in Cycle 2.
    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        access_expires_at=tokens.access_expires_at,
        refresh_expires_at=tokens.refresh_expires_at,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    req: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    audit: AuditService = Depends(get_audit_service),
    limiter=Depends(get_rate_limiter),
):
    ip = request.client.host if request.client else "unknown"
    # Rate limit refresh to prevent token spray
    lim = limiter.allow(f"refresh:{ip}", limit=60, window_s=60)
    if not lim.allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    try:
        tokens = auth.refresh(db, refresh_token=req.refresh_token)
    except InvalidToken:
        try:
            audit.log(db, action="auth.refresh.failure", user_id=None, client_ip=ip)
        except Exception:
            pass

        raise HTTPException(status_code=401, detail="Invalid refresh token")

    try:
        audit.log(db, action="auth.refresh.success", user_id=int(auth.decode_access_token(tokens.access_token)), client_ip=ip)
    except Exception:
        pass

    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        access_expires_at=tokens.access_expires_at,
        refresh_expires_at=tokens.refresh_expires_at,
    )


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


@router.post("/logout")
def logout(
    req: LogoutRequest,
    request: Request,
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    principal: Principal = Depends(get_current_principal),
    audit: AuditService = Depends(get_audit_service),
):
    auth.logout(db, refresh_token=req.refresh_token)
    ip = request.client.host if request.client else "unknown"
    try:
        audit.log(
            db,
            action="auth.logout",
            user_id=principal.user.id if principal.user else None,
            client_ip=ip,
            metadata={"actor": principal.actor_key},
        )
    except Exception:
        pass
    return {"status": "ok"}


@router.get("/me", response_model=MeResponse)
def me(
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    user=Depends(get_current_user),
):
    perms = sorted(auth.user_permissions(db, user))
    return MeResponse(
        id=user.id,
        username=user.username,
        roles=[r.name for r in user.roles or []],
        permissions=perms,
    )
