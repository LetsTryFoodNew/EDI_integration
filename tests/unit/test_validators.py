"""
Unit tests for Phase 5 — validation engine and all rule classes.

Each rule is tested for: happy path (pass), failure case, and at least one edge case.
ValidationEngine and validate_po workflow are tested with mocked DB.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_po(**kwargs: object) -> MagicMock:
    po = MagicMock()
    po.id = uuid.uuid4()
    po.buyer_gstin = kwargs.get("buyer_gstin", "27AABCT1234M1Z5")
    po.ship_to_code = kwargs.get("ship_to_code", None)
    po.grand_total = kwargs.get("grand_total", None)
    po.po_status = kwargs.get("po_status", "PARSED")
    po.trading_partner_id = kwargs.get("trading_partner_id", uuid.uuid4())
    return po


def _make_line(**kwargs: object) -> MagicMock:
    line = MagicMock()
    line.id = uuid.uuid4()
    line.line_number = kwargs.get("line_number", 1)
    line.buyer_sku = kwargs.get("buyer_sku", "TEST-SKU-001")
    line.buyer_sku_description = kwargs.get("buyer_sku_description", "Test Product 100g")
    line.buyer_uom = kwargs.get("buyer_uom", "PC")
    line.ordered_qty = kwargs.get("ordered_qty", Decimal("10"))
    line.unit_price = kwargs.get("unit_price", Decimal("50.00"))
    line.cgst_rate = kwargs.get("cgst_rate", Decimal("6"))
    line.sgst_rate = kwargs.get("sgst_rate", Decimal("6"))
    line.igst_rate = kwargs.get("igst_rate", Decimal("0"))
    line.line_total = kwargs.get("line_total", Decimal("500.00"))
    line.sku_mapping = kwargs.get("sku_mapping", None)
    line.sku_mapping_id = kwargs.get("sku_mapping_id", None)
    line.sap_material_no = kwargs.get("sap_material_no", None)
    line.b1_whs_code = kwargs.get("b1_whs_code", None)
    return line


def _make_partner(**kwargs: object) -> MagicMock:
    p = MagicMock()
    p.id = uuid.uuid4()
    p.code = kwargs.get("code", "BLINKIT")
    p.api_config = kwargs.get("api_config", {})
    return p


def _make_ctx(po=None, lines=None, partner=None, session=None):
    from app.validators.engine import ValidationContext
    return ValidationContext(
        po=po or _make_po(),
        lines=lines or [],
        partner=partner or _make_partner(),
        session=session or MagicMock(),
    )


# ── GstinFormatRule ───────────────────────────────────────────────────────────

class TestGstinFormatRule:
    def setup_method(self) -> None:
        from app.validators.rules.gstin import GstinFormatRule
        self.rule = GstinFormatRule()

    def test_valid_gstin_passes(self) -> None:
        ctx = _make_ctx(po=_make_po(buyer_gstin="27AABCT1234M1Z5"))
        assert self.rule.run(ctx) == []

    def test_missing_gstin_is_error(self) -> None:
        ctx = _make_ctx(po=_make_po(buyer_gstin=None))
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].severity == "ERROR"
        assert violations[0].issue_code == "E001_MISSING_BUYER_GSTIN"

    def test_empty_string_gstin_is_error(self) -> None:
        ctx = _make_ctx(po=_make_po(buyer_gstin=""))
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].issue_code == "E001_MISSING_BUYER_GSTIN"

    def test_malformed_gstin_too_short(self) -> None:
        ctx = _make_ctx(po=_make_po(buyer_gstin="27AABCT1234"))
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].issue_code == "E001_INVALID_BUYER_GSTIN"

    def test_lowercase_gstin_fails(self) -> None:
        ctx = _make_ctx(po=_make_po(buyer_gstin="27aabct1234m1z5"))
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].severity == "ERROR"

    def test_another_valid_gstin(self) -> None:
        ctx = _make_ctx(po=_make_po(buyer_gstin="29AATFB2317J1ZS"))
        assert self.rule.run(ctx) == []


# ── TaxConsistencyRule ────────────────────────────────────────────────────────

class TestTaxConsistencyRule:
    def setup_method(self) -> None:
        from app.validators.rules.tax_consistency import TaxConsistencyRule
        self.rule = TaxConsistencyRule()

    def test_cgst_sgst_only_passes(self) -> None:
        line = _make_line(cgst_rate=Decimal("6"), sgst_rate=Decimal("6"), igst_rate=Decimal("0"))
        ctx = _make_ctx(lines=[line])
        assert self.rule.run(ctx) == []

    def test_igst_only_passes(self) -> None:
        line = _make_line(cgst_rate=Decimal("0"), sgst_rate=Decimal("0"), igst_rate=Decimal("12"))
        ctx = _make_ctx(lines=[line])
        assert self.rule.run(ctx) == []

    def test_cgst_and_igst_both_nonzero_is_error(self) -> None:
        line = _make_line(cgst_rate=Decimal("6"), sgst_rate=Decimal("6"), igst_rate=Decimal("12"))
        ctx = _make_ctx(lines=[line])
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].severity == "ERROR"
        assert violations[0].issue_code == "E005_TAX_CGST_AND_IGST"

    def test_cgst_sgst_mismatch_is_warning(self) -> None:
        line = _make_line(cgst_rate=Decimal("6"), sgst_rate=Decimal("9"), igst_rate=Decimal("0"))
        ctx = _make_ctx(lines=[line])
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].severity == "WARNING"
        assert violations[0].issue_code == "W005_CGST_SGST_MISMATCH"

    def test_multiple_lines_independent(self) -> None:
        good = _make_line(line_number=1, cgst_rate=Decimal("6"), sgst_rate=Decimal("6"), igst_rate=Decimal("0"))
        bad = _make_line(line_number=2, cgst_rate=Decimal("6"), sgst_rate=Decimal("6"), igst_rate=Decimal("12"))
        ctx = _make_ctx(lines=[good, bad])
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].line_id == bad.id

    def test_all_zeros_passes(self) -> None:
        line = _make_line(cgst_rate=Decimal("0"), sgst_rate=Decimal("0"), igst_rate=Decimal("0"))
        ctx = _make_ctx(lines=[line])
        assert self.rule.run(ctx) == []


# ── TotalReconciliationRule ───────────────────────────────────────────────────

class TestTotalReconciliationRule:
    def setup_method(self) -> None:
        from app.validators.rules.total_reconciliation import TotalReconciliationRule
        self.rule = TotalReconciliationRule()

    def test_matching_totals_passes(self) -> None:
        po = _make_po(grand_total=Decimal("1000.00"))
        line = _make_line(line_total=Decimal("1000.00"))
        ctx = _make_ctx(po=po, lines=[line])
        assert self.rule.run(ctx) == []

    def test_within_tolerance_passes(self) -> None:
        po = _make_po(grand_total=Decimal("1000.00"))
        line = _make_line(line_total=Decimal("999.50"))  # diff = 0.50 < 1.00
        ctx = _make_ctx(po=po, lines=[line])
        assert self.rule.run(ctx) == []

    def test_exceeds_tolerance_is_warning(self) -> None:
        po = _make_po(grand_total=Decimal("1000.00"))
        line = _make_line(line_total=Decimal("998.00"))  # diff = 2.00 > 1.00
        ctx = _make_ctx(po=po, lines=[line])
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].severity == "WARNING"
        assert violations[0].issue_code == "W006_TOTAL_MISMATCH"

    def test_no_grand_total_skips(self) -> None:
        po = _make_po(grand_total=None)
        ctx = _make_ctx(po=po)
        assert self.rule.run(ctx) == []

    def test_multiple_lines_summed(self) -> None:
        po = _make_po(grand_total=Decimal("1500.00"))
        lines = [
            _make_line(line_number=1, line_total=Decimal("500.00")),
            _make_line(line_number=2, line_total=Decimal("1000.00")),
        ]
        ctx = _make_ctx(po=po, lines=lines)
        assert self.rule.run(ctx) == []

    def test_line_with_none_total_excluded_from_sum(self) -> None:
        po = _make_po(grand_total=Decimal("500.00"))
        lines = [
            _make_line(line_number=1, line_total=Decimal("500.00")),
            _make_line(line_number=2, line_total=None),
        ]
        ctx = _make_ctx(po=po, lines=lines)
        assert self.rule.run(ctx) == []


# ── MoqRule ───────────────────────────────────────────────────────────────────

class TestMoqRule:
    def setup_method(self) -> None:
        from app.validators.rules.moq import MoqRule
        self.rule = MoqRule()

    def test_no_moq_config_passes(self) -> None:
        ctx = _make_ctx(lines=[_make_line()])
        assert self.rule.run(ctx) == []

    def test_above_moq_passes(self) -> None:
        partner = _make_partner(api_config={"moq": {"TEST-SKU-001": 6}})
        line = _make_line(buyer_sku="TEST-SKU-001", ordered_qty=Decimal("10"))
        ctx = _make_ctx(lines=[line], partner=partner)
        assert self.rule.run(ctx) == []

    def test_below_moq_is_warning(self) -> None:
        partner = _make_partner(api_config={"moq": {"TEST-SKU-001": 12}})
        line = _make_line(buyer_sku="TEST-SKU-001", ordered_qty=Decimal("6"))
        ctx = _make_ctx(lines=[line], partner=partner)
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].severity == "WARNING"
        assert violations[0].issue_code == "W007_BELOW_MOQ"

    def test_moq_from_mapping_notes(self) -> None:
        mapping = MagicMock()
        mapping.notes = '{"moq": 24}'
        line = _make_line(ordered_qty=Decimal("10"), sku_mapping=mapping)
        ctx = _make_ctx(lines=[line])
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].issue_code == "W007_BELOW_MOQ"

    def test_exact_moq_quantity_passes(self) -> None:
        partner = _make_partner(api_config={"moq": {"TEST-SKU-001": 6}})
        line = _make_line(buyer_sku="TEST-SKU-001", ordered_qty=Decimal("6"))
        ctx = _make_ctx(lines=[line], partner=partner)
        assert self.rule.run(ctx) == []


# ── PriceVarianceRule ─────────────────────────────────────────────────────────

class TestPriceVarianceRule:
    def setup_method(self) -> None:
        from app.validators.rules.pricing import PriceVarianceRule
        self.rule = PriceVarianceRule()

    def _make_mapping_with_price(self, contracted: str) -> MagicMock:
        m = MagicMock()
        m.notes = f'{{"contracted_price": {contracted}}}'
        return m

    def test_no_contracted_price_passes(self) -> None:
        line = _make_line(unit_price=Decimal("50"), sku_mapping=None)
        ctx = _make_ctx(lines=[line])
        assert self.rule.run(ctx) == []

    def test_within_tolerance_passes(self) -> None:
        mapping = self._make_mapping_with_price("100.00")
        line = _make_line(unit_price=Decimal("105"), sku_mapping=mapping)  # 5% < 10% default
        ctx = _make_ctx(lines=[line])
        assert self.rule.run(ctx) == []

    def test_exceeds_tolerance_is_warning(self) -> None:
        mapping = self._make_mapping_with_price("100.00")
        line = _make_line(unit_price=Decimal("115"), sku_mapping=mapping)  # 15% > 10%
        ctx = _make_ctx(lines=[line])
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].severity == "WARNING"
        assert violations[0].issue_code == "W004_PRICE_VARIANCE"

    def test_custom_tolerance_from_partner_config(self) -> None:
        partner = _make_partner(api_config={"price_variance_threshold_pct": 20})
        mapping = self._make_mapping_with_price("100.00")
        line = _make_line(unit_price=Decimal("115"), sku_mapping=mapping)  # 15% < 20%
        ctx = _make_ctx(lines=[line], partner=partner)
        assert self.rule.run(ctx) == []

    def test_no_unit_price_on_line_passes(self) -> None:
        mapping = self._make_mapping_with_price("100.00")
        line = _make_line(unit_price=None, sku_mapping=mapping)
        ctx = _make_ctx(lines=[line])
        assert self.rule.run(ctx) == []


# ── ShipToMappingRule ─────────────────────────────────────────────────────────

class TestShipToMappingRule:
    def setup_method(self) -> None:
        from app.validators.rules.ship_to_mapping import ShipToMappingRule
        self.rule = ShipToMappingRule()

    def test_no_ship_to_code_passes(self) -> None:
        ctx = _make_ctx(po=_make_po(ship_to_code=None))
        assert self.rule.run(ctx) == []

    def test_mapped_ship_to_passes(self) -> None:
        mapping = MagicMock()
        mapping.b1_whs_code = "WH-01"
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = mapping

        po = _make_po(ship_to_code="DELHI-DC-01")
        line = _make_line()
        ctx = _make_ctx(po=po, lines=[line], session=session)
        assert self.rule.run(ctx) == []
        assert line.b1_whs_code == "WH-01"

    def test_unmapped_ship_to_is_warning(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        po = _make_po(ship_to_code="UNKNOWN-WAREHOUSE")
        ctx = _make_ctx(po=po, session=session)
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].severity == "WARNING"
        assert violations[0].issue_code == "W003_SHIP_TO_UNMAPPED"


# ── SkuMappingRule ────────────────────────────────────────────────────────────

class TestSkuMappingRule:
    def setup_method(self) -> None:
        from app.validators.rules.sku_mapping import SkuMappingRule
        self.rule = SkuMappingRule()

    def _session_with_exact_mapping(self, material_id: uuid.UUID) -> MagicMock:
        """Mock session that returns an exact SkuMapping on the first call."""
        mapping = MagicMock()
        mapping.id = uuid.uuid4()
        mapping.material_id = material_id
        mapping.b1_whs_code = None

        mat = MagicMock()
        mat.b1_item_code = "LTF-001"

        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = mapping
        session.get.return_value = mat
        return session

    def test_exact_mapping_found_passes(self) -> None:
        mat_id = uuid.uuid4()
        session = self._session_with_exact_mapping(mat_id)
        line = _make_line()
        ctx = _make_ctx(lines=[line], session=session)
        violations = self.rule.run(ctx)
        assert violations == []

    def test_no_mapping_found_is_error(self) -> None:
        session = MagicMock()
        # All three queries return None (exact, cross-partner, no materials for fuzzy)
        session.execute.return_value.scalar_one_or_none.return_value = None
        session.execute.return_value.first.return_value = None
        session.execute.return_value.scalars.return_value.all.return_value = []

        line = _make_line(buyer_sku="TOTALLY-UNKNOWN-SKU")
        ctx = _make_ctx(lines=[line], session=session)
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].severity == "ERROR"
        assert violations[0].issue_code == "E002_SKU_UNRESOLVED"

    def test_multiple_lines_one_unmapped(self) -> None:
        mat_id = uuid.uuid4()
        mapping = MagicMock()
        mapping.id = uuid.uuid4()
        mapping.material_id = mat_id

        mat = MagicMock()
        mat.b1_item_code = "LTF-001"

        call_count = 0

        def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            # First call (line 1 exact lookup) → found
            # Subsequent calls (line 2 lookups) → not found
            if call_count == 1:
                result.scalar_one_or_none.return_value = mapping
                result.first.return_value = None
                result.scalars.return_value.all.return_value = []
            else:
                result.scalar_one_or_none.return_value = None
                result.first.return_value = None
                result.scalars.return_value.all.return_value = []
            return result

        session = MagicMock()
        session.execute.side_effect = execute_side_effect
        session.get.return_value = mat

        line1 = _make_line(line_number=1, buyer_sku="MAPPED-SKU")
        line2 = _make_line(line_number=2, buyer_sku="UNMAPPED-SKU")
        ctx = _make_ctx(lines=[line1, line2], session=session)
        violations = self.rule.run(ctx)
        assert len(violations) == 1
        assert violations[0].line_id == line2.id


# ── ValidationEngine ──────────────────────────────────────────────────────────

class TestValidationEngine:
    def test_engine_with_no_rules_returns_empty(self) -> None:
        from app.validators.engine import ValidationEngine
        engine = ValidationEngine(rules=[])
        ctx = _make_ctx()
        result = engine.run(ctx)
        assert result.violations == []
        assert not result.has_errors
        assert not result.has_warnings

    def test_engine_collects_violations_from_all_rules(self) -> None:
        from app.validators.engine import BaseRule, RuleViolation, ValidationEngine

        class AlwaysError(BaseRule):
            def run(self, ctx: object) -> list[RuleViolation]:
                return [RuleViolation("E_TEST", "ERROR", "test error")]

        class AlwaysWarn(BaseRule):
            def run(self, ctx: object) -> list[RuleViolation]:
                return [RuleViolation("W_TEST", "WARNING", "test warning")]

        engine = ValidationEngine(rules=[AlwaysError(), AlwaysWarn()])
        result = engine.run(_make_ctx())
        assert len(result.violations) == 2
        assert result.has_errors
        assert result.has_warnings

    def test_engine_wraps_rule_exception_as_error(self) -> None:
        from app.validators.engine import BaseRule, RuleViolation, ValidationEngine

        class Exploder(BaseRule):
            def run(self, ctx: object) -> list[RuleViolation]:
                raise RuntimeError("unexpected crash")

        engine = ValidationEngine(rules=[Exploder()])
        result = engine.run(_make_ctx())
        assert len(result.violations) == 1
        assert result.violations[0].issue_code == "E999_RULE_INTERNAL_ERROR"
        assert result.has_errors

    def test_has_errors_false_with_only_warnings(self) -> None:
        from app.validators.engine import BaseRule, RuleViolation, ValidationEngine

        class WarnOnly(BaseRule):
            def run(self, ctx: object) -> list[RuleViolation]:
                return [RuleViolation("W_ONLY", "WARNING", "just a warning")]

        engine = ValidationEngine(rules=[WarnOnly()])
        result = engine.run(_make_ctx())
        assert not result.has_errors
        assert result.has_warnings
