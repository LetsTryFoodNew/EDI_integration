"""
Unit tests for the Zepto ASN (856) builder.

Zepto rejected the pre-contract payload with a list of missing mandatory fields
(PurchaseOrderNumber, InvoiceNumber, TotalDiscountAmount, GrandTotalAmount,
ItemDetails), because the invoice-driven path was sending the partner-neutral body
untouched. These pin the contract shape from
_archive/backend_old/assets/API Curls.csv.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.adapters.outbound.zepto_asn import build_zepto_asn_payload


def _material(**kw):
    base = dict(item_code="FG00310", item_name="Let's Try Pudina MaKhana 57g",
                ean_code="8906161390443", invntry_uom="EA", mrp=Decimal("160.00"))
    base.update(kw)
    return SimpleNamespace(**base)


def _session(material=None):
    s = MagicMock()
    s.execute.return_value.scalar_one_or_none.return_value = material or _material()
    return s


def _inv_line(**kw):
    base = dict(b1_item_code="FG00310", description="Let's Try Pudina MaKhana 57g",
                qty=Decimal("180"), uom="EA", unit_price=Decimal("67.20"),
                taxable_amount=Decimal("12096.00"), hsn_code="21069099",
                po_line=SimpleNamespace(buyer_sku="f0a0e480-uuid",
                                        buyer_sku_description="Pudina Makhana",
                                        buyer_material_code="102597"))
    base.update(kw)
    return SimpleNamespace(**base)


def _build(inv_lines=None, asn_lines=None, material=None):
    po = SimpleNamespace(buyer_po_number="P900103", buyer_po_date=date(2026, 8, 24),
                         po_expiry_date=date(2026, 9, 7),
                         requested_delivery_date=date(2026, 8, 31),
                         trading_partner_id="tp-1", raw_message_id=None)
    asn = SimpleNamespace(asn_number="ASN-1", shipment_date=date(2026, 8, 24),
                          line_items=asn_lines if asn_lines is not None else [
                              SimpleNamespace(b1_item_code="FG00310",
                                              batch_number="LTF-202608",
                                              expiry_date=date(2027, 8, 24),
                                              manufacturing_date=date(2026, 2, 24))])
    invoice = SimpleNamespace(invoice_number="DUMMY-ZEPTO-1", invoice_date=date(2026, 8, 24),
                              subtotal_amount=Decimal("12096.00"),
                              grand_total=Decimal("12700.80"),
                              discount_amount=Decimal("0"),
                              line_items=inv_lines if inv_lines is not None else [_inv_line()])
    partner = SimpleNamespace(code="ZEPTO", api_config={"vendor_code": "KK-1102"})
    seller = SimpleNamespace(name="Let's Try Foods Private Limited")
    return build_zepto_asn_payload(_session(material), po, asn, invoice, partner, seller)


class TestZeptoAsnContractShape:
    def test_every_mandatory_top_level_key_is_present(self) -> None:
        """The five Zepto named in its rejection, plus the seller block."""
        payload, _ = _build()

        assert payload["purchaseOrderDetails"]["purchaseOrderNumber"] == "P900103"
        assert payload["invoiceDetails"]["invoiceNumber"] == "DUMMY-ZEPTO-1"
        assert payload["invoiceTotals"]["discountDetails"]["totalDiscountAmount"] == 0
        assert payload["invoiceTotals"]["grandTotalAmount"] == 12700.8
        assert len(payload["itemDetails"]) == 1
        assert payload["seller"]["soldFrom"]["id"] == "KK-1102"

    def test_invoice_number_is_the_invoice_not_the_asn(self) -> None:
        """The old builder sent asn.asn_number here — the same bug Blinkit had."""
        payload, _ = _build()

        assert payload["invoiceDetails"]["invoiceNumber"] == "DUMMY-ZEPTO-1"
        assert "ASN-1" not in str(payload["invoiceDetails"])

    def test_item_carries_both_product_identifiers(self) -> None:
        payload, _ = _build()
        ident = payload["itemDetails"][0]["productIdentifier"]

        assert ident["buyerProductIdentifier"]["skuCode"] == "f0a0e480-uuid"
        assert ident["sellerProductIdentifier"]["itemCode"] == "FG00310"
        assert ident["sellerProductIdentifier"]["identifier"] == {
            "identifierType": "EAN", "identifierValue": "8906161390443",
        }

    def test_quantity_is_pieces_with_explicit_uom(self) -> None:
        """Contract rule 4: unit sizes, never case sizes."""
        qty = _build()[0]["itemDetails"][0]["quantity"]

        assert qty["invoicedQuantity"] == {"amount": 180, "unitOfMeasure": "EA"}
        assert qty["freeQuantity"]["amount"] == 0

    def test_integral_amounts_encode_as_int(self) -> None:
        payload, _ = _build()

        assert isinstance(payload["itemDetails"][0]["quantity"]["invoicedQuantity"]["amount"], int)
        assert isinstance(payload["invoiceTotals"]["discountDetails"]["totalDiscountAmount"], int)

    def test_due_date_is_derived_and_warned_about(self) -> None:
        payload, warnings = _build()

        assert payload["invoiceDetails"]["dueDate"] == "2026-09-23"
        assert any("dueDate derived" in w for w in warnings)

    def test_missing_batch_warns_without_blocking(self) -> None:
        payload, warnings = _build(asn_lines=[])

        assert payload["itemDetails"][0]["batchDetails"]["batchNumber"] == ""
        assert any("no batch_number" in w for w in warnings)

    def test_missing_vendor_code_warns(self) -> None:
        po = SimpleNamespace(buyer_po_number="P1", buyer_po_date=None, po_expiry_date=None,
                             requested_delivery_date=None, trading_partner_id="t",
                             raw_message_id=None)
        asn = SimpleNamespace(asn_number="A1", shipment_date=None, line_items=[])
        invoice = SimpleNamespace(invoice_number="I1", invoice_date=date(2026, 8, 24),
                                  subtotal_amount=Decimal("1"), grand_total=Decimal("1"),
                                  discount_amount=Decimal("0"), line_items=[_inv_line()])
        partner = SimpleNamespace(code="ZEPTO", api_config={})
        _payload, warnings = build_zepto_asn_payload(
            _session(), po, asn, invoice, partner, SimpleNamespace(name="LTF"))

        assert any("vendor code" in w for w in warnings)


class TestContractFieldTable:
    """
    Fields the v12 contract's API Body table names that the first cut omitted, plus
    the one it typed as Numeric that was being filled with a UUID.
    """

    def test_mrp_and_base_price_are_sent(self) -> None:
        item = _build()[0]["itemDetails"][0]

        assert item["mrp"] == 160
        assert item["basePrice"] == 67.2

    def test_material_code_omitted_when_not_known(self) -> None:
        """
        Typed Numeric. The SKU UUID is not a stand-in, and skuCode alone satisfies
        "one of skuCode or materialCode has to be present".
        """
        payload, _ = _build()
        buyer = payload["itemDetails"][0]["productIdentifier"]["buyerProductIdentifier"]

        assert buyer["skuCode"] == "f0a0e480-uuid"
        assert "materialCode" not in buyer or buyer["materialCode"] != "f0a0e480-uuid"


class TestZeptoResponseHandling:
    """
    Response Codes: 2XX processed, 4XX parsing/missing-field errors where "retires
    wont work", 5XX intermittent and retryable.
    """

    def test_errors_array_flattens(self) -> None:
        from app.adapters.api.zepto_api import _errors_to_text

        out = _errors_to_text([
            {"code": "E01", "message": "No previous event found for PO Code: P1"},
            {"message": "second problem"},
        ])

        assert "E01" in out
        assert "No previous event found" in out
        assert "second problem" in out

    def test_plain_string_errors_survive(self) -> None:
        from app.adapters.api.zepto_api import _errors_to_text

        assert "boom" in _errors_to_text(["boom"])

    def test_empty_errors_is_empty_text(self) -> None:
        from app.adapters.api.zepto_api import _errors_to_text

        assert _errors_to_text([]) == ""


class TestAsnAckEnvelope:
    """
    Zepto accepted ASN MI399MEA002F4 and the adapter reported "no asnNumber returned",
    because it read the response through _unwrap -- which hands back the *contents* of
    "data" and drops the sibling "errors" array. asnNumber then sits one level
    shallower than the lookup expected.

    A success read as a failure is the mirror of the Blinkit bug: there a rejection was
    recorded as delivered, here a delivery was recorded as refused. Both come from
    trusting one shape of one field.
    """

    def test_documented_shape(self) -> None:
        from app.adapters.api.zepto_api import _asn_ack

        errors, asn = _asn_ack({"errors": [], "data": {"asnNumber": "MI399MEA002F4"}})

        assert errors == []
        assert asn == "MI399MEA002F4"

    def test_already_unwrapped_shape(self) -> None:
        from app.adapters.api.zepto_api import _asn_ack

        _errors, asn = _asn_ack({"asnNumber": "MI399MEA002F4"})

        assert asn == "MI399MEA002F4"

    def test_proxy_envelope(self) -> None:
        from app.adapters.api.zepto_api import _asn_ack

        _errors, asn = _asn_ack(
            {"proxied": True, "status_code": 200,
             "data": {"errors": [], "data": {"asnNumber": "JAI005MEA00972"}}}
        )

        assert asn == "JAI005MEA00972"

    def test_errors_survive_alongside_data(self) -> None:
        from app.adapters.api.zepto_api import _asn_ack

        errors, asn = _asn_ack({
            "errors": [{"code": 400, "error": "PO is not in RELEASED / CONFIRMED status"}],
            "data": None,
        })

        assert asn is None
        assert errors and "RELEASED" in str(errors[0])

    def test_non_dict_body_is_survivable(self) -> None:
        from app.adapters.api.zepto_api import _asn_ack

        assert _asn_ack(None) == ([], None)
        assert _asn_ack("boom") == ([], None)


class TestMultipleBatches:
    """
    A line filled from more than one batch.

    `batchDetails` is one object per itemDetails entry, so a line filled from two
    batches ships as two entries. Before this, the ASN lines were collapsed into
    `{code: line}` and only the last batch survived — the other vanished from the ASN
    while its quantity stayed in `invoiceTotals`, telling Zepto less had shipped than
    was billed.
    """

    def _two_batches(self):
        return _build(asn_lines=[
            SimpleNamespace(b1_item_code="FG00310", batch_number="LTF-A",
                            shipped_qty=Decimal("120"), expiry_date=date(2027, 8, 24),
                            manufacturing_date=date(2026, 2, 24)),
            SimpleNamespace(b1_item_code="FG00310", batch_number="LTF-B",
                            shipped_qty=Decimal("60"), expiry_date=date(2027, 9, 30),
                            manufacturing_date=date(2026, 3, 30)),
        ])

    def test_one_entry_per_batch(self) -> None:
        payload, _ = self._two_batches()
        items = payload["itemDetails"]
        assert len(items) == 2
        assert [i["batchDetails"]["batchNumber"] for i in items] == ["LTF-A", "LTF-B"]

    def test_quantities_split_and_still_add_up(self) -> None:
        payload, _ = self._two_batches()
        amounts = [i["quantity"]["invoicedQuantity"]["amount"] for i in payload["itemDetails"]]
        assert amounts == [120, 60]
        assert sum(amounts) == 180

    def test_item_sequence_numbers_stay_unique(self) -> None:
        # They number the rows actually sent, so two rows cannot share one.
        payload, _ = self._two_batches()
        seqs = [i["itemSequenceNumber"] for i in payload["itemDetails"]]
        assert seqs == [1, 2]

    def test_article_sequence_number_identifies_the_article(self) -> None:
        # Both rows are the same article, so it does not advance with the rows.
        payload, _ = self._two_batches()
        arts = [
            i["productIdentifier"]["buyerProductIdentifier"]["articleSequenceNumber"]
            for i in payload["itemDetails"]
        ]
        assert arts == [1, 1]

    def test_per_unit_price_is_shared_not_divided(self) -> None:
        payload, _ = self._two_batches()
        assert {i["basePrice"] for i in payload["itemDetails"]} == {67.20}

    def test_invoice_total_counts_the_line_once(self) -> None:
        # taxable_total accumulates per invoice line; adding it per batch would double
        # the value of any split line.
        payload, _ = self._two_batches()
        assert payload["invoiceTotals"]["taxableAmount"] == 12096.00

    def test_each_row_keeps_its_own_dates(self) -> None:
        payload, _ = self._two_batches()
        expiries = [i["batchDetails"]["expiryDate"] for i in payload["itemDetails"]]
        assert expiries == ["2027-08-24", "2027-09-30"]

    def test_a_single_batch_is_unchanged(self) -> None:
        payload, _ = _build()
        assert len(payload["itemDetails"]) == 1
        assert payload["itemDetails"][0]["quantity"]["invoicedQuantity"]["amount"] == 180
