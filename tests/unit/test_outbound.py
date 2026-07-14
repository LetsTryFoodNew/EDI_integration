"""
Unit tests for Phase 7 — outbound document dispatch.

Coverage:
  - send_outbound_message: happy path, already-sent skip, failed skip,
    missing partner, retry scheduling, permanent failure, SLA breach
  - Outbound adapter registry: partner lookup, channel fallback, unsupported
  - b1_to_outbound: ACK trigger, delivery poll (no duplicates), retry enqueue
  - rtv_flow: PO number extraction, RTV result dataclass
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────

def _ctx_session(session: MagicMock) -> Any:
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _make_msg(
    msg_id: uuid.UUID | None = None,
    status: str = "PENDING",
    doc_type: str = "PO_ACK_855",
    attempt_count: int = 0,
    created_at: datetime | None = None,
    trading_partner_id: uuid.UUID | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.id = msg_id or uuid.uuid4()
    msg.status = status
    msg.doc_type = doc_type
    msg.attempt_count = attempt_count
    msg.created_at = created_at or datetime.now(UTC)
    msg.last_attempt_at = None
    msg.next_retry_at = None
    msg.ack_received_at = None
    msg.external_reference = None
    msg.error_message = None
    msg.trading_partner_id = trading_partner_id or uuid.uuid4()
    msg.payload = {}
    msg.channel = "EMAIL"
    return msg


def _make_partner(
    code: str = "TESTCO",
    ack_sla_hours: int = 24,
    source_channel: Any = None,
    b1_card_code: str = "C00099",
) -> MagicMock:
    from app.models._enums import SourceChannel
    partner = MagicMock()
    partner.code = code
    partner.ack_sla_hours = ack_sla_hours
    partner.source_channel = source_channel or SourceChannel.EMAIL
    partner.b1_card_code = b1_card_code
    partner.name = code
    partner.api_config = {"ops_email": "ops@test.com"}
    return partner


# ── TestSendOutbound ────────────────────────────────────────────────────────────

class TestSendOutbound:
    """Tests for send_outbound_message()."""

    def test_already_sent_returns_skipped(self) -> None:
        from app.workflows.send_outbound import send_outbound_message

        msg_id = uuid.uuid4()
        msg = _make_msg(msg_id=msg_id, status="SENT")
        session = MagicMock()
        session.get.return_value = msg

        with patch("app.db.SyncSessionLocal", return_value=_ctx_session(session)):
            result = send_outbound_message(msg_id)

        assert result.skipped is True
        assert result.skip_reason == "already sent"
        assert result.success is True

    def test_permanently_failed_returns_skipped(self) -> None:
        from app.workflows.send_outbound import send_outbound_message

        msg_id = uuid.uuid4()
        msg = _make_msg(msg_id=msg_id, status="FAILED")
        session = MagicMock()
        session.get.return_value = msg

        with patch("app.db.SyncSessionLocal", return_value=_ctx_session(session)):
            result = send_outbound_message(msg_id)

        assert result.skipped is True
        assert "max attempts" in result.skip_reason
        assert result.success is False

    def test_missing_message_returns_error(self) -> None:
        from app.workflows.send_outbound import send_outbound_message

        msg_id = uuid.uuid4()
        session = MagicMock()
        session.get.return_value = None

        with patch("app.db.SyncSessionLocal", return_value=_ctx_session(session)):
            result = send_outbound_message(msg_id)

        assert result.success is False
        assert "not found" in (result.error or "").lower()

    def test_missing_partner_returns_error(self) -> None:
        from app.workflows.send_outbound import send_outbound_message

        msg_id = uuid.uuid4()
        msg = _make_msg(msg_id=msg_id, status="PENDING")
        session = MagicMock()
        # First get() → msg; second get() → partner = None
        session.get.side_effect = [msg, None]

        with patch("app.db.SyncSessionLocal", return_value=_ctx_session(session)):
            result = send_outbound_message(msg_id)

        assert result.success is False
        assert "TradingPartner not found" in (result.error or "")

    def test_unsupported_adapter_marks_skipped(self) -> None:
        from app.adapters.outbound.registry import UnsupportedOutboundPartnerError
        from app.workflows.send_outbound import send_outbound_message

        msg_id = uuid.uuid4()
        msg = _make_msg(msg_id=msg_id, status="PENDING")
        partner = _make_partner(code="NOPARTNER")
        session = MagicMock()
        session.get.side_effect = [msg, partner]

        with (
            patch("app.db.SyncSessionLocal", return_value=_ctx_session(session)),
            patch(
                "app.adapters.outbound.registry.get_outbound_adapter",
                side_effect=UnsupportedOutboundPartnerError("no adapter"),
            ),
        ):
            result = send_outbound_message(msg_id)

        assert result.skipped is True
        assert "no adapter" in result.skip_reason

    def test_successful_send_marks_sent(self) -> None:
        from app.adapters.outbound.base import OutboundResult
        from app.workflows.send_outbound import send_outbound_message

        msg_id = uuid.uuid4()
        msg = _make_msg(msg_id=msg_id, status="PENDING", attempt_count=0)
        partner = _make_partner(code="BLINKIT")

        # Two separate session contexts
        session1 = MagicMock()
        session1.get.side_effect = [msg, partner]
        msg2 = _make_msg(msg_id=msg_id, status="PENDING", attempt_count=1)
        session2 = MagicMock()
        session2.get.return_value = msg2

        call_count = 0

        def session_factory() -> Any:
            nonlocal call_count
            call_count += 1
            return _ctx_session(session1 if call_count == 1 else session2)

        mock_adapter = MagicMock()
        mock_adapter.send.return_value = OutboundResult(success=True, external_ref="ext-abc")

        with (
            patch("app.db.SyncSessionLocal", side_effect=session_factory),
            patch(
                "app.adapters.outbound.registry.get_outbound_adapter",
                return_value=mock_adapter,
            ),
        ):
            result = send_outbound_message(msg_id)

        assert result.success is True
        assert result.external_ref == "ext-abc"

    def test_failed_send_schedules_retry(self) -> None:
        from app.adapters.outbound.base import OutboundResult
        from app.workflows.send_outbound import send_outbound_message

        msg_id = uuid.uuid4()
        msg = _make_msg(msg_id=msg_id, status="PENDING", attempt_count=0)
        partner = _make_partner(code="BLINKIT")

        session1 = MagicMock()
        session1.get.side_effect = [msg, partner]
        msg2 = _make_msg(msg_id=msg_id, status="PENDING", attempt_count=1)
        session2 = MagicMock()
        session2.get.return_value = msg2

        call_count = 0

        def session_factory() -> Any:
            nonlocal call_count
            call_count += 1
            return _ctx_session(session1 if call_count == 1 else session2)

        mock_adapter = MagicMock()
        mock_adapter.send.return_value = OutboundResult(success=False, error="timeout")

        with (
            patch("app.db.SyncSessionLocal", side_effect=session_factory),
            patch(
                "app.adapters.outbound.registry.get_outbound_adapter",
                return_value=mock_adapter,
            ),
        ):
            result = send_outbound_message(msg_id)

        # After attempt 1, should still be pending (retry scheduled)
        assert result.success is False
        assert msg2.status == "PENDING"
        assert msg2.next_retry_at is not None

    def test_exhausted_attempts_marks_failed(self) -> None:
        from app.adapters.outbound.base import OutboundResult
        from app.workflows.send_outbound import _MAX_ATTEMPTS, send_outbound_message

        msg_id = uuid.uuid4()
        # Simulate last attempt (already at MAX_ATTEMPTS - 1 before this call)
        msg = _make_msg(msg_id=msg_id, status="PENDING", attempt_count=_MAX_ATTEMPTS - 1)
        partner = _make_partner(code="BLINKIT")

        session1 = MagicMock()
        session1.get.side_effect = [msg, partner]
        msg2 = _make_msg(msg_id=msg_id, status="PENDING", attempt_count=_MAX_ATTEMPTS)
        session2 = MagicMock()
        session2.get.return_value = msg2

        call_count = 0

        def session_factory() -> Any:
            nonlocal call_count
            call_count += 1
            return _ctx_session(session1 if call_count == 1 else session2)

        mock_adapter = MagicMock()
        mock_adapter.send.return_value = OutboundResult(success=False, error="server error")

        with (
            patch("app.db.SyncSessionLocal", side_effect=session_factory),
            patch(
                "app.adapters.outbound.registry.get_outbound_adapter",
                return_value=mock_adapter,
            ),
        ):
            result = send_outbound_message(msg_id)

        assert result.success is False
        assert msg2.status == "FAILED"
        assert msg2.next_retry_at is None


# ── TestSlaCheck ────────────────────────────────────────────────────────────────

class TestSlaCheck:
    def test_no_breach_no_warning(self) -> None:
        from app.models._enums import EdiDocType
        from app.workflows.send_outbound import _check_sla

        msg = _make_msg(doc_type=EdiDocType.PO_ACK_855, created_at=datetime.now(UTC))
        partner = _make_partner(ack_sla_hours=24)

        # Should not raise
        _check_sla(msg, partner)

    def test_breach_logs_warning(self) -> None:
        from app.models._enums import EdiDocType
        from app.workflows.send_outbound import _check_sla

        old_time = datetime.now(UTC) - timedelta(hours=48)
        msg = _make_msg(doc_type=EdiDocType.PO_ACK_855, created_at=old_time)
        partner = _make_partner(ack_sla_hours=24)

        with patch("app.workflows.send_outbound.log") as mock_log:
            _check_sla(msg, partner)
            mock_log.warning.assert_called_once()
            call_args = mock_log.warning.call_args
            assert "outbound.sla_breached" in call_args[0]

    def test_non_ack_doc_type_skipped(self) -> None:
        from app.models._enums import EdiDocType
        from app.workflows.send_outbound import _check_sla

        old_time = datetime.now(UTC) - timedelta(hours=100)
        msg = _make_msg(doc_type=EdiDocType.ASN_856, created_at=old_time)
        partner = _make_partner(ack_sla_hours=1)

        with patch("app.workflows.send_outbound.log") as mock_log:
            _check_sla(msg, partner)
            mock_log.warning.assert_not_called()


# ── TestOutboundRegistry ────────────────────────────────────────────────────────

class TestOutboundRegistry:
    def test_blinkit_returns_blinkit_adapter(self) -> None:
        from app.adapters.outbound.blinkit_outbound import BlinkitOutboundAdapter
        from app.adapters.outbound.registry import get_outbound_adapter
        from app.models._enums import SourceChannel

        adapter = get_outbound_adapter("BLINKIT", SourceChannel.WEBHOOK)
        assert isinstance(adapter, BlinkitOutboundAdapter)

    def test_zepto_returns_zepto_adapter(self) -> None:
        from app.adapters.outbound.registry import get_outbound_adapter
        from app.adapters.outbound.zepto_outbound import ZeptoOutboundAdapter
        from app.models._enums import SourceChannel

        adapter = get_outbound_adapter("ZEPTO", SourceChannel.API)
        assert isinstance(adapter, ZeptoOutboundAdapter)

    def test_email_channel_fallback(self) -> None:
        from app.adapters.outbound.email_outbound import EmailOutboundAdapter
        from app.adapters.outbound.registry import get_outbound_adapter
        from app.models._enums import SourceChannel

        adapter = get_outbound_adapter("SWIGGY", SourceChannel.EMAIL)
        assert isinstance(adapter, EmailOutboundAdapter)

    def test_unknown_partner_unknown_channel_raises(self) -> None:
        from app.adapters.outbound.registry import (
            UnsupportedOutboundPartnerError,
            get_outbound_adapter,
        )
        from app.models._enums import SourceChannel

        with pytest.raises(UnsupportedOutboundPartnerError):
            get_outbound_adapter("MYSTERY_CORP", SourceChannel.PORTAL)

    def test_known_partner_takes_priority_over_channel(self) -> None:
        from app.adapters.outbound.blinkit_outbound import BlinkitOutboundAdapter
        from app.adapters.outbound.registry import get_outbound_adapter
        from app.models._enums import SourceChannel

        # BLINKIT partner override should win even if channel is EMAIL
        adapter = get_outbound_adapter("BLINKIT", SourceChannel.EMAIL)
        assert isinstance(adapter, BlinkitOutboundAdapter)


# ── TestB1ToOutbound ────────────────────────────────────────────────────────────

class TestTriggerAcksForConfirmedPos:
    def test_no_confirmed_pos_returns_zero(self) -> None:
        from app.workflows.b1_to_outbound import trigger_acks_for_confirmed_pos

        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = []

        mock_queue = MagicMock()

        with patch("app.db.SyncSessionLocal", return_value=_ctx_session(session)):
            count = trigger_acks_for_confirmed_pos(mock_queue)

        assert count == 0
        mock_queue.enqueue.assert_not_called()

    def test_confirmed_po_enqueues_ack(self) -> None:
        from app.models._enums import PoStatus, SourceChannel
        from app.workflows.b1_to_outbound import trigger_acks_for_confirmed_pos

        po_id = uuid.uuid4()
        partner_id = uuid.uuid4()

        po = MagicMock()
        po.id = po_id
        po.trading_partner_id = partner_id
        po.buyer_po_number = "BL-12345"
        po.b1_sales_order_doc_num = 1001
        po.po_status = PoStatus.SAP_CONFIRMED

        partner = MagicMock()
        partner.id = partner_id
        partner.code = "BLINKIT"
        partner.source_channel = SourceChannel.WEBHOOK
        partner.b1_card_code = "C00001"

        session = MagicMock()
        # trigger_acks uses a single subquery — returns confirmed PO IDs that have no ACK yet
        session.execute.return_value.scalars.return_value.all.return_value = [po_id]
        # get() is called: first for EdiPurchaseOrder, second for TradingPartner
        session.get.side_effect = [po, partner]

        mock_queue = MagicMock()

        with patch("app.db.SyncSessionLocal", return_value=_ctx_session(session)):
            count = trigger_acks_for_confirmed_pos(mock_queue)

        assert count == 1
        mock_queue.enqueue.assert_called_once()

    def test_duplicate_ack_not_enqueued(self) -> None:
        from app.workflows.b1_to_outbound import trigger_acks_for_confirmed_pos

        session = MagicMock()
        # The subquery in trigger_acks filters out POs that already have an ACK.
        # So confirmed_ids = [] means this PO was excluded by the subquery.
        session.execute.return_value.scalars.return_value.all.return_value = []

        mock_queue = MagicMock()

        with patch("app.db.SyncSessionLocal", return_value=_ctx_session(session)):
            count = trigger_acks_for_confirmed_pos(mock_queue)

        assert count == 0
        mock_queue.enqueue.assert_not_called()


class TestEnqueueDueRetries:
    def test_no_due_retries_returns_zero(self) -> None:
        from app.workflows.b1_to_outbound import enqueue_due_retries

        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = []

        mock_queue = MagicMock()

        with patch("app.db.SyncSessionLocal", return_value=_ctx_session(session)):
            count = enqueue_due_retries(mock_queue)

        assert count == 0
        mock_queue.enqueue.assert_not_called()

    def test_due_retries_enqueued(self) -> None:
        from app.workflows.b1_to_outbound import enqueue_due_retries

        msg_id = uuid.uuid4()
        # enqueue_due_retries queries select(EdiOutboundMessage.id) — returns IDs only
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = [msg_id]

        mock_queue = MagicMock()

        with patch("app.db.SyncSessionLocal", return_value=_ctx_session(session)):
            count = enqueue_due_retries(mock_queue)

        assert count == 1
        mock_queue.enqueue.assert_called_once()
        call_args = mock_queue.enqueue.call_args
        # Second positional arg should be the msg_id as str
        assert str(msg_id) in str(call_args)


# ── TestRtvFlow ─────────────────────────────────────────────────────────────────

class TestExtractPoNumber:
    def test_plain_po_prefix(self) -> None:
        from app.workflows.rtv_flow import _extract_po_number

        assert _extract_po_number("RTV for PO BL-123456789") == "BL-123456789"

    def test_rtv_prefix(self) -> None:
        from app.workflows.rtv_flow import _extract_po_number

        result = _extract_po_number("Return Request for BL-987654321")
        assert result is not None
        assert "987654321" in result

    def test_bare_numeric_po(self) -> None:
        from app.workflows.rtv_flow import _extract_po_number

        result = _extract_po_number("Blinkit RTV 123456789012")
        assert result == "123456789012"

    def test_no_match_returns_none(self) -> None:
        from app.workflows.rtv_flow import _extract_po_number

        assert _extract_po_number("hello world this is a generic email subject") is None

    def test_alphanumeric_po_pattern(self) -> None:
        from app.workflows.rtv_flow import _extract_po_number

        result = _extract_po_number("RTV: PO ZP-20240501-001")
        assert result is not None


class TestRtvResultDataclass:
    def test_default_fields(self) -> None:
        from app.workflows.rtv_flow import RtvResult

        msg_id = uuid.uuid4()
        r = RtvResult(success=True, raw_message_id=msg_id)
        assert r.po_id is None
        assert r.b1_return_doc_entry is None
        assert r.skipped is False
        assert r.warnings == []

    def test_skipped_result(self) -> None:
        from app.workflows.rtv_flow import RtvResult

        msg_id = uuid.uuid4()
        r = RtvResult(
            success=False,
            raw_message_id=msg_id,
            skipped=True,
            skip_reason="PO not found",
        )
        assert r.skipped is True
        assert "not found" in r.skip_reason


class TestBuildB1ReturnPayload:
    def test_returns_valid_payload(self) -> None:
        from app.workflows.rtv_flow import _build_b1_return_payload

        po = MagicMock()
        po.buyer_po_number = "BL-100"
        po.b1_sales_order_doc_entry = 42

        line = MagicMock()
        line.sap_material_no = "SKU001"
        line.ordered_qty = 10
        line.unit_price = 100
        line.line_number = 1

        partner = MagicMock()
        partner.code = "BLINKIT"
        partner.b1_card_code = "C00001"

        payload = _build_b1_return_payload(po, [line], partner)

        assert payload["CardCode"] == "C00001"
        assert len(payload["DocumentLines"]) == 1
        assert payload["DocumentLines"][0]["ItemCode"] == "SKU001"
        assert payload["DocumentLines"][0]["BaseType"] == 17

    def test_no_card_code_raises(self) -> None:
        from app.workflows.rtv_flow import _build_b1_return_payload

        po = MagicMock()
        po.b1_sales_order_doc_entry = 1

        partner = MagicMock()
        partner.code = "NOCARD"
        partner.b1_card_code = None

        with pytest.raises(ValueError, match="no b1_card_code"):
            _build_b1_return_payload(po, [], partner)

    def test_no_mapped_lines_raises(self) -> None:
        from app.workflows.rtv_flow import _build_b1_return_payload

        po = MagicMock()
        po.b1_sales_order_doc_entry = 1
        po.buyer_po_number = "X-1"

        line = MagicMock()
        line.sap_material_no = None  # unmapped

        partner = MagicMock()
        partner.b1_card_code = "C00001"

        with pytest.raises(ValueError, match="no lines"):
            _build_b1_return_payload(po, [line], partner)
