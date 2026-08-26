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
