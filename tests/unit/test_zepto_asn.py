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
                ean_code="8906161390443", invntry_uom="EA")
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
        assert ident["buyerProductIdentifier"]["materialCode"] == "102597"
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
