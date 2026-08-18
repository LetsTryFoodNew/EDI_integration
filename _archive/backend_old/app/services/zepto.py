"""
ZeptoService — Smart routing for Zepto Silk Route API calls.

LOCAL  : Your Mac → Render Server (static IP) → Zepto API ✅
PROD   : Render Server (static IP) → Zepto API directly ✅

This solves the IP whitelisting problem: your local Mac has a dynamic IP
that Zepto will block, but Render has static IPs (74.220.48.0/24 and
74.220.56.0/24) that are already whitelisted with Zepto.

Auth:  X-Client-Id + X-Client-Secret headers (not Bearer token)
Hosts: QA   → silkroute.zeptonow.dev
       Prod → silkroute.zepto.co.in

⚠️  CREDENTIALS ARE TIED TO A HOST. Zepto issues a *separate* clientId/secret
    pair per tier. Sending a QA pair to the Prod host returns HTTP 400:
        {"errors":[{"code":400,"error":"Invalid Client credentials"}]}
    ...which reads like a bad password but is really a wrong-host error.

    So the host is chosen by ZEPTO_ENV (qa|prod) — the tier your credentials
    belong to — NOT by ENVIRONMENT. ENVIRONMENT only decides whether we go
    through the Render proxy. Mixing the two is what caused the outage:
    ENVIRONMENT=production silently flipped the host to Prod while the
    credentials in .env were still QA-tier.

    Note the two tiers also behave differently at the network layer:
      QA   is IP-whitelisted → un-whitelisted callers get HTTP 428, no body.
      Prod is NOT IP-whitelisted → it answers, then rejects on credentials.

Key rules from API contract v12:
- All write APIs require X-Idempotency-Key header
- Rate limit: 60 RPM per clientId per API
- Quantities must be in pieces (PC), not case sizes
- No ASN update API — cancel + recreate with a new invoiceNumber
- Use eventId as idempotency key when polling PO events
- PO PDF links expire in ~7 days — download promptly
- All timestamps are UTC
"""

import httpx
import json
import os
import uuid
import logging
from typing import Optional

logger = logging.getLogger("edi.zepto")

QA_BASE_URL   = "https://silkroute.zeptonow.dev"
PROD_BASE_URL = "https://silkroute.zepto.co.in"

# The only hosts we will ever talk to. Keyed by credential tier.
TIER_HOSTS = {
    "qa":   QA_BASE_URL,
    "prod": PROD_BASE_URL,
}
DEFAULT_TIER = "qa"

# Zepto caps pageSize at 20 and sorts PO events OLDEST-FIRST, so the newest POs
# live on the LAST page. Anything that wants "recent POs" must paginate.
MAX_PAGE_SIZE = 20
MAX_PAGES     = 100   # 2,000 POs — guard against a runaway hasNext


def resolve_tier(raw: Optional[str]) -> str:
    """
    Normalise a ZEPTO_ENV value to a known credential tier.

    Defaults to QA. Defaulting to Prod is what broke this integration: an
    unset/typo'd value must never silently point QA credentials at the Prod
    host, because Zepto answers that with "Invalid Client credentials".
    """
    tier = (raw or DEFAULT_TIER).strip().lower()
    if tier in ("production", "prd"):
        tier = "prod"
    if tier not in TIER_HOSTS:
        logger.warning(
            "ZEPTO_ENV=%r is not one of %s — falling back to %r",
            raw, sorted(TIER_HOSTS), DEFAULT_TIER,
        )
        tier = DEFAULT_TIER
    return tier


class ZeptoService:
    def __init__(self):
        self.env           = os.getenv("ENVIRONMENT", "local")
        self.client_id     = os.getenv("ZEPTO_CLIENT_ID", "")
        self.client_secret = os.getenv("ZEPTO_CLIENT_SECRET", "")
        self.render_url    = os.getenv("RENDER_URL", "").rstrip("/")

        # Host follows the CREDENTIAL tier, never ENVIRONMENT. See module docstring.
        self.tier     = resolve_tier(os.getenv("ZEPTO_ENV"))
        self.base_url = (os.getenv("ZEPTO_BASE_URL") or TIER_HOSTS[self.tier]).rstrip("/")

        if not self.client_id or not self.client_secret:
            logger.error(
                "ZeptoService: ZEPTO_CLIENT_ID / ZEPTO_CLIENT_SECRET missing — "
                "every call will fail with 'X-Client-ID is required'"
            )

        if self.env == "local":
            logger.info(
                "ZeptoService: LOCAL mode — via Render proxy (%s) → %s tier (%s)",
                self.render_url, self.tier, self.base_url,
            )
        else:
            logger.info(
                "ZeptoService: DIRECT mode — %s tier (%s)", self.tier, self.base_url,
            )

    def _unwrap(self, raw) -> dict:
        """Strip the Render proxy wrapper {proxied: True, data: <zepto>} if present."""
        if isinstance(raw, dict) and raw.get("proxied"):
            inner = raw.get("data")
            return inner if isinstance(inner, dict) else {}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _zepto_message(body) -> str:
        """
        Extract a clean, human-readable message from a Zepto error body.

        Zepto error shapes seen in the wild:
          {"errors": [{"code": 400, "error": "Invalid record. ..."}], "data": null}
          {"message": "...", "statusCode": 400}
          "plain string"
        """
        if isinstance(body, str):
            # Try to parse JSON string first
            try:
                body = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                return body
        if isinstance(body, dict):
            errors = body.get("errors")
            if isinstance(errors, list) and errors:
                msgs = [e.get("error") or e.get("message", "") for e in errors if isinstance(e, dict)]
                msgs = [m for m in msgs if m]
                if msgs:
                    return "; ".join(msgs)
            for key in ("message", "error", "detail"):
                if body.get(key) and isinstance(body[key], str):
                    return body[key]
            return json.dumps(body)
        return str(body)

    def _proxy_error(self, raw) -> Optional[dict]:
        """
        Old Render proxy returns HTTP 200 even when Zepto returns 4xx, packing
        the real status in {proxied: True, status_code: <zepto>, data: <body>}.
        Detect that here so every caller can surface the real Zepto error.
        """
        if isinstance(raw, dict) and raw.get("proxied"):
            status = raw.get("status_code", 200)
            if status >= 400:
                return {
                    "success":     False,
                    "status_code": status,
                    "error":       self._zepto_message(raw.get("data", "")),
                }
        return None

    @property
    def via_proxy(self) -> bool:
        """Local runs tunnel through Render so Zepto sees a whitelisted static IP."""
        return self.env == "local"

    @property
    def effective_target(self) -> str:
        """The URL we actually dial — the proxy in local mode, Zepto otherwise."""
        return f"{self.render_url}/api/proxy/zepto" if self.via_proxy else self.base_url

    def _url(self, path: str) -> str:
        """
        LOCAL  → https://po-integration-backend.onrender.com/api/proxy/zepto/api/v1/external/...
        DIRECT → https://silkroute.zeptonow.dev/api/v1/external/...   (tier-dependent)

        In local mode every request leaves from Render's static IP, so Zepto's
        IP whitelist check passes even though your Mac's IP keeps changing.
        """
        path = path.lstrip("/")
        if self.via_proxy:
            if not self.render_url:
                raise RuntimeError(
                    "ENVIRONMENT=local requires RENDER_URL to be set — without it the "
                    "request would be sent to a scheme-less relative URL. Either set "
                    "RENDER_URL, or set ENVIRONMENT=production to call Zepto directly "
                    "from a whitelisted host."
                )
            url = f"{self.render_url}/api/proxy/zepto/{path}"
            logger.debug("ZeptoService [LOCAL] via Render → %s tier: %s", self.tier, url)
        else:
            url = f"{self.base_url}/{path}"
            logger.debug("ZeptoService [DIRECT] → %s tier: %s", self.tier, url)
        return url

    def _headers(self, idempotency_key: Optional[str] = None) -> dict:
        h = {
            "Content-Type":    "application/json",
            "X-Client-Id":     self.client_id,
            "X-Client-Secret": self.client_secret,
        }
        if idempotency_key:
            h["X-Idempotency-Key"] = idempotency_key
        if self.via_proxy:
            # Tells the Render proxy which Zepto host to forward to, so the tier
            # our credentials belong to survives the extra hop.
            h["X-Zepto-Env"] = self.tier
        return h

    # ── 1. List PO Events ────────────────────────────────────────────────────────
    async def list_po_events(
        self,
        days: int,
        vendor_codes: Optional[list] = None,
        po_codes: Optional[list] = None,
        include_all_po_events: bool = False,
        include_line_item_details: bool = False,
        page_size: int = 10,
        page_number: int = 1,
    ) -> dict:
        """
        Retrieve ONE page of PO snapshots for POs created in the past `days` days.
        Max days=45, max pageSize=20, max 10 vendor/po codes per request.
        Use the returned eventId as an idempotency key to avoid re-processing.

        ⚠️  Zepto returns results OLDEST-FIRST and paginates. Page 1 is therefore
            the oldest POs, not the newest. Call list_all_po_events() unless you
            genuinely want a single page — see that method for the full story.
        """
        url = self._url("/api/v1/external/po/events")
        params: dict = {
            "days":                    min(days, 45),
            "pageSize":                min(page_size, 20),
            "pageNumber":              page_number,
            "includeAllPoEvents":      str(include_all_po_events).lower(),
            "includeLineItemDetails":  str(include_line_item_details).lower(),
        }
        if vendor_codes:
            params["vendorCodes"] = ",".join(vendor_codes[:10])
        if po_codes:
            params["poCodes"] = ",".join(po_codes[:10])

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, params=params, headers=self._headers())
                raw = response.json()
                err = self._proxy_error(raw)
                if err:
                    logger.error("Zepto list_po_events (via proxy) HTTP %s: %s", err["status_code"], err["error"])
                    return err
                response.raise_for_status()
                logger.info("Zepto list_po_events: HTTP %s, days=%s", response.status_code, days)
                return {"success": True, "status_code": response.status_code, "data": self._unwrap(raw)}
        except httpx.HTTPStatusError as e:
            logger.error("Zepto list_po_events HTTP %s: %s", e.response.status_code, e.response.text)
            return {"success": False, "status_code": e.response.status_code, "error": self._zepto_message(e.response.text)}
        except Exception as e:
            logger.error("Zepto list_po_events failed: %s", e)
            return {"success": False, "error": str(e)}

    # ── 1b. List ALL PO Events (paginated) ───────────────────────────────────────
    async def list_all_po_events(
        self,
        days: int,
        vendor_codes: Optional[list] = None,
        po_codes: Optional[list] = None,
        include_all_po_events: bool = False,
        include_line_item_details: bool = False,
        max_pages: int = MAX_PAGES,
        newest_first: bool = True,
    ) -> dict:
        """
        Walk every page of PO events and return the complete set, newest first.

        WHY THIS EXISTS
        ---------------
        Zepto returns PO events sorted OLDEST-FIRST and caps pageSize at 20, and
        the old code only ever requested page 1. With 237 POs in the QA account
        that meant callers saw P367110-P367119 — ten expired POs from June — and
        the four POs Zepto's team raised on 28 Jul (P368477-P368480) sat on
        page 12 where nothing ever looked. New POs were invisible by design.

        So: paginate to exhaustion, then reverse the order so the caller gets the
        newest POs at the top regardless of how Zepto sorts them.

        Dedupes on eventId (a PO can appear more than once across pages when
        includeAllPoEvents=true, and pages can shift if Zepto ingests a PO
        mid-walk). Stops at max_pages so a runaway hasNext can't loop forever.
        """
        collected: list = []
        seen: set = set()
        pages_fetched = 0
        page = 1

        while page <= max_pages:
            result = await self.list_po_events(
                days=days,
                vendor_codes=vendor_codes,
                po_codes=po_codes,
                include_all_po_events=include_all_po_events,
                include_line_item_details=include_line_item_details,
                page_size=MAX_PAGE_SIZE,
                page_number=page,
            )
            if not result.get("success"):
                # Partial data is worse than a clear failure on page 1; but if we
                # already have pages, return what we got and flag it.
                if pages_fetched == 0:
                    return result
                logger.warning(
                    "Zepto list_all_po_events: page %s failed (%s) — returning %s POs from %s pages",
                    page, result.get("error"), len(collected), pages_fetched,
                )
                break

            body = (result.get("data") or {}).get("data") or {}
            orders = body.get("purchaseOrders") or []
            pages_fetched += 1

            for po in orders:
                key = po.get("eventId") or po.get("code")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                collected.append(po)

            if not body.get("hasNext"):
                break
            page += 1
        else:
            logger.warning(
                "Zepto list_all_po_events: hit max_pages=%s — results may be truncated",
                max_pages,
            )

        if newest_first:
            # Zepto sorts oldest-first, so reverse it.
            #
            # Sort on orderDate, NOT timestamp. `timestamp` is the last EVENT time,
            # so a June PO that just expired carries an August timestamp and would
            # outrank a genuinely new PO — which buries exactly the POs someone
            # opening this list is looking for. orderDate is when the PO was raised,
            # which is what "newest PO" means to an ops user.
            collected.sort(
                key=lambda p: (
                    p.get("orderDate") or "",
                    p.get("timestamp") or "",
                    p.get("code") or "",
                ),
                reverse=True,
            )

        logger.info(
            "Zepto list_all_po_events: %s POs across %s pages (days=%s)",
            len(collected), pages_fetched, days,
        )
        return {
            "success":     True,
            "status_code": 200,
            "data": {
                "errors": [],
                "data": {
                    "purchaseOrders": collected,
                    "hasNext":        False,
                    "pageNumber":     1,
                    "pageSize":       len(collected),
                    "totalFetched":   len(collected),
                    "pagesFetched":   pages_fetched,
                },
            },
        }

    # ── 2a. Create ASN ───────────────────────────────────────────────────────────
    async def create_asn(self, payload: dict, idempotency_key: Optional[str] = None) -> dict:
        """
        Submit an ASN / invoice against a Zepto PO.
        Returns asnNumber — store it, you need it for cancellation.
        invoiceNumber must be unique per request; there is no update API.
        Quantities must be in pieces (PC), not case sizes.
        On 5XX errors, retrying with the same idempotency key is safe.
        """
        url = self._url("/api/v1/external/asn")
        key = idempotency_key or str(uuid.uuid4())
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, json=payload, headers=self._headers(key))
                raw = response.json()
                err = self._proxy_error(raw)
                if err:
                    logger.error("Zepto create_asn (via proxy) HTTP %s: %s", err["status_code"], err["error"])
                    return err
                response.raise_for_status()
                data       = self._unwrap(raw)
                asn_number = data.get("data", {}).get("asnNumber")
                po_number  = payload.get("purchaseOrderDetails", {}).get("purchaseOrderNumber")
                logger.info("Zepto ASN created: %s for PO %s", asn_number, po_number)
                return {
                    "success":     True,
                    "status_code": response.status_code,
                    "data":        data,
                    "asn_number":  asn_number,
                }
        except httpx.HTTPStatusError as e:
            logger.error("Zepto create_asn HTTP %s: %s", e.response.status_code, e.response.text)
            return {"success": False, "status_code": e.response.status_code, "error": self._zepto_message(e.response.text)}
        except Exception as e:
            logger.error("Zepto create_asn failed: %s", e)
            return {"success": False, "error": str(e)}

    # ── 2b. Cancel ASN ───────────────────────────────────────────────────────────
    async def cancel_asn(self, asn_number: str, idempotency_key: Optional[str] = None) -> dict:
        """
        Cancel an existing ASN by its Zepto-issued asnNumber.
        To update an ASN: cancel it here, then call create_asn with a new invoiceNumber.
        """
        url = self._url("/api/v1/external/asn")
        key = idempotency_key or str(uuid.uuid4())
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.delete(
                    url,
                    params={"asnNumber": asn_number},
                    headers=self._headers(key),
                )
                raw = response.json()
                err = self._proxy_error(raw)
                if err:
                    logger.error("Zepto cancel_asn (via proxy) HTTP %s: %s", err["status_code"], err["error"])
                    return err
                response.raise_for_status()
                logger.info("Zepto ASN cancelled: %s", asn_number)
                return {"success": True, "status_code": response.status_code, "data": self._unwrap(raw)}
        except httpx.HTTPStatusError as e:
            logger.error("Zepto cancel_asn HTTP %s: %s", e.response.status_code, e.response.text)
            return {"success": False, "status_code": e.response.status_code, "error": self._zepto_message(e.response.text)}
        except Exception as e:
            logger.error("Zepto cancel_asn failed: %s", e)
            return {"success": False, "error": str(e)}

    # ── 2c. List ASNs ────────────────────────────────────────────────────────────
    async def list_asns(
        self,
        po_code: str,
        page_size: int = 10,
        page_number: int = 1,
    ) -> dict:
        """Fetch all ASNs (and their statuses) created against a given PO code."""
        url = self._url("/api/v1/external/asn")
        params = {
            "poCode":     po_code,
            "pageSize":   page_size,
            "pageNumber": page_number,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, params=params, headers=self._headers())
                raw = response.json()
                err = self._proxy_error(raw)
                if err:
                    logger.error("Zepto list_asns (via proxy) HTTP %s: %s", err["status_code"], err["error"])
                    return err
                response.raise_for_status()
                return {"success": True, "status_code": response.status_code, "data": self._unwrap(raw)}
        except httpx.HTTPStatusError as e:
            logger.error("Zepto list_asns HTTP %s: %s", e.response.status_code, e.response.text)
            return {"success": False, "status_code": e.response.status_code, "error": self._zepto_message(e.response.text)}
        except Exception as e:
            logger.error("Zepto list_asns failed: %s", e)
            return {"success": False, "error": str(e)}

    # ── 3. Request PO Amendment ──────────────────────────────────────────────────
    async def request_po_amendment(
        self,
        po_number: str,
        payload: dict,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """
        Request a PO-level or line-item amendment from Zepto.
        Supported attributeNames: MRP, BASE_PRICE, EAN, CASE_SIZE, EXPIRY_DATE.
        payload must include purchaseOrderAmendment.purchaseOrderNumber.
        """
        url = self._url(f"/api/v1/external/po/{po_number}/amendment")
        key = idempotency_key or str(uuid.uuid4())
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, json=payload, headers=self._headers(key))
                raw = response.json()
                err = self._proxy_error(raw)
                if err:
                    logger.error("Zepto request_po_amendment (via proxy) HTTP %s: %s", err["status_code"], err["error"])
                    return err
                response.raise_for_status()
                logger.info("Zepto PO amendment submitted: %s", po_number)
                return {"success": True, "status_code": response.status_code, "data": self._unwrap(raw)}
        except httpx.HTTPStatusError as e:
            logger.error("Zepto request_po_amendment HTTP %s: %s", e.response.status_code, e.response.text)
            return {"success": False, "status_code": e.response.status_code, "error": self._zepto_message(e.response.text)}
        except Exception as e:
            logger.error("Zepto request_po_amendment failed: %s", e)
            return {"success": False, "error": str(e)}

    # ── Health Check ─────────────────────────────────────────────────────────────
    async def health_check(self) -> dict:
        """
        Test Zepto API connectivity using a minimal PO events call.

        Reports the *effective* target (the proxy in local mode) rather than
        base_url — the old version always printed base_url, which made a local
        run look like it was talking to Zepto directly and hid proxy problems.

        Reads the response body so a credential/tier mismatch shows up as
        "Invalid Client credentials" instead of a bare HTTP 400.
        """
        url = self._url("/api/v1/external/po/events")
        info = {
            "environment":  self.env,
            "tier":         self.tier,
            "zepto_host":   self.base_url,
            "endpoint":     self.effective_target,
            "via_proxy":    self.via_proxy,
            "credentials_set": bool(self.client_id and self.client_secret),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    url,
                    params={"days": 1, "pageSize": 1, "pageNumber": 1},
                    headers=self._headers(),
                )
            ok = response.status_code < 400
            result = {**info, "reachable": True, "ok": ok, "status_code": response.status_code}
            if not ok:
                result["error"] = self._zepto_message(response.text)
                if response.status_code == 428:
                    result["hint"] = (
                        "HTTP 428 = caller IP is not whitelisted by Zepto. Route via "
                        "the Render proxy (ENVIRONMENT=local) or whitelist this host."
                    )
                elif "credential" in result["error"].lower():
                    result["hint"] = (
                        f"Credentials rejected by the {self.tier} host. ZEPTO_CLIENT_ID/"
                        f"SECRET must be the pair Zepto issued for {self.tier} — a QA "
                        "pair sent to Prod fails exactly like this. Check ZEPTO_ENV."
                    )
            return result
        except Exception as e:
            return {**info, "reachable": False, "ok": False, "error": str(e)}


# ── Single instance — import this everywhere ─────────────────────────────────
zepto_service = ZeptoService()
