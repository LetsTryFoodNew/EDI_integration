"""
Unit tests for Phase 4 API adapters — ZeptoApiAdapter and BlinkitApiAdapter.

Uses httpx respx for HTTP mocking. Tests cover:
  1. ZeptoApiAdapter.fetch_new_pos — happy path, pagination, 429 rate-limit, 5xx retry
  2. BlinkitApiAdapter.acknowledge_po — happy path, 5xx retry, max retries exceeded
  3. BlinkitApiAdapter.send_asn — happy path, error response
  4. Webhook route — Blinkit inbound (mocked DB)
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ── ZeptoApiAdapter ───────────────────────────────────────────────────────────

class TestZeptoApiAdapter:
    def setup_method(self) -> None:
        from app.adapters.api.zepto_api import ZeptoApiAdapter
        self.adapter = ZeptoApiAdapter()
        self.adapter._client_id = "test-client-id"
        self.adapter._client_secret = "test-client-secret"
        self.adapter._base_url = "https://silkroute.test.zepto"

    def _events_url(self) -> str:
        return "https://silkroute.test.zepto/api/v1/external/po/events"

    def test_fetch_returns_fetched_pos(self) -> None:
        import respx
        import httpx

        response_body = json.loads((FIXTURES / "zepto_po_events_response.json").read_text())

        with respx.mock:
            respx.get(self._events_url()).mock(
                return_value=httpx.Response(200, json=response_body)
            )
            results = self.adapter.fetch_new_pos(since=None)

        assert len(results) == 2
        assert results[0].external_id == "evt_abc123def456"
        assert results[0].po_number == "P365999"
        assert results[1].external_id == "evt_bcd234efg567"

    def test_fetch_deduplicates_same_event_id(self) -> None:
        import respx
        import httpx

        body = json.loads((FIXTURES / "zepto_po_events_response.json").read_text())
        # Duplicate the first PO (same eventId)
        body["data"]["purchaseOrders"].append(body["data"]["purchaseOrders"][0])

        with respx.mock:
            respx.get(self._events_url()).mock(
                return_value=httpx.Response(200, json=body)
            )
            results = self.adapter.fetch_new_pos()

        assert len(results) == 2  # deduped — not 3

    def test_fetch_stops_when_has_next_false(self) -> None:
        import respx
        import httpx

        body = json.loads((FIXTURES / "zepto_po_events_response.json").read_text())
        body["data"]["hasNext"] = False

        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=body)

        with respx.mock:
            respx.get(self._events_url()).mock(side_effect=handler)
            self.adapter.fetch_new_pos(max_pages=5)

        assert call_count == 1  # should not paginate further

    def test_fetch_paginates_when_has_next_true(self) -> None:
        import respx
        import httpx

        page1 = {
            "data": {
                "purchaseOrders": [
                    {"purchaseOrderNumber": "P100", "eventId": "evt_p1", "lineItems": [], "buyerDetails": {}}
                ],
                "hasNext": True,
            }
        }
        page2 = {
            "data": {
                "purchaseOrders": [
                    {"purchaseOrderNumber": "P200", "eventId": "evt_p2", "lineItems": [], "buyerDetails": {}}
                ],
                "hasNext": False,
            }
        }

        responses = [
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
        ]

        with respx.mock:
            respx.get(self._events_url()).mock(side_effect=responses)
            results = self.adapter.fetch_new_pos(max_pages=5)

        assert len(results) == 2
        assert results[0].external_id == "evt_p1"
        assert results[1].external_id == "evt_p2"

    def test_fetch_returns_empty_on_500(self) -> None:
        import respx
        import httpx

        with respx.mock:
            # 3 consecutive 500s — should exhaust retries and return empty
            respx.get(self._events_url()).mock(
                return_value=httpx.Response(500, json={"error": "server error"})
            )
            # Patch sleep to avoid slowing the test
            with patch("app.adapters.api.zepto_api.time.sleep"):
                results = self.adapter.fetch_new_pos()

        assert results == []

    def test_fetch_respects_429_retry_after(self) -> None:
        import respx
        import httpx

        body = json.loads((FIXTURES / "zepto_po_events_response.json").read_text())
        responses = [
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json=body),
        ]

        slept: list[float] = []

        with respx.mock:
            respx.get(self._events_url()).mock(side_effect=responses)
            with patch("app.adapters.api.zepto_api.time.sleep", side_effect=slept.append):
                results = self.adapter.fetch_new_pos()

        assert len(results) == 2
        assert slept[0] == 1  # respected Retry-After: 1

    def test_since_to_days_none(self) -> None:
        from app.adapters.api.zepto_api import _since_to_days
        assert _since_to_days(None) == 7

    def test_since_to_days_recent(self) -> None:
        from app.adapters.api.zepto_api import _since_to_days
        from datetime import timedelta
        since = datetime.now(UTC).replace(microsecond=0) - timedelta(days=2)
        assert _since_to_days(since) == 3  # 2 days + 1 for partial

    def test_since_to_days_capped_at_45(self) -> None:
        from app.adapters.api.zepto_api import _since_to_days
        from datetime import timedelta
        since = datetime.now(UTC) - timedelta(days=100)
        assert _since_to_days(since) == 45

    def test_send_asn_success(self) -> None:
        import respx
        import httpx

        asn_response = {"data": {"data": {"asnNumber": "ASN-12345"}}}
        asn_url = "https://silkroute.test.zepto/api/v1/external/asn"

        with respx.mock:
            respx.post(asn_url).mock(return_value=httpx.Response(200, json=asn_response))
            result = self.adapter.send_asn(
                {"purchaseOrderDetails": {"purchaseOrderNumber": "P365999"}},
                idempotency_key="idem-001",
            )

        assert result["success"] is True
        assert result["asn_number"] == "ASN-12345"

    def test_send_asn_error(self) -> None:
        import respx
        import httpx

        asn_url = "https://silkroute.test.zepto/api/v1/external/asn"

        with respx.mock:
            respx.post(asn_url).mock(
                return_value=httpx.Response(400, json={"errors": [{"error": "Invalid PO"}]})
            )
            with patch("app.adapters.api.zepto_api.time.sleep"):
                result = self.adapter.send_asn({})

        assert result["success"] is False
        assert "Invalid PO" in result["error"]


# ── BlinkitApiAdapter ─────────────────────────────────────────────────────────

class TestBlinkitApiAdapter:
    def setup_method(self) -> None:
        from app.adapters.api.blinkit_api import BlinkitApiAdapter
        self.adapter = BlinkitApiAdapter()
        self.adapter._api_key = "test-api-key"
        self.adapter._vendor_id = "18309"
        self.adapter._base_url = "https://dev.test.blinkit"

    def _ack_url(self) -> str:
        return "https://dev.test.blinkit/webhook/public/v1/po/acknowledgement"

    def _asn_url(self) -> str:
        return "https://dev.test.blinkit/webhook/public/v1/asn"

    def test_acknowledge_po_success(self) -> None:
        import respx
        import httpx

        with respx.mock:
            respx.post(self._ack_url()).mock(
                return_value=httpx.Response(200, json={"success": True})
            )
            result = self.adapter.acknowledge_po("BL-001", status="accepted")

        assert result["success"] is True

    def test_acknowledge_po_includes_correct_headers(self) -> None:
        import respx
        import httpx

        sent_headers: dict[str, str] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            sent_headers.update(dict(request.headers))
            return httpx.Response(200, json={"success": True})

        with respx.mock:
            respx.post(self._ack_url()).mock(side_effect=capture)
            self.adapter.acknowledge_po("BL-001")

        assert sent_headers.get("api-key") == "test-api-key"
        assert sent_headers.get("x-vendor-id") == "18309"

    def test_acknowledge_po_retries_on_5xx(self) -> None:
        import respx
        import httpx

        responses = [
            httpx.Response(503, json={"error": "server busy"}),
            httpx.Response(200, json={"success": True}),
        ]

        with respx.mock:
            respx.post(self._ack_url()).mock(side_effect=responses)
            with patch("app.adapters.api.blinkit_api.time.sleep"):
                result = self.adapter.acknowledge_po("BL-001")

        assert result["success"] is True

    def test_acknowledge_po_fails_after_max_retries(self) -> None:
        import respx
        import httpx

        with respx.mock:
            respx.post(self._ack_url()).mock(
                return_value=httpx.Response(503, json={"error": "overloaded"})
            )
            with patch("app.adapters.api.blinkit_api.time.sleep"):
                result = self.adapter.acknowledge_po("BL-001")

        assert result["success"] is False

    def test_acknowledge_po_no_retry_on_4xx(self) -> None:
        import respx
        import httpx

        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(400, json={"error": "bad request"})

        with respx.mock:
            respx.post(self._ack_url()).mock(side_effect=handler)
            with patch("app.adapters.api.blinkit_api.time.sleep"):
                result = self.adapter.acknowledge_po("BL-001")

        assert result["success"] is False
        assert call_count == 1  # no retry on 4xx

    def test_send_asn_success(self) -> None:
        import respx
        import httpx

        with respx.mock:
            respx.post(self._asn_url()).mock(
                return_value=httpx.Response(200, json={"asn_id": "ASN-BL-9999", "success": True})
            )
            result = self.adapter.send_asn({"po_number": "BL-001"})

        assert result["success"] is True
        assert result["asn_id"] == "ASN-BL-9999"

    def test_send_asn_rate_limited(self) -> None:
        import respx
        import httpx

        responses = [
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"asn_id": "ASN-BL-0001"}),
        ]

        slept: list[float] = []

        with respx.mock:
            respx.post(self._asn_url()).mock(side_effect=responses)
            with patch("app.adapters.api.blinkit_api.time.sleep", side_effect=slept.append):
                result = self.adapter.send_asn({})

        assert result["success"] is True
        assert slept[0] == 2


# ── Blinkit webhook route ─────────────────────────────────────────────────────

class TestBlinkitWebhookRoute:
    """
    Tests for POST /api/webhooks/BLINKIT using FastAPI TestClient.
    DB + RQ are mocked via patch.
    """

    def _make_partner(self, with_secret: bool = False) -> MagicMock:
        p = MagicMock()
        p.id = __import__("uuid").uuid4()
        p.code = "BLINKIT"
        p.webhook_secret = "my-secret" if with_secret else None
        p.source_channel = "WEBHOOK"
        return p

    def _make_app_with_mock_db(self, db_returns: Any) -> Any:
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.deps import get_sync_db

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.scalar_one_or_none.return_value = db_returns

        def override_db():
            yield mock_session

        app.dependency_overrides[get_sync_db] = override_db
        client = TestClient(app, raise_server_exceptions=False)
        return client, app

    def test_unknown_partner_returns_404(self) -> None:
        client, app = self._make_app_with_mock_db(None)
        try:
            resp = client.post("/api/webhooks/UNKNOWN_PARTNER", json={"po_number": "X"})
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_blinkit_returns_ack_format(self) -> None:
        partner = self._make_partner()
        client, app = self._make_app_with_mock_db(partner)
        try:
            with patch("app.api.routes.webhooks._save_raw_message", return_value=None):
                resp = client.post(
                    "/api/webhooks/BLINKIT",
                    json={"po_number": "BL-001", "type": "PO_CREATION", "details": {}},
                )
            assert resp.status_code == 200
            body = resp.json()
            assert body["success"] is True
            assert body["data"]["po_number"] == "BL-001"
            assert body["data"]["po_status"] == "processing"
        finally:
            app.dependency_overrides.clear()

    def test_blinkit_rejects_bad_api_key(self) -> None:
        partner = self._make_partner(with_secret=True)
        client, app = self._make_app_with_mock_db(partner)
        try:
            resp = client.post(
                "/api/webhooks/BLINKIT",
                json={"po_number": "BL-001"},
                headers={"api-key": "wrong-key"},
            )
            assert resp.status_code == 401
        finally:
            app.dependency_overrides.clear()

    def test_blinkit_accepts_correct_api_key(self) -> None:
        partner = self._make_partner(with_secret=True)
        client, app = self._make_app_with_mock_db(partner)
        try:
            with patch("app.api.routes.webhooks._save_raw_message", return_value=None):
                resp = client.post(
                    "/api/webhooks/BLINKIT",
                    json={"po_number": "BL-002"},
                    headers={"api-key": "my-secret"},
                )
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_blinkit_handles_malformed_json_gracefully(self) -> None:
        partner = self._make_partner()
        client, app = self._make_app_with_mock_db(partner)
        try:
            with patch("app.api.routes.webhooks._save_raw_message", return_value=None):
                resp = client.post(
                    "/api/webhooks/BLINKIT",
                    content=b"not valid json",
                    headers={"Content-Type": "application/json"},
                )
            # Must return 200 — Blinkit must always get an ACK
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()
