"""
Blinkit PO parser — parses the webhook JSON payload into a canonical EDI850.

Source format: JSON posted to our webhook endpoint, stored in raw_messages.payload.

Webhook body structure (re-implemented from _archive/backend_old/app/routes.py):
  {
    "po_number": "BL-XXXXX",
    "type": "PO_CREATION",
    "details": {
      "delivery_date": "YYYY-MM-DD",
      "expiry_date":   "YYYY-MM-DD",
      "issue_date":    "YYYY-MM-DD",
      "total_qty":     100,
      "total_amount":  50000.00,
      "outlet_id":     "12345",
      "buyer_details": {
        "name": "Blinkit WH Name",
        "gstin": "27XXXXXXXXX",
        "destination_address": {
          "line1": "...", "line2": "...", "city": "...",
          "state": "...", "postal_code": "..."
        }
      },
      "item_data": [
        {
          "item_id": "100001",  ← Blinkit's internal item ID
          "sku_code": "SKU123", ← buyer SKU we map against
          "upc": "8901234567890",
          "name": "Product Name",
          "units_ordered": 50,
          "basic_price": 100.00,
          "mrp": 120.00,
          "hsn_code": "20089900",
          "tax_details": {
            "igst_percentage": null,
            "cgst_percentage": 6.0,
            "sgst_percentage": 6.0
          }
        }
      ]
    }
  }

Known quirks (from _archive/backend_old/app/routes.py):
  - igst_percentage may be null (intrastate) or 0.0; cgst+sgst are then used
  - total_amount in header may differ from sum of lines (Blinkit rounds centrally)
  - PO_CANCELLATION events arrive in the same structure with type="PO_CANCELLATION"
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import structlog

from app.models._enums import PoStatus, SourceChannel
from app.parsers.base import BaseParser, ParseResult
from app.schemas.canonical import EDI850, EDI850Line, EDIAddress

log = structlog.get_logger(__name__)

_ZERO = Decimal("0")
_TWO_DP = Decimal("0.01")


class BlinkitParser(BaseParser):
    """Parses Blinkit webhook JSON (stored in raw_message.payload) into EDI850."""

    @property
    def partner_code(self) -> str:
        return "BLINKIT"

    def can_parse(self, raw_message: Any) -> bool:
        p = raw_message.payload or {}
        return bool(p.get("po_number") and "details" in p)

    def parse(self, raw_message: Any) -> ParseResult:
        try:
            return self._do_parse(raw_message.payload or {}, raw_message)
        except Exception as exc:
            log.exception("blinkit_parser.error", raw_id=str(getattr(raw_message, "id", "")))
            return ParseResult(
                success=False,
                errors=[f"Unexpected parse error: {exc}"],
                parser_name="BlinkitParser",
            )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _do_parse(self, payload: dict[str, Any], raw_message: Any) -> ParseResult:
        po_number: str | None = payload.get("po_number")
        if not po_number:
            return ParseResult(success=False, errors=["Missing po_number"], parser_name="BlinkitParser")

        details: dict[str, Any] = payload.get("details") or {}
        buyer: dict[str, Any] = details.get("buyer_details") or {}
        dest: dict[str, Any] = buyer.get("destination_address") or {}

        ship_to = EDIAddress(
            name=buyer.get("name"),
            line1=dest.get("line1"),
            line2=dest.get("line2"),
            city=dest.get("city"),
            state=dest.get("state"),
            pincode=dest.get("postal_code"),
            gstin=buyer.get("gstin"),
            warehouse_code=str(details.get("outlet_id", "")) or None,
        )

        lines, line_errors = self._parse_lines(details.get("item_data") or [])
        if not lines:
            return ParseResult(
                success=False,
                errors=["No line items could be parsed"] + line_errors,
                parser_name="BlinkitParser",
            )

        cgst_total = _sum_decimal(li.cgst_amount for li in lines)
        sgst_total = _sum_decimal(li.sgst_amount for li in lines)
        igst_total = _sum_decimal(li.igst_amount for li in lines)
        subtotal = _sum_decimal(li.taxable_amount for li in lines)

        header_total = _to_decimal(details.get("total_amount"))
        # Use header total if provided; otherwise compute from lines
        grand_total = header_total if header_total else (subtotal + cgst_total + sgst_total + igst_total)

        doc = EDI850(
            id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            trading_partner_code="BLINKIT",
            source_channel=SourceChannel.WEBHOOK,
            raw_message_id=getattr(raw_message, "id", None),
            buyer_po_number=po_number,
            buyer_po_date=_parse_date(details.get("issue_date")),
            requested_delivery_date=_parse_date(details.get("delivery_date")),
            ship_to=ship_to,
            buyer_gstin=buyer.get("gstin"),
            buyer_name=buyer.get("name"),
            subtotal_amount=subtotal,
            cgst_amount=cgst_total or None,
            sgst_amount=sgst_total or None,
            igst_amount=igst_total or None,
            grand_total=grand_total,
            line_items=lines,
            po_status=PoStatus.PARSED,
        )

        return ParseResult(
            success=True,
            doc=doc,
            warnings=line_errors,
            parser_name="BlinkitParser",
        )

    def _parse_lines(
        self, item_data: list[dict[str, Any]]
    ) -> tuple[list[EDI850Line], list[str]]:
        lines: list[EDI850Line] = []
        errors: list[str] = []
        for idx, item in enumerate(item_data, start=1):
            try:
                lines.append(_blinkit_item_to_line(item, idx))
            except Exception as exc:
                errors.append(f"Line {idx} (sku={item.get('sku_code', '?')}): {exc}")
        return lines, errors


# ── Helpers ───────────────────────────────────────────────────────────────────

def _blinkit_item_to_line(item: dict[str, Any], line_number: int) -> EDI850Line:
    buyer_sku = item.get("sku_code") or item.get("item_id") or ""
    if not buyer_sku:
        raise ValueError("item has no sku_code or item_id")

    qty = _to_decimal(item.get("units_ordered"))
    if qty <= _ZERO:
        raise ValueError(f"units_ordered must be > 0, got {qty}")

    unit_price = _to_decimal(item.get("basic_price"))
    taxable = (qty * unit_price).quantize(_TWO_DP, ROUND_HALF_UP)

    tax = item.get("tax_details") or {}
    cgst_rate = _to_decimal(tax.get("cgst_percentage"))
    sgst_rate = _to_decimal(tax.get("sgst_percentage"))
    igst_raw = tax.get("igst_percentage")
    igst_rate = _to_decimal(igst_raw) if igst_raw is not None else _ZERO

    cgst_amt = (taxable * cgst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) if cgst_rate else None
    sgst_amt = (taxable * sgst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) if sgst_rate else None
    igst_amt = (taxable * igst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) if igst_rate else None

    line_total = taxable + (cgst_amt or _ZERO) + (sgst_amt or _ZERO) + (igst_amt or _ZERO)

    return EDI850Line(
        line_number=line_number,
        buyer_sku=buyer_sku,
        buyer_sku_description=item.get("name"),
        hsn_code=item.get("hsn_code"),
        ordered_qty=qty,
        buyer_uom="EA",
        unit_price=unit_price,
        taxable_amount=taxable,
        cgst_rate=cgst_rate or None,
        cgst_amount=cgst_amt,
        sgst_rate=sgst_rate or None,
        sgst_amount=sgst_amt,
        igst_rate=igst_rate or None,
        igst_amount=igst_amt,
        line_total=line_total.quantize(_TWO_DP, ROUND_HALF_UP),
    )


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return _ZERO
    try:
        return Decimal(str(value))
    except Exception:
        return _ZERO


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _sum_decimal(values: Any) -> Decimal:
    return sum((v for v in values if v is not None), _ZERO)
