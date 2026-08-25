"""
Unit tests for ASN cancellation.

Zepto exposes DELETE /api/v1/external/asn?asnNumber=... (contract v12 §2.b) and states
it is half of the only correction path, since there is no update API. Blinkit's POVMS
ASN Sync contract defines creation only, so there is nothing to call — and saying so is
the point: an ASN marked cancelled here while the retailer still holds it is worse than
one never cancelled, because their warehouse still expects the truck.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.workflows.cancel_asn import NO_CANCEL_API, cancel_asn


def _db(asn, partner, msg=None):
    db = MagicMock()
    db.get.side_effect = lambda model, _id: (
        asn if model.__name__ == "EdiAdvanceShipNotice" else partner
    )
    db.execute.return_value.scalar_one_or_none.return_value = msg
    return db


def _asn(status="SENT", number="ASN-1"):
    return SimpleNamespace(id=uuid.uuid4(), asn_number=number, status=status,
                           trading_partner_id=uuid.uuid4())


def _msg(status="SENT", partner_reference="JAI005MEA00972"):
    return SimpleNamespace(id=uuid.uuid4(), status=status,
                           partner_reference=partner_reference, next_retry_at=None,
                           error_message=None)


class TestBlinkitHasNoCancellationApi:
    def test_blinkit_is_refused_with_the_reason(self) -> None:
        asn = _asn()
        db = _db(asn, SimpleNamespace(code="BLINKIT"))

        result = cancel_asn(db, asn.id, cancelled_by="ops@x.com")

        assert result.success is False
        assert "no cancellation endpoint" in (result.error or "")

    def test_blinkit_status_is_left_alone(self) -> None:
        """The whole point: do not mark it cancelled when the retailer still has it."""
        asn = _asn()
        db = _db(asn, SimpleNamespace(code="BLINKIT"))

        cancel_asn(db, asn.id, cancelled_by="ops@x.com")

        assert asn.status == "SENT"

    def test_registry_names_blinkit(self) -> None:
        assert "BLINKIT" in NO_CANCEL_API
        assert "ZEPTO" not in NO_CANCEL_API


class TestZeptoCancellation:
    def test_calls_zepto_with_their_asn_number(self) -> None:
        asn, msg = _asn(), _msg(partner_reference="JAI005MEA00972")
        db = _db(asn, SimpleNamespace(code="ZEPTO"), msg)

        with patch("app.adapters.api.zepto_api.ZeptoApiAdapter") as adapter:
            adapter.return_value.cancel_asn.return_value = {"success": True}
            result = cancel_asn(db, asn.id, cancelled_by="ops@x.com")

        adapter.return_value.cancel_asn.assert_called_once()
        assert adapter.return_value.cancel_asn.call_args[0][0] == "JAI005MEA00972"
        assert result.success is True
        assert asn.status == "CANCELLED"
        assert msg.status == "CANCELLED"

    def test_refusal_leaves_local_state_untouched(self) -> None:
        asn, msg = _asn(), _msg()
        db = _db(asn, SimpleNamespace(code="ZEPTO"), msg)

        with patch("app.adapters.api.zepto_api.ZeptoApiAdapter") as adapter:
            adapter.return_value.cancel_asn.return_value = {
                "success": False, "error": "ASN not found",
            }
            result = cancel_asn(db, asn.id, cancelled_by="ops@x.com")

        assert result.success is False
        assert asn.status == "SENT"
        assert msg.status == "SENT"

    def test_missing_partner_reference_is_refused(self) -> None:
        """Sent before partner_reference existed — nothing to address the cancel to."""
        asn, msg = _asn(), _msg(partner_reference=None)
        db = _db(asn, SimpleNamespace(code="ZEPTO"), msg)

        result = cancel_asn(db, asn.id, cancelled_by="ops@x.com")

        assert result.success is False
        assert "directly" in (result.error or "")
        assert asn.status == "SENT"


class TestNotYetDispatched:
    def test_pending_asn_cancels_locally_without_calling_out(self) -> None:
        asn, msg = _asn(), _msg(status="PENDING", partner_reference=None)
        db = _db(asn, SimpleNamespace(code="ZEPTO"), msg)

        with patch("app.adapters.api.zepto_api.ZeptoApiAdapter") as adapter:
            result = cancel_asn(db, asn.id, cancelled_by="ops@x.com")

        adapter.assert_not_called()
        assert result.success is True
        assert asn.status == "CANCELLED"
        assert msg.status == "FAILED"
        assert msg.next_retry_at is None

    def test_already_cancelled_is_idempotent(self) -> None:
        asn = _asn(status="CANCELLED")
        db = _db(asn, SimpleNamespace(code="ZEPTO"))

        result = cancel_asn(db, asn.id, cancelled_by="ops@x.com")

        assert result.success is True
        assert result.already_cancelled is True


class TestCancellationIdempotencyKey:
    """
    The outbound message id was sent as the idempotency key for both the create and
    the cancel, so Zepto matched the cancellation against the creation it had already
    processed and refused it:

        Past interaction found. Skipping duplicate event
        (requestId: 5b35d5ed-92a0-4a3d-a78f-b2a2394043b4)

    The contract says the key identifies a request, and create and cancel are two.
    """

    def test_key_differs_from_the_message_id_used_to_send(self) -> None:
        from app.workflows.cancel_asn import _cancel_key

        msg_id = uuid.uuid4()

        assert _cancel_key(msg_id) != str(msg_id)

    def test_key_is_stable_so_a_retried_cancel_stays_idempotent(self) -> None:
        from app.workflows.cancel_asn import _cancel_key

        msg_id = uuid.uuid4()

        assert _cancel_key(msg_id) == _cancel_key(msg_id)

    def test_different_messages_get_different_keys(self) -> None:
        from app.workflows.cancel_asn import _cancel_key

        assert _cancel_key(uuid.uuid4()) != _cancel_key(uuid.uuid4())

    def test_cancel_is_called_with_the_derived_key(self) -> None:
        from app.workflows.cancel_asn import _cancel_key, cancel_asn

        asn, msg = _asn(), _msg()
        db = _db(asn, SimpleNamespace(code="ZEPTO"), msg)

        with patch("app.adapters.api.zepto_api.ZeptoApiAdapter") as adapter:
            adapter.return_value.cancel_asn.return_value = {"success": True}
            cancel_asn(db, asn.id, cancelled_by="ops@x.com")

        sent_key = adapter.return_value.cancel_asn.call_args.kwargs["idempotency_key"]
        assert sent_key == _cancel_key(msg.id)
        assert sent_key != str(msg.id)
