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

from app.config import get_settings

log = structlog.get_logger(__name__)

_PREPROD_BASE = "https://dev.partnersbiz.com"
_PROD_BASE = "https://api.partnersbiz.com"

_MAX_RETRIES = 3
_RETRY_BACKOFF = (1, 5, 30)  # seconds between retries


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
                asn_id = (
                    data.get("asn_id")
                    or data.get("data", {}).get("asn_id")
                    or data.get("id")
                )
                log.info("blinkit.asn.sent", po_number=payload.get("po_number"), asn_id=asn_id)
                return {"success": True, "status_code": resp.status_code, "data": data, "asn_id": asn_id}

            except httpx.HTTPStatusError as exc:
                log.error(
                    "blinkit.asn.http_error",
                    status_code=exc.response.status_code,
                    attempt=attempt,
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
    import json
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(body)
    except Exception:
        return body
    if isinstance(parsed, dict):
        for key in ("message", "error", "detail", "description"):
            if parsed.get(key):
                return str(parsed[key])
        return json.dumps(parsed)
    return str(parsed)
