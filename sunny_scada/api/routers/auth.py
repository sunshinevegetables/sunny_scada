from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sunny_scada.api.deps import get_auth_service, get_db, get_current_user
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
):
    try:
        tokens = auth.authenticate(db, username=req.username, password=req.password)
    except UserLocked as e:
        # Keep response minimal (avoid disclosing too much)
        from fastapi import HTTPException

        raise HTTPException(status_code=423, detail=str(e))
    except InvalidCredentials:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Invalid username or password")

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
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
):
    try:
        tokens = auth.refresh(db, refresh_token=req.refresh_token)
    except InvalidToken:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Invalid refresh token")

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
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
):
    auth.logout(db, refresh_token=req.refresh_token)
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
