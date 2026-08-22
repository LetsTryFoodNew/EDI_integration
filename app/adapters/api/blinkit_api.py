"""
Blinkit outbound API client — sends ACKs and ASNs to Blinkit's partner portal.

PO flow:  Blinkit → POST /api/webhooks/blinkit  (inbound, handled by webhooks route)
          Our system → POST /webhook/public/v1/po/acknowledgement  (outbound ACK)
          Our system → POST /webhook/public/v1/asn                 (outbound ASN)

Auth:     api-key header + x-vendor-id header
Pre-prod: https://dev.partnersbiz.com
Prod:     https://api.partnersbiz.com

Blinkit has NO inbound pull API — POs arrive only via webhook push.
This module handles OUTBOUND calls only.

Re-implemented from _archive/backend_old/app/services/blinkit.py
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.adapters.outbound.blinkit_asn import interpret_asn_response
from app.config import get_settings

log = structlog.get_logger(__name__)

_PREPROD_BASE = "https://dev.partnersbiz.com"
_PROD_BASE = "https://api.partnersbiz.com"

_MAX_RETRIES = 3
_RETRY_BACKOFF = (1, 5, 30)  # seconds between retries
# Cap on a flattened error string. Long enough for several field errors, short
# enough that error_message stays readable in the outbound tab.
_ERROR_MAX_CHARS = 1500


class BlinkitApiAdapter:
    """
    Outbound Blinkit API client.
    All methods are synchronous (called from RQ workers).
    """

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.blinkit_api_key
        self._vendor_id = s.blinkit_vendor_id
        self._base_url = (
            s.blinkit_base_url
            or (_PREPROD_BASE if s.environment != "production" else _PROD_BASE)
        ).rstrip("/")
        self._path_po_ack = s.blinkit_path_po_ack
        self._path_asn = s.blinkit_path_asn

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "api-key": self._api_key,
            "x-vendor-id": str(self._vendor_id),
        }
        if idempotency_key:
            h["X-Idempotency-Key"] = idempotency_key
        return h

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    def acknowledge_po(
        self,
        po_number: str,
        status: str = "processing",
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        Send a PO acknowledgement to Blinkit.
        status: processing | accepted | partially_accepted | rejected
        Send "processing" immediately after receipt; final status once processed.

        Re-implemented from _archive/backend_old/app/services/blinkit.py:acknowledge_po
        """
        url = self._url(self._path_po_ack)
        payload = {
            "success": status != "rejected",
            "message": f"PO {po_number} acknowledged — {status}",
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data": {
                "po_status": status.upper(),
                "po_number": po_number,
                "errors": errors or [],
                "warnings": warnings or [],
            },
        }

        for attempt, backoff in enumerate(_RETRY_BACKOFF, start=1):
            try:
                with httpx.Client(timeout=15) as client:
                    resp = client.post(url, json=payload, headers=self._headers(idempotency_key))

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", backoff))
                    log.warning(
                        "blinkit.ack.rate_limited",
                        po_number=po_number,
                        retry_after=retry_after,
                        attempt=attempt,
                    )
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                log.info("blinkit.ack.sent", po_number=po_number, status=status)
                return {"success": True, "status_code": resp.status_code, "data": resp.json()}

            except httpx.HTTPStatusError as exc:
                log.error(
                    "blinkit.ack.http_error",
                    po_number=po_number,
                    status_code=exc.response.status_code,
                    attempt=attempt,
                    error=_parse_blinkit_error(exc.response.text),
                )
                if attempt < _MAX_RETRIES and exc.response.status_code >= 500:
                    time.sleep(backoff)
                    continue
                return {
                    "success": False,
                    "status_code": exc.response.status_code,
                    "error": _parse_blinkit_error(exc.response.text),
                }

            except Exception as exc:
                log.error("blinkit.ack.error", po_number=po_number, error=str(exc), attempt=attempt)
                if attempt < _MAX_RETRIES:
                    time.sleep(backoff)
                    continue
                return {"success": False, "error": str(exc)}

        return {"success": False, "error": "Max retries exceeded"}

    def send_asn(
        self,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        POST an ASN/invoice to Blinkit.
        Returns asn_id on success.

        Re-implemented from _archive/backend_old/app/services/blinkit.py:create_asn
        """
        url = self._url(self._path_asn)

        for attempt, backoff in enumerate(_RETRY_BACKOFF, start=1):
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.post(url, json=payload, headers=self._headers(idempotency_key))

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", backoff))
                    log.warning("blinkit.asn.rate_limited", retry_after=retry_after, attempt=attempt)
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()

                # A 2xx says the request was processed, not that the ASN was accepted.
                # The contract is explicit that full acceptance, partial acceptance and
                # rejection all return 2xx, and its own example pairs
                # "successful": true with "asn_sync_status": "REJECTED". Reading the
                # status line alone would mark a rejected ASN as delivered, and the
                # first anyone would hear of it is a truck turned away at the DC.
                ack = interpret_asn_response(data)
                if ack.accepted:
                    log.info(
                        "blinkit.asn.accepted",
                        po_number=payload.get("po_number"),
                        invoice_number=payload.get("invoice_number"),
                        asn_id=ack.asn_id,
                        status=ack.status,
                        warnings=len(ack.warnings),
                    )
                    return {
                        "success": True, "status_code": resp.status_code,
                        "data": data, "asn_id": ack.asn_id, "ack": ack,
                    }

                log.error(
                    "blinkit.asn.rejected",
                    po_number=payload.get("po_number"),
                    invoice_number=payload.get("invoice_number"),
                    status=ack.status,
                    http_status=resp.status_code,
                    asn_errors=[e.get("code") for e in ack.asn_errors],
                    item_errors=[e.get("code") for e in ack.item_errors],
                )
                # Not retried: a rejection is a decision about the content, so resending
                # the identical body would be rejected identically.
                return {
                    "success": False, "status_code": resp.status_code,
                    "data": data, "ack": ack, "error": ack.summary,
                }

            except httpx.HTTPStatusError as exc:
                log.error(
                    "blinkit.asn.http_error",
                    status_code=exc.response.status_code,
                    attempt=attempt,
                    error=_parse_blinkit_error(exc.response.text),
                )
                if attempt < _MAX_RETRIES and exc.response.status_code >= 500:
                    time.sleep(backoff)
                    continue
                return {
                    "success": False,
                    "status_code": exc.response.status_code,
                    "error": _parse_blinkit_error(exc.response.text),
                }

            except Exception as exc:
                log.error("blinkit.asn.error", error=str(exc), attempt=attempt)
                if attempt < _MAX_RETRIES:
                    time.sleep(backoff)
                    continue
                return {"success": False, "error": str(exc)}

        return {"success": False, "error": "Max retries exceeded"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_blinkit_error(body: str | bytes) -> str:
    """
    Flatten a Blinkit 4xx body into one diagnosable line.

    The summary field alone is useless: a rejected ASN comes back as
    `{"message": "Validation failed", ...}` with the *actual* fault in a nested
    errors array. Keeping only the summary left "Validation failed" in
    error_message and threw away which field Blinkit objected to, so the outbound
    tab said a send had failed without saying what to fix. Returns the summary
    followed by every error entry found, so the retry that follows is
    diagnosable from the log alone.
    """
    import json

    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(body)
    except Exception:
        return body[:_ERROR_MAX_CHARS]
    if not isinstance(parsed, dict):
        return str(parsed)[:_ERROR_MAX_CHARS]

    summary = ""
    for key in ("message", "error", "detail", "description"):
        if parsed.get(key):
            summary = str(parsed[key])
            break

    details = [_format_error_entry(e) for e in _collect_error_entries(parsed)]
    details = [d for d in details if d]

    if not summary and not details:
        return json.dumps(parsed)[:_ERROR_MAX_CHARS]
    if not details:
        return summary[:_ERROR_MAX_CHARS]
    return f"{summary or 'Rejected'} | " + "; ".join(details)[:_ERROR_MAX_CHARS]


def _collect_error_entries(parsed: dict[str, Any]) -> list[Any]:
    """Gather error rows from wherever Blinkit puts them on a 4xx."""
    nested = parsed.get("data")
    containers: list[Any] = [parsed, nested if isinstance(nested, dict) else {}]
    entries: list[Any] = []
    for container in containers:
        for key in ("errors", "validationErrors", "fieldErrors", "details"):
            found = container.get(key)
            if isinstance(found, list):
                entries.extend(found)
    return entries


def _format_error_entry(entry: Any) -> str:
    """One error row as `[code@level] field: message` — whichever parts are present."""
    if not isinstance(entry, dict):
        return str(entry)

    code = entry.get("code") or entry.get("error_code") or entry.get("errorCode")
    level = entry.get("level")
    tag = f"{code}@{level}" if code and level else (str(code or level or "") or "")

    field = entry.get("field") or entry.get("item_id") or entry.get("path")
    message = (
        entry.get("message")
        or entry.get("description")
        or entry.get("detail")
        or entry.get("reason")
        or ""
    )
    if not message and not field:
        import json

        return json.dumps(entry)

    parts = [p for p in (f"[{tag}]" if tag else "", f"{field}:" if field else "", str(message)) if p]
    return " ".join(parts)
