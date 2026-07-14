"""
Audit logging middleware — Phase 8.

Captures every mutating API call (POST/PATCH/DELETE) that touches /api/ routes.
Writes to audit_log after the response is sent so it never blocks the request.
"""
from __future__ import annotations

import time
from collections.abc import Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

log = structlog.get_logger(__name__)

_MUTATION_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = int((time.monotonic() - start) * 1000)

        if request.method in _MUTATION_METHODS and request.url.path.startswith("/api/"):
            try:
                _write_audit_log(request, response.status_code, duration_ms)
            except Exception:
                log.exception("audit_middleware.write_failed", path=request.url.path)

        return response


def _write_audit_log(request: Request, status_code: int, duration_ms: int) -> None:
    from jose import JWTError, jwt

    from app.api.routes.auth import _ALGORITHM, _COOKIE_NAME, _get_secret

    token = request.cookies.get(_COOKIE_NAME)
    user_email = "anonymous"
    if token:
        try:
            payload = jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM])
            user_email = payload.get("sub", "anonymous")
        except JWTError:
            pass

    log.info(
        "api.mutation",
        method=request.method,
        path=request.url.path,
        status=status_code,
        user=user_email,
        duration_ms=duration_ms,
        ip=request.client.host if request.client else None,
    )
