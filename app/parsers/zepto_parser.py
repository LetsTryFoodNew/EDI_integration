"""
Zepto PO parser — parses one Zepto PO event object into a canonical EDI850.

Source format: one purchaseOrder object from Zepto Silk Route API
  GET /api/v1/external/po/events → data.purchaseOrders[]
  The Phase 4 adapter stores each PO individually in raw_messages.payload.

Payload structure (inferred from _archive/backend_old/app/services/zepto.py
and the ASN payload contract):
  {
    "purchaseOrderNumber": "P365999",
    "purchaseOrderDate":   "2024-01-15T00:00:00Z",
    "vendorCode":          "V001",
    "eventId":             "evt_abc123",
    "totalAmount":         50000.00,
    "buyerDetails": {
      "gstin":   "27XXXXXXXXX",
      "name":    "Zepto Dark Store",
      "address": "Flat 101, ..."
    },
    "lineItems": [
      {
        "lineNumber": 1,
        "productIdentifier": {
          "buyerProductIdentifier": {
            "skuCode":     "Z-SKU-001",
            "productName": "Peri Peri Makhana 80g"
          }
        },
        "quantity": {
          "orderedQuantity": { "amount": 50, "unit": "PC" }
        },
        "pricing": {
          "unitPrice": 90.00
        },
        "taxDetails": {
          "hsnCode":  "20089900",
          "cgstRate": 6.0,
          "sgstRate": 6.0,
          "igstRate": 0.0
        }
      }
    ]
  }

Known quirks (from Zepto API contract v12, cross-referenced with archive):
  - All timestamps are UTC ISO-8601
  - Quantities must be in pieces (PC); case-size conversion is our responsibility
  - eventId should be used as idempotency key (stored in raw_message.external_id)
  - PO PDF URL is in expiringUrlForPoPDF — valid for ~7 days
  - Rate limit: 60 RPM per clientId
"""
from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import structlog

from app.models._enums import PoStatus, SourceChannel
from app.parsers.base import BaseParser, ParseResult
from app.parsers.blinkit_parser import _parse_date, _sum_decimal, _to_decimal
from app.schemas.canonical import EDI850, EDI850Line, EDIAddress

log = structlog.get_logger(__name__)

_ZERO = Decimal("0")
_TWO_DP = Decimal("0.01")


class ZeptoParser(BaseParser):
    """Parses one Zepto purchaseOrder JSON object (raw_message.payload) into EDI850."""

    @property
    def partner_code(self) -> str:
        return "ZEPTO"

    def can_parse(self, raw_message: Any) -> bool:
        p = raw_message.payload or {}
        return bool(p.get("purchaseOrderNumber") and "lineItems" in p)

    def parse(self, raw_message: Any) -> ParseResult:
        try:
            return self._do_parse(raw_message.payload or {}, raw_message)
        except Exception as exc:
            log.exception("zepto_parser.error", raw_id=str(getattr(raw_message, "id", "")))
            return ParseResult(
                success=False,
                errors=[f"Unexpected parse error: {exc}"],
                parser_name="ZeptoParser",
            )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _do_parse(self, payload: dict[str, Any], raw_message: Any) -> ParseResult:
        po_number: str | None = payload.get("purchaseOrderNumber")
        if not po_number:
            return ParseResult(
                success=False, errors=["Missing purchaseOrderNumber"], parser_name="ZeptoParser"
            )

        buyer: dict[str, Any] = payload.get("buyerDetails") or {}

        ship_to = EDIAddress(
            name=buyer.get("name"),
            line1=buyer.get("address"),
            gstin=buyer.get("gstin"),
        )

        lines, line_errors = self._parse_lines(payload.get("lineItems") or [])
        if not lines:
            return ParseResult(
                success=False,
                errors=["No line items could be parsed"] + line_errors,
                parser_name="ZeptoParser",
            )

        cgst_total = _sum_decimal(li.cgst_amount for li in lines)
        sgst_total = _sum_decimal(li.sgst_amount for li in lines)
        igst_total = _sum_decimal(li.igst_amount for li in lines)
        subtotal = _sum_decimal(li.taxable_amount for li in lines)

        header_total = _to_decimal(payload.get("totalAmount"))
        grand_total = header_total if header_total else (subtotal + cgst_total + sgst_total + igst_total)

        doc = EDI850(
            id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            trading_partner_code="ZEPTO",
            source_channel=SourceChannel.API,
            raw_message_id=getattr(raw_message, "id", None),
            buyer_po_number=po_number,
            buyer_po_date=_parse_date(payload.get("purchaseOrderDate")),
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
            parser_name="ZeptoParser",
        )

    def _parse_lines(
        self, items: list[dict[str, Any]]
    ) -> tuple[list[EDI850Line], list[str]]:
        lines: list[EDI850Line] = []
        errors: list[str] = []
        for item in items:
            line_no = item.get("lineNumber", len(lines) + 1)
            try:
                lines.append(_zepto_item_to_line(item, line_no))
            except Exception as exc:
                sku = (
                    item.get("productIdentifier", {})
                    .get("buyerProductIdentifier", {})
                    .get("skuCode", "?")
                )
                errors.append(f"Line {line_no} (sku={sku}): {exc}")
        return lines, errors


# ── Helpers ───────────────────────────────────────────────────────────────────

def _zepto_item_to_line(item: dict[str, Any], line_number: int) -> EDI850Line:
    buyer_id = item.get("productIdentifier") or {}
    buyer_sku_info = (buyer_id.get("buyerProductIdentifier") or {})
    buyer_sku = buyer_sku_info.get("skuCode") or ""
    if not buyer_sku:
        raise ValueError("lineItem has no buyerProductIdentifier.skuCode")

    qty_block = (item.get("quantity") or {}).get("orderedQuantity") or {}
    qty = _to_decimal(qty_block.get("amount"))
    if qty <= _ZERO:
        raise ValueError(f"orderedQuantity.amount must be > 0, got {qty}")

    pricing = item.get("pricing") or {}
    unit_price = _to_decimal(pricing.get("unitPrice"))
    taxable = (qty * unit_price).quantize(_TWO_DP, ROUND_HALF_UP)

    tax = item.get("taxDetails") or {}
    cgst_rate = _to_decimal(tax.get("cgstRate"))
    sgst_rate = _to_decimal(tax.get("sgstRate"))
    igst_rate = _to_decimal(tax.get("igstRate"))

    cgst_amt = (taxable * cgst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) if cgst_rate else None
    sgst_amt = (taxable * sgst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) if sgst_rate else None
    igst_amt = (taxable * igst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) if igst_rate else None

    line_total = taxable + (cgst_amt or _ZERO) + (sgst_amt or _ZERO) + (igst_amt or _ZERO)

    return EDI850Line(
        line_number=line_number,
        buyer_sku=buyer_sku,
        buyer_sku_description=buyer_sku_info.get("productName"),
        hsn_code=tax.get("hsnCode"),
        ordered_qty=qty,
        buyer_uom=qty_block.get("unit", "PC"),
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
