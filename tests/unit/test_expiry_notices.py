"""
A partner's "this PO is over" notice must not become a purchase order.

Zepto's event feed carries an `UpdatePO` with `status: EXPIRED` when a PO passes its
expiryDate, and the poll window reaches back as far as their 45-day cap. Two defects
between the parser and the database turned that housekeeping into work for ops:

  1. `_save_canonical_po` hardcoded `po_status=PARSED`, discarding the CANCELLED the
     parser had already worked out. validate_po's guard against touching a dead PO
     therefore never fired, and 533 expired Zepto POs sat in the exceptions queue.

  2. An expiry for a PO number we had never held created a *new* PO at version 1,
     because there was nothing to supersede. 303 of 540 Zepto POs existed for no
     other reason — which is how four real POs in a day read as twenty-eight.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models._enums import PoStatus
from app.parsers.zepto_parser import ZeptoParser
from app.workflows import parse_and_persist as pap

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _raw(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), payload=payload, external_id="evt-1", trading_partner_id=uuid4()
    )


def _parse(status: str) -> object:
    payload = json.loads((FIXTURES / "zepto_po_event.json").read_text())
    payload["status"] = status
    result = ZeptoParser().parse(_raw(payload))
    assert result.success and result.doc is not None
    return result.doc


class TestPersistedStatus:
    def test_expired_po_is_stored_cancelled_not_parsed(self) -> None:
        assert pap._persisted_status(_parse("EXPIRED")) is PoStatus.CANCELLED

    def test_live_po_is_stored_parsed(self) -> None:
        assert pap._persisted_status(_parse("RELEASED")) is PoStatus.PARSED

    @pytest.mark.parametrize("status", [PoStatus.RECEIVED, PoStatus.VALIDATED, None])
    def test_non_terminal_doc_status_is_ignored(self, status: PoStatus | None) -> None:
        # EDI850.po_status defaults to RECEIVED and most parsers never set it, so a
        # non-terminal value is silence rather than an instruction. Honouring it would
        # let a doc skip straight past PARSED.
        assert pap._persisted_status(SimpleNamespace(po_status=status)) is PoStatus.PARSED

    def test_superseded_is_honoured_too(self) -> None:
        doc = SimpleNamespace(po_status=PoStatus.SUPERSEDED)
        assert pap._persisted_status(doc) is PoStatus.SUPERSEDED


class TestOrphanTerminalNotice:
    def _check(self, doc: object, *, existing: object | None, monkeypatch) -> bool:
        monkeypatch.setattr(pap, "_find_existing_po", lambda *a, **k: existing)
        partner = SimpleNamespace(id=uuid4(), code="ZEPTO")
        return pap._is_orphan_terminal_notice(object(), doc, partner)

    def test_expiry_for_a_po_we_never_held_is_skipped(self, monkeypatch) -> None:
        assert self._check(_parse("EXPIRED"), existing=None, monkeypatch=monkeypatch) is True

    def test_expiry_for_a_po_we_hold_is_processed(self, monkeypatch) -> None:
        # This one matters: it closes out a live order, which is the whole point of
        # receiving the notice. Skipping it would leave the PO open forever.
        held = SimpleNamespace(id=uuid4(), version=1, po_status=PoStatus.PARSED)
        assert self._check(_parse("EXPIRED"), existing=held, monkeypatch=monkeypatch) is False

    def test_a_live_po_is_never_skipped(self, monkeypatch) -> None:
        assert self._check(_parse("RELEASED"), existing=None, monkeypatch=monkeypatch) is False
