"""
Add-and-update on one endpoint, keyed on the field SAP identifies the record by.

SAP does not track what the middleware already holds, so making it ask first — POST,
read 409, switch to PUT — turns every record into two round trips and a race, and the
409 told it nothing it could act on. These endpoints decide for themselves:

    /api/master-data/materials          item_code                    -> ItemCode
    /api/master-data/partners           code                         -> CardCode
    /api/master-data/ship-to/sync       partner_code + buyer_whs_code
    /api/master-data/bill-to/sync       partner_code + buyer_bill_to_code
    /api/master-data/sku-mappings/sync  partner_code + buyer_sku
    /api/invoices                       b1_invoice_doc_entry, then invoice_number

What each one must never do is as important as what it does: a routine master-data
refresh cannot unwire a live integration, resurrect something a person deleted, or
renumber an invoice the retailer already holds an ASN against.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models._enums import SourceChannel

# ── Partner integration config ────────────────────────────────────────────────

class TestPartnerIntegrationConfigIsProtected:
    """
    A SAP Business Partner record says nothing about how we fetch that partner's
    orders. A master-data refresh that flipped a live API partner to MANUAL would stop
    its ingestion with no error anywhere — the scheduler simply stops polling it.
    """

    @staticmethod
    def _apply(partner, body):
        from app.api.routes.master_data import _update_partner_from_push

        db, request, user, response = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        user.email = "ops@letstryfoods.com"
        request.client.host = "10.0.0.1"
        db.refresh = lambda _obj: None
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.api.routes.master_data._partner_write_response",
                       lambda p: SimpleNamespace(code=p.code))
            _update_partner_from_push(db, partner, body, request, user, response)
        return partner, response

    def _partner(self, **kw):
        base = dict(id="p-1", code="ZEPTO", name="Zepto", source_channel=SourceChannel.API,
                    gmail_label=None, webhook_secret="live-secret", asn_sla_hours=48,
                    b1_card_code="D00004", gstin=None, pan_card=None, business_type=None,
                    group_name=None, phone_numbers=None, email_address=None,
                    is_active=True, deleted_at=None)
        return SimpleNamespace(**{**base, **kw})

    def _body(self, **kw):
        base = dict(code="ZEPTO", name="Zepto Ltd", source_channel="MANUAL",
                    gmail_label=None, webhook_secret=None, asn_sla_hours=48,
                    b1_card_code="D00004", gstin="06AAICK4821A1ZZ", pan_card=None,
                    business_type=None, group_name=None, phone_numbers=None,
                    email_address=None, is_active=True, status=None)
        ns = SimpleNamespace(**{**base, **kw})
        ns.model_dump = lambda mode=None: {"code": ns.code, "name": ns.name}
        return ns

    def test_master_data_is_taken_from_the_push(self) -> None:
        partner, _ = self._apply(self._partner(), self._body())
        assert partner.name == "Zepto Ltd"
        assert partner.gstin == "06AAICK4821A1ZZ"

    def test_a_defaulted_manual_channel_never_demotes_a_live_partner(self) -> None:
        # TradingPartnerCreate defaults source_channel to MANUAL, so "MANUAL" on an
        # update is indistinguishable from "not supplied". Applying it would stop
        # Zepto's polling.
        partner, _ = self._apply(self._partner(), self._body(source_channel="MANUAL"))
        assert partner.source_channel is SourceChannel.API

    def test_an_explicit_channel_does_not_overwrite_a_set_one(self) -> None:
        partner, _ = self._apply(self._partner(), self._body(source_channel="EMAIL"))
        assert partner.source_channel is SourceChannel.API

    def test_an_explicit_channel_does_fill_an_unset_one(self) -> None:
        # A partner sitting inert at MANUAL is a gap worth filling.
        partner, _ = self._apply(
            self._partner(source_channel=SourceChannel.MANUAL),
            self._body(source_channel="EMAIL"),
        )
        assert partner.source_channel is SourceChannel.EMAIL

    def test_an_existing_secret_is_never_overwritten(self) -> None:
        partner, _ = self._apply(self._partner(), self._body(webhook_secret="new"))
        assert partner.webhook_secret == "live-secret"

    def test_a_missing_secret_is_filled(self) -> None:
        partner, _ = self._apply(
            self._partner(webhook_secret=None), self._body(webhook_secret="new"),
        )
        assert partner.webhook_secret == "new"

    def test_an_update_answers_200_not_201(self) -> None:
        _, response = self._apply(self._partner(), self._body())
        assert response.status_code == 200

    def test_an_unusable_channel_is_refused_rather_than_stored(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            self._apply(
                self._partner(source_channel=SourceChannel.MANUAL),
                self._body(source_channel="CARRIER_PIGEON"),
            )
        assert exc.value.status_code == 422


# ── Invoice identity ──────────────────────────────────────────────────────────

class TestInvoiceMatching:
    """
    SAP identifies an A/R Invoice by DocEntry, its immutable primary key. Matching on
    it is what makes the endpoint a true add-or-update for them. invoice_number stays
    the fallback: it is the legal number the retailer reconciles against, and the first
    push usually has no DocEntry yet — B1 assigns it on posting.
    """

    @staticmethod
    def _find(payload, *returns):
        """
        `returns` is what each successive db.execute should yield, in call order. The
        code makes one query when there is no DocEntry and two when a DocEntry misses,
        so the harness must not assume a fixed number.
        """
        from app.workflows.invoice_from_sap import _find_existing_invoice

        db = MagicMock()
        wrapped = []
        for value in returns:
            r = MagicMock()
            r.scalar_one_or_none.return_value = value
            wrapped.append(r)
        db.execute.side_effect = wrapped
        return _find_existing_invoice(db, payload), db

    def test_doc_entry_is_tried_first(self) -> None:
        stored = SimpleNamespace(invoice_number="INV-1")
        payload = SimpleNamespace(b1_invoice_doc_entry=9912, invoice_number="INV-1")
        found, db = self._find(payload, stored)
        assert found is stored
        assert db.execute.call_count == 1, "number lookup should not run once DocEntry hits"

    def test_falls_back_to_the_invoice_number(self) -> None:
        # The common first push: posted in B1 but DocEntry not sent yet.
        stored = SimpleNamespace(invoice_number="INV-1")
        payload = SimpleNamespace(b1_invoice_doc_entry=None, invoice_number="INV-1")
        found, db = self._find(payload, stored)
        assert found is stored
        assert db.execute.call_count == 1

    def test_a_doc_entry_miss_still_checks_the_number(self) -> None:
        stored = SimpleNamespace(invoice_number="INV-1")
        payload = SimpleNamespace(b1_invoice_doc_entry=9912, invoice_number="INV-1")
        found, db = self._find(payload, None, stored)
        assert found is stored
        assert db.execute.call_count == 2

    def test_a_genuinely_new_invoice_matches_nothing(self) -> None:
        payload = SimpleNamespace(b1_invoice_doc_entry=1, invoice_number="INV-NEW")
        found, _ = self._find(payload, None, None)
        assert found is None
