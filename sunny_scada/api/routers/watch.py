from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from sunny_scada.api.deps import (
    get_auth_service,
    get_current_watch_principal,
    get_db,
    get_watch_service,
)
from sunny_scada.api.security import Principal
from sunny_scada.db.models import User
from sunny_scada.services.auth_service import AuthService, InvalidCredentials, UserLocked
from sunny_scada.services.watch_service import WatchService, iso_utc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watch", tags=["watch"])


class WatchTokenRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=300)


class WatchTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str


class WatchDatapointResult(BaseModel):
    id: int
    label: str
    unit: str
    equipment_name: str


class WatchDatapointsResponse(BaseModel):
    results: list[WatchDatapointResult]


class WatchLatestValue(BaseModel):
    value: float | int | None
    unit: str
    quality: str
    timestamp: str | None


class WatchLatestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: str
    values: dict[str, WatchLatestValue]


@router.post(
    "/token",
    response_model=WatchTokenResponse,
    responses={
        200: {
            "description": "Watch access token",
            "content": {
                "application/json": {
                    "example": {
                        "access_token": "<JWT>",
                        "token_type": "bearer",
                        "expires_at": "2026-02-22T18:30:00Z",
                    }
                }
            },
        },
        401: {"description": "Invalid credentials"},
    },
)
async def issue_watch_token(
    req: WatchTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
):
    started = time.perf_counter()
    ip = request.client.host if request.client else "unknown"

    try:
        tokens = auth.authenticate(db, username=req.username, password=req.password)
    except (InvalidCredentials, UserLocked):
        logger.warning(
            "watch.token.failed",
            extra={"username": req.username, "client_ip": ip},
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user_id = int(auth.decode_access_token(tokens.access_token))
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    settings = request.app.state.settings
    ttl_h = max(24, min(72, int(getattr(settings, "watch_token_ttl_hours", 48))))
    token, expires_at = auth.issue_user_access_token(
        user=user,
        ttl_s=ttl_h * 3600,
        scope="watch",
    )

    logger.info(
        "watch.token.issued",
        extra={"user_id": user_id, "username": req.username, "client_ip": ip, "ttl_h": ttl_h},
    )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if elapsed_ms > 500.0:
        logger.warning(
            "watch.slow_request",
            extra={"path": "/api/watch/token", "duration_ms": round(elapsed_ms, 2), "user_id": user_id},
        )

    return WatchTokenResponse(
        access_token=token,
        token_type="bearer",
        expires_at=iso_utc(expires_at),
    )


@router.get(
    "/datapoints",
    response_model=WatchDatapointsResponse,
    responses={
        200: {
            "description": "Searchable datapoints",
            "content": {
                "application/json": {
                    "example": {
                        "results": [
                            {
                                "id": 101,
                                "label": "Chamber 1 Temp",
                                "unit": "°C",
                                "equipment_name": "Cold Room 1",
                            }
                        ]
                    }
                }
            },
        }
    },
)
async def list_watch_datapoints(
    request: Request,
    q: str | None = Query(default=None),
    equipment_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_watch_principal),
    svc: WatchService = Depends(get_watch_service),
):
    started = time.perf_counter()

    results = svc.list_datapoints(
        db,
        principal,
        q=q,
        equipment_id=equipment_id,
        limit=limit,
    )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if elapsed_ms > 500.0:
        logger.warning(
            "watch.slow_request",
            extra={
                "path": "/api/watch/datapoints",
                "duration_ms": round(elapsed_ms, 2),
                "actor": principal.actor_key,
                "result_count": len(results),
            },
        )

    return WatchDatapointsResponse(results=results)


def _parse_ids_csv(ids: str) -> list[int]:
    out: list[int] = []
    for raw in str(ids or "").split(","):
        part = raw.strip()
        if not part:
            continue
        try:
            val = int(part)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid ids") from exc
        if val <= 0:
            raise HTTPException(status_code=400, detail="Invalid ids")
        if val not in out:
            out.append(val)
    return out


@router.get(
    "/datapoints/latest",
    response_model=WatchLatestResponse,
    responses={
        200: {
            "description": "Latest value snapshot",
            "content": {
                "application/json": {
                    "example": {
                        "ts": "2026-02-21T10:15:00Z",
                        "values": {
                            "1": {
                                "value": 4.6,
                                "unit": "°C",
                                "quality": "good",
                                "timestamp": "2026-02-21T10:14:58Z",
                            },
                            "2": {
                                "value": None,
                                "unit": "bar",
                                "quality": "no_data",
                                "timestamp": None,
                            },
                        },
                    }
                }
            },
        },
        400: {"description": "Invalid ids or too many ids"},
    },
)
async def latest_watch_datapoints(
    request: Request,
    ids: str = Query(..., description="Comma-separated cfg_data_point ids"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_watch_principal),
    svc: WatchService = Depends(get_watch_service),
):
    started = time.perf_counter()

    parsed_ids = _parse_ids_csv(ids)
    if not parsed_ids:
        raise HTTPException(status_code=400, detail="ids is required")
    if len(parsed_ids) > 6:
        raise HTTPException(status_code=400, detail="A maximum of 6 ids is allowed")

    payload = svc.latest_values(db, principal, ids=parsed_ids)

    if payload.get("values") is None:
        payload["values"] = {}

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if elapsed_ms > 500.0:
        logger.warning(
            "watch.slow_request",
            extra={
                "path": "/api/watch/datapoints/latest",
                "duration_ms": round(elapsed_ms, 2),
                "actor": principal.actor_key,
                "requested_ids": len(parsed_ids),
                "returned_ids": len(payload.get("values", {})),
            },
        )

    return WatchLatestResponse.model_validate(payload)
