"""
An ASN going to a partner with no API.

Swiggy, LOTS and the other mail partners have no wire, so their 856 is an email.
EmailOutboundAdapter is deliberately dumb — BaseOutboundAdapter says payload
construction happens before dispatch — and it reads `to`, `subject` and `body_text`
off the payload. The canonical ASN body has none of those, so before this the Swiggy
ASN was headed for Gmail with an empty To header and a subject of "(no subject)":
delivered nowhere, and recorded as SENT.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.adapters.outbound.email_asn import build_email_asn_payload

BODY = {
    "asn_number": "ASN-DUMMY-SWIGGY-46581",
    "po_number": "CMMPO17234",
    "invoice_number": "DUMMY-SWIGGY-46581",
    "carrier": "Blue Dart",
    "tracking_number": "TRK-46581",
    "irn": None,
    "line_items": [
        {"buyer_sku": "17118", "b1_item_code": "FG00233", "shipped_qty": "15.0000",
         "batch_number": "LTF-202608-46581", "expiry_date": "2027-08-26"},
        {"buyer_sku": "35446", "b1_item_code": "FG00268", "shipped_qty": "495.0000",
         "batch_number": "LTF-202608-46581", "expiry_date": "2027-08-26"},
    ],
}

PARTNER = SimpleNamespace(code="SWIGGY", email_address="instamart.vendors@swiggy.in")
SELLER = SimpleNamespace(name="Let's Try Foods Private Limited")
PO = SimpleNamespace(buyer_po_number="CMMPO17234")
ASN = SimpleNamespace(asn_number="ASN-DUMMY-SWIGGY-46581")
INVOICE = SimpleNamespace(invoice_number="DUMMY-SWIGGY-46581")


def build(partner=PARTNER, body=None):
    return build_email_asn_payload(PO, ASN, INVOICE, partner, SELLER, body or BODY)


class TestEnvelope:
    def test_carries_everything_the_adapter_reads(self) -> None:
        # _build_mime reads exactly these three. A missing one is a silent bad send.
        payload, _ = build()
        for key in ("to", "subject", "body_text"):
            assert payload.get(key), f"{key} missing — _build_mime would send it empty"

    def test_addressed_to_the_partner(self) -> None:
        payload, warnings = build()
        assert payload["to"] == "instamart.vendors@swiggy.in"
        assert warnings == []

    def test_subject_identifies_the_shipment(self) -> None:
        # A warehouse mailbox reconciles on these three numbers.
        subject = build()[0]["subject"]
        assert "ASN-DUMMY-SWIGGY-46581" in subject
        assert "CMMPO17234" in subject
        assert "DUMMY-SWIGGY-46581" in subject

    def test_a_partner_with_no_email_warns_rather_than_sending_to_nobody(self) -> None:
        payload, warnings = build(partner=SimpleNamespace(code="LOTS", email_address=None))
        assert payload["to"] == ""
        assert any("email_address" in w for w in warnings)

    def test_canonical_body_is_kept(self) -> None:
        # The outbound tab still shows the shipment, and a formatter change can
        # re-render without rebuilding from the invoice.
        assert build()[0]["asn"] == BODY


class TestBody:
    def test_every_line_appears_in_both_parts(self) -> None:
        # Warehouse mailboxes and EDI mailbots strip HTML; the lines have to survive it.
        payload, _ = build()
        for part in ("body_text", "body_html"):
            for sku in ("17118", "35446"):
                assert sku in payload[part], f"{sku} missing from {part}"
            assert "FG00233" in payload[part]
            assert "LTF-202608-46581" in payload[part]

    def test_quantities_lose_the_stored_scale(self) -> None:
        # 15.0000 on a delivery note reads as a mistake.
        payload, _ = build()
        assert " 15 " in payload["body_text"] or "\t15" in payload["body_text"]
        assert ">15<" in payload["body_html"]
        assert "15.0000" not in payload["body_text"]
        assert "15.0000" not in payload["body_html"]

    def test_absent_optional_fields_are_left_out(self) -> None:
        # irn is None here; an "IRN: None" row on a delivery note is worse than no row.
        payload, _ = build()
        assert "IRN" not in payload["body_text"]
        assert "None" not in payload["body_text"]

    def test_present_optional_fields_are_shown(self) -> None:
        payload, _ = build()
        assert "Blue Dart" in payload["body_text"]
        assert "TRK-46581" in payload["body_html"]

    def test_html_is_escaped(self) -> None:
        body = {**BODY, "line_items": [
            {"buyer_sku": "<script>x</script>", "b1_item_code": "FG1",
             "shipped_qty": "1", "batch_number": "B", "expiry_date": "2027-01-01"},
        ]}
        payload, _ = build(body=body)
        assert "<script>" not in payload["body_html"]
        assert "&lt;script&gt;" in payload["body_html"]

    def test_survives_an_empty_shipment(self) -> None:
        payload, _ = build(body={**BODY, "line_items": []})
        assert payload["subject"]
        assert "0 line(s)" in payload["body_text"]


class TestPartnerEmailLookup:
    """
    Where outbound mail is addressed.

    `_partner_email` read only `api_config["ops_email"]` and ignored
    `trading_partners.email_address` — the first-class column Master Data edits. Every
    855 and every email 856 was therefore built with `to: ""`. Swiggy's ACK for
    CMMPO17234 had been retrying since creation, with Gmail answering "Recipient
    address required". That one failed loudly; an adapter less strict about an empty
    To header would have reported the ACK delivered.
    """

    @staticmethod
    def _lookup(partner):
        from app.workflows.b1_to_outbound import _partner_email

        return _partner_email(partner)

    def test_uses_the_email_address_column(self) -> None:
        partner = SimpleNamespace(email_address="instamart.vendors@swiggy.in", api_config=None)
        assert self._lookup(partner) == "instamart.vendors@swiggy.in"

    def test_column_wins_over_the_legacy_config_key(self) -> None:
        # Master Data edits the column, so an edit there must take effect.
        partner = SimpleNamespace(
            email_address="new@swiggy.in", api_config={"ops_email": "stale@swiggy.in"}
        )
        assert self._lookup(partner) == "new@swiggy.in"

    def test_falls_back_to_ops_email(self) -> None:
        partner = SimpleNamespace(email_address=None, api_config={"ops_email": "ops@lots.in"})
        assert self._lookup(partner) == "ops@lots.in"

    def test_blank_column_falls_through_rather_than_winning(self) -> None:
        partner = SimpleNamespace(email_address="   ", api_config={"ops_email": "ops@lots.in"})
        assert self._lookup(partner) == "ops@lots.in"

    def test_no_address_anywhere_is_empty(self) -> None:
        assert self._lookup(SimpleNamespace(email_address=None, api_config={})) == ""


class TestInvoiceAttachment:
    """
    The tax invoice rides along with the ASN.

    Named rather than carried: send_outbound renders the PDF from the invoice record
    immediately before dispatch, so it always matches the invoice — a stored copy goes
    stale the moment an IRN arrives on a re-push — and no base64 blob sits in the
    payload column.
    """

    def test_envelope_names_the_invoice(self) -> None:
        payload, _ = build()
        assert payload["attach_invoice"] == "DUMMY-SWIGGY-46581"

    def test_a_blank_body_field_still_finds_the_invoice_record(self) -> None:
        # The body is a convenience copy; the invoice row is the source of truth.
        payload, _ = build(body={**BODY, "invoice_number": None})
        assert payload["attach_invoice"] == "DUMMY-SWIGGY-46581"

    def test_no_invoice_anywhere_means_no_attachment_request(self) -> None:
        payload, _ = build_email_asn_payload(
            PO, ASN, SimpleNamespace(invoice_number=None), PARTNER, SELLER,
            {**BODY, "invoice_number": None},
        )
        assert "attach_invoice" not in payload

    def test_the_body_says_the_invoice_is_attached(self) -> None:
        # Otherwise a reader who cannot see attachments has no idea one exists.
        payload, _ = build()
        for part in ("body_text", "body_html"):
            assert "attached" in payload[part]


class TestMimeAssembly:
    @staticmethod
    def _mime(payload):
        from app.adapters.outbound.email_outbound import EmailOutboundAdapter

        return EmailOutboundAdapter._build_mime(payload)

    def _base(self):
        return {"to": "a@b.in", "subject": "S", "body_text": "T", "body_html": "<p>H</p>"}

    def test_without_attachments_it_stays_alternative(self) -> None:
        msg = self._mime(self._base())
        assert msg.get_content_subtype() == "alternative"

    def test_an_attachment_forces_a_mixed_wrapper(self) -> None:
        # text/html are the same content and the reader picks one; a PDF is different
        # content. Attaching it into `alternative` makes clients treat it as another
        # rendering of the body and quietly hide it.
        msg = self._mime({**self._base(), "attachments": [
            {"filename": "Invoice-X.pdf", "mime_type": "application/pdf", "content": b"%PDF-1.4"},
        ]})
        assert msg.get_content_subtype() == "mixed"
        parts = msg.get_payload()
        assert parts[0].get_content_subtype() == "alternative"
        assert parts[1].get_filename() == "Invoice-X.pdf"
        assert parts[1].get_payload(decode=True) == b"%PDF-1.4"

    def test_headers_survive_the_wrapper(self) -> None:
        msg = self._mime({**self._base(), "attachments": [
            {"filename": "x.pdf", "mime_type": "application/pdf", "content": b"x"},
        ]})
        assert msg["To"] == "a@b.in"
        assert msg["Subject"] == "S"

    def test_attachment_is_marked_for_download(self) -> None:
        msg = self._mime({**self._base(), "attachments": [
            {"filename": "x.pdf", "mime_type": "application/pdf", "content": b"x"},
        ]})
        assert "attachment" in msg.get_payload()[1]["Content-Disposition"]


class TestPoSourceAttachment:
    """
    The order as the partner sent it, attached back beside the invoice.

    Read from wherever ingestion stored it rather than re-rendered: the point is to
    hand their accounts desk the same document they issued, and a reconstruction would
    invite an argument about which copy is authoritative.
    """

    def test_envelope_asks_for_the_source_po(self) -> None:
        payload, _ = build()
        assert payload["attach_po_source"] is True

    def test_the_body_mentions_both_documents(self) -> None:
        payload, _ = build()
        for part in ("body_text", "body_html"):
            assert "tax invoice" in payload[part]
            assert "purchase order" in payload[part]


class TestAttachmentAssembly:
    """`_with_attachments` — what actually ends up on the message."""

    @staticmethod
    def _run(monkeypatch, *, invoice_pdf=b"%PDF-inv", sources=(), fetch_error=False):
        import app.workflows.send_outbound as so

        msg = SimpleNamespace(
            po_id="po-1",
            payload={"to": "a@b.in", "attach_invoice": "INV-1", "attach_po_source": True},
        )
        monkeypatch.setattr(
            so, "_invoice_attachment",
            lambda *a: ([{"filename": "Invoice-INV-1.pdf",
                          "mime_type": "application/pdf",
                          "content": invoice_pdf}] if invoice_pdf else []),
        )

        def fake_sources(session, message):
            if fetch_error:
                return []
            return [{"filename": f"PO-X-{n}", "mime_type": "application/pdf",
                     "content": c} for n, c in sources]

        monkeypatch.setattr(so, "_po_source_attachments", fake_sources)
        return so._with_attachments(object(), msg)

    def test_both_documents_are_attached(self, monkeypatch) -> None:
        payload = self._run(monkeypatch, sources=[("po.pdf", b"%PDF-po")])
        names = [a["filename"] for a in payload["attachments"]]
        assert names == ["Invoice-INV-1.pdf", "PO-X-po.pdf"]

    def test_request_keys_do_not_reach_the_adapter(self, monkeypatch) -> None:
        # They are instructions to this function, not part of the email.
        payload = self._run(monkeypatch, sources=[("po.pdf", b"x")])
        assert "attach_invoice" not in payload
        assert "attach_po_source" not in payload

    def test_a_missing_source_still_sends_the_invoice(self, monkeypatch) -> None:
        # An unreadable PO copy must not cost the retailer the invoice.
        payload = self._run(monkeypatch, fetch_error=True)
        assert [a["filename"] for a in payload["attachments"]] == ["Invoice-INV-1.pdf"]

    def test_a_failed_invoice_render_still_sends_the_po(self, monkeypatch) -> None:
        payload = self._run(monkeypatch, invoice_pdf=b"", sources=[("po.pdf", b"y")])
        assert [a["filename"] for a in payload["attachments"]] == ["PO-X-po.pdf"]

    def test_nothing_renderable_leaves_the_payload_alone(self, monkeypatch) -> None:
        payload = self._run(monkeypatch, invoice_pdf=b"")
        assert "attachments" not in payload
        assert payload["to"] == "a@b.in"

    def test_oversized_files_are_dropped_not_sent(self, monkeypatch) -> None:
        # Gmail refuses a message over 25 MB outright rather than sending part of it,
        # so a delivery note carrying one document beats one that never arrives.
        import app.workflows.send_outbound as so

        huge = b"x" * (so._MAX_ATTACHMENT_BYTES + 1)
        payload = self._run(monkeypatch, sources=[("huge.pdf", huge)])
        assert [a["filename"] for a in payload["attachments"]] == ["Invoice-INV-1.pdf"]


class TestSourceFileNaming:
    """
    What the retailer's accounts desk actually sees in their downloads folder.

    Partners generate names like
    "DG8TMD12QLBDILJRUSF7_CREATE_OTB_PURCHASE_ORDER_ae4e21a5-814d-4c24-9bb2-...xlsx",
    which identifies nothing and is what they will have to find again later.
    """

    @staticmethod
    def _sources(monkeypatch, files):
        import app.workflows.send_outbound as so

        monkeypatch.setattr(so, "fetch_attachment", lambda att: b"data", raising=False)
        monkeypatch.setattr(
            "app.adapters.storage.fetch_attachment", lambda att: b"data", raising=False
        )

        po = SimpleNamespace(buyer_po_number="CMMPO17234", raw_message_id="raw-1")
        raw = SimpleNamespace(attachment_paths=[{"filename": f} for f in files])
        session = SimpleNamespace(get=lambda model, _id: po if "Purchase" in model.__name__ else raw)
        return so._po_source_attachments(session, SimpleNamespace(po_id="po-1"))

    def test_renamed_to_the_po_number(self, monkeypatch) -> None:
        out = self._sources(monkeypatch, [
            "DG8TMD12QLBDILJRUSF7_CREATE_OTB_PURCHASE_ORDER_ae4e21a5.pdf",
        ])
        assert [a["filename"] for a in out] == ["PO-CMMPO17234.pdf"]

    def test_two_of_the_same_type_do_not_collide(self, monkeypatch) -> None:
        # Otherwise the second silently replaces the first on download.
        out = self._sources(monkeypatch, ["a.pdf", "b.pdf"])
        assert [a["filename"] for a in out] == ["PO-CMMPO17234.pdf", "PO-CMMPO17234-2.pdf"]

    def test_different_types_keep_their_own_extension(self, monkeypatch) -> None:
        out = self._sources(monkeypatch, ["order.pdf", "order.xlsx"])
        assert [a["filename"] for a in out] == ["PO-CMMPO17234.pdf", "PO-CMMPO17234.xlsx"]

    def test_spreadsheets_get_a_real_content_type(self, monkeypatch) -> None:
        # The slim image has no system mime entry for .xlsx, so guess_type returned
        # octet-stream and mail clients refused to preview it.
        out = self._sources(monkeypatch, ["order.xlsx"])
        assert out[0]["mime_type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_pdfs_get_a_real_content_type(self, monkeypatch) -> None:
        out = self._sources(monkeypatch, ["order.pdf"])
        assert out[0]["mime_type"] == "application/pdf"

    def test_an_unknown_extension_falls_back_rather_than_failing(self, monkeypatch) -> None:
        out = self._sources(monkeypatch, ["order.weird"])
        assert out[0]["mime_type"] == "application/octet-stream"
        assert out[0]["filename"] == "PO-CMMPO17234.weird"
