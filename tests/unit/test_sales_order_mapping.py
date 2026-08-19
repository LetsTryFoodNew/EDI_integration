"""
Unit tests for the canonical PO → B1 Sales Order mapping.

The tax code is what these mostly pin. Under the India localization the branch is the
"from" state for place of supply, so the same PO booked against a different branch is a
different tax treatment — and a wrong one produces a document B1 accepts happily, with
the error surfacing at GST filing rather than at push time. Nothing here may silently
choose a branch, a state, or a rate.

The expected payload shape is taken from documents actually posted in TESTECPL260422
(e.g. DocEntry 1764), not from the generic Service Layer reference.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.mappers.po_to_sales_order import MappingError, build_sales_order_payload
from app.utils.gst import (
    is_interstate,
    normalize_state,
    resolve_state,
    state_from_gstin,
    vat_group,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _branch(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), bpl_id=5, bpl_name="Maharashtra", state="MH",
        gstin="27AADCL9999Q1ZY", disabled=False, is_active=True, notes=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _partner(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), code="BLINKIT", name="BLINK COMMERCE PRIVATE LIMITED",
        b1_card_code="D00086",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _po(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), buyer_po_number="2264110001442",
        buyer_po_date=date(2026, 8, 19), requested_delivery_date=date(2026, 8, 24),
        currency="INR", buyer_gstin="27ABCDE1234F1Z5",
        ship_to_address={"state": "Maharashtra", "postal_code": "421302"},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _line(**kw) -> SimpleNamespace:
    base = dict(
        line_number=1, buyer_sku="10116317", sap_material_no="FG00310",
        ordered_qty=Decimal("240"), inventory_qty=None, unit_price=Decimal("31.01"),
        discount_pct=None, b1_whs_code=None, buyer_uom="PCS", hsn_code=None,
        cgst_rate=Decimal("2.5"), sgst_rate=Decimal("2.5"), igst_rate=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


_UNSET = object()


def _build(po=None, lines=_UNSET, branch=None, **kw):
    # Sentinel, not `or`: an explicitly empty line list is a case under test, and
    # `lines or [_line()]` would quietly substitute the default for it.
    return build_sales_order_payload(
        po=po or _po(),
        lines=[_line()] if lines is _UNSET else lines,
        partner=kw.pop("partner", None) or _partner(),
        branch=branch or _branch(),
        sku_mappings=kw.pop("sku_mappings", None),
        warehouse_code=kw.pop("warehouse_code", "FG_MH"),
        **kw,
    )


# ── State resolution ──────────────────────────────────────────────────────────

class TestStateResolution:
    @pytest.mark.parametrize(
        ("gstin", "expected"),
        [("27ABCDE1234F1Z5", "MH"), ("06AADCH7038R1Z1", "HR"),
         ("07AAECZ7891F1ZB", "DL"), ("29AAECG1234K1Z5", "KA")],
    )
    def test_gstin_prefix_wins(self, gstin: str, expected: str) -> None:
        assert state_from_gstin(gstin) == expected

    def test_names_and_codes_both_resolve(self) -> None:
        assert normalize_state("Maharashtra") == "MH"
        assert normalize_state("MH") == "MH"
        assert normalize_state("  maharashtra ") == "MH"

    def test_b1_uses_its_own_abbreviations(self) -> None:
        """B1 says BH/OD/UA where ISO-style lists say BR/OR/UK. B1 is what we compare against."""
        assert normalize_state("Bihar") == "BH"
        assert normalize_state("Odisha") == "OD"
        assert normalize_state("Uttarakhand") == "UA"

    def test_common_partner_spellings_resolve(self) -> None:
        assert normalize_state("Orissa") == "OD"
        assert normalize_state("New Delhi") == "DL"
        assert normalize_state("Pondicherry") == "PY"

    def test_gstin_beats_a_contradictory_state_field(self) -> None:
        # 27 is Maharashtra; the text says Haryana. The registration is authoritative.
        assert resolve_state(gstin="27ABCDE1234F1Z5", state="Haryana") == "MH"

    def test_unknown_state_is_none_not_a_guess(self) -> None:
        assert normalize_state("Atlantis") is None
        assert is_interstate("Atlantis", "MH") is None


# ── Tax code ──────────────────────────────────────────────────────────────────

class TestVatGroup:
    def test_naming_matches_posted_documents(self) -> None:
        assert vat_group(5, interstate=False) == "CSGST@5"
        assert vat_group(5, interstate=True) == "IGST@5"

    def test_rate_is_the_combined_total(self) -> None:
        """A 2.5+2.5 split is CSGST@5, not CSGST@2.5 — B1 carries the combined rate."""
        payload, _ = _build()
        assert payload["DocumentLines"][0]["VatGroup"] == "CSGST@5"

    def test_trailing_zeros_are_trimmed(self) -> None:
        assert vat_group(18.0, interstate=False) == "CSGST@18"
        assert vat_group(Decimal("12.00"), interstate=True) == "IGST@12"
        assert vat_group(2.5, interstate=False) == "CSGST@2.5"


class TestPlaceOfSupply:
    def test_same_state_is_cgst_sgst(self) -> None:
        payload, _ = _build(branch=_branch(bpl_id=5, state="MH"))
        assert payload["DocumentLines"][0]["VatGroup"] == "CSGST@5"

    def test_different_state_is_igst(self) -> None:
        """Identical PO, Haryana branch — the tax must follow the branch, not the seller."""
        payload, _ = _build(
            branch=_branch(bpl_id=1, bpl_name="Haryana", state="HR", gstin="06AADCL9999Q1ZK"),
        )
        assert payload["DocumentLines"][0]["VatGroup"] == "IGST@5"

    def test_igst_line_rate_is_used_whole(self) -> None:
        payload, _ = _build(
            branch=_branch(bpl_id=1, state="HR", gstin="06AADCL9999Q1ZK"),
            lines=[_line(cgst_rate=None, sgst_rate=None, igst_rate=Decimal("12"))],
        )
        assert payload["DocumentLines"][0]["VatGroup"] == "IGST@12"

    def test_undeterminable_state_refuses_to_push(self) -> None:
        """Better a blocked push than a document taxed on a guess."""
        po = _po(buyer_gstin=None, ship_to_address={"state": "Atlantis"})
        with pytest.raises(MappingError, match="place of supply"):
            _build(po=po)

    def test_missing_buyer_gstin_falls_back_with_a_warning(self) -> None:
        po = _po(buyer_gstin=None, ship_to_address={"state": "Maharashtra"})
        payload, warnings = _build(po=po)
        assert payload["DocumentLines"][0]["VatGroup"] == "CSGST@5"
        assert any("GSTIN" in w for w in warnings)

    def test_missing_line_rate_defaults_loudly(self) -> None:
        payload, warnings = _build(lines=[_line(cgst_rate=None, sgst_rate=None, igst_rate=None)])
        assert payload["DocumentLines"][0]["VatGroup"] == "CSGST@5"
        assert any("defaulted" in w for w in warnings)


# ── Payload shape ─────────────────────────────────────────────────────────────

class TestHeader:
    def test_matches_the_posted_document_shape(self) -> None:
        payload, warnings = _build(ship_to_code="421302-HOT", pay_to_code="421302-HOT")
        assert payload["CardCode"] == "D00086"
        assert payload["CardName"] == "BLINK COMMERCE PRIVATE LIMITED"
        assert payload["DocDate"] == "2026-08-19"
        assert payload["TaxDate"] == "2026-08-19"
        assert payload["DocDueDate"] == "2026-08-24"
        assert payload["BPL_IDAssignedToInvoice"] == 5
        assert payload["DocCurrency"] == "INR"
        assert payload["ShipToCode"] == "421302-HOT"
        assert payload["PayToCode"] == "421302-HOT"
        assert warnings == []

    def test_num_at_card_carries_the_retailers_po_number(self) -> None:
        """NumAtCard is how finance reconciles the Sales Order back to the retailer."""
        payload, _ = _build()
        assert payload["NumAtCard"] == "2264110001442"

    def test_dc_tat_is_the_turnaround_in_days(self) -> None:
        payload, _ = _build()
        assert payload["U_DC_TAT"] == 5           # 19 Aug → 24 Aug
        assert payload["U_OrdType"] == "N"

    def test_no_undefined_udf_is_sent(self) -> None:
        """U_MWOrderID is not defined on ORDR here; an unknown property fails the POST."""
        payload, _ = _build()
        assert "U_MWOrderID" not in payload
        udfs = {k for k in payload if k.startswith("U_")}
        assert udfs <= {"U_OrdType", "U_POEXP_DT", "U_DC_TAT"}

    def test_missing_ship_to_warns_rather_than_silently_defaulting(self) -> None:
        _, warnings = _build()
        assert any("ShipToCode" in w for w in warnings)

    def test_missing_card_code_is_refused(self) -> None:
        with pytest.raises(MappingError, match="b1_card_code"):
            _build(partner=_partner(b1_card_code=None))


class TestLines:
    def test_line_shape(self) -> None:
        payload, _ = _build()
        assert payload["DocumentLines"] == [{
            "ItemCode": "FG00310",
            "Quantity": 240.0,
            "WarehouseCode": "FG_MH",
            "Price": 31.01,
            "VatGroup": "CSGST@5",
            "DiscountPercent": 0.0,
            "Currency": "INR",
            "ShipDate": "2026-08-24",
        }]

    def test_no_uom_fields_are_sent(self) -> None:
        """Items here have no UoM group; posted lines carry UoMCode "Manual"."""
        payload, _ = _build()
        line = payload["DocumentLines"][0]
        assert "UnitOfMeasureCode" not in line
        assert "UoMEntry" not in line

    def test_selection_beats_the_warehouse_cached_on_the_line(self) -> None:
        """
        ShipToMappingRule caches a default warehouse on each line. The operator's
        choice must win, or picking a warehouse in the dialog would do nothing — and
        the cached value can disagree with the chosen branch, which B1 rejects.
        """
        payload, warnings = _build(
            lines=[_line(b1_whs_code="WH01")], warehouse_code="FG_MH",
        )
        assert payload["DocumentLines"][0]["WarehouseCode"] == "FG_MH"
        assert any("WH01" in w and "FG_MH" in w for w in warnings)

    def test_agreeing_line_warehouse_produces_no_noise(self) -> None:
        payload, warnings = _build(
            lines=[_line(b1_whs_code="FG_MH")], warehouse_code="FG_MH",
            ship_to_code="421302-HOT",   # otherwise the missing-ShipToCode warning fires
        )
        assert payload["DocumentLines"][0]["WarehouseCode"] == "FG_MH"
        assert warnings == []

    def test_inventory_qty_wins_over_ordered_qty(self) -> None:
        payload, _ = _build(lines=[_line(ordered_qty=Decimal("10"), inventory_qty=Decimal("240"))])
        assert payload["DocumentLines"][0]["Quantity"] == 240.0

    def test_uom_conversion_applies_when_no_inventory_qty(self) -> None:
        mapping = SimpleNamespace(qty_per_buyer_uom=Decimal("24"))
        payload, _ = _build(
            lines=[_line(ordered_qty=Decimal("10"))],
            sku_mappings={"10116317": mapping},
        )
        assert payload["DocumentLines"][0]["Quantity"] == 240.0

    def test_unmapped_item_is_refused(self) -> None:
        with pytest.raises(MappingError, match="no B1 item code"):
            _build(lines=[_line(sap_material_no=None)])

    def test_zero_quantity_is_refused(self) -> None:
        with pytest.raises(MappingError, match="quantity"):
            _build(lines=[_line(ordered_qty=Decimal("0"))])

    def test_no_lines_is_refused(self) -> None:
        with pytest.raises(MappingError, match="at least one line"):
            _build(lines=[])

    def test_no_warehouse_is_refused(self) -> None:
        with pytest.raises(MappingError, match="No warehouse"):
            _build(warehouse_code="")
