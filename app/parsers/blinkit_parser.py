"""
Blinkit PO parser — webhook JSON to canonical EDI850.

Authoritative source: Blinkit's "POVMS - Purchase Order Creation API Contracts"
(2026-02-10), archived at
_archive/backend_old/assets/POVMS-Purchase Order Creation API Contracts-100226-093712_blinkit.txt
— that file is a PDF despite the .txt extension. Field numbering below (3.6.x) refers to
its tables, so a future contract revision can be diffed against this parser directly.

Payload shape (contract section 3):
  {
    "type":      "PO_CREATION",          # BlinkitEventType
    "po_number": "2264110001440",
    "tenant":    "HYPERPURE",            # BlinkitTenant — BLINKIT | HYPERPURE
    "details": {
      "po_number":     "...",            # 3.1  must equal the top-level po_number
      "issue_date":    "ISO-8601 UTC",
      "expiry_date":   "ISO-8601 UTC",   # 3.2
      "delivery_date": "ISO-8601 UTC",   # 3.3
      "outlet_id":     12543,
      "vehicle_details":  {"license_number": "DL-311/431"},          # 3.5
      "buyer_details":    {...},         # 3.4  name, gstin, destination_address,
                                         #      registered_address, contact_details[]
      "supplier_details": {...},         # 3.5  id, name, gstin, pan, addresses, contacts
      "item_data": [                     # 3.6
        {
          "item_id":       10016623,     # 3.6.1  integer, always present
          "sku_code":      "",           # 3.6.2  OPTIONAL — often empty, see below
          "line_number":   0,            # 3.6.3  integer, ZERO-BASED in the contract
          "units_ordered": 240,          # 3.6.4
          "landing_price": 32.56,        # 3.6.5  incl. logistics + taxes
          "basic_price":   31.01,        # 3.6.6  cost price, pre-tax  ← we price on this
          "tax_details":   {...},        # 3.6.7  cgst/sgst/igst/cess/additional_cess
          "name":          "...",        # 3.6.8
          "mrp":           42,           # 3.6.9
          "upc":           "8901774002349",  # 3.6.10
          "uom":           {"unit": "ml", "value": 12},   # 3.6.11
          "crates_config": {"crates_ordered": 14, "crate_size": 10}   # 3.6.12
        }
      ],
      "total_sku":         1,            # 3.7
      "total_qty":         240,          # 3.8
      "total_amount":      42,           # 3.9
      "custom_attributes": [...]         # 3.10
    }
  }

Contract details that actually change behaviour:

  - `sku_code` (3.6.2) is OPTIONAL and the contract's own example ships it EMPTY. It is
    the field we map on, so an empty value would produce a blank buyer_sku and an
    unmappable line. We fall back to `item_id`, which is mandatory — but the mapping in
    SAP must then be keyed on the item_id, so a warning is raised to make that visible.

  - `line_number` (3.6.3) is ZERO-BASED in the contract example. Every other partner and
    our own UI are 1-based, and edi_po_line_items is unique on (po_id, line_number), so a
    PO whose numbering starts at 0 is shifted by +1 for the whole PO. Positions stay
    consistent; only the offset moves.

  - `basic_price` (3.6.6) is the pre-tax cost price and is what we bill against.
    `landing_price` (3.6.5) is inclusive of logistics and taxes — pricing on it would
    inflate the taxable value and double-count tax. It is captured only for a
    cross-check warning.

  - `uom` (3.6.11) carries the real unit. The previous implementation hardcoded "EA",
    which silently mislabels a 12 ml item as 12 each.

  - `hsn_code` is NOT in the contract at any level. It is still read when present
    (production payloads have carried it), but its absence is normal and not an error.

  - `tenant` (section 4) may be HYPERPURE, which is a different legal buyer from Blinkit.
    Booking a Hyperpure PO against the Blinkit CardCode would invoice the wrong customer,
    so a non-BLINKIT tenant raises a warning rather than passing silently.

Quirks confirmed in production (_archive/backend_old/app/routes.py):
  - igst_percentage may be null (intrastate) or 0.0; cgst+sgst are then used
  - total_amount in the header may differ from the sum of lines (Blinkit rounds centrally)
  - PO_CANCELLATION events arrive in the same structure with type="PO_CANCELLATION"
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

import structlog

from app.models._enums import PoStatus, SourceChannel
from app.parsers.base import BaseParser, ParseResult
from app.schemas.canonical import EDI850, EDI850Line, EDIAddress

log = structlog.get_logger(__name__)


class BlinkitTenant(StrEnum):
    """
    Contract section 4 — which Blinkit-group business raised the PO.

    HYPERPURE is Zomato's B2B arm and a different legal buyer from Blinkit, with its own
    GSTIN and CardCode. Both arrive on the same webhook, so the value decides who gets
    invoiced; it is not cosmetic.
    """

    BLINKIT = "BLINKIT"
    HYPERPURE = "HYPERPURE"


class BlinkitEventType(StrEnum):
    """Contract section 1 — `type`. Cancellations reuse the creation payload shape."""

    PO_CREATION = "PO_CREATION"
    PO_CANCELLATION = "PO_CANCELLATION"


class BlinkitAckStatus(StrEnum):
    """
    Contract response enum for `data.po_status`.

    Lower-case per the enum table. The contract's own example JSON shows
    "PARTIALLY_ACCEPTED" upper-case, contradicting its table — we follow the table, which
    is the normative part.

    PROCESSING is what our webhook returns immediately: we store and queue the PO, and
    the real outcome is sent later via the acknowledgement flow (contract section 12).
    """

    PROCESSING = "processing"
    ACCEPTED = "accepted"
    PARTIALLY_ACCEPTED = "partially_accepted"
    REJECTED = "rejected"


class BlinkitErrorCode(StrEnum):
    """Contract section 11 — codes we may return on the acknowledgement."""

    DUPLICATE_PO = "E101"          # Duplicate PO number
    OUTLET_NOT_FOUND = "E102"      # Outlet not found
    SUPPLIER_GST_MISMATCH = "E103"
    BUYER_GST_MISMATCH = "E104"
    SKU_LEVEL_ERROR = "E105"       # e.g. MRP not found, qty exceeds max allowed


class BlinkitWarningCode(StrEnum):
    """Contract section 11 — warning codes."""

    UOM_MISMATCH = "W101"          # created with the default UOM


_ZERO = Decimal("0")
_TWO_DP = Decimal("0.01")
# Blinkit rounds centrally, so a sub-rupee gap between the header total and the sum
# of lines is expected. Anything wider is a real disagreement worth surfacing.
_TOTAL_TOLERANCE = Decimal("1.00")


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

        line_errors.extend(_check_contract_header(payload, details, lines))

        cgst_total = _sum_decimal(li.cgst_amount for li in lines)
        sgst_total = _sum_decimal(li.sgst_amount for li in lines)
        igst_total = _sum_decimal(li.igst_amount for li in lines)
        cess_total = _sum_decimal(li.cess_amount for li in lines)
        subtotal = _sum_decimal(li.taxable_amount for li in lines)

        header_total = _to_decimal(details.get("total_amount"))
        # Use header total if provided; otherwise compute from lines
        grand_total = header_total if header_total else (
            subtotal + cgst_total + sgst_total + igst_total + cess_total
        )

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
            cess_amount=cess_total or None,
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
        """
        Build lines, honouring the contract's own `line_number` (3.6.3).

        The contract numbers lines from ZERO. Our UI, every other partner, and the
        (po_id, line_number) unique constraint all assume 1-based, so a PO whose numbering
        starts at 0 is shifted by +1 across the whole PO — relative positions are
        preserved, only the offset moves. Falling back to positional numbering when the
        field is absent keeps older payloads working.
        """
        notes: list[str] = []
        numbers = [
            item.get("line_number") for item in item_data
            if isinstance(item.get("line_number"), int)
        ]
        # Shift only when the payload genuinely uses 0-based numbering.
        offset = 1 if numbers and min(numbers) == 0 else 0

        lines: list[EDI850Line] = []
        for idx, item in enumerate(item_data, start=1):
            raw_no = item.get("line_number")
            line_no = (raw_no + offset) if isinstance(raw_no, int) else idx
            try:
                lines.append(_blinkit_item_to_line(item, line_no, notes))
            except Exception as exc:
                sku = item.get("sku_code") or item.get("item_id") or "?"
                notes.append(f"Line {line_no} (sku={sku}): {exc}")
        return lines, notes


# ── Helpers ───────────────────────────────────────────────────────────────────

def _check_contract_header(
    payload: dict[str, Any], details: dict[str, Any], lines: list[EDI850Line]
) -> list[str]:
    """
    Contract checks that belong to the PO as a whole, returned as warnings.

    None of these should block ingestion — a PO we can read is worth storing even if a
    header count disagrees. They exist so a contract drift surfaces on the PO in the
    exceptions queue rather than being discovered months later in a reconciliation.
    """
    notes: list[str] = []

    # Section 4 — tenant decides WHO is being invoiced. HYPERPURE is a different legal
    # buyer from Blinkit, with its own GSTIN and CardCode.
    tenant = str(payload.get("tenant") or "").strip().upper()
    if not tenant:
        notes.append("tenant (section 4) missing — assuming BLINKIT")
    elif tenant not in {t.value for t in BlinkitTenant}:
        notes.append(
            f"tenant '{tenant}' is not a contract value "
            f"({', '.join(t.value for t in BlinkitTenant)})"
        )
    elif tenant != BlinkitTenant.BLINKIT:
        notes.append(
            f"tenant is {tenant}, not BLINKIT — this is a different legal buyer. "
            "Confirm the SAP CardCode before pushing, or the wrong customer is invoiced."
        )

    # Section 1 vs 3.1 — the contract requires these to match.
    top = str(payload.get("po_number") or "").strip()
    inner = str(details.get("po_number") or "").strip()
    if inner and top and inner != top:
        notes.append(
            f"po_number mismatch: top-level '{top}' vs details.po_number '{inner}' (3.1)"
        )

    # 3.7 / 3.8 — declared counts vs what we actually parsed.
    total_sku = details.get("total_sku")
    if isinstance(total_sku, int) and total_sku != len(lines):
        notes.append(
            f"total_sku (3.7) says {total_sku} but {len(lines)} line(s) parsed"
        )

    total_qty = details.get("total_qty")
    if total_qty is not None:
        declared = _to_decimal(total_qty)
        actual = _sum_decimal(li.ordered_qty for li in lines)
        if declared and declared != actual:
            notes.append(
                f"total_qty (3.8) says {declared} but line quantities sum to {actual}"
            )

    # 3.9 — the header total is what Blinkit will pay against, so it wins when present.
    # But it is also the figure most worth distrusting: the contract's own example ships
    # total_amount 42 for a PO whose lines come to 7,814.52. A silent divergence here is
    # an invoice dispute later, so anything beyond rounding is called out.
    total_amount = details.get("total_amount")
    if total_amount is not None:
        declared_amt = _to_decimal(total_amount)
        computed = _sum_decimal(li.line_total for li in lines)
        if declared_amt and computed and abs(declared_amt - computed) > _TOTAL_TOLERANCE:
            notes.append(
                f"total_amount (3.9) says {declared_amt} but lines total {computed} "
                f"(difference {abs(declared_amt - computed)}). The header value is used; "
                "verify before invoicing."
            )

    return notes


def _blinkit_item_to_line(
    item: dict[str, Any], line_number: int, warnings: list[str] | None = None
) -> EDI850Line:
    """
    One item_data entry (contract 3.6) to a canonical line.

    `warnings` collects non-fatal contract observations — an empty sku_code, a UOM we
    had to default — so they reach the PO instead of being lost in logs.
    """
    notes = warnings if warnings is not None else []

    # 3.6.2 sku_code is optional and frequently empty; 3.6.1 item_id is mandatory.
    # str() because item_id is an integer in the contract and buyer_sku is a string key.
    raw_sku = str(item.get("sku_code") or "").strip()
    item_id = str(item.get("item_id") or "").strip()
    buyer_sku = raw_sku or item_id
    if not buyer_sku:
        raise ValueError("item has neither sku_code (3.6.2) nor item_id (3.6.1)")
    if not raw_sku and item_id:
        notes.append(
            f"Line {line_number}: sku_code empty, mapping on item_id {item_id} instead "
            "— the SAP mapping for this line must be keyed on the item_id."
        )

    qty = _to_decimal(item.get("units_ordered"))
    if qty <= _ZERO:
        raise ValueError(f"units_ordered (3.6.4) must be > 0, got {qty}")

    # 3.6.6 basic_price is the pre-tax cost price. 3.6.5 landing_price is inclusive of
    # logistics and taxes — billing on it would inflate taxable value and double-count tax.
    unit_price = _to_decimal(item.get("basic_price"))
    taxable = (qty * unit_price).quantize(_TWO_DP, ROUND_HALF_UP)

    tax = item.get("tax_details") or {}
    cgst_rate = _to_decimal(tax.get("cgst_percentage"))
    sgst_rate = _to_decimal(tax.get("sgst_percentage"))
    igst_raw = tax.get("igst_percentage")
    igst_rate = _to_decimal(igst_raw) if igst_raw is not None else _ZERO
    cess_rate = _to_decimal(tax.get("cess_percentage"))

    cgst_amt = (taxable * cgst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) if cgst_rate else None
    sgst_amt = (taxable * sgst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) if sgst_rate else None
    igst_amt = (taxable * igst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) if igst_rate else None

    # 3.6.7.4 is a percentage; 3.6.7.5 is an absolute amount. They are different units and
    # both may appear, so the percentage is computed and the flat value added on top.
    cess_amt = (taxable * cess_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP) if cess_rate else _ZERO
    cess_amt += _to_decimal(tax.get("additional_cess_value"))

    line_total = (
        taxable + (cgst_amt or _ZERO) + (sgst_amt or _ZERO) + (igst_amt or _ZERO) + cess_amt
    )

    # 3.6.11 uom carries the real unit ("ml", "kg"). Hardcoding "EA" mislabels a 12 ml
    # item as 12 each, which then converts wrongly against sku_mapping.qty_per_buyer_uom.
    uom = (item.get("uom") or {}).get("unit")
    buyer_uom = str(uom).strip() if uom else None
    if not buyer_uom:
        buyer_uom = "EA"
        notes.append(
            f"Line {line_number}: uom.unit (3.6.11.1) missing, defaulted to EA "
            f"[{BlinkitWarningCode.UOM_MISMATCH}]"
        )

    return EDI850Line(
        line_number=line_number,
        buyer_sku=buyer_sku,
        buyer_sku_description=item.get("name"),
        # Not in the contract at any level, but production payloads have carried it.
        hsn_code=item.get("hsn_code"),
        ordered_qty=qty,
        buyer_uom=buyer_uom,
        unit_price=unit_price,
        taxable_amount=taxable,
        cgst_rate=cgst_rate or None,
        cgst_amount=cgst_amt,
        sgst_rate=sgst_rate or None,
        sgst_amount=sgst_amt,
        igst_rate=igst_rate or None,
        igst_amount=igst_amt,
        cess_rate=cess_rate or None,
        cess_amount=cess_amt or None,
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
