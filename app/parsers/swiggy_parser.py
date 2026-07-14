"""
Swiggy/Scootsy PO parser — parses SpreadsheetML .xls attachments from procurement emails.

Source: Gmail label SWIGGY_PO; sender domains: scootsy.com, swiggy.in
File format: Microsoft SpreadsheetML (XML with .xls extension, NOT binary xls)
Attachment naming pattern: SOTY-{SELLER_CODE}-{PO_NUMBER}.xls

XLS flat-cell layout after XML parse:
  [0]  'Purchase Order'
  [1]  'Vendor Name :'
  [2]  'PO No :'   [3] po_number   [4] 'PO Date :'   [5] date_str   ...
  [10] 'Expected Delivery Date:'  [11] date_str  ...
  [14] 'Reference PO Code:'   [15] vendor_address
  [16] 'Billing Address'
  [17] 'Shipping Address'     [18] ship_addr
  [20] 'S.'  [21] 'Item Code' ... [42] 'Amt (INR)'   ← 23 column-header cells
  [43] '1'   [44] sku  [45] desc  ... [60] total      ← 18 cells per line item
  ...
  (footer row: large decimal, not a line number)
  ... 'Grand Total (INR)'  grand_total_value

Known quirks:
  - All sampled POs use IGST (interstate); CGST+SGST columns are always 0.
  - Item descriptions have embedded \\n from merged cells (replaced with space).
  - Prices use 5-digit precision; we round to 6dp for Decimal safety.
  - Subject pattern: '{CITY} {CODE}-{PO_NUMBER}-{VENDOR_NAME}'.
  - Vendor address is at flat-cell [15] (after 'Reference PO Code:' label row).
"""
from __future__ import annotations

import html
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import requests
import structlog

from app.models._enums import PoStatus, SourceChannel
from app.parsers.base import BaseParser, ParseResult
from app.schemas.canonical import EDI850, EDI850Line, EDIAddress

log = structlog.get_logger(__name__)

_SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"
_ZERO = Decimal("0")
_TWO_DP = Decimal("0.01")
_COLS_PER_LINE = 18  # values per line-item row in the flat cell list
_HEADER_COLS = 23   # 'S.' … 'Amt (INR)' (indices 20-42 in sample)


class SwiggyParser(BaseParser):
    """
    Parses Swiggy/Scootsy POs from SpreadsheetML .xls email attachments.
    Downloads the .xls from its Cloudinary URL stored in attachment_paths.
    """

    @property
    def partner_code(self) -> str:
        return "SWIGGY"

    def can_parse(self, raw_message: Any) -> bool:
        paths = raw_message.attachment_paths or []
        if not isinstance(paths, list):
            return False
        return any(
            att.get("filename", "").lower().endswith(".xls")
            for att in paths
            if isinstance(att, dict)
        )

    def parse(self, raw_message: Any) -> ParseResult:
        try:
            return self._do_parse(raw_message)
        except Exception as exc:
            log.exception("swiggy_parser.error", raw_id=str(getattr(raw_message, "id", "")))
            return ParseResult(
                success=False,
                errors=[f"Unexpected parse error: {exc}"],
                parser_name="SwiggyParser",
            )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _do_parse(self, raw_message: Any) -> ParseResult:
        paths = raw_message.attachment_paths or []
        xls_att = next(
            (att for att in paths if isinstance(att, dict) and att.get("filename", "").lower().endswith(".xls")),
            None,
        )
        if not xls_att:
            return ParseResult(
                success=False,
                errors=["No .xls attachment found — cannot parse Swiggy PO"],
                parser_name="SwiggyParser",
            )

        url: str = xls_att.get("url", "")
        if not url:
            return ParseResult(
                success=False,
                errors=["Attachment has no URL"],
                parser_name="SwiggyParser",
            )

        content = _download(url)
        if content is None:
            return ParseResult(
                success=False,
                errors=[f"Failed to download attachment from {url}"],
                parser_name="SwiggyParser",
            )

        cells = _flat_cells(content)
        if not cells:
            return ParseResult(
                success=False,
                errors=["Could not parse SpreadsheetML XML — empty or invalid file"],
                parser_name="SwiggyParser",
            )

        po_number = _extract_after(cells, "PO No :") or _po_from_filename(xls_att.get("filename", ""))
        if not po_number:
            return ParseResult(
                success=False,
                errors=["Cannot determine PO number from XLS or filename"],
                parser_name="SwiggyParser",
            )

        po_date = _parse_date_flexible(_extract_after(cells, "PO Date :"))
        delivery_date = _parse_date_flexible(_extract_after(cells, "Expected Delivery Date:"))
        ship_addr_raw = _extract_after(cells, "Shipping Address")

        lines, line_errors = _parse_line_items(cells)
        if not lines:
            return ParseResult(
                success=False,
                errors=["No line items found in XLS"] + line_errors,
                parser_name="SwiggyParser",
            )

        subtotal = _sum_decimal(li.taxable_amount for li in lines if li.taxable_amount)
        cgst_total = _sum_decimal(li.cgst_amount for li in lines if li.cgst_amount)
        sgst_total = _sum_decimal(li.sgst_amount for li in lines if li.sgst_amount)
        igst_total = _sum_decimal(li.igst_amount for li in lines if li.igst_amount)
        grand_total = subtotal + cgst_total + sgst_total + igst_total

        # Prefer footer grand total from the XLS if present
        xls_grand_total = _extract_footer_grand_total(cells)
        if xls_grand_total:
            grand_total = xls_grand_total

        doc = EDI850(
            id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            trading_partner_code="SWIGGY",
            source_channel=SourceChannel.EMAIL,
            raw_message_id=getattr(raw_message, "id", None),
            buyer_po_number=po_number,
            buyer_po_date=po_date,
            requested_delivery_date=delivery_date,
            ship_to=_parse_ship_to(ship_addr_raw),
            buyer_name="Scootsy Logistics Private Limited",
            subtotal_amount=subtotal or None,
            cgst_amount=cgst_total or None,
            sgst_amount=sgst_total or None,
            igst_amount=igst_total or None,
            grand_total=grand_total or None,
            line_items=lines,
            po_status=PoStatus.PARSED,
        )

        log.info(
            "swiggy_parser.success",
            po_number=po_number,
            line_count=len(lines),
            grand_total=str(grand_total),
        )
        return ParseResult(
            success=True,
            doc=doc,
            warnings=line_errors,
            parser_name="SwiggyParser",
        )


# ── XLS download & parse ──────────────────────────────────────────────────────

def _download(url: str) -> bytes | None:
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        log.warning("swiggy_parser.download_failed", url=url, error=str(exc))
        return None


def _flat_cells(content: bytes) -> list[str]:
    """
    Parse SpreadsheetML XML and return a flat ordered list of non-empty cell values.
    Handles the Scootsy XLS which uses XML SpreadsheetML format with .xls extension.
    """
    try:
        text = content.decode("utf-8", errors="replace")
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        log.warning("swiggy_parser.xml_parse_failed", error=str(exc))
        return []

    cells: list[str] = []
    for ws in root.iter(f"{{{_SS_NS}}}Worksheet"):
        for row in ws.iter(f"{{{_SS_NS}}}Row"):
            for cell in row.iter(f"{{{_SS_NS}}}Cell"):
                data = cell.find(f"{{{_SS_NS}}}Data")
                if data is not None and data.text:
                    val = html.unescape(data.text).replace("\n", " ").strip()
                    if val:
                        cells.append(val)
    return cells


# ── Cell extraction helpers ───────────────────────────────────────────────────

def _extract_after(cells: list[str], label: str) -> str | None:
    """Find a label in the flat cells list and return the immediately following cell."""
    label_norm = label.strip().lower()
    for i, c in enumerate(cells):
        if c.strip().lower() == label_norm and i + 1 < len(cells):
            return cells[i + 1]
    return None


def _po_from_filename(filename: str) -> str | None:
    """Extract PO number from 'SOTY-{SELLER_CODE}-{PO_NUMBER}.xls'."""
    stem = filename.rsplit(".", 1)[0]
    parts = stem.split("-")
    if len(parts) >= 3:
        return parts[-1]
    return None


def _parse_ship_to(raw: str | None) -> EDIAddress | None:
    if not raw:
        return None
    # Split first line as name, rest as address
    lines = [l.strip() for l in raw.split(",") if l.strip()]
    if not lines:
        return None
    name = "Scootsy Logistics Private Limited"
    return EDIAddress(
        name=name,
        line1=raw[:200] if raw else None,
    )


def _parse_date_flexible(value: str | None) -> date | None:
    if not value:
        return None
    # ISO datetime: '2026-07-25T00:00:00.000'
    if "T" in value:
        value = value.split("T")[0]
    # ISO date: '2026-07-25'
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        pass
    # English: 'Jul 14, 2026'
    import datetime
    for fmt in ("%b %d, %Y", "%d %b %Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    log.warning("swiggy_parser.unparsable_date", value=value)
    return None


# ── Line item parsing ─────────────────────────────────────────────────────────

def _parse_line_items(cells: list[str]) -> tuple[list[EDI850Line], list[str]]:
    """
    Find the column-header row ('S.' … 'Amt (INR)') then read 18-cell chunks.
    Stops when the first cell of a chunk is not a positive integer.
    """
    # Find 'S.' followed by 'Item Code' to locate the header row
    s_idx = -1
    for i, c in enumerate(cells):
        if c == "S." and i + 1 < len(cells) and cells[i + 1] == "Item Code":
            s_idx = i
            break
    if s_idx < 0:
        return [], ["Could not locate column headers in XLS"]

    items_start = s_idx + _HEADER_COLS  # skip 23 header cells
    lines: list[EDI850Line] = []
    errors: list[str] = []
    i = items_start

    while i + _COLS_PER_LINE <= len(cells):
        chunk = cells[i: i + _COLS_PER_LINE]
        # First cell is the line number (positive integer ≤ 999)
        try:
            line_no = int(float(chunk[0]))
            if line_no < 1 or line_no > 999:
                break
        except (ValueError, TypeError):
            break

        try:
            lines.append(_chunk_to_line(chunk, line_no))
        except Exception as exc:
            errors.append(f"Line {line_no} (sku={chunk[1] if len(chunk) > 1 else '?'}): {exc}")

        i += _COLS_PER_LINE

    return lines, errors


def _chunk_to_line(chunk: list[str], line_number: int) -> EDI850Line:
    """
    Map an 18-cell chunk to EDI850Line.

    Chunk layout (0-indexed):
      [0]  S.No          [1]  Item Code      [2]  Item Desc   [3] HSN
      [4]  Qty           [5]  MRP            [6]  Unit Cost   [7] Taxable Value
      [8]  CGST Rate     [9]  CGST Amt
      [10] SGST Rate     [11] SGST Amt
      [12] IGST Rate     [13] IGST Amt
      [14] CESS Rate     [15] CESS Amt
      [16] Add.CESS Amt  [17] Total
    """
    buyer_sku = chunk[1].strip()
    if not buyer_sku:
        raise ValueError("Empty item code")

    # Clean description: Scootsy wraps long names with embedded newlines already replaced
    desc_raw = chunk[2] if len(chunk) > 2 else ""
    description = re.sub(r"\s+", " ", desc_raw).strip() or None

    # Strip embedded product attributes noise: 'Colour:  Size: size Brand:CAMPAIGN'
    if description and "Colour:" in description:
        description = re.split(r"Colour:", description)[0].strip(" \t\n\r\x0b\x0c-")

    hsn = (chunk[3] if len(chunk) > 3 else "").strip() or None
    qty = _to_decimal(chunk[4] if len(chunk) > 4 else "0")
    if qty <= _ZERO:
        raise ValueError(f"Qty must be > 0, got {qty}")

    unit_price = _to_decimal(chunk[6] if len(chunk) > 6 else "0")
    taxable = _to_decimal(chunk[7] if len(chunk) > 7 else "0")

    cgst_rate = _to_decimal(chunk[8] if len(chunk) > 8 else "0")
    cgst_amt = _to_decimal(chunk[9] if len(chunk) > 9 else "0")
    sgst_rate = _to_decimal(chunk[10] if len(chunk) > 10 else "0")
    sgst_amt = _to_decimal(chunk[11] if len(chunk) > 11 else "0")
    igst_rate = _to_decimal(chunk[12] if len(chunk) > 12 else "0")
    igst_amt = _to_decimal(chunk[13] if len(chunk) > 13 else "0")
    cess_rate = _to_decimal(chunk[14] if len(chunk) > 14 else "0")
    cess_amt = _to_decimal(chunk[15] if len(chunk) > 15 else "0")
    line_total = _to_decimal(chunk[17] if len(chunk) > 17 else "0")

    return EDI850Line(
        line_number=line_number,
        buyer_sku=buyer_sku,
        buyer_sku_description=description,
        hsn_code=hsn,
        ordered_qty=qty,
        buyer_uom="EA",
        unit_price=unit_price if unit_price else None,
        taxable_amount=taxable if taxable else None,
        cgst_rate=cgst_rate if cgst_rate else None,
        cgst_amount=cgst_amt.quantize(_TWO_DP, ROUND_HALF_UP) if cgst_amt else None,
        sgst_rate=sgst_rate if sgst_rate else None,
        sgst_amount=sgst_amt.quantize(_TWO_DP, ROUND_HALF_UP) if sgst_amt else None,
        igst_rate=igst_rate if igst_rate else None,
        igst_amount=igst_amt.quantize(_TWO_DP, ROUND_HALF_UP) if igst_amt else None,
        cess_rate=cess_rate if cess_rate else None,
        cess_amount=cess_amt.quantize(_TWO_DP, ROUND_HALF_UP) if cess_amt else None,
        line_total=line_total.quantize(_TWO_DP, ROUND_HALF_UP) if line_total else None,
    )


def _extract_footer_grand_total(cells: list[str]) -> Decimal | None:
    """Find 'Grand Total (INR)' label and return the numeric value after it."""
    for i, c in enumerate(cells):
        if c.strip().lower() == "grand total (inr)" and i + 1 < len(cells):
            return _to_decimal(cells[i + 1]) or None
    return None


# ── Numeric helpers ───────────────────────────────────────────────────────────

def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return _ZERO
    try:
        return Decimal(str(value).strip())
    except Exception:
        return _ZERO


def _sum_decimal(values: Any) -> Decimal:
    return sum((v for v in values if v is not None), _ZERO)
