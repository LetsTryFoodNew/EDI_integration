"""
SAP Business One Service Layer client.

Connects to B1's REST/OData v4 API (Service Layer).
Uses `requests` (sync) — SAP worker runs in a sync RQ job.

Key design choices:
  - Sessions managed by SessionPool (thread-safe, max N concurrent).
  - Auto-renews on 401: invalidates session → re-logins → retries once.
  - All money values serialized as float (6 decimal places) per B1 spec.
  - verify_ssl is configurable — MUST be True in production (see CLAUDE.md §7).
  - Never raises on 404 in get_*; returns None instead.

B1 base paths:
  Login:          POST  /b1s/v1/Login
  Logout:         POST  /b1s/v1/Logout
  Sales Orders:   POST  /b1s/v1/Orders
  Deliveries:     POST  /b1s/v1/DeliveryNotes
  AR Invoices:    POST  /b1s/v1/Invoices
  Returns:        POST  /b1s/v1/Returns
  Credit Notes:   POST  /b1s/v1/CreditNotes
  Items:          GET   /b1s/v1/Items('{ItemCode}')
  Business Ptrs:  GET   /b1s/v1/BusinessPartners('{CardCode}')
  Generic query:  GET   /b1s/v1/{Entity}?$filter=...
"""
from __future__ import annotations

import time
from typing import Any

import requests
import structlog

from app.sap_b1.errors import B1ApiError, B1SessionError
from app.sap_b1.session_pool import SessionPool

log = structlog.get_logger(__name__)

_B1_API_BASE = "/b1s/v1"
_REQUEST_TIMEOUT = 60   # seconds
_MAX_RETRIES = 1        # retry once after session renewal


class ServiceLayerClient:
    """
    Thin wrapper around the B1 Service Layer REST API.

    Instantiate once per process; the session pool is thread-safe.
    """

    def __init__(
        self,
        base_url: str,
        company_db: str,
        username: str,
        password: str,
        pool_size: int = 2,
        verify_ssl: bool = True,
    ) -> None:
        if not base_url:
            raise ValueError("B1 base_url must not be empty")
        self._base_url = base_url.rstrip("/")
        self._company_db = company_db
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._pool = SessionPool(
            max_sessions=pool_size,
            login_fn=self._login,
            logout_fn=self._logout,
        )

    # ── Document operations ───────────────────────────────────────────────────

    def create_sales_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /b1s/v1/Orders — creates a Sales Order in B1."""
        return self._post("/Orders", payload, operation="create_sales_order")

    def create_delivery(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /b1s/v1/DeliveryNotes — creates a Delivery Note."""
        return self._post("/DeliveryNotes", payload, operation="create_delivery")

    def create_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /b1s/v1/Invoices — creates an A/R Invoice."""
        return self._post("/Invoices", payload, operation="create_invoice")

    def create_return(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /b1s/v1/Returns — creates an A/R Return (for RTV)."""
        return self._post("/Returns", payload, operation="create_return")

    def create_credit_note(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /b1s/v1/CreditNotes — creates an A/R Credit Memo."""
        return self._post("/CreditNotes", payload, operation="create_credit_note")

    # ── Master data reads ─────────────────────────────────────────────────────

    def get_item(self, item_code: str) -> dict[str, Any] | None:
        """GET /b1s/v1/Items('{item_code}') — returns None if not found."""
        return self._get_one(f"/Items('{item_code}')", operation="get_item")

    def get_business_partner(self, card_code: str) -> dict[str, Any] | None:
        """GET /b1s/v1/BusinessPartners('{card_code}') — returns None if not found."""
        return self._get_one(f"/BusinessPartners('{card_code}')", operation="get_bp")

    def query(
        self,
        entity: str,
        params: dict[str, str] | None = None,
        select: str | None = None,
        filter_expr: str | None = None,
        top: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Generic OData GET with optional $select, $filter, $top.
        Returns the `value` array from the OData response.
        """
        query_params: dict[str, str] = dict(params or {})
        if select:
            query_params["$select"] = select
        if filter_expr:
            query_params["$filter"] = filter_expr
        if top is not None:
            query_params["$top"] = str(top)

        result = self._get(f"/{entity}", params=query_params, operation=f"query_{entity}")
        return result.get("value", [])

    # ── Session management ────────────────────────────────────────────────────

    def close(self) -> None:
        """Logout all sessions; call on process shutdown."""
        self._pool.close_all()

    # ── Internal HTTP helpers ─────────────────────────────────────────────────

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        return self._request("POST", path, json_body=payload, operation=operation)

    def _get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        operation: str = "get",
    ) -> dict[str, Any]:
        return self._request("GET", path, params=params, operation=operation)

    def _get_one(
        self,
        path: str,
        operation: str,
    ) -> dict[str, Any] | None:
        try:
            return self._request("GET", path, operation=operation)
        except B1ApiError as exc:
            if exc.http_status == 404:
                return None
            raise

    def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        operation: str = "request",
    ) -> dict[str, Any]:
        """
        Execute a Service Layer request, acquiring a session from the pool.
        Retries once with a fresh session on 401.
        """
        session_id = self._pool.acquire()
        try:
            return self._do_request(
                method, path, session_id, json_body, params, operation,
            )
        except B1SessionError:
            # Session expired — invalidate, get a fresh one, retry once
            self._pool.invalidate(session_id)
            log.warning("b1.session_expired_retrying", operation=operation)
            session_id = self._pool.acquire()
            try:
                return self._do_request(
                    method, path, session_id, json_body, params, operation,
                )
            finally:
                self._pool.release(session_id)
        finally:
            # Release only if it wasn't already invalidated
            import contextlib
            with contextlib.suppress(Exception):
                self._pool.release(session_id)

    def _do_request(
        self,
        method: str,
        path: str,
        session_id: str,
        json_body: dict[str, Any] | None,
        params: dict[str, str] | None,
        operation: str,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{_B1_API_BASE}{path}"
        cookies = {"B1SESSION": session_id, "CompanyDB": self._company_db}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        t_start = time.monotonic()
        try:
            resp = requests.request(
                method=method,
                url=url,
                json=json_body,
                params=params,
                cookies=cookies,
                headers=headers,
                verify=self._verify_ssl,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise B1ApiError(
                message=f"Network error calling B1 {operation}: {exc}",
                code="NETWORK_ERROR",
                http_status=0,
            ) from exc

        duration_ms = int((time.monotonic() - t_start) * 1000)

        log.debug(
            "b1.response",
            operation=operation,
            method=method,
            path=path,
            status=resp.status_code,
            duration_ms=duration_ms,
        )

        try:
            body = resp.json() if resp.content else {}
        except Exception:
            body = {"_raw": resp.text}

        if resp.status_code >= 400:
            raise B1ApiError.from_response(resp.status_code, body)

        return body  # type: ignore[return-value]

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def _login(self) -> str:
        """Create a new B1 session and return its SessionId."""
        url = f"{self._base_url}{_B1_API_BASE}/Login"
        payload = {
            "CompanyDB": self._company_db,
            "UserName": self._username,
            "Password": self._password,
        }
        try:
            resp = requests.post(
                url,
                json=payload,
                verify=self._verify_ssl,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise B1ApiError(
                message=f"B1 login network error: {exc}",
                code="LOGIN_NETWORK_ERROR",
            ) from exc

        if resp.status_code != 200:
            try:
                body = resp.json()
            except Exception:
                body = {}
            raise B1SessionError.from_response(resp.status_code, body)  # type: ignore[return-value]

        session_id: str = resp.json()["SessionId"]
        log.info("b1.login_ok", session_id=session_id[:8] + "...")
        return session_id

    def _logout(self, session_id: str) -> None:
        """Politely close a B1 session."""
        try:
            requests.post(
                f"{self._base_url}{_B1_API_BASE}/Logout",
                cookies={"B1SESSION": session_id, "CompanyDB": self._company_db},
                verify=self._verify_ssl,
                timeout=10,
            )
            log.debug("b1.logout_ok", session_id=session_id[:8] + "...")
        except Exception as exc:
            log.warning("b1.logout_failed", session_id=session_id[:8] + "...", error=str(exc))


# ── Module-level singleton (lazy-initialized) ─────────────────────────────────

_client: ServiceLayerClient | None = None
_client_lock = __import__("threading").Lock()


def get_b1_client() -> ServiceLayerClient:
    """
    Return the module-level ServiceLayerClient singleton.
    Initialised on first call from app/config settings.
    """
    global _client  # noqa: PLW0603
    if _client is None:
        with _client_lock:
            if _client is None:
                from app.config import get_settings
                s = get_settings()
                _client = ServiceLayerClient(
                    base_url=s.b1_service_layer_url,
                    company_db=s.b1_company_db,
                    username=s.b1_username,
                    password=s.b1_password,
                    pool_size=s.b1_session_pool_size,
                    verify_ssl=s.b1_verify_ssl,
                )
    return _client
