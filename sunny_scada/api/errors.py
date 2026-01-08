from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(_, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "error": {"type": "http", "status": exc.status_code, "detail": exc.detail},
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Validation error",
                "error": {"type": "validation", "issues": exc.errors()},
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_, exc: Exception):
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "error": {"type": "internal"},
            },
        )
