"""
Global exception handlers — one consistent error envelope for every failure.

Default FastAPI errors are accurate but not actionable. The classic example:
sending a JSON body with `Content-Type: text/plain` (Postman's "raw > Text") yields

    {"detail":[{"type":"model_attributes_type","loc":["body"],
                "msg":"Input should be a valid dictionary or object to extract fields from"}]}

which never mentions the actual problem — the header. Every handler here aims to say
what was wrong, where, what we received, and what to do about it.

Envelope (identical for all errors):

    {
      "error": {
        "code":       "VALIDATION_ERROR",
        "message":    "human summary",
        "details":    [{"field": "...", "problem": "...", "received": "..."}],
        "hint":       "actionable next step",      # omitted when there isn't one
        "request_id": "5f3c…"                      # quote this when reporting a bug
      }
    }
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

if TYPE_CHECKING:
    from collections.abc import Sequence

log = structlog.get_logger(__name__)

_MAX_ECHO = 200          # chars of caller input echoed back
_JSON_CONTENT_TYPES = ("application/json", "application/problem+json")


def _request_id() -> str:
    return uuid.uuid4().hex[:12]


def _envelope(
    code: str,
    message: str,
    *,
    details: Sequence[dict[str, Any]] | None = None,
    hint: str | None = None,
    request_id: str,
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details:
        err["details"] = list(details)
    if hint:
        err["hint"] = hint
    return {"error": err}


def _truncate(value: Any) -> Any:
    """Echo caller input back, but never dump an unbounded body into a response."""
    if isinstance(value, str) and len(value) > _MAX_ECHO:
        return value[:_MAX_ECHO] + f"… ({len(value)} chars total)"
    return value


def _field_path(loc: Sequence[Any]) -> str:
    """('body','items',0,'item_code') -> 'items[0].item_code'"""
    parts: list[str] = []
    for i, seg in enumerate(loc):
        if i == 0 and seg in ("body", "query", "path", "header", "cookie"):
            continue                       # section is reported separately
        if isinstance(seg, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{seg}]"
            else:
                parts.append(f"[{seg}]")
        else:
            parts.append(str(seg))
    return ".".join(parts) or "(whole body)"


def _explain(err: dict[str, Any]) -> str:
    """Turn a pydantic error type into something a human can act on."""
    etype = err.get("type", "")
    msg = err.get("msg", "Invalid value")
    match etype:
        case "missing":
            return "required field is missing"
        case "extra_forbidden":
            return "unknown field — check the spelling against the API document"
        case "string_type":
            return "expected a string (quote the value, e.g. \"400093\")"
        case "int_parsing" | "int_type":
            return "expected a whole number"
        case "decimal_parsing" | "float_parsing":
            return "expected a number, e.g. 18.00"
        case "bool_type" | "bool_parsing":
            return "expected true or false (not \"Y\"/\"N\")"
        case "string_too_long":
            return f"value is too long — {msg.lower()}"
        case "too_short":
            return "the list is empty — send at least one row"
        case "too_long":
            return f"too many rows in one request — {msg.lower()}"
        case _:
            return msg


def _batch_wrapper_hint(errors: Sequence[dict[str, Any]]) -> str | None:
    """
    The /sync endpoints take a batch — {"mappings": [...]}, {"items": [...]},
    {"partners": [...]} — and posting a single bare record is a very easy mistake.
    Pydantic reports only "field 'mappings' is missing", which does not say that the
    body needs wrapping. Detect the shape and spell out the fix.

    Trigger: exactly one `missing` error at ("body", <key>), where the body we received
    is itself an object that does not contain <key> — i.e. a single unwrapped record.
    """
    if len(errors) != 1:
        return None
    err = errors[0]
    loc = tuple(err.get("loc", ()))
    if err.get("type") != "missing" or len(loc) != 2 or loc[0] != "body":
        return None
    key = str(loc[1])
    received = err.get("input")
    if not isinstance(received, dict) or key in received:
        return None
    return (
        f"This endpoint takes a batch, not a single record. "
        f'Wrap your object in {{"{key}": [ ... ]}} — for example '
        f'{{"{key}": [{{ ...your fields... }}]}}. Up to 2000 rows per request.'
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach every handler. Called once from app.main."""

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = _request_id()
        raw = exc.errors()

        # ── Special case: body arrived as text, not parsed JSON ────────────────
        # Pydantic reports this as model_attributes_type at loc == ("body",).
        # The real cause is almost always a missing/incorrect Content-Type.
        content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
        body_not_object = any(
            e.get("type") in ("model_attributes_type", "dict_type", "list_type")
            and tuple(e.get("loc", ())) == ("body",)
            for e in raw
        )
        if body_not_object and content_type not in _JSON_CONTENT_TYPES:
            log.warning(
                "api.bad_content_type", request_id=rid,
                path=request.url.path, content_type=content_type or "(none)",
            )
            return JSONResponse(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                content=_envelope(
                    "UNSUPPORTED_MEDIA_TYPE",
                    (
                        f"The request body was received as plain text, not JSON, because "
                        f"Content-Type is {content_type or 'missing'!r}."
                    ),
                    details=[{
                        "field": "Content-Type",
                        "problem": "must be application/json",
                        "received": content_type or "(no Content-Type header)",
                    }],
                    hint=(
                        "In Postman: Body tab -> raw -> change the dropdown on the right "
                        "from Text to JSON. With curl add: -H 'Content-Type: application/json'."
                    ),
                    request_id=rid,
                ),
            )

        # ── Normal field-level validation errors ──────────────────────────────
        details = [
            {
                "field": _field_path(e.get("loc", ())),
                "in": (e.get("loc") or ["body"])[0],
                "problem": _explain(e),
                "received": _truncate(e.get("input")),
            }
            for e in raw
        ]
        n = len(details)
        hint = _batch_wrapper_hint(raw) or "Compare the payload against docs/sap-master-data-api.md."
        log.warning("api.validation_error", request_id=rid, path=request.url.path, count=n)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_envelope(
                "VALIDATION_ERROR",
                f"{n} field{'s' if n != 1 else ''} failed validation.",
                details=details,
                hint=hint,
                request_id=rid,
            ),
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        rid = _request_id()
        detail = str(getattr(exc, "orig", exc))
        log.error("api.integrity_error", request_id=rid, path=request.url.path, error=detail)

        low = detail.lower()
        if "unique" in low:
            code, msg, hint = (
                "DUPLICATE",
                "A record with this key already exists.",
                "Use the /sync endpoint — it updates existing rows instead of failing.",
            )
        elif "foreign key" in low:
            code, msg, hint = (
                "REFERENCE_NOT_FOUND",
                "This record references something that does not exist yet.",
                "Sync Item_master before SKU_Mapping — see the ordering in section 11 of the API document.",
            )
        else:
            code, msg, hint = ("CONSTRAINT_VIOLATION", "The database rejected this write.", None)

        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_envelope(code, msg, details=[{"database": _truncate(detail)}],
                              hint=hint, request_id=rid),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        rid = _request_id()
        codes = {
            400: "BAD_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN",
            404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT",
            415: "UNSUPPORTED_MEDIA_TYPE", 422: "VALIDATION_ERROR",
        }
        # Webhook endpoints authenticate with an `api-key` header, not a Bearer
        # token — telling a partner to call /auth/login sends them down a path that
        # cannot work for them (they have no user account).
        is_webhook = request.url.path.startswith("/api/webhooks/")
        hints = {
            401: (
                "Send your webhook key as the 'api-key' header. It must match the "
                "secret configured for this partner; contact us if you need it reissued."
                if is_webhook else
                "Send 'Authorization: Bearer <access_token>'. Tokens expire after 8 hours — "
                "call POST /auth/login again."
            ),
            405: "Check the HTTP method against the API document.",
        }
        # A route may raise HTTPException(detail={"immutable_fields": [...]}) to report
        # structured problems. Render those as `details` rather than str()-ing the dict
        # into the message, which produces unreadable Python-repr output.
        detail = exc.detail
        code = codes.get(exc.status_code, "HTTP_ERROR")
        message: str = str(detail)
        details: list[dict[str, Any]] | None = None
        hint = hints.get(exc.status_code)

        if isinstance(detail, dict) and "immutable_fields" in detail:
            details = detail["immutable_fields"]
            code = "IMMUTABLE_FIELD"
            n = len(details)
            message = (
                f"{n} field{'s' if n != 1 else ''} cannot be changed by this endpoint."
            )
            hint = "Send the current value, or omit the field entirely."
        elif isinstance(detail, dict | list):
            details = detail if isinstance(detail, list) else [detail]
            message = "Request rejected."

        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code, message, details=details, hint=hint, request_id=rid),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """
        Last resort. The traceback is logged server-side against request_id but never
        returned — internal paths and SQL must not leak to an API consumer.
        """
        rid = _request_id()
        log.exception(
            "api.unhandled_error", request_id=rid,
            path=request.url.path, method=request.method, error=str(exc),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(
                "INTERNAL_ERROR",
                "The server hit an unexpected error. It has been logged.",
                hint=f"Quote request_id '{rid}' when reporting this.",
                request_id=rid,
            ),
        )
