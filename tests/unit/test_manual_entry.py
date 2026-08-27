"""
Hand-keyed purchase orders.

Partners with no integration (LOTS by phone, Reliance/JioMart until its scraper is
built) get their orders typed in. The entry becomes a raw_message and runs the same
pipeline as a Blinkit webhook, so what matters here is that the parser produces a
canonical EDI850 nothing downstream can distinguish — and that the two things the
operator cannot be asked to get right, the GST split and the arithmetic, are derived
rather than typed.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models._enums import PoStatus, SourceChannel
from app.parsers.manual_parser import ENTRY_TYPE, ManualEntryParser, is_manual_entry

SELLER_HARYANA = "06AAFCE9432L1ZC"


def _raw(**overrides) -> SimpleNamespace:
    payload = {
        "_entry_type": ENTRY_TYPE,
        "partner_code": "LOTS",
        "buyer_po_number": "LOTS-2026-0117",
        "buyer_po_date": "2026-08-25",
        "requested_delivery_date": "2026-08-28",
        "buyer_name": "LOTS Wholesale Solutions",
        "seller_gstin": SELLER_HARYANA,
        "seller_state": "Haryana",
        "ship_to": {"warehouse_code": "LOTS-DEL-01", "state": "Haryana"},
        "line_items": [
            {"buyer_sku": "10116319", "ordered_qty": "36", "unit_price": "92.86",
             "gst_rate": "5", "hsn_code": "21069099", "buyer_uom": "PC"},
        ],
        "entered_by": "ops@letstryfoods.com",
    }
    payload.update(overrides)
    return SimpleNamespace(id=uuid4(), payload=payload)


def _parse(**overrides):
    return ManualEntryParser().parse(_raw(**overrides))


class TestRouting:
    def test_recognises_a_manual_entry(self) -> None:
        assert is_manual_entry(_raw()) is True

    def test_ignores_a_partner_payload(self) -> None:
        assert is_manual_entry(SimpleNamespace(payload={"eventId": "x"})) is False

    def test_ignores_a_message_with_no_payload(self) -> None:
        assert is_manual_entry(SimpleNamespace(payload=None)) is False


class TestGstSplit:
    def test_same_state_is_cgst_sgst(self) -> None:
        # Haryana -> Haryana. 36 x 92.86 = 3342.96 taxable, 2.5% each way.
        line = _parse().doc.line_items[0]
        assert line.igst_amount is None
        assert line.cgst_rate == Decimal("2.5")
        assert line.cgst_amount == Decimal("83.57")

    def test_the_two_halves_are_always_equal(self) -> None:
        # 167.15 halved is 83.575. Rounding the combined amount gives 83.58 and 83.57,
        # and a CGST that differs from its SGST is queried at filing. Each half is
        # computed from its own rate instead.
        line = _parse().doc.line_items[0]
        assert line.cgst_amount == line.sgst_amount

    def test_the_header_agrees_with_the_lines_it_was_built_from(self) -> None:
        # Equal halves can sum to a paisa under the combined figure. That is only safe
        # because nothing computes the total a second way — this is the guard.
        doc = _parse().doc
        assert doc.grand_total == sum(line.line_total for line in doc.line_items)
        assert doc.cgst_amount == sum(line.cgst_amount for line in doc.line_items)

    def test_different_state_is_igst(self) -> None:
        doc = _parse(ship_to={"warehouse_code": "LOTS-MUM-01", "state": "Maharashtra"}).doc
        line = doc.line_items[0]
        assert line.cgst_amount is None and line.sgst_amount is None
        assert line.igst_rate == Decimal("5")
        assert line.igst_amount == Decimal("167.15")

    def test_gstin_beats_a_typed_state(self) -> None:
        # A GSTIN prefix is unambiguous; a typed state name is not. 27 = Maharashtra,
        # so this is interstate however the state box was filled in.
        doc = _parse(ship_to={"state": "Haryana", "gstin": "27AAFCD5862R1ZX"}).doc
        assert doc.line_items[0].igst_amount is not None

    def test_unknown_ship_to_state_is_refused_not_guessed(self) -> None:
        result = _parse(ship_to={"warehouse_code": "LOTS-X"})
        assert result.success is False
        assert any("split" in e.lower() for e in result.errors)

    def test_unknown_seller_state_is_refused(self) -> None:
        result = _parse(seller_gstin=None, seller_state=None)
        assert result.success is False
        assert any("seller" in e.lower() for e in result.errors)


class TestDerivedAmounts:
    def test_totals_are_the_sum_of_the_lines(self) -> None:
        doc = _parse(line_items=[
            {"buyer_sku": "A", "ordered_qty": "10", "unit_price": "100", "gst_rate": "5"},
            {"buyer_sku": "B", "ordered_qty": "3", "unit_price": "250", "gst_rate": "12"},
        ]).doc
        assert doc.subtotal_amount == Decimal("1750.00")
        assert doc.grand_total == sum(line.line_total for line in doc.line_items)
        assert doc.grand_total == doc.subtotal_amount + doc.cgst_amount + doc.sgst_amount

    def test_discount_reduces_the_taxable_amount(self) -> None:
        doc = _parse(line_items=[
            {"buyer_sku": "A", "ordered_qty": "10", "unit_price": "100",
             "gst_rate": "5", "discount_pct": "10"},
        ]).doc
        line = doc.line_items[0]
        assert line.taxable_amount == Decimal("900.00")
        assert doc.total_discount == Decimal("100.00")

    def test_zero_rated_line_carries_no_tax(self) -> None:
        doc = _parse(line_items=[
            {"buyer_sku": "A", "ordered_qty": "5", "unit_price": "40", "gst_rate": "0"},
        ]).doc
        line = doc.line_items[0]
        assert line.line_total == Decimal("200.00")
        assert (line.cgst_amount, line.sgst_amount, line.igst_amount) == (None, None, None)


class TestCanonicalShape:
    def test_produces_a_manual_channel_document(self) -> None:
        doc = _parse().doc
        assert doc.source_channel is SourceChannel.MANUAL
        assert doc.po_status is PoStatus.PARSED
        assert doc.trading_partner_code == "LOTS"
        assert doc.buyer_po_number == "LOTS-2026-0117"
        assert str(doc.buyer_po_date) == "2026-08-25"
        assert str(doc.requested_delivery_date) == "2026-08-28"
        assert doc.ship_to.warehouse_code == "LOTS-DEL-01"

    def test_line_numbers_are_sequential(self) -> None:
        doc = _parse(line_items=[
            {"buyer_sku": f"SKU{i}", "ordered_qty": "1", "unit_price": "10", "gst_rate": "5"}
            for i in range(4)
        ]).doc
        assert [line.line_number for line in doc.line_items] == [1, 2, 3, 4]

    def test_operator_note_survives_as_a_warning(self) -> None:
        result = _parse(notes="phoned in by Rakesh, 25 Aug")
        assert any("Rakesh" in w for w in result.warnings)


class TestRejections:
    def test_no_po_number(self) -> None:
        result = _parse(buyer_po_number="")
        assert result.success is False
        assert any("buyer_po_number" in e for e in result.errors)

    def test_no_line_items(self) -> None:
        result = _parse(line_items=[])
        assert result.success is False

    @pytest.mark.parametrize("qty", ["0", "-5", "abc", None])
    def test_bad_quantity(self, qty) -> None:
        result = _parse(line_items=[
            {"buyer_sku": "A", "ordered_qty": qty, "unit_price": "10", "gst_rate": "5"},
        ])
        assert result.success is False
        assert any("quantity" in e.lower() for e in result.errors)

    def test_missing_sku(self) -> None:
        result = _parse(line_items=[
            {"buyer_sku": "", "ordered_qty": "1", "unit_price": "10", "gst_rate": "5"},
        ])
        assert result.success is False
        assert any("SKU" in e for e in result.errors)

    def test_every_bad_line_is_reported_not_just_the_first(self) -> None:
        # Ops keying 40 lines should get one list of what to fix, not 40 round trips.
        result = _parse(line_items=[
            {"buyer_sku": "", "ordered_qty": "1", "unit_price": "10", "gst_rate": "5"},
            {"buyer_sku": "B", "ordered_qty": "0", "unit_price": "10", "gst_rate": "5"},
        ])
        assert result.success is False
        assert len(result.errors) >= 2

    def test_never_raises(self) -> None:
        assert ManualEntryParser().parse(SimpleNamespace(id=uuid4(), payload=None)).success is False


class TestOperatorChosenItem:
    """
    A keyed-in line can name its SAP item directly.

    A manual partner has no catalogue for a buyer SKU to be mapped from, so requiring a
    sku_mapping row first would make hand-keyed orders impossible to process — every
    LOTS line came back E002_SKU_UNRESOLVED. The operator picks the item out of the
    material master instead, which is a decision rather than a guess, and SkuMappingRule
    still verifies it against master data.
    """

    def test_the_chosen_item_lands_on_the_canonical_line(self) -> None:
        doc = _parse(line_items=[
            {"buyer_sku": "PHONE-ORDER-1", "ordered_qty": "10", "unit_price": "100",
             "gst_rate": "5", "b1_item_code": "FG00233"},
        ]).doc
        assert doc.line_items[0].sap_material_no == "FG00233"

    def test_omitting_it_leaves_the_line_for_the_usual_lookup(self) -> None:
        # Nothing else sets sap_material_no before validation, so absent must mean
        # absent — otherwise the rule cannot tell a choice from a default.
        doc = _parse().doc
        assert doc.line_items[0].sap_material_no is None

    def test_blank_is_treated_as_omitted(self) -> None:
        doc = _parse(line_items=[
            {"buyer_sku": "A", "ordered_qty": "1", "unit_price": "10",
             "gst_rate": "5", "b1_item_code": "   "},
        ]).doc
        assert doc.line_items[0].sap_material_no is None


class TestSkuRulePreassigned:
    """
    SkuMappingRule's handling of a line that already names an item.

    This must not become a way for a *partner* PO to skip mapping: no partner parser
    sets sap_material_no, so the branch is only reachable from a hand-keyed order.
    """

    @staticmethod
    def _run(line_code, material):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from app.validators.rules.sku_mapping import SkuMappingRule

        line = SimpleNamespace(
            id=uuid4(), line_number=1, buyer_sku="X", sap_material_no=line_code,
            sku_mapping_id=None,
        )
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = material
        ctx = SimpleNamespace(session=session, lines=[line], partner=SimpleNamespace(id=uuid4()))
        return SkuMappingRule().run(ctx)

    def _material(self, **kw):
        from types import SimpleNamespace

        fields = {
            "item_code": "FG00233", "item_name": "Khatta Meetha",
            "valid_for": 1, "frozen_for": False, "id": uuid4(),
        }
        return SimpleNamespace(**{**fields, **kw})

    def test_a_valid_item_passes(self) -> None:
        assert self._run("FG00233", self._material()) == []

    def test_an_item_not_in_the_master_is_refused(self) -> None:
        # FG00460 was mapped for Blinkit and does not exist in SAP; B1 answered
        # ODBC -2028 and rejected the whole 18-line document. Caught here instead.
        violations = self._run("FG00460", None)
        assert len(violations) == 1
        assert violations[0].issue_code == "E002_SKU_UNRESOLVED"
        assert "material master" in violations[0].message

    def test_an_inactive_item_is_refused(self) -> None:
        violations = self._run("FG00349", self._material(valid_for=0))
        assert len(violations) == 1
        assert "inactive or frozen" in violations[0].message

    def test_a_frozen_item_is_refused(self) -> None:
        violations = self._run("FG00349", self._material(frozen_for=True))
        assert len(violations) == 1
        assert "inactive or frozen" in violations[0].message


class TestCatalogueMerge:
    """
    What a picked line gets filled with.

    Two sources overlap. The mapping is the partner-specific fact — a contracted unit
    price for LOTS is not the price for anyone else, and the UoM they order in is
    theirs — so it wins. Item data fills the rest, because HSN, GST rate, MRP, EAN and
    case size are properties of the product whoever is buying it.
    """

    @staticmethod
    def _material(**kw):
        from types import SimpleNamespace

        fields = {
            "item_code": "FG00319", "item_name": "Let's Try Lite Snacks Sticks 57g",
            "hsn": "21069099", "tax_rate": None, "mrp": 60, "ean_code": "8906161391365",
            "case_size": 36, "invntry_uom": "EA", "sal_unit_msr": None,
        }
        return SimpleNamespace(**{**fields, **kw})

    @staticmethod
    def _mapping(**kw):
        from types import SimpleNamespace

        fields = {"buyer_sku": "104584368", "buyer_uom": "PCS", "unit_price": "31.43"}
        return SimpleNamespace(**{**fields, **kw})

    def _row(self, material=None, mapping=None):
        from app.api.routes.manual_inbox import _catalogue_row

        return _catalogue_row(material or self._material(), mapping)

    def test_a_mapped_item_carries_the_partners_own_fields(self) -> None:
        row = self._row(mapping=self._mapping())
        assert row.mapped is True
        assert row.buyer_sku == "104584368"
        assert str(row.unit_price) == "31.43"
        assert row.buyer_uom == "PCS"

    def test_item_data_fills_the_rest(self) -> None:
        row = self._row(mapping=self._mapping())
        assert row.hsn_code == "21069099"
        assert row.ean_code == "8906161391365"
        assert row.case_size == 36

    def test_an_unmapped_item_is_still_pickable(self) -> None:
        # A hand-keyed order is often for something never sold to this partner before;
        # hiding it would send the operator to Master Data mid-entry.
        row = self._row()
        assert row.mapped is False
        assert row.b1_item_code == "FG00319"
        assert row.buyer_sku is None
        assert row.unit_price is None

    def test_an_unmapped_item_falls_back_to_the_item_uom(self) -> None:
        assert self._row().buyer_uom == "EA"
        assert self._row(material=self._material(sal_unit_msr="BOX")).buyer_uom == "BOX"

    def test_a_mapping_uom_beats_the_item_uom(self) -> None:
        row = self._row(mapping=self._mapping(buyer_uom="PCS"))
        assert row.buyer_uom == "PCS"

    def test_a_mapping_with_no_uom_falls_through(self) -> None:
        row = self._row(mapping=self._mapping(buyer_uom=None))
        assert row.buyer_uom == "EA"

    def test_quantity_is_never_supplied(self) -> None:
        # Nothing in master data knows how many were ordered — it is the one number
        # genuinely on the paper in front of the operator.
        assert not hasattr(self._row(mapping=self._mapping()), "ordered_qty")

    def test_a_missing_gst_rate_stays_absent_rather_than_guessed(self) -> None:
        # material_master.tax_rate is not populated by the item sync today. Filling a
        # rate we do not know would misfile GST on a real order.
        assert self._row(mapping=self._mapping()).gst_rate is None
