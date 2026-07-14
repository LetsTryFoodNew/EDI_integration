"""
Unit tests for Phase 6 — SAP B1 Service Layer integration.

Tests cover:
  - B1ApiError / B1SessionError / B1ClosedPeriodError parsing
  - SessionPool acquire/release/invalidate/timeout/expiry
  - ServiceLayerClient: create_sales_order (200/201), 401 retry, 400 errors, network errors
  - po_to_sales_order mapper: header UDFs, line UoM conversion, missing CardCode, missing ItemCode
  - canonical_to_b1 workflow: idempotency, status transitions, B1 log written on both success & failure

Uses the `responses` library to mock `requests` HTTP calls.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest
import responses as responses_lib

from app.sap_b1.errors import B1ApiError, B1ClosedPeriodError, B1SessionError
from app.sap_b1.session_pool import SessionPool, _SESSION_TTL_S


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _err_body(code: int, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": {"lang": "en-us", "value": message}}}


def _make_partner(**kwargs: Any) -> MagicMock:
    p = MagicMock()
    p.id = uuid.uuid4()
    p.code = kwargs.get("code", "BLINKIT")
    p.name = kwargs.get("name", "Blinkit")
    p.b1_card_code = kwargs.get("b1_card_code", "C_BLINKIT")
    return p


def _make_seller(**kwargs: Any) -> MagicMock:
    s = MagicMock()
    s.b1_bpl_id = kwargs.get("b1_bpl_id", 1)
    return s


def _make_po(**kwargs: Any) -> MagicMock:
    po = MagicMock()
    po.id = uuid.uuid4()
    po.correlation_id = uuid.uuid4()
    po.buyer_po_number = kwargs.get("buyer_po_number", "PO-001")
    po.buyer_gstin = kwargs.get("buyer_gstin", "27AABCT1234M1Z5")
    po.buyer_po_date = kwargs.get("buyer_po_date", None)
    po.requested_delivery_date = kwargs.get("requested_delivery_date", None)
    po.created_at = datetime.now(UTC)
    po.b1_sales_order_doc_entry = kwargs.get("b1_sales_order_doc_entry", None)
    po.b1_sales_order_doc_num = kwargs.get("b1_sales_order_doc_num", None)
    return po


def _make_line(**kwargs: Any) -> MagicMock:
    line = MagicMock()
    line.id = uuid.uuid4()
    line.line_number = kwargs.get("line_number", 1)
    line.buyer_sku = kwargs.get("buyer_sku", "SKU-001")
    line.sap_material_no = kwargs.get("sap_material_no", "ITEM001")
    line.ordered_qty = kwargs.get("ordered_qty", Decimal("10"))
    line.unit_price = kwargs.get("unit_price", Decimal("50.00"))
    line.b1_whs_code = kwargs.get("b1_whs_code", "WH01")
    line.buyer_uom = kwargs.get("buyer_uom", "PC")
    line.hsn_code = kwargs.get("hsn_code", "21069099")
    return line


def _make_mapping(**kwargs: Any) -> MagicMock:
    m = MagicMock()
    m.buyer_sku = kwargs.get("buyer_sku", "SKU-001")
    m.qty_per_buyer_uom = kwargs.get("qty_per_buyer_uom", Decimal("1"))
    return m


# ─────────────────────────────────────────────────────────────────────────────
# B1ApiError
# ─────────────────────────────────────────────────────────────────────────────

class TestB1ApiError:
    def test_from_response_basic_400(self) -> None:
        body = _err_body(-1, "Item does not exist")
        exc = B1ApiError.from_response(400, body)
        assert isinstance(exc, B1ApiError)
        assert exc.http_status == 400
        assert exc.b1_code == -1
        assert "Item does not exist" in str(exc)

    def test_from_response_401_raises_session_error(self) -> None:
        body = _err_body(-1, "Session expired")
        exc = B1ApiError.from_response(401, body)
        assert isinstance(exc, B1SessionError)
        assert exc.http_status == 401

    def test_from_response_closed_period(self) -> None:
        body = _err_body(-5002, "Posting period is closed for 01-2024")
        exc = B1ApiError.from_response(400, body)
        assert isinstance(exc, B1ClosedPeriodError)
        assert exc.b1_code == -5002

    def test_from_response_closed_period_by_message(self) -> None:
        body = _err_body(0, "The posting period is locked")
        exc = B1ApiError.from_response(400, body)
        assert isinstance(exc, B1ClosedPeriodError)

    def test_repr(self) -> None:
        exc = B1ApiError("test error", code=42, http_status=500)
        r = repr(exc)
        assert "B1ApiError" in r
        assert "42" in r

    def test_missing_error_key_falls_back(self) -> None:
        exc = B1ApiError.from_response(400, {})
        assert isinstance(exc, B1ApiError)
        assert exc.b1_code == 0


# ─────────────────────────────────────────────────────────────────────────────
# SessionPool
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionPool:
    def _make_pool(self, max_sessions: int = 2, sessions: list[str] | None = None) -> SessionPool:
        session_ids = iter(sessions or ["s1", "s2", "s3", "s4", "s5"])
        logged_out: list[str] = []

        def login_fn() -> str:
            return next(session_ids)

        def logout_fn(sid: str) -> None:
            logged_out.append(sid)

        pool = SessionPool(max_sessions=max_sessions, login_fn=login_fn, logout_fn=logout_fn)
        pool._logged_out = logged_out  # type: ignore[attr-defined]
        return pool

    def test_acquire_creates_session(self) -> None:
        pool = self._make_pool()
        sid = pool.acquire()
        assert sid == "s1"
        assert pool.active_count == 1
        assert pool.busy_count == 1

    def test_acquire_reuses_free_session(self) -> None:
        pool = self._make_pool()
        sid = pool.acquire()
        pool.release(sid)
        sid2 = pool.acquire()
        assert sid == sid2  # reused
        assert pool.active_count == 1

    def test_acquire_creates_up_to_max(self) -> None:
        pool = self._make_pool(max_sessions=2)
        s1 = pool.acquire()
        s2 = pool.acquire()
        assert pool.active_count == 2
        assert pool.busy_count == 2
        pool.release(s1)
        pool.release(s2)

    def test_acquire_blocks_when_all_busy_then_succeeds(self) -> None:
        pool = self._make_pool(max_sessions=1)
        s1 = pool.acquire()

        released = threading.Event()

        def releaser() -> None:
            time.sleep(0.1)
            pool.release(s1)
            released.set()

        t = threading.Thread(target=releaser, daemon=True)
        t.start()
        s2 = pool.acquire()  # should block until releaser fires
        assert s2 == s1  # reused
        released.wait(timeout=2)

    def test_acquire_timeout_raises(self) -> None:
        import app.sap_b1.session_pool as sp_module
        original = sp_module._ACQUIRE_TIMEOUT_S
        sp_module._ACQUIRE_TIMEOUT_S = 0.1  # type: ignore[attr-defined]
        try:
            pool = self._make_pool(max_sessions=1)
            pool.acquire()  # fills the pool
            with pytest.raises(TimeoutError):
                pool.acquire()  # times out
        finally:
            sp_module._ACQUIRE_TIMEOUT_S = original  # type: ignore[attr-defined]

    def test_invalidate_removes_session(self) -> None:
        pool = self._make_pool()
        sid = pool.acquire()
        pool.invalidate(sid)
        assert pool.active_count == 0

    def test_close_all_clears_pool(self) -> None:
        pool = self._make_pool()
        pool.acquire()
        pool.close_all()
        assert pool.active_count == 0

    def test_expired_session_purged_on_next_acquire(self) -> None:
        pool = self._make_pool()
        s1 = pool.acquire()
        pool.release(s1)

        # Force-expire the session
        with pool._lock:
            pool._sessions[0].last_used = datetime(2000, 1, 1, tzinfo=UTC)

        s2 = pool.acquire()
        # A new session was created because s1 was purged
        assert s2 != s1


# ─────────────────────────────────────────────────────────────────────────────
# ServiceLayerClient
# ─────────────────────────────────────────────────────────────────────────────

BASE = "http://b1-test:50000"
COMPANY_DB = "SBO_TEST"
LOGIN_URL = f"{BASE}/b1s/v1/Login"
ORDERS_URL = f"{BASE}/b1s/v1/Orders"


def _add_login(session_id: str = "SESS123", status: int = 200) -> None:
    responses_lib.add(
        responses_lib.POST, LOGIN_URL,
        json={"SessionId": session_id},
        status=status,
    )


class TestServiceLayerClient:
    def _make_client(self) -> Any:
        from app.sap_b1.client import ServiceLayerClient
        return ServiceLayerClient(
            base_url=BASE,
            company_db=COMPANY_DB,
            username="EDI_BOT",
            password="secret",
            pool_size=1,
            verify_ssl=False,
        )

    @responses_lib.activate
    def test_create_sales_order_success(self) -> None:
        _add_login()
        responses_lib.add(
            responses_lib.POST, ORDERS_URL,
            json={"DocEntry": 42, "DocNum": 100},
            status=201,
        )
        client = self._make_client()
        result = client.create_sales_order({"CardCode": "C_BLINKIT"})
        assert result["DocEntry"] == 42
        assert result["DocNum"] == 100

    @responses_lib.activate
    def test_create_sales_order_400_raises_b1_api_error(self) -> None:
        _add_login()
        responses_lib.add(
            responses_lib.POST, ORDERS_URL,
            json=_err_body(-1, "Item code does not exist"),
            status=400,
        )
        client = self._make_client()
        with pytest.raises(B1ApiError) as exc_info:
            client.create_sales_order({"CardCode": "C_BLINKIT"})
        assert exc_info.value.http_status == 400
        assert "Item code" in str(exc_info.value)

    @responses_lib.activate
    def test_401_triggers_relogin_and_retry(self) -> None:
        # First login succeeds, second login gives a fresh session
        _add_login("OLD_SESS")
        _add_login("NEW_SESS")  # re-login after 401
        # First request: 401
        responses_lib.add(
            responses_lib.POST, ORDERS_URL,
            json=_err_body(-1, "Session expired"),
            status=401,
        )
        # Retry succeeds
        responses_lib.add(
            responses_lib.POST, ORDERS_URL,
            json={"DocEntry": 99, "DocNum": 200},
            status=201,
        )
        client = self._make_client()
        result = client.create_sales_order({"CardCode": "C_BLINKIT"})
        assert result["DocEntry"] == 99

    @responses_lib.activate
    def test_get_item_returns_none_on_404(self) -> None:
        _add_login()
        responses_lib.add(
            responses_lib.GET, f"{BASE}/b1s/v1/Items('FAKE')",
            json=_err_body(-1, "Not found"),
            status=404,
        )
        client = self._make_client()
        result = client.get_item("FAKE")
        assert result is None

    @responses_lib.activate
    def test_get_item_success(self) -> None:
        _add_login()
        responses_lib.add(
            responses_lib.GET, f"{BASE}/b1s/v1/Items('ITEM001')",
            json={"ItemCode": "ITEM001", "ItemName": "Test Item"},
            status=200,
        )
        client = self._make_client()
        result = client.get_item("ITEM001")
        assert result is not None
        assert result["ItemCode"] == "ITEM001"

    @responses_lib.activate
    def test_network_error_raises_b1_api_error(self) -> None:
        import requests as req_lib
        _add_login()
        responses_lib.add(
            responses_lib.POST, ORDERS_URL,
            body=req_lib.exceptions.ConnectionError("Connection refused"),
        )
        client = self._make_client()
        with pytest.raises(B1ApiError) as exc_info:
            client.create_sales_order({})
        assert "Network error" in str(exc_info.value)

    @responses_lib.activate
    def test_query_returns_value_array(self) -> None:
        _add_login()
        responses_lib.add(
            responses_lib.GET, f"{BASE}/b1s/v1/Orders",
            json={"value": [{"DocEntry": 1}, {"DocEntry": 2}]},
            status=200,
        )
        client = self._make_client()
        result = client.query("Orders", top=10)
        assert len(result) == 2
        assert result[0]["DocEntry"] == 1

    def test_empty_base_url_raises(self) -> None:
        from app.sap_b1.client import ServiceLayerClient
        with pytest.raises(ValueError, match="base_url"):
            ServiceLayerClient(base_url="", company_db="X", username="u", password="p")


# ─────────────────────────────────────────────────────────────────────────────
# po_to_sales_order mapper
# ─────────────────────────────────────────────────────────────────────────────

class TestPoToSalesOrder:
    def _call(self, po: Any = None, lines: list[Any] | None = None,
              partner: Any = None, seller: Any = None,
              sku_mappings: dict[str, Any] | None = None) -> dict[str, Any]:
        from app.mappers.po_to_sales_order import build_sales_order_payload
        return build_sales_order_payload(
            po=po or _make_po(),
            lines=lines if lines is not None else [_make_line()],
            partner=partner or _make_partner(),
            seller=seller or _make_seller(),
            sku_mappings=sku_mappings if sku_mappings is not None else {"SKU-001": _make_mapping()},
        )

    def test_header_fields_populated(self) -> None:
        po = _make_po(buyer_po_number="PO-XYZ", buyer_gstin="27AABCT1234M1Z5")
        partner = _make_partner(b1_card_code="C_TEST", code="TEST_CO")
        payload = self._call(po=po, partner=partner)

        assert payload["CardCode"] == "C_TEST"
        assert payload["U_EDI_PO_NUMBER"] == "PO-XYZ"
        assert payload["U_BUYER_GSTIN"] == "27AABCT1234M1Z5"
        assert payload["U_EDI_SOURCE"] == "TEST_CO"
        assert "U_EDI_DOC_UUID" in payload
        assert "U_EDI_RECEIVED_AT" in payload

    def test_line_fields_populated(self) -> None:
        line = _make_line(buyer_sku="SKU-001", sap_material_no="ITEM001",
                          ordered_qty=Decimal("5"), unit_price=Decimal("100.00"),
                          b1_whs_code="WH01", hsn_code="12345678")
        mapping = _make_mapping(buyer_sku="SKU-001", qty_per_buyer_uom=Decimal("1"))
        payload = self._call(lines=[line], sku_mappings={"SKU-001": mapping})

        doc_line = payload["DocumentLines"][0]
        assert doc_line["ItemCode"] == "ITEM001"
        assert doc_line["Quantity"] == 5.0
        assert doc_line["Price"] == 100.0
        assert doc_line["WarehouseCode"] == "WH01"
        assert doc_line["HSNOrSACCode"] == "12345678"
        assert doc_line["U_BUYER_SKU"] == "SKU-001"

    def test_uom_conversion_applied(self) -> None:
        line = _make_line(buyer_sku="SKU-001", ordered_qty=Decimal("10"))
        mapping = _make_mapping(buyer_sku="SKU-001", qty_per_buyer_uom=Decimal("24"))  # 1 case = 24 pcs
        payload = self._call(lines=[line], sku_mappings={"SKU-001": mapping})
        assert payload["DocumentLines"][0]["Quantity"] == pytest.approx(240.0)

    def test_missing_card_code_raises(self) -> None:
        from app.mappers.po_to_sales_order import build_sales_order_payload
        partner = _make_partner(b1_card_code=None)
        partner.b1_card_code = None
        with pytest.raises(ValueError, match="b1_card_code"):
            build_sales_order_payload(
                po=_make_po(), lines=[_make_line()],
                partner=partner, seller=_make_seller(),
                sku_mappings={"SKU-001": _make_mapping()},
            )

    def test_missing_item_code_raises(self) -> None:
        from app.mappers.po_to_sales_order import build_sales_order_payload
        line = _make_line(sap_material_no=None)
        line.sap_material_no = None
        with pytest.raises(ValueError, match="sap_material_no"):
            build_sales_order_payload(
                po=_make_po(), lines=[line],
                partner=_make_partner(), seller=_make_seller(),
                sku_mappings={"SKU-001": _make_mapping()},
            )

    def test_empty_lines_raises(self) -> None:
        from app.mappers.po_to_sales_order import build_sales_order_payload
        with pytest.raises(ValueError, match="DocumentLine"):
            build_sales_order_payload(
                po=_make_po(), lines=[],
                partner=_make_partner(), seller=_make_seller(),
                sku_mappings={},
            )

    def test_bpl_id_defaults_to_1(self) -> None:
        seller = _make_seller(b1_bpl_id=None)
        seller.b1_bpl_id = None
        payload = self._call(seller=seller)
        assert payload["BPL_IDAssignedToInvoice"] == 1

    def test_bpl_id_uses_seller_value(self) -> None:
        seller = _make_seller(b1_bpl_id=3)
        payload = self._call(seller=seller)
        assert payload["BPL_IDAssignedToInvoice"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# canonical_to_b1 workflow (push_po_to_b1)
# ─────────────────────────────────────────────────────────────────────────────

class TestPushPoToB1:
    """Integration-style tests for the push_po_to_b1 workflow with mocked DB and B1 client."""

    def _mock_session(self, po: Any, partner: Any, seller: Any, lines: list[Any],
                      sku_mappings: dict[str, Any]) -> MagicMock:
        """Build a mock DB session that returns our test objects."""
        session = MagicMock()
        session.get.side_effect = lambda model, pk: {
            (type(po), po.id): po,
            (type(partner), partner.id): None,  # accessed via select
        }.get((model, pk))

        def mock_execute(stmt: Any) -> MagicMock:
            result = MagicMock()
            # Partner query
            if "SellerEntity" in str(stmt):
                result.scalar_one_or_none.return_value = seller
            # Lines query
            elif "EdiPoLineItem" in str(stmt):
                result.scalars.return_value.all.return_value = lines
            # SkuMapping query
            elif "SkuMapping" in str(stmt):
                result.scalars.return_value.all.return_value = list(sku_mappings.values())
            # EdiPurchaseOrder query (for sap push scheduler)
            elif "EdiPurchaseOrder" in str(stmt):
                result.scalars.return_value.all.return_value = [po.id]
            else:
                result.scalar_one_or_none.return_value = None
                result.scalars.return_value.all.return_value = []
            return result

        session.execute.side_effect = mock_execute
        session.__enter__ = lambda s: s
        session.__exit__ = MagicMock(return_value=False)
        return session

    def _ctx_session(self, session: MagicMock) -> Any:
        """Return a context-manager-aware mock that yields session."""
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=session)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    def test_idempotency_already_pushed(self) -> None:
        from app.workflows.canonical_to_b1 import push_po_to_b1
        po = _make_po(b1_sales_order_doc_entry=42, b1_sales_order_doc_num=100)

        session = MagicMock()
        session.get.return_value = po

        with patch("app.db.SyncSessionLocal", return_value=self._ctx_session(session)):
            result = push_po_to_b1(po.id)

        assert result.skipped is True
        assert result.skip_reason == "already pushed"
        assert result.b1_doc_entry == 42

    def test_wrong_status_skipped(self) -> None:
        from app.workflows.canonical_to_b1 import push_po_to_b1
        from app.models._enums import PoStatus
        po = _make_po()
        po.po_status = PoStatus.PARSED
        po.b1_sales_order_doc_entry = None

        session = MagicMock()
        session.get.return_value = po

        with patch("app.db.SyncSessionLocal", return_value=self._ctx_session(session)):
            result = push_po_to_b1(po.id)

        assert result.skipped is True
        assert "PARSED" in result.skip_reason

    def test_po_not_found(self) -> None:
        from app.workflows.canonical_to_b1 import push_po_to_b1
        po_id = uuid.uuid4()

        session = MagicMock()
        session.get.return_value = None

        with patch("app.db.SyncSessionLocal", return_value=self._ctx_session(session)):
            result = push_po_to_b1(po_id)

        assert result.success is False
        assert "not found" in result.error

    def test_unmapped_sku_marks_rejected(self) -> None:
        from app.workflows.canonical_to_b1 import push_po_to_b1
        from app.models._enums import PoStatus

        po = _make_po()
        po.po_status = PoStatus.VALIDATED
        po.b1_sales_order_doc_entry = None
        po.trading_partner_id = uuid.uuid4()

        line = _make_line(sap_material_no=None)
        line.sap_material_no = None
        partner = _make_partner()
        seller = _make_seller()

        session = MagicMock()

        def _get(model: Any, pk: Any) -> Any:
            return po if pk == po.id else partner

        session.get.side_effect = _get

        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = seller
        execute_result.scalars.return_value.all.return_value = [line]
        session.execute.return_value = execute_result

        with patch("app.db.SyncSessionLocal", return_value=self._ctx_session(session)):
            result = push_po_to_b1(po.id)

        assert result.success is False
        assert "unmapped" in result.error.lower()
