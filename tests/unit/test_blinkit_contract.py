"""
Blinkit parser against the POVMS Purchase Order Creation contract (2026-02-10).

Section numbers refer to the contract tables, archived at
_archive/backend_old/assets/POVMS-Purchase Order Creation API Contracts-*.txt (a PDF).

These pin the contract behaviours that differ from what production payloads had shown,
so a future contract revision fails here rather than in an invoice dispute.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.parsers.blinkit_parser import (
    BlinkitAckStatus,
    BlinkitErrorCode,
    BlinkitEventType,
    BlinkitParser,
    BlinkitTenant,
)


def _payload(**overrides):
    """The contract's own example (section 3), minimally parameterised."""
    item = {
        "item_id": 10016623,
        "sku_code": "",
        "line_number": 0,
        "units_ordered": 240,
        "landing_price": 32.56,
        "basic_price": 31.01,
        "tax_details": {
            "cgst_percentage": 2.5, "sgst_percentage": 2.5,
            "igst_percentage": None, "cess_percentage": None,
            "additional_cess_value": None,
        },
        "crates_config": {"crates_ordered": 14, "crate_size": 10},
        "name": "Name of Item 0", "mrp": 42, "upc": "8901774002349",
        "uom": {"unit": "ml", "value": 12},
    }
    item.update(overrides.pop("item", {}))
    details = {
        "po_number": "2264110001440",
        "outlet_id": 12543,
        "issue_date": "2025-04-20T00:00:00.000Z",
        "expiry_date": "2025-04-20T00:00:00.000Z",
        "delivery_date": "2025-04-25T00:00:00.000Z",
        "vehicle_details": {"license_number": "DL-311/431"},
        "buyer_details": {
            "name": "Buyer name", "gstin": "27ABCDE1234F1Z5",
            "destination_address": {
                "line1": "123, Street", "city": "Mumbai", "state": "Maharashtra",
                "postal_code": "400077", "country": "India",
            },
        },
        "supplier_details": {"id": "67890", "gstin": "27ABCDE1234F1Z5", "pan": "ABCDE1234F"},
        "item_data": [item],
        "total_sku": 1, "total_qty": 240, "total_amount": 7814.52,
    }
    details.update(overrides.pop("details", {}))
    body = {"type": "PO_CREATION", "po_number": "2264110001440", "tenant": "BLINKIT",
            "details": details}
    body.update(overrides)
    return body


def _parse(payload):
    class Raw:
        id = uuid.uuid4()
    Raw.payload = payload
    return BlinkitParser().parse(Raw())


class TestContractEnums:
    def test_tenant_values(self) -> None:
        assert {t.value for t in BlinkitTenant} == {"BLINKIT", "HYPERPURE"}

    def test_event_types(self) -> None:
        assert BlinkitEventType.PO_CREATION == "PO_CREATION"
        assert BlinkitEventType.PO_CANCELLATION == "PO_CANCELLATION"

    def test_ack_status_is_lowercase(self) -> None:
        """
        The contract's enum table is lower-case; its example JSON shows
        "PARTIALLY_ACCEPTED". The table is the normative part.
        """
        assert BlinkitAckStatus.PROCESSING == "processing"
        assert BlinkitAckStatus.PARTIALLY_ACCEPTED == "partially_accepted"
        assert {s.value for s in BlinkitAckStatus} == {
            "processing", "accepted", "partially_accepted", "rejected"
        }

    def test_error_codes(self) -> None:
        assert BlinkitErrorCode.DUPLICATE_PO == "E101"
        assert BlinkitErrorCode.OUTLET_NOT_FOUND == "E102"


class TestLineNumbering:
    def test_zero_based_is_shifted_to_one_based(self) -> None:
        """3.6.3 numbers from zero; our UI and the (po_id, line_number) key assume 1."""
        r = _parse(_payload())
        assert r.success
        assert r.doc.line_items[0].line_number == 1

    def test_one_based_payload_is_left_alone(self) -> None:
        r = _parse(_payload(item={"line_number": 1}))
        assert r.doc.line_items[0].line_number == 1

    def test_relative_order_preserved_across_shift(self) -> None:
        body = _payload()
        base = body["details"]["item_data"][0]
        body["details"]["item_data"] = [
            {**base, "line_number": 0, "item_id": 1},
            {**base, "line_number": 1, "item_id": 2},
            {**base, "line_number": 2, "item_id": 3},
        ]
        body["details"]["total_sku"] = 3
        r = _parse(body)
        assert [li.line_number for li in r.doc.line_items] == [1, 2, 3]
        assert [li.buyer_sku for li in r.doc.line_items] == ["1", "2", "3"]

    def test_missing_line_number_falls_back_to_position(self) -> None:
        body = _payload()
        del body["details"]["item_data"][0]["line_number"]
        r = _parse(body)
        assert r.doc.line_items[0].line_number == 1


class TestSkuFallback:
    def test_empty_sku_code_falls_back_to_item_id(self) -> None:
        """3.6.2 is optional and the contract's own example ships it empty."""
        r = _parse(_payload())
        assert r.doc.line_items[0].buyer_sku == "10016623"
        assert any("sku_code empty" in w for w in r.warnings)

    def test_populated_sku_code_wins(self) -> None:
        r = _parse(_payload(item={"sku_code": "SKU123"}))
        assert r.doc.line_items[0].buyer_sku == "SKU123"
        assert not any("sku_code empty" in w for w in r.warnings)

    def test_neither_identifier_fails_that_line_only(self) -> None:
        body = _payload()
        base = body["details"]["item_data"][0]
        body["details"]["item_data"] = [
            {**base, "sku_code": "", "item_id": None},
            {**base, "sku_code": "GOOD", "line_number": 1},
        ]
        r = _parse(body)
        assert r.success
        assert len(r.doc.line_items) == 1
        assert r.doc.line_items[0].buyer_sku == "GOOD"


class TestUom:
    def test_uom_unit_is_used(self) -> None:
        """Hardcoding EA mislabels a 12 ml item as 12 each."""
        assert _parse(_payload()).doc.line_items[0].buyer_uom == "ml"

    def test_missing_uom_defaults_with_warning(self) -> None:
        body = _payload()
        del body["details"]["item_data"][0]["uom"]
        r = _parse(body)
        assert r.doc.line_items[0].buyer_uom == "EA"
        assert any("W101" in w for w in r.warnings)


class TestPricingAndTax:
    def test_prices_on_basic_price_not_landing_price(self) -> None:
        """
        3.6.6 basic_price is pre-tax; 3.6.5 landing_price already includes tax and
        logistics. Pricing on landing_price would inflate taxable value and double-count.
        """
        line = _parse(_payload()).doc.line_items[0]
        assert line.unit_price == Decimal("31.01")
        assert line.taxable_amount == Decimal("7442.40")   # 240 * 31.01

    def test_intrastate_split(self) -> None:
        line = _parse(_payload()).doc.line_items[0]
        assert line.cgst_amount == Decimal("186.06")       # 2.5% of 7442.40
        assert line.sgst_amount == Decimal("186.06")
        assert line.igst_amount is None

    def test_cess_percentage_and_flat_value_combine(self) -> None:
        """3.6.7.4 is a percentage, 3.6.7.5 an absolute amount — different units."""
        r = _parse(_payload(item={"tax_details": {
            "cgst_percentage": 0, "sgst_percentage": 0, "igst_percentage": 5,
            "cess_percentage": 1, "additional_cess_value": 10,
        }}))
        line = r.doc.line_items[0]
        assert line.cess_amount == Decimal("84.42")        # 1% of 7442.40 + 10
        assert r.doc.cess_amount == Decimal("84.42")


class TestHeaderChecks:
    def test_hyperpure_tenant_warns(self) -> None:
        """HYPERPURE is a different legal buyer — wrong CardCode invoices the wrong party."""
        r = _parse(_payload(tenant="HYPERPURE"))
        assert r.success
        assert any("HYPERPURE" in w and "legal buyer" in w for w in r.warnings)

    def test_blinkit_tenant_is_silent(self) -> None:
        assert not any("tenant" in w for w in _parse(_payload()).warnings)

    def test_unknown_tenant_warns(self) -> None:
        assert any("not a contract value" in w for w in _parse(_payload(tenant="ZOMATO")).warnings)

    def test_po_number_mismatch_warns(self) -> None:
        """Contract 3.1 requires details.po_number to equal the top-level value."""
        r = _parse(_payload(details={"po_number": "DIFFERENT"}))
        assert any("po_number mismatch" in w for w in r.warnings)

    def test_total_sku_mismatch_warns(self) -> None:
        assert any("total_sku" in w for w in _parse(_payload(details={"total_sku": 5})).warnings)

    def test_total_qty_mismatch_warns(self) -> None:
        assert any("total_qty" in w for w in _parse(_payload(details={"total_qty": 999})).warnings)

    def test_total_amount_divergence_warns(self) -> None:
        """The contract's own example ships total_amount 42 for a 7,814.52 PO."""
        r = _parse(_payload(details={"total_amount": 42}))
        assert any("total_amount" in w for w in r.warnings)

    def test_rounding_difference_does_not_warn(self) -> None:
        r = _parse(_payload(details={"total_amount": 7814.90}))
        assert not any("total_amount" in w for w in r.warnings)


class TestOptionalBlocksAreTolerated:
    @pytest.mark.parametrize("key", [
        "vehicle_details", "supplier_details", "custom_attributes", "expiry_date",
    ])
    def test_missing_optional_block_still_parses(self, key: str) -> None:
        """These carry no canonical destination; their absence must not fail a PO."""
        body = _payload()
        body["details"].pop(key, None)
        assert _parse(body).success
