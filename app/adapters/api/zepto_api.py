"""
Zepto API adapter — polls Zepto Silk Route API for new PO events.

Pull endpoint: GET /api/v1/external/po/events
Auth:          X-Client-Id + X-Client-Secret headers (NOT Bearer token)
QA host:       https://silkroute.zeptonow.dev
Prod host:     https://silkroute.zepto.co.in

Key rules from API contract v12 (cross-referenced with _archive/backend_old/app/services/zepto.py):
  - Rate limit: 60 RPM per clientId
  - Max days=45, max pageSize=20
  - Use eventId as the idempotency key — stored as raw_message.external_id
  - All timestamps are UTC
  - Quantities in pieces (PC) — case-size conversion is our job
  - No Retry-After header on 429 — use fixed backoff

Re-implemented from _archive/backend_old/app/services/zepto.py
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.adapters.api.base import BaseApiAdapter, FetchedPO
from app.config import get_settings

log = structlog.get_logger(__name__)

_QA_BASE = "https://silkroute.zeptonow.dev"
_PROD_BASE = "https://silkroute.zepto.co.in"

_RPM_LIMIT = 60
_MIN_REQUEST_INTERVAL = 60 / _RPM_LIMIT  # seconds between requests
_MAX_RETRIES = 3
_RETRY_BACKOFF = (5, 15, 60)  # seconds between retries on 5xx


class ZeptoApiAdapter(BaseApiAdapter):
    """
    Polls Zepto for new PO events. Called synchronously by the RQ ingest worker.

    Watermark tracking:
      TradingPartner.api_config["last_fetched_at"] (ISO-8601 UTC string)
      Updated after a successful full page sweep.

    Pagination:
      Zepto's API returns pages; we iterate until data.hasNext == false or
      we hit max_pages. This protects against infinite loops on large backlogs.
    """

    @property
    def partner_code(self) -> str:
        return "ZEPTO"

    def __init__(self) -> None:
        s = get_settings()
        self._client_id = s.zepto_client_id
        self._client_secret = s.zepto_client_secret
        default_base = _QA_BASE if s.environment != "production" else _PROD_BASE
        self._base_url = (s.zepto_base_url or default_base).rstrip("/")
        self._last_request_time: float = 0.0

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Client-Id": self._client_id,
            "X-Client-Secret": self._client_secret,
        }
        if idempotency_key:
            h["X-Idempotency-Key"] = idempotency_key
        return h

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    def _rate_limit_wait(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)

    def fetch_new_pos(
        self,
        since: datetime | None = None,
        max_pages: int = 10,
    ) -> list[FetchedPO]:
        """
        Fetch all PO events since `since`, paginating until done or max_pages.
        Returns one FetchedPO per unique eventId.
        """
        days = _since_to_days(since)
        results: list[FetchedPO] = []
        seen_event_ids: set[str] = set()
        page = 1

        while page <= max_pages:
            page_data = self._fetch_page(days=days, page=page, page_size=20)
            if page_data is None:
                break

            purchase_orders: list[dict[str, Any]] = (
                page_data.get("purchaseOrders") or []
            )

            for po in purchase_orders:
                event_id = str(po.get("eventId") or "")
                if not event_id or event_id in seen_event_ids:
                    continue
                seen_event_ids.add(event_id)
                results.append(FetchedPO(
                    external_id=event_id,
                    payload=po,
                    received_at=datetime.now(UTC),
                    po_number=str(po.get("purchaseOrderNumber") or ""),
                ))

            has_next = page_data.get("hasNext", False)
            log.debug(
                "zepto.fetch.page",
                page=page,
                pos_this_page=len(purchase_orders),
                has_next=has_next,
            )

            if not has_next:
                break
            page += 1

        log.info("zepto.fetch.done", total=len(results), pages=page - 1, days=days)
        return results

    def _fetch_page(
        self,
        days: int,
        page: int,
        page_size: int,
    ) -> dict[str, Any] | None:
        """Fetch one page from the Zepto PO events endpoint with retry."""
        url = self._url("/api/v1/external/po/events")
        params: dict[str, Any] = {
            "days": min(days, 45),
            "pageSize": min(page_size, 20),
            "pageNumber": page,
            "includeAllPoEvents": "false",
            "includeLineItemDetails": "true",
        }

        for attempt, backoff in enumerate(_RETRY_BACKOFF, start=1):
            self._rate_limit_wait()
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.get(url, params=params, headers=self._headers())
                self._last_request_time = time.monotonic()

                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", backoff))
                    log.warning("zepto.fetch.rate_limited", page=page, wait=wait, attempt=attempt)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                body = resp.json()
                # Response shape: {"data": {"purchaseOrders": [...], "hasNext": bool}}
                return _unwrap_zepto(body)

            except httpx.HTTPStatusError as exc:
                log.error(
                    "zepto.fetch.http_error",
                    status_code=exc.response.status_code,
                    page=page,
                    attempt=attempt,
                    error=_zepto_error_msg(exc.response.text),
                )
                if attempt < _MAX_RETRIES and exc.response.status_code >= 500:
                    time.sleep(backoff)
                    continue
                return None

            except Exception as exc:
                log.error("zepto.fetch.error", error=str(exc), page=page, attempt=attempt)
                if attempt < _MAX_RETRIES:
                    time.sleep(backoff)
                    continue
                return None

        return None

    def send_asn(
        self,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        POST an ASN/invoice to Zepto. Returns asnNumber on success.
        No update API — to change an ASN: cancel + recreate with a new invoiceNumber.

        Re-implemented from _archive/backend_old/app/services/zepto.py:create_asn
        """
        import uuid as _uuid
        url = self._url("/api/v1/external/asn")
        key = idempotency_key or str(_uuid.uuid4())

        for attempt, backoff in enumerate(_RETRY_BACKOFF, start=1):
            self._rate_limit_wait()
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.post(url, json=payload, headers=self._headers(key))
                self._last_request_time = time.monotonic()

                resp.raise_for_status()
                data = _unwrap_zepto(resp.json()) or {}
                asn_number = data.get("data", {}).get("asnNumber")
                po_number = payload.get("purchaseOrderDetails", {}).get("purchaseOrderNumber")
                log.info("zepto.asn.sent", po_number=po_number, asn_number=asn_number)
                return {
                    "success": True,
                    "status_code": resp.status_code,
                    "data": data,
                    "asn_number": asn_number,
                }

            except httpx.HTTPStatusError as exc:
                log.error(
                    "zepto.asn.http_error",
                    status_code=exc.response.status_code,
                    attempt=attempt,
                    error=_zepto_error_msg(exc.response.text),
                )
                if attempt < _MAX_RETRIES and exc.response.status_code >= 500:
                    time.sleep(backoff)
                    continue
                return {
                    "success": False,
                    "status_code": exc.response.status_code,
                    "error": _zepto_error_msg(exc.response.text),
                }

            except Exception as exc:
                log.error("zepto.asn.error", error=str(exc), attempt=attempt)
                if attempt < _MAX_RETRIES:
                    time.sleep(backoff)
                    continue
                return {"success": False, "error": str(exc)}

        return {"success": False, "error": "Max retries exceeded"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _since_to_days(since: datetime | None) -> int:
    """Convert a watermark datetime to the `days` param Zepto expects (1–45)."""
    if since is None:
        return 7  # default: look back 7 days
    now = datetime.now(UTC)
    delta = now - since.replace(tzinfo=UTC) if since.tzinfo is None else now - since
    days = max(1, delta.days + 1)  # +1 for partial day
    return min(days, 45)


def _unwrap_zepto(raw: Any) -> dict[str, Any] | None:
    """
    Zepto response shape: {"data": {"purchaseOrders": [...], "hasNext": bool}}
    Return the inner `data` dict.
    """
    if isinstance(raw, dict):
        inner = raw.get("data")
        if isinstance(inner, dict):
            return inner
        return raw
    return None


def _zepto_error_msg(body: str | bytes) -> str:
    import json
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(body)
    except Exception:
        return body
    if isinstance(parsed, dict):
        errors = parsed.get("errors")
        if isinstance(errors, list) and errors:
            msgs = [
                e.get("error") or e.get("message", "")
                for e in errors
                if isinstance(e, dict)
            ]
            msgs = [m for m in msgs if m]
            if msgs:
                return "; ".join(msgs)
        for key in ("message", "error", "detail"):
            if parsed.get(key) and isinstance(parsed[key], str):
                return parsed[key]
        return json.dumps(parsed)
    return str(parsed)
