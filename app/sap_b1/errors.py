"""
SAP B1 Service Layer error hierarchy.

B1 error responses look like:
  {"error": {"code": -5006, "message": {"lang": "en-us", "value": "..."}}}
"""
from __future__ import annotations

from typing import Any


class B1ApiError(Exception):
    """Base exception for all SAP B1 Service Layer errors."""

    def __init__(
        self,
        message: str,
        code: int | str = 0,
        http_status: int = 0,
        raw: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.b1_code = code
        self.http_status = http_status
        self.raw = raw or {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.b1_code}, http={self.http_status}, msg={self})"

    @classmethod
    def from_response(cls, http_status: int, body: dict[str, Any]) -> B1ApiError:
        """Parse the standard B1 error envelope."""
        err = body.get("error", {})
        code = err.get("code", 0)
        msg_obj = err.get("message", {})
        message = msg_obj.get("value", str(body)) if isinstance(msg_obj, dict) else str(msg_obj)

        if http_status == 401:
            return B1SessionError(message=message, code=code, http_status=http_status, raw=body)
        if code == -5002 or "posting period" in message.lower():
            return B1ClosedPeriodError(message=message, code=code, http_status=http_status, raw=body)

        return cls(message=message, code=code, http_status=http_status, raw=body)


class B1SessionError(B1ApiError):
    """Authentication failure — session expired or invalid credentials."""


class B1ClosedPeriodError(B1ApiError):
    """Posting period is closed; the document date must be changed."""
