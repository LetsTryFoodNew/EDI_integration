"""
Zepto API adapter — polls Zepto Silk Route API for new PO events.

Pull endpoint: GET /api/v1/external/po/events
Auth:          X-Client-Id + X-Client-Secret headers (NOT Bearer token)
QA host:       https://silkroute.zeptonow.dev
Prod host:     https://silkroute.zepto.co.in

Routing (re-implemented from _archive/backend_old/app/services/zepto.py):
  LOCAL       → Mac → Render proxy (whitelisted static IP) → Zepto QA
                URL: {RENDER_URL}/api/proxy/zepto/{path}
  STAGING     → Render proxy → Zepto QA  (same proxy, different env label)
  PRODUCTION  → Direct to silkroute.zepto.co.in

Why proxy?  Zepto whitelists specific IPs. Local/dev machines have dynamic IPs
that Zepto blocks.  The Render.com deployment at po-integration-backend.onrender.com
has static IPs already whitelisted by Zepto.

Proxy response envelope (HTTP 200 even when Zepto returns 4xx):
  {
    "proxied":     true,
    "status_code": <real Zepto HTTP status>,
    "data":        <full Zepto response body>
  }
  The inner Zepto body is itself: {"data": {"purchaseOrders": [...], "hasNext": bool}}
  So via proxy: raw["data"]["data"]["purchaseOrders"]
  Direct:       raw["data"]["purchaseOrders"]

Key rules from API contract v12:
  - Rate limit: 60 RPM per clientId
  - Max days=45, max pageSize=20
  - eventId is the idempotency key — stored as raw_message.external_id
  - All timestamps are UTC
  - Quantities in pieces (PC) — case-size conversion is our responsibility
  - No Retry-After header on 429 — use fixed backoff
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

# Minimum history requested on every poll — see _since_to_days() for why this floor
# exists rather than trusting the watermark delta. Zepto caps `days` at 45.
_MIN_LOOKBACK_DAYS = 14


class ZeptoApiAdapter(BaseApiAdapter):
    """
    Polls Zepto for new PO events. Called synchronously by the RQ ingest worker.

    Routing:
      environment == "production"  → direct to silkroute.zepto.co.in
      everything else              → via Render proxy (uses whitelisted static IP)

    Watermark tracking:
      TradingPartner.api_config["last_fetched_at"] (ISO-8601 UTC string)
      Updated after a successful full page sweep.

    Pagination:
      Zepto returns pages; we iterate until data.hasNext == false or
      we hit max_pages (protects against infinite loops on large backlogs).
    """

    @property
    def partner_code(self) -> str:
        return "ZEPTO"

    def __init__(self) -> None:
        s = get_settings()
        self._client_id = s.zepto_client_id
        self._client_secret = s.zepto_client_secret
        self._render_url = s.render_url.rstrip("/") if s.render_url else ""
        self._environment = s.environment

        # Choose base URL for direct (non-proxy) calls
        if s.zepto_base_url:
            self._zepto_base = s.zepto_base_url.rstrip("/")
        elif s.environment == "production":
            self._zepto_base = _PROD_BASE
        else:
            self._zepto_base = _QA_BASE

        # Are we routing through the Render proxy?
        self._use_proxy = (s.environment != "production") and bool(self._render_url)

        if self._use_proxy:
            log.info(
                "zepto.adapter.init",
                mode="proxy",
                proxy=self._render_url,
                zepto_target=self._zepto_base,
            )
        else:
            log.info("zepto.adapter.init", mode="direct", target=self._zepto_base)

        self._last_request_time: float = 0.0

    def _build_url(self, path: str) -> str:
        """
        LOCAL/STAGING  → https://po-integration-backend.onrender.com/api/proxy/zepto/api/v1/...
        PRODUCTION     → https://silkroute.zepto.co.in/api/v1/...
        """
        path = path.lstrip("/")
        if self._use_proxy:
            return f"{self._render_url}/api/proxy/zepto/{path}"
        return f"{self._zepto_base}/{path}"

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Client-Id": self._client_id,
            "X-Client-Secret": self._client_secret,
        }
        if idempotency_key:
            h["X-Idempotency-Key"] = idempotency_key
        return h

    def _rate_limit_wait(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)

    # ── Public API ────────────────────────────────────────────────────────────

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
                if page == 1:
                    # Total failure must NOT look like "no new POs" — returning []
                    # here once let the watermark advance past unfetched data while
                    # every request was being rejected. Raise so the workflow records
                    # the error and leaves the watermark alone.
                    raise RuntimeError(
                        "Zepto fetch failed on the first page — see zepto.fetch.* logs"
                    )
                break

            purchase_orders: list[dict[str, Any]] = page_data.get("purchaseOrders") or []

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
                via_proxy=self._use_proxy,
            )

            if not has_next:
                break
            page += 1

        if page > max_pages and page_data is not None and page_data.get("hasNext"):
            # Stopping mid-stream is survivable -- we poll often and dedup on eventId --
            # but it must not be silent. A cap reached quietly is how the last two
            # ingest bugs stayed hidden.
            log.warning(
                "zepto.fetch.truncated",
                max_pages=max_pages,
                fetched=len(results),
                days=days,
            )

        log.info(
            "zepto.fetch.done",
            total=len(results),
            pages=page - 1,
            days=days,
            via_proxy=self._use_proxy,
        )
        return results

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
        url = self._build_url("/api/v1/external/asn")
        key = idempotency_key or str(_uuid.uuid4())

        for attempt, backoff in enumerate(_RETRY_BACKOFF, start=1):
            self._rate_limit_wait()
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.post(url, json=payload, headers=self._headers(key))
                self._last_request_time = time.monotonic()

                body = resp.json()

                # Proxy error check (HTTP 200 but real Zepto error inside)
                proxy_err = _check_proxy_error(body)
                if proxy_err:
                    log.error(
                        "zepto.asn.proxy_error",
                        status_code=proxy_err["status_code"],
                        error=proxy_err["error"],
                        attempt=attempt,
                    )
                    return {"success": False, **proxy_err}

                resp.raise_for_status()
                data = _unwrap(body)
                po_number = payload.get("purchaseOrderDetails", {}).get("purchaseOrderNumber")

                # A 2xx means "processed", not "accepted". The contract's response
                # structure carries an `errors` array alongside `data`, and a body with
                # errors and no asnNumber is a refusal however healthy the status line
                # looks. Reporting that as sent would mark a shipment notice delivered
                # that Zepto never recorded.
                errors = data.get("errors") or []
                asn_number = (data.get("data") or {}).get("asnNumber")
                if errors or not asn_number:
                    detail = _errors_to_text(errors) or "no asnNumber returned"
                    log.error(
                        "zepto.asn.rejected",
                        po_number=po_number,
                        status_code=resp.status_code,
                        error=detail,
                    )
                    return {
                        "success": False,
                        "status_code": resp.status_code,
                        "data": data,
                        "error": detail,
                        # Content was understood and refused; resending it unchanged
                        # earns the same answer.
                        "permanent": True,
                    }

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
                    # Contract, Response Codes: "4XX Parsing Errors, Missing mandatory
                    # fields. (retires wont work on this request)". 5xx is the retryable
                    # class and is already handled above.
                    "permanent": exc.response.status_code < 500,
                }

            except Exception as exc:
                log.error("zepto.asn.error", error=str(exc), attempt=attempt)
                if attempt < _MAX_RETRIES:
                    time.sleep(backoff)
                    continue
                return {"success": False, "error": str(exc)}

        return {"success": False, "error": "Max retries exceeded"}

    def cancel_asn(
        self,
        asn_number: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        Cancel a previously accepted ASN (contract §2.b).

            DELETE /api/v1/external/asn?asnNumber=JAI005MEA00972

        `asn_number` is **Zepto's** id, returned as `data.asnNumber` when they accepted
        the ASN -- not our ASN-... reference. Sending ours cancels nothing.

        This is the only way to correct a sent ASN: the contract states there is no
        update endpoint, so a wrong one must be cancelled and re-created under a
        *different* invoiceNumber. Re-using the invoice number after cancelling is
        rejected as a duplicate.

        Retries follow the same rule as creation -- 5xx only. A 4xx here means the ASN
        number is unknown or already cancelled, and repeating the call cannot change it.
        """
        import uuid as _uuid

        if not asn_number:
            return {"success": False, "error": "No Zepto asnNumber to cancel", "permanent": True}

        url = self._build_url("/api/v1/external/asn")
        key = idempotency_key or str(_uuid.uuid4())

        for attempt, backoff in enumerate(_RETRY_BACKOFF, start=1):
            self._rate_limit_wait()
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.delete(
                        url, params={"asnNumber": asn_number}, headers=self._headers(key)
                    )
                self._last_request_time = time.monotonic()

                body = resp.json() if resp.content else {}
                proxy_err = _check_proxy_error(body)
                if proxy_err:
                    log.error("zepto.asn_cancel.proxy_error", asn_number=asn_number, **proxy_err)
                    return {"success": False, **proxy_err}

                resp.raise_for_status()
                data = _unwrap(body) if body else {}
                errors = data.get("errors") or []
                if errors:
                    detail = _errors_to_text(errors)
                    log.error(
                        "zepto.asn_cancel.rejected", asn_number=asn_number, error=detail
                    )
                    return {
                        "success": False,
                        "status_code": resp.status_code,
                        "error": detail,
                        "permanent": True,
                    }

                log.info("zepto.asn_cancel.ok", asn_number=asn_number)
                return {"success": True, "status_code": resp.status_code, "data": data}

            except httpx.HTTPStatusError as exc:
                detail = _zepto_error_msg(exc.response.text)
                log.error(
                    "zepto.asn_cancel.http_error",
                    asn_number=asn_number,
                    status_code=exc.response.status_code,
                    attempt=attempt,
                    error=detail,
                )
                if attempt < _MAX_RETRIES and exc.response.status_code >= 500:
                    time.sleep(backoff)
                    continue
                return {
                    "success": False,
                    "status_code": exc.response.status_code,
                    "error": detail,
                    "permanent": exc.response.status_code < 500,
                }

            except Exception as exc:
                log.error("zepto.asn_cancel.error", asn_number=asn_number, error=str(exc))
                if attempt < _MAX_RETRIES:
                    time.sleep(backoff)
                    continue
                return {"success": False, "error": str(exc)}

        return {"success": False, "error": "Max retries exceeded"}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fetch_page(
        self,
        days: int,
        page: int,
        page_size: int,
    ) -> dict[str, Any] | None:
        """Fetch one page from the Zepto PO events endpoint with retry."""
        url = self._build_url("/api/v1/external/po/events")
        params: dict[str, Any] = {
            "days": min(days, 45),
            "pageSize": min(page_size, 20),
            "pageNumber": page,
            # MUST be "true". The contract calls false "just the latest PO snapshot",
            # which sounds like the cheaper option and is what this sent for months --
            # but that view omits newly released POs entirely and is not ordered
            # newest-first, so with a page cap they were unreachable. Four POs released
            # on 2026-08-25 were absent from 240 rows across 12 pages under false, and
            # first on page 1 under true. We key raw_messages on eventId and supersede
            # by version, so the event stream is the right feed regardless.
            "includeAllPoEvents": "true",
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
                    log.warning(
                        "zepto.fetch.rate_limited",
                        page=page, wait=wait, attempt=attempt,
                    )
                    time.sleep(wait)
                    continue

                if resp.status_code == 428:
                    # Zepto returns 428 with an EMPTY body when the calling IP is not
                    # on its allowlist. Nothing about that response mentions IPs, and
                    # the empty body used to surface as a JSON decode error
                    # ("Expecting value: line 1 column 1"), which reads like a
                    # credentials or payload fault and sent us chasing the wrong thing.
                    # Retrying cannot help — the IP will not change mid-run — so fail
                    # immediately rather than burning the backoff budget.
                    log.error(
                        "zepto.fetch.ip_not_whitelisted",
                        status_code=428,
                        url=str(resp.url),
                        page=page,
                        detail=(
                            "Zepto rejected this IP (HTTP 428, empty body). Credentials "
                            "are not the problem. Run the fetch from a whitelisted host, "
                            "or ask Zepto to allowlist this egress IP."
                        ),
                    )
                    return None

                try:
                    body = resp.json()
                except ValueError:
                    # Non-JSON body = we are being blocked or misrouted (e.g. calling
                    # Zepto directly from a non-whitelisted IP returns an HTML page).
                    # Log what actually came back — "Expecting value" hides the cause.
                    log.error(
                        "zepto.fetch.non_json_response",
                        status_code=resp.status_code,
                        url=str(resp.url),
                        body_snippet=resp.text[:200],
                        page=page,
                        attempt=attempt,
                    )
                    if attempt < _MAX_RETRIES:
                        time.sleep(backoff)
                        continue
                    return None

                # Proxy surfaces real Zepto errors as HTTP 200 with status_code field
                proxy_err = _check_proxy_error(body)
                if proxy_err:
                    log.error(
                        "zepto.fetch.proxy_error",
                        status_code=proxy_err["status_code"],
                        error=proxy_err["error"],
                        page=page,
                        attempt=attempt,
                    )
                    # 401/403 = credential problem; don't retry
                    if proxy_err["status_code"] in (401, 403):
                        return None
                    if attempt < _MAX_RETRIES and proxy_err["status_code"] >= 500:
                        time.sleep(backoff)
                        continue
                    return None

                resp.raise_for_status()

                # _unwrap strips proxy envelope AND Zepto's outer "data" key
                # giving us {"purchaseOrders": [...], "hasNext": bool}
                return _unwrap(body)

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
                log.error(
                    "zepto.fetch.error", error=str(exc), page=page, attempt=attempt,
                )
                if attempt < _MAX_RETRIES:
                    time.sleep(backoff)
                    continue
                return None

        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _since_to_days(since: datetime | None) -> int:
    """
    Convert a watermark datetime to the `days` param Zepto expects (1–45).

    Always asks for at least `_MIN_LOOKBACK_DAYS`, regardless of how recently we last
    polled. The watermark advances on every successful poll and the scheduler runs every
    few minutes, so a naive `now - since` delta collapses to days=1 almost immediately —
    which is what production had been sending. A one-day window leaves no margin for a PO
    that Zepto backdates, publishes late, or that we miss during an outage: once it falls
    outside the window it is never requested again.

    Re-requesting the overlap costs nothing downstream. `raw_messages` is unique on
    (trading_partner_id, external_id) where external_id is Zepto's `eventId`, so events
    we have already stored are skipped before they ever reach the parser.
    """
    if since is None:
        return _MIN_LOOKBACK_DAYS
    now = datetime.now(UTC)
    delta = now - (since.replace(tzinfo=UTC) if since.tzinfo is None else since)
    return min(max(_MIN_LOOKBACK_DAYS, delta.days + 1), 45)


def _check_proxy_error(raw: Any) -> dict[str, Any] | None:
    """
    Render proxy always returns HTTP 200.
    When Zepto returns 4xx/5xx, the proxy packs it as:
      {proxied: true, status_code: <zepto_status>, data: <zepto_body>}
    Detect and surface the real error so callers don't silently swallow it.
    """
    if isinstance(raw, dict) and raw.get("proxied"):
        status = raw.get("status_code", 200)
        if isinstance(status, int) and status >= 400:
            return {
                "status_code": status,
                "error": _zepto_error_msg(raw.get("data", "")),
            }
    return None


def _unwrap(raw: Any) -> dict[str, Any]:
    """
    Strip proxy envelope (if present) then strip Zepto's outer "data" key.

    Via proxy:
      raw = {proxied:true, status_code:200, data: {data: {purchaseOrders:[...]}}}
      → strip proxy  → {data: {purchaseOrders: [...]}}
      → strip zepto  → {purchaseOrders: [...], hasNext: false}

    Direct:
      raw = {data: {purchaseOrders: [...], hasNext: false}}
      → strip zepto  → {purchaseOrders: [...], hasNext: false}
    """
    if not isinstance(raw, dict):
        return {}
    # Strip proxy envelope
    if raw.get("proxied"):
        raw = raw.get("data") or {}
        if not isinstance(raw, dict):
            return {}
    # Strip Zepto outer "data" key
    inner = raw.get("data")
    if isinstance(inner, dict):
        return inner
    return raw


def _zepto_error_msg(body: str | bytes | Any) -> str:
    import json
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if not isinstance(body, str | dict):
        return str(body)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            return body
    if isinstance(body, dict):
        errors = body.get("errors")
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
            if body.get(key) and isinstance(body[key], str):
                return body[key]
        return json.dumps(body)
    return str(body)


def _errors_to_text(errors: Any) -> str:
    """Flatten Zepto's `errors` array into one diagnosable line."""
    if not isinstance(errors, list):
        return str(errors or "")
    parts: list[str] = []
    for e in errors:
        if isinstance(e, dict):
            code = e.get("code") or e.get("errorCode") or ""
            msg = e.get("message") or e.get("description") or e.get("detail") or ""
            parts.append(f"[{code}] {msg}".strip() if code else str(msg))
        else:
            parts.append(str(e))
    return "; ".join(p for p in parts if p)
