"""
Zepto PO parser — parses one Zepto PO event object into a canonical EDI850.

Source format: one purchaseOrder object from Zepto Silk Route API
  GET /api/v1/external/po/events → data.purchaseOrders[]
  The Phase 4 adapter stores each PO individually in raw_messages.payload.

Actual payload structure (verified from live QA API 2026-07-17):
  {
    "code":         "P368265",        ← PO number
    "orderDate":    "2026-07-16T10:15:23Z",
    "deliveryDate": "2026-08-09T18:30:00Z",
    "eventId":      "7e1eba2b-...",
    "eventType":    "CreatePO",
    "vendorCode":   "KKT-45129",
    "vendorName":   "...",
    "toStoreName":  "TEST-MUM-FARUKHNAGR",
    "toStoreCode":  "MI042M",
    "status":       "RELEASED",
    "isInterstate": true,
    "address": {
      "storeShippingAddress": "...",
      "storeBillingAddress":  "...",
      "vendorAddress":        "..."
    },
    "financialDetails": {
      "entityGSTIN": "27AAFCD5862R013",   ← buyer GSTIN
      "vendorGSTIN": "27AAFCD5862R013",   ← our GSTIN
      "entityPAN":   "...",
      "vendorPAN":   "..."
    },
    "poLineItems": [
      {
        "skuCode":       "007ec75c-...",   ← buyer's UUID for the item
        "materialCode":  "2223",           ← buyer's material code (use as buyer_sku)
        "ean":           "B1234",
        "productName":   "Fortune Soyabean Oil - 120g",
        "brandName":     "...",
        "hsnCode":       "96190030",
        "quantity":      5,
        "packSize":      1,
        "costPrice":     91,               ← per-unit cost (tax-exclusive)
        "mrp":           130,
        "totalAmount":   455,              ← quantity * costPrice (incl tax)
        "cgstValue":     0,
        "sgstValue":     0,
        "igstValue":     9.75,
        "cgstPercentage": 0,
        "sgstPercentage": 0,
        "igstPercentage": 12,
        "cessValue":     0,
        "margin":        30,
        "taxExclusiveCost": 81.25
      }
    ],
    "expiringUrlForPoPDF": "https://..."  ← PDF URL, valid ~7 days
  }

Known quirks:
  - All timestamps are UTC ISO-8601
  - Line items key is `poLineItems` (not `lineItems`)
  - PO number is in `code` (not `purchaseOrderNumber`)
  - eventId is stored as raw_message.external_id (idempotency key)
  - `skuCode` (UUID) is the mapping key — SKU_Mapping.buyer_sku is built on it
  - `materialCode` is a secondary buyer-side ref, fallback only
  - `costPrice` is the per-unit cost Zepto pays (matches taxExclusiveCost * qty roughly)
  - taxExclusiveCost is the per-unit pre-tax price we should use for unit_price
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


# Zepto's own PO-lifecycle `status` field — values confirmed from live traffic
# (RELEASED, EXPIRED so far). Any status in this set means the PO is dead on
# Zepto's side (expired, cancelled, etc.) and should never reach SKU mapping
# or SAP push. Extend this set if Zepto is observed sending other terminal
# statuses (e.g. a literal "CANCELLED").
_TERMINAL_ZEPTO_STATUSES: frozenset[str] = frozenset({"EXPIRED"})


class ZeptoParser(BaseParser):
    """Parses one Zepto purchaseOrder JSON object (raw_message.payload) into EDI850."""

    @property
    def partner_code(self) -> str:
        return "ZEPTO"

    def can_parse(self, raw_message: Any) -> bool:
        # Zepto payloads use "code" as the PO number and "poLineItems" for lines
        p = raw_message.payload or {}
        return bool(p.get("code") and "poLineItems" in p)

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
        # Actual Zepto field: "code" (not "purchaseOrderNumber")
        po_number: str | None = payload.get("code")
        if not po_number:
            return ParseResult(
                success=False, errors=["Missing 'code' (PO number)"], parser_name="ZeptoParser"
            )

        fin: dict[str, Any] = payload.get("financialDetails") or {}
        addr: dict[str, Any] = payload.get("address") or {}
        is_terminal = payload.get("status") in _TERMINAL_ZEPTO_STATUSES

        ship_to = EDIAddress(
            name=payload.get("toStoreName"),
            line1=addr.get("storeShippingAddress"),
            gstin=fin.get("entityGSTIN"),
        )

        # Actual Zepto field: "poLineItems" (not "lineItems")
        lines, line_errors = self._parse_lines(payload.get("poLineItems") or [])
        if not lines and not is_terminal:
            return ParseResult(
                success=False,
                errors=["No line items could be parsed"] + line_errors,
                parser_name="ZeptoParser",
            )

        cgst_total = _sum_decimal(li.cgst_amount for li in lines)
        sgst_total = _sum_decimal(li.sgst_amount for li in lines)
        igst_total = _sum_decimal(li.igst_amount for li in lines)
        subtotal = _sum_decimal(li.taxable_amount for li in lines)

        grand_total = subtotal + cgst_total + sgst_total + igst_total

        doc = EDI850(
            id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            trading_partner_code="ZEPTO",
            source_channel=SourceChannel.API,
            raw_message_id=getattr(raw_message, "id", None),
            buyer_po_number=po_number,
            buyer_po_date=_parse_date(payload.get("orderDate")),
            ship_to=ship_to,
            buyer_gstin=fin.get("entityGSTIN"),
            buyer_name=payload.get("toStoreName") or payload.get("entityName"),
            subtotal_amount=subtotal,
            cgst_amount=cgst_total or None,
            sgst_amount=sgst_total or None,
            igst_amount=igst_total or None,
            grand_total=grand_total,
            line_items=lines,
            po_status=PoStatus.CANCELLED if is_terminal else PoStatus.PARSED,
        )

        warnings = list(line_errors)
        if is_terminal:
            warnings.append(
                f"Zepto reports this PO as status={payload.get('status')!r} — "
                "parsed for record-keeping only, excluded from validation/SAP push."
            )

        return ParseResult(
            success=True,
            doc=doc,
            warnings=warnings,
            parser_name="ZeptoParser",
        )

    def _parse_lines(
        self, items: list[dict[str, Any]]
    ) -> tuple[list[EDI850Line], list[str]]:
        lines: list[EDI850Line] = []
        errors: list[str] = []
        for idx, item in enumerate(items):
            line_no = idx + 1
            try:
                lines.append(_zepto_item_to_line(item, line_no))
            except Exception as exc:
                sku = item.get("skuCode") or item.get("materialCode") or "?"
                errors.append(f"Line {line_no} (sku={sku}): {exc}")
        return lines, errors


# ── Helpers ───────────────────────────────────────────────────────────────────

def _zepto_item_to_line(item: dict[str, Any], line_number: int) -> EDI850Line:
    # buyer_sku: prefer skuCode — the UUID Zepto uses in its own catalogue and the key
    # the ops mapping sheet (SKU_Mapping.buyer_sku) is built on. materialCode ("2223")
    # is a secondary ref that the sheet does NOT key on; preferring it made every line
    # miss its mapping despite the mappings being loaded.
    buyer_sku = item.get("skuCode") or item.get("materialCode") or ""
    if not buyer_sku:
        raise ValueError("poLineItem has no materialCode or skuCode")

    qty = _to_decimal(item.get("quantity"))
    if qty <= _ZERO:
        raise ValueError(f"quantity must be > 0, got {qty}")

    # taxExclusiveCost is the per-unit pre-tax cost price Zepto pays us
    unit_price = _to_decimal(item.get("taxExclusiveCost") or item.get("costPrice"))
    taxable = (qty * unit_price).quantize(_TWO_DP, ROUND_HALF_UP)

    cgst_rate = _to_decimal(item.get("cgstPercentage"))
    sgst_rate = _to_decimal(item.get("sgstPercentage"))
    igst_rate = _to_decimal(item.get("igstPercentage"))

    # Zepto sends pre-computed tax values — use them directly for accuracy
    cgst_amt = _to_decimal(item.get("cgstValue")) or None
    sgst_amt = _to_decimal(item.get("sgstValue")) or None
    igst_amt = _to_decimal(item.get("igstValue")) or None

    # Recalculate if pre-computed values are zero but rate is set (data quality guard)
    if cgst_rate and not cgst_amt:
        cgst_amt = (taxable * cgst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) or None
    if sgst_rate and not sgst_amt:
        sgst_amt = (taxable * sgst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) or None
    if igst_rate and not igst_amt:
        igst_amt = (taxable * igst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) or None

    line_total = taxable + (cgst_amt or _ZERO) + (sgst_amt or _ZERO) + (igst_amt or _ZERO)

    return EDI850Line(
        line_number=line_number,
        buyer_sku=buyer_sku,
        buyer_sku_description=item.get("productName"),
        hsn_code=item.get("hsnCode"),
        ordered_qty=qty,
        buyer_uom="PC",
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
