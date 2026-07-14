"""
Unit tests for Phase 3 parsers — BlinkitParser and ZeptoParser.

Each parser is tested with:
  1. Happy path (full valid payload)
  2. Missing mandatory field (po_number / purchaseOrderNumber)
  3. Multi-line PO with mixed tax regimes
  4. Line-level error (zero qty) — partial parse
  5. Extra / unknown fields are safely ignored

Fixtures live in tests/fixtures/*.json.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _raw(payload: dict, partner_code: str = "BLINKIT") -> MagicMock:
    """Build a minimal mock RawMessage with the given payload dict."""
    raw = MagicMock()
    raw.id = None
    raw.payload = payload
    raw.payload_raw = None
    # Attach a fake trading_partner for LLM fallback partner_code lookup
    raw.trading_partner = SimpleNamespace(code=partner_code)
    return raw


# ── BlinkitParser ─────────────────────────────────────────────────────────────

class TestBlinkitParser:
    def setup_method(self) -> None:
        from app.parsers.blinkit_parser import BlinkitParser
        self.parser = BlinkitParser()

    def _load(self, filename: str) -> dict:
        return json.loads((FIXTURES / filename).read_text())

    def test_partner_code(self) -> None:
        assert self.parser.partner_code == "BLINKIT"

    def test_can_parse_valid_payload(self) -> None:
        payload = self._load("blinkit_po_webhook.json")
        assert self.parser.can_parse(_raw(payload)) is True

    def test_cannot_parse_empty_payload(self) -> None:
        assert self.parser.can_parse(_raw({})) is False

    def test_cannot_parse_missing_details(self) -> None:
        assert self.parser.can_parse(_raw({"po_number": "BL-001"})) is False

    def test_happy_path_cgst_sgst(self) -> None:
        payload = self._load("blinkit_po_webhook.json")
        result = self.parser.parse(_raw(payload))

        assert result.success is True
        assert result.doc is not None
        doc = result.doc

        assert doc.buyer_po_number == "BL-2024-001234"
        assert doc.buyer_po_date is not None
        assert doc.requested_delivery_date is not None
        assert doc.buyer_gstin == "27AABCT1234M1Z5"
        assert len(doc.line_items) == 2

        line1 = doc.line_items[0]
        assert line1.buyer_sku == "LTF-MAKHANA-80"
        assert line1.ordered_qty == Decimal("100")
        assert line1.unit_price == Decimal("90.00")
        assert line1.cgst_rate == Decimal("6.0")
        assert line1.sgst_rate == Decimal("6.0")
        assert line1.igst_rate is None
        # 100 * 90 * 6% = 540.00
        assert line1.cgst_amount == Decimal("540.00")
        assert line1.sgst_amount == Decimal("540.00")
        assert line1.igst_amount is None

        # line_total = 9000 + 540 + 540 = 10080.00
        assert line1.line_total == Decimal("10080.00")

    def test_happy_path_igst(self) -> None:
        payload = self._load("blinkit_po_webhook_igst.json")
        result = self.parser.parse(_raw(payload))

        assert result.success is True
        doc = result.doc
        assert doc is not None
        assert len(doc.line_items) == 1

        line = doc.line_items[0]
        assert line.igst_rate == Decimal("12.0")
        assert line.cgst_rate is None
        assert line.sgst_rate is None
        # igst_amount = 30 * 90 * 0.12 = 324.00
        assert line.igst_amount == Decimal("324.00")
        assert line.line_total == Decimal("3024.00")  # 2700 + 324

    def test_missing_po_number(self) -> None:
        payload = self._load("blinkit_po_webhook.json")
        del payload["po_number"]
        result = self.parser.parse(_raw(payload))

        assert result.success is False
        assert any("po_number" in e.lower() for e in result.errors)

    def test_missing_item_data(self) -> None:
        payload = self._load("blinkit_po_webhook.json")
        payload["details"]["item_data"] = []
        result = self.parser.parse(_raw(payload))

        assert result.success is False
        assert any("line" in e.lower() for e in result.errors)

    def test_line_with_zero_qty_skipped(self) -> None:
        payload = self._load("blinkit_po_webhook.json")
        payload["details"]["item_data"][0]["units_ordered"] = 0
        result = self.parser.parse(_raw(payload))

        # Second line should still parse; zero-qty line goes to warnings
        assert result.success is True
        assert len(result.doc.line_items) == 1
        assert len(result.warnings) > 0

    def test_grand_total_uses_header_when_present(self) -> None:
        payload = self._load("blinkit_po_webhook.json")
        payload["details"]["total_amount"] = 99999.0
        result = self.parser.parse(_raw(payload))

        assert result.success is True
        assert result.doc.grand_total == Decimal("99999.00")

    def test_grand_total_computed_from_lines_when_missing(self) -> None:
        payload = self._load("blinkit_po_webhook.json")
        del payload["details"]["total_amount"]
        result = self.parser.parse(_raw(payload))

        assert result.success is True
        # Should compute: sum of line_totals
        computed = sum(line.line_total for line in result.doc.line_items)
        assert result.doc.grand_total == computed

    def test_extra_fields_ignored(self) -> None:
        payload = self._load("blinkit_po_webhook.json")
        payload["unknown_field"] = "should not cause error"
        payload["details"]["item_data"][0]["future_field"] = 42
        result = self.parser.parse(_raw(payload))
        assert result.success is True

    def test_cancellation_type_still_parses(self) -> None:
        payload = self._load("blinkit_po_webhook.json")
        payload["type"] = "PO_CANCELLATION"
        result = self.parser.parse(_raw(payload))
        # Parser creates a doc regardless of type — caller decides what to do with it
        assert result.success is True
        assert result.doc.buyer_po_number == "BL-2024-001234"


# ── ZeptoParser ───────────────────────────────────────────────────────────────

class TestZeptoParser:
    def setup_method(self) -> None:
        from app.parsers.zepto_parser import ZeptoParser
        self.parser = ZeptoParser()

    def _load(self, filename: str) -> dict:
        return json.loads((FIXTURES / filename).read_text())

    def test_partner_code(self) -> None:
        assert self.parser.partner_code == "ZEPTO"

    def test_can_parse_valid_payload(self) -> None:
        payload = self._load("zepto_po_event.json")
        assert self.parser.can_parse(_raw(payload, "ZEPTO")) is True

    def test_cannot_parse_empty_payload(self) -> None:
        assert self.parser.can_parse(_raw({}, "ZEPTO")) is False

    def test_cannot_parse_missing_line_items_key(self) -> None:
        payload = {"purchaseOrderNumber": "P001"}
        assert self.parser.can_parse(_raw(payload, "ZEPTO")) is False

    def test_happy_path(self) -> None:
        payload = self._load("zepto_po_event.json")
        result = self.parser.parse(_raw(payload, "ZEPTO"))

        assert result.success is True
        doc = result.doc
        assert doc is not None

        assert doc.buyer_po_number == "P365999"
        assert doc.buyer_gstin == "27AABCT1234M1Z5"
        assert len(doc.line_items) == 2

        line1 = doc.line_items[0]
        assert line1.buyer_sku == "Z-SKU-001"
        assert line1.buyer_sku_description == "Peri Peri Makhana 80g"
        assert line1.ordered_qty == Decimal("100")
        assert line1.unit_price == Decimal("90.00")
        assert line1.hsn_code == "20089900"
        assert line1.buyer_uom == "PC"
        # cgstRate=6 → cgst_amount = 9000 * 6/100 = 540.00
        assert line1.cgst_amount == Decimal("540.00")
        assert line1.sgst_amount == Decimal("540.00")
        assert line1.line_total == Decimal("10080.00")  # 9000 + 540 + 540

    def test_missing_po_number(self) -> None:
        payload = self._load("zepto_po_event.json")
        del payload["purchaseOrderNumber"]
        result = self.parser.parse(_raw(payload, "ZEPTO"))

        assert result.success is False
        assert any("purchaseOrderNumber" in e for e in result.errors)

    def test_missing_buyer_sku_in_line(self) -> None:
        payload = self._load("zepto_po_event.json")
        # Remove the buyerProductIdentifier from first line
        del payload["lineItems"][0]["productIdentifier"]["buyerProductIdentifier"]["skuCode"]
        result = self.parser.parse(_raw(payload, "ZEPTO"))

        # Second line should still parse; first goes to warnings
        assert result.success is True
        assert len(result.doc.line_items) == 1
        assert len(result.warnings) > 0

    def test_zero_ordered_qty_skipped(self) -> None:
        payload = self._load("zepto_po_event.json")
        payload["lineItems"][0]["quantity"]["orderedQuantity"]["amount"] = 0
        result = self.parser.parse(_raw(payload, "ZEPTO"))

        assert result.success is True
        assert len(result.doc.line_items) == 1

    def test_po_date_parsed(self) -> None:
        payload = self._load("zepto_po_event.json")
        result = self.parser.parse(_raw(payload, "ZEPTO"))

        assert result.success is True
        from datetime import date
        assert result.doc.buyer_po_date == date(2024, 1, 15)

    def test_total_amount_from_header(self) -> None:
        payload = self._load("zepto_po_event.json")
        payload["totalAmount"] = 99999.0
        result = self.parser.parse(_raw(payload, "ZEPTO"))

        assert result.success is True
        assert result.doc.grand_total == Decimal("99999.00")

    def test_empty_line_items_fails(self) -> None:
        payload = self._load("zepto_po_event.json")
        payload["lineItems"] = []
        result = self.parser.parse(_raw(payload, "ZEPTO"))

        assert result.success is False
        assert any("line" in e.lower() for e in result.errors)

    def test_multi_line_totals_correct(self) -> None:
        payload = self._load("zepto_po_event.json")
        result = self.parser.parse(_raw(payload, "ZEPTO"))

        assert result.success is True
        doc = result.doc
        assert len(doc.line_items) == 2

        # Line 1: 100 * 90 = 9000 + 540 + 540 = 10080
        # Line 2: 150 * 45 = 6750 + 405 + 405 = 7560
        assert doc.line_items[0].line_total == Decimal("10080.00")
        assert doc.line_items[1].line_total == Decimal("7560.00")

        # Grand total comes from header (51390.0) not computed — check header used
        assert doc.grand_total == Decimal("51390.00")
        # Subtotal computed from lines regardless
        assert doc.subtotal_amount == Decimal("15750.00")  # 9000 + 6750


# ── ParseResult contract ──────────────────────────────────────────────────────

class TestParseResultContract:
    """Parser output must always conform to the ParseResult contract."""

    def test_blinkit_result_has_parser_name(self) -> None:
        from app.parsers.blinkit_parser import BlinkitParser
        parser = BlinkitParser()
        result = parser.parse(_raw({"po_number": "X", "details": {}}))
        assert result.parser_name != ""

    def test_zepto_result_has_parser_name(self) -> None:
        from app.parsers.zepto_parser import ZeptoParser
        parser = ZeptoParser()
        result = parser.parse(_raw({"purchaseOrderNumber": "X", "lineItems": []}, "ZEPTO"))
        assert result.parser_name != ""

    def test_blinkit_never_raises(self) -> None:
        from app.parsers.blinkit_parser import BlinkitParser
        parser = BlinkitParser()
        # Completely malformed payload — must return ParseResult, not raise
        result = parser.parse(_raw({"po_number": "X", "details": {"item_data": [{"units_ordered": "NaN"}]}}, "BLINKIT"))
        assert result is not None

    def test_zepto_never_raises(self) -> None:
        from app.parsers.zepto_parser import ZeptoParser
        parser = ZeptoParser()
        # Deeply nested missing keys
        result = parser.parse(_raw({
            "purchaseOrderNumber": "Y",
            "lineItems": [{"lineNumber": 1}]
        }, "ZEPTO"))
        assert result is not None
