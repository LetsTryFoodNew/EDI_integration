"""
Swiggy parser — both attachment generations.

Swiggy switched from SpreadsheetML .xls to genuine .xlsx around 2026-08-06 with no
notice, and 183 POs failed silently with "No .xls attachment found" because the
detector used endswith(".xls"), which does not match ".xlsx". These tests pin both
paths so the next format change is caught by CI rather than by a missing PO.

The .xlsx fixture is built programmatically rather than committed as a binary: the real
files carry live customer addresses and GSTINs, and a synthesised grid makes the layout
being asserted visible in the test itself.
"""
from __future__ import annotations

import io
from decimal import Decimal

import pytest

from app.parsers.swiggy_parser import (
    _extract_ooxml,
    _find_spreadsheet,
    _is_ooxml,
)


def _build_xlsx(rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _blank(n: int) -> list:
    return [None] * n


def _sample_rows() -> list[list]:
    """Mirrors the real layout: two-row header, merged-cell gaps, labelled footer."""
    header = ["S.", "Item Code", "Item Desc", "HSN Code", None, "Qty", "MRP",
              "Unit Base Cost (INR)", None, "Taxable Value (INR)", "CGST", None,
              None, "SGST/UGST", None, None, "IGST", None, None, "CESS", None,
              "Additional CESS", "Total (INR)"]
    sub = ["No", None, None, None, None, None, None, None, None, None, "Rate",
           None, "Amt (INR)", "Rate", None, "Amt (INR)", "Rate", "Amt (INR)",
           None, "Rate", "Amt (INR)"]
    return [
        ["Purchase Order"],
        [" Vendor Name : "] + _blank(10) + ["PO No :"] + _blank(6) + ["JCFPO04877"],
        _blank(11) + ["PO Date :"] + _blank(6) + ["Aug 6, 2026"],
        _blank(11) + ["Expected Delivery Date:"] + _blank(6) + ["Aug 21, 2026"],
        ["Billing Address"] + _blank(10) + ["Shipping Address"],
        ["SCOOTSY BILLING\nMumbai"] + _blank(10) + ["SCOOTSY LOGISTICS PRIVATE LIMITED\nCoimbatore, Tamil Nadu\nGSTIN: 33AAECS1234K1Z9"],
        header,
        sub,
        # qty 36 @ 114.28571 → taxable 4114.29, IGST 5% = 205.71, total 4320.00
        [1, "17115", "Let's Try Pudina\nMakhana 57g", "21069099", None, 36, 160.00,
         114.28571, None, 4114.29, 0.00, None, 0.00, 0.00, None, 0.00, 5.00,
         205.71, None, 0.00, 0.00, 0.00, 4320.00],
        [2, "17121", "Let's Try Garlic Bhujia", "21069099", None, 36, 120.00,
         85.71429, None, 3085.71, 0.00, None, 0.00, 0.00, None, 0.00, 5.00,
         154.29, None, 0.00, 0.00, 0.00, 3240.00],
        # Unlabelled totals row — columns are offset from the item rows.
        _blank(8) + [7200.00, 0.00, None, None, 0.00, None, None, 360.00],
        _blank(12) + ["Grand Total (INR)"] + _blank(6) + [7560.00],
    ]


class TestAttachmentSelection:
    def test_finds_xlsx_alongside_pdf(self) -> None:
        """The regression: .xlsx must be found even though it does not end in '.xls'."""
        att = _find_spreadsheet([
            {"filename": "PO_CREATE_OTB.pdf", "url": "u1"},
            {"filename": "PO_CREATE_OTB.xlsx", "url": "u2"},
        ])
        assert att is not None
        assert att["filename"].endswith(".xlsx")

    def test_finds_legacy_xls(self) -> None:
        att = _find_spreadsheet([
            {"filename": "SOTY-1N83380098-CI3PO71549.pdf", "url": "u1"},
            {"filename": "SOTY-1N83380098-CI3PO71549.xls", "url": "u2"},
        ])
        assert att is not None
        assert att["filename"].endswith(".xls")

    def test_prefers_xlsx_when_both_present(self) -> None:
        """A transition email carrying both should use the current format."""
        att = _find_spreadsheet([
            {"filename": "po.xls", "url": "legacy"},
            {"filename": "po.xlsx", "url": "current"},
        ])
        assert att["url"] == "current"

    def test_pdf_only_returns_none(self) -> None:
        assert _find_spreadsheet([{"filename": "po.pdf", "url": "u"}]) is None

    def test_attachment_without_url_is_skipped(self) -> None:
        assert _find_spreadsheet([{"filename": "po.xlsx"}]) is None


class TestFormatDetection:
    def test_xlsx_detected_by_zip_magic(self) -> None:
        assert _is_ooxml(_build_xlsx([["x"]])) is True

    def test_spreadsheetml_is_not_ooxml(self) -> None:
        assert _is_ooxml(b'<?xml version="1.0"?><Workbook/>') is False


class TestOoxmlExtraction:
    @pytest.fixture
    def sheet(self):
        s, errors = _extract_ooxml(_build_xlsx(_sample_rows()))
        assert s is not None, errors
        assert errors == []
        return s

    def test_header_fields(self, sheet) -> None:
        assert sheet.po_number == "JCFPO04877"
        assert str(sheet.po_date) == "2026-08-06"
        assert str(sheet.delivery_date) == "2026-08-21"

    def test_shipping_address_not_billing(self, sheet) -> None:
        """Both blocks sit side by side; picking the wrong one mis-taxes the order."""
        assert "Coimbatore" in sheet.ship_to_raw
        assert "BILLING" not in sheet.ship_to_raw

    def test_line_items(self, sheet) -> None:
        assert len(sheet.lines) == 2
        first = sheet.lines[0]
        assert first.buyer_sku == "17115"
        assert first.hsn_code == "21069099"
        assert first.ordered_qty == Decimal("36")
        assert first.taxable_amount == Decimal("4114.29")
        assert first.igst_amount == Decimal("205.71")
        assert first.igst_rate == Decimal("5.00")
        assert first.line_total == Decimal("4320.00")

    def test_embedded_newlines_flattened(self, sheet) -> None:
        assert "\n" not in (sheet.lines[0].buyer_sku_description or "")

    def test_totals_row_is_not_read_as_a_line(self, sheet) -> None:
        """The totals row has no serial number and must not become a line item."""
        assert all(li.buyer_sku for li in sheet.lines)
        assert len(sheet.lines) == 2

    def test_grand_total_from_labelled_row(self, sheet) -> None:
        """
        Taken from the 'Grand Total (INR)' label, not the unlabelled totals row above
        it — that row's columns are offset and reading it positionally picks 360.00.
        """
        assert sheet.grand_total == Decimal("7560.00")

    def test_lines_reconcile_with_grand_total(self, sheet) -> None:
        total = sum(
            (li.taxable_amount or 0) + (li.igst_amount or 0) for li in sheet.lines
        )
        assert abs(Decimal(total) - sheet.grand_total) <= Decimal("1.00")


class TestOoxmlResilience:
    def test_missing_header_row_reports_clearly(self) -> None:
        sheet, errors = _extract_ooxml(_build_xlsx([["Purchase Order"], ["nothing"]]))
        assert sheet is None
        assert any("Item Code" in e for e in errors)

    def test_corrupt_file_does_not_raise(self) -> None:
        sheet, errors = _extract_ooxml(b"PK\x03\x04 not really a workbook")
        assert sheet is None
        assert errors

    def test_bad_row_does_not_lose_the_whole_po(self) -> None:
        """One unparseable quantity must cost that line, not the other 38."""
        rows = _sample_rows()
        rows[8][5] = 0          # qty 0 on line 1
        sheet, errors = _extract_ooxml(_build_xlsx(rows))
        assert sheet is not None
        assert len(sheet.lines) == 1
        assert sheet.lines[0].buyer_sku == "17121"
        assert any("quantity" in e for e in errors)
