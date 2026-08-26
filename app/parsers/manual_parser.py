"""
Parser for purchase orders keyed in by hand.

Some partners have no wire at all. LOTS Wholesale send orders by phone and paper;
Reliance/JioMart publish theirs on a portal whose scraper is Phase 9 work. Until
those arrive on their own, ops key them in, and this turns those keystrokes into the
same canonical EDI850 every other partner produces -- so validation, SKU mapping, the
SAP push and the outbound 855/856 all work identically. Nothing downstream can tell a
manual PO from a Blinkit webhook, which is the point.

Payload shape (written by POST /api/manual-inbox/entries, stored verbatim):

    {
      "_entry_type": "MANUAL_PO",
      "buyer_po_number": "LOTS-2026-0117",
      "buyer_po_date": "2026-08-25",
      "requested_delivery_date": "2026-08-28",
      "buyer_name": "LOTS Wholesale Solutions",
      "buyer_gstin": "06AABCL1234C1ZX",
      "ship_to": {"warehouse_code": "LOTS-DEL-01", "name": "...", "state": "Haryana", ...},
      "line_items": [
        {"buyer_sku": "10116319", "ordered_qty": "36", "unit_price": "92.86",
         "gst_rate": "5", "hsn_code": "21069099", "buyer_uom": "PC", ...}
      ],
      "entered_by": "ops@letstryfoods.com",
      "notes": "phoned in by Rakesh, 25 Aug"
    }

**What the operator types and what this derives.** They type quantity, unit price and
one GST rate per line. Everything arithmetic -- taxable amount, the CGST/SGST-vs-IGST
split, line totals, header totals -- is computed here rather than keyed, because a
hand-typed total that disagrees with its own lines by a rupee trips
TotalReconciliationRule and parks the PO in the exceptions queue. Deriving means the
document reconciles by construction and the operator has fewer boxes to get wrong.

**The split is decided by place of supply**, exactly as `app/utils/gst.py` and
CLAUDE.md §8 have it: seller state vs ship-to state, CGST+SGST when they match and
IGST when they do not. When either state is unknown the split cannot be decided, and
this refuses rather than guessing -- picking one silently would misfile GST on a real
order.
"""
from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import structlog

from app.models._enums import PoStatus, SourceChannel
from app.parsers.base import BaseParser, ParseResult
from app.schemas.canonical import EDI850, EDI850Line, EDIAddress
from app.utils.gst import is_interstate, resolve_state

if TYPE_CHECKING:
    from app.models.raw_messages import RawMessage

log = structlog.get_logger(__name__)

ENTRY_TYPE = "MANUAL_PO"

_ZERO = Decimal("0")
_TWO_DP = Decimal("0.01")
_HUNDRED = Decimal("100")


def is_manual_entry(raw_message: Any) -> bool:
    """True when this raw message is a hand-keyed PO, whoever the partner is."""
    payload = getattr(raw_message, "payload", None)
    return isinstance(payload, dict) and payload.get("_entry_type") == ENTRY_TYPE


class ManualEntryParser(BaseParser):
    """
    Turns a hand-keyed PO into a canonical EDI850.

    Registered by payload rather than by partner code: every partner without an
    integration produces the same shape, so one parser covers all of them and each
    new manual partner needs no code.
    """

    @property
    def partner_code(self) -> str:
        # Not partner-specific. parse_and_persist routes to this parser on the
        # payload marker, so this value only ever labels a log line.
        return "MANUAL"

    def can_parse(self, raw_message: RawMessage) -> bool:
        return is_manual_entry(raw_message)

    def parse(self, raw_message: RawMessage) -> ParseResult:
        try:
            return self._parse(raw_message)
        except Exception as exc:  # a parser must never raise — see BaseParser
            log.exception("manual.parse_error", error=str(exc))
            return ParseResult(
                success=False,
                errors=[f"Manual entry could not be parsed: {exc}"],
                parser_name="ManualEntryParser",
            )

    # ── internals ─────────────────────────────────────────────────────────────

    def _parse(self, raw_message: RawMessage) -> ParseResult:
        payload: dict[str, Any] = getattr(raw_message, "payload", None) or {}

        po_number = str(payload.get("buyer_po_number") or "").strip()
        if not po_number:
            return ParseResult(
                success=False,
                errors=["Missing buyer_po_number"],
                parser_name="ManualEntryParser",
            )

        raw_lines = payload.get("line_items") or []
        if not raw_lines:
            return ParseResult(
                success=False,
                errors=["A purchase order needs at least one line item"],
                parser_name="ManualEntryParser",
            )

        ship_to_data: dict[str, Any] = payload.get("ship_to") or {}
        ship_to = EDIAddress(
            name=ship_to_data.get("name"),
            line1=ship_to_data.get("line1"),
            line2=ship_to_data.get("line2"),
            city=ship_to_data.get("city"),
            state=ship_to_data.get("state"),
            pincode=ship_to_data.get("pincode"),
            country=ship_to_data.get("country") or "India",
            gstin=ship_to_data.get("gstin"),
            warehouse_code=ship_to_data.get("warehouse_code"),
        )

        interstate, split_error = _decide_split(payload, ship_to)
        if split_error:
            return ParseResult(
                success=False, errors=[split_error], parser_name="ManualEntryParser"
            )

        lines: list[EDI850Line] = []
        errors: list[str] = []
        warnings: list[str] = []
        for index, raw_line in enumerate(raw_lines, start=1):
            line, line_errors = _build_line(raw_line, index, interstate=bool(interstate))
            errors.extend(line_errors)
            if line is not None:
                lines.append(line)

        if errors:
            return ParseResult(
                success=False, errors=errors, parser_name="ManualEntryParser"
            )

        totals = _header_totals(lines)
        if payload.get("notes"):
            warnings.append(f"Operator note: {payload['notes']}")

        doc = EDI850(
            id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            trading_partner_code=str(payload.get("partner_code") or "").upper(),
            source_channel=SourceChannel.MANUAL,
            raw_message_id=getattr(raw_message, "id", None),
            buyer_po_number=po_number,
            buyer_po_date=_parse_date(payload.get("buyer_po_date")),
            requested_delivery_date=_parse_date(payload.get("requested_delivery_date")),
            ship_to=ship_to,
            buyer_gstin=payload.get("buyer_gstin"),
            buyer_name=payload.get("buyer_name"),
            currency=str(payload.get("currency") or "INR"),
            subtotal_amount=totals["subtotal"],
            total_discount=totals["discount"] or None,
            cgst_amount=totals["cgst"] or None,
            sgst_amount=totals["sgst"] or None,
            igst_amount=totals["igst"] or None,
            cess_amount=totals["cess"] or None,
            grand_total=totals["grand_total"],
            line_items=lines,
            po_status=PoStatus.PARSED,
        )

        return ParseResult(
            success=True,
            doc=doc,
            warnings=warnings,
            parser_name="ManualEntryParser",
        )


def _decide_split(payload: dict[str, Any], ship_to: EDIAddress) -> tuple[bool | None, str]:
    """
    Work out CGST+SGST vs IGST, or say why it cannot be worked out.

    Both states are read GSTIN-first because a GSTIN prefix is unambiguous while a
    typed state name is not. Refusing on an unknown state is deliberate: silently
    defaulting to intra-state would put CGST+SGST on an inter-state order and the
    error would only surface at the retailer's reconciliation.
    """
    seller_state = resolve_state(
        gstin=payload.get("seller_gstin"), state=payload.get("seller_state")
    )
    buyer_state = resolve_state(
        gstin=ship_to.gstin or payload.get("buyer_gstin"), state=ship_to.state
    )

    if seller_state is None:
        return None, (
            "Cannot decide the GST split: the seller entity has no usable GSTIN or "
            "state. Set them in Master Data before keying orders in."
        )
    if buyer_state is None:
        return None, (
            "Cannot decide the GST split: the ship-to has no usable GSTIN or state. "
            "Enter the delivery state (or its GSTIN) on the order."
        )

    return is_interstate(seller_state, buyer_state), ""


def _build_line(
    raw: Any, line_number: int, *, interstate: bool
) -> tuple[EDI850Line | None, list[str]]:
    """Build one canonical line, deriving every amount from qty, price and rate."""
    if not isinstance(raw, dict):
        return None, [f"Line {line_number}: not a line item"]

    errors: list[str] = []
    sku = str(raw.get("buyer_sku") or "").strip()
    if not sku:
        errors.append(f"Line {line_number}: missing buyer SKU")

    qty = _dec(raw.get("ordered_qty"))
    if qty is None or qty <= _ZERO:
        errors.append(f"Line {line_number}: quantity must be a positive number")

    price = _dec(raw.get("unit_price"))
    if price is None or price < _ZERO:
        errors.append(f"Line {line_number}: unit price must be zero or more")

    gst_rate = _dec(raw.get("gst_rate")) or _ZERO
    if gst_rate < _ZERO:
        errors.append(f"Line {line_number}: GST rate cannot be negative")

    if errors:
        return None, errors

    assert qty is not None and price is not None  # guarded above

    gross = _money(qty * price)
    discount_pct = _dec(raw.get("discount_pct")) or _ZERO
    discount = _money(gross * discount_pct / _HUNDRED) if discount_pct else _ZERO
    taxable = _money(gross - discount)

    cess_rate = _dec(raw.get("cess_rate")) or _ZERO
    cess_amount = _money(taxable * cess_rate / _HUNDRED) if cess_rate else _ZERO

    if interstate:
        igst_rate, igst_amount = gst_rate, _money(taxable * gst_rate / _HUNDRED)
        cgst_rate = sgst_rate = _ZERO
        cgst_amount = sgst_amount = _ZERO
    else:
        # The pair splits the combined rate, so 5% GST is 2.5% + 2.5%. Each half is
        # computed from its own rate rather than by halving the combined amount:
        # on a 167.15 total, halving gives 83.58 and 83.57, and CGST != SGST is
        # queried at filing. Equal halves may sum to a paisa less than the combined
        # figure, which costs nothing here because every total downstream is a sum of
        # these line amounts -- nothing computes tax a second way to disagree with.
        igst_rate = igst_amount = _ZERO
        cgst_rate = sgst_rate = gst_rate / Decimal(2)
        cgst_amount = sgst_amount = _money(taxable * cgst_rate / _HUNDRED)

    line_total = _money(taxable + cgst_amount + sgst_amount + igst_amount + cess_amount)

    return EDI850Line(
        line_number=line_number,
        buyer_sku=sku,
        buyer_sku_description=(raw.get("description") or None),
        hsn_code=(raw.get("hsn_code") or None),
        ordered_qty=qty,
        buyer_uom=(raw.get("buyer_uom") or None),
        unit_price=price,
        discount_pct=discount_pct or None,
        taxable_amount=taxable,
        cgst_rate=cgst_rate or None,
        cgst_amount=cgst_amount or None,
        sgst_rate=sgst_rate or None,
        sgst_amount=sgst_amount or None,
        igst_rate=igst_rate or None,
        igst_amount=igst_amount or None,
        cess_rate=cess_rate or None,
        cess_amount=cess_amount or None,
        line_total=line_total,
    ), []


def _header_totals(lines: list[EDI850Line]) -> dict[str, Decimal]:
    """Header figures as the sum of the lines, so the two cannot disagree."""
    def total(attr: str) -> Decimal:
        return _money(sum((getattr(line, attr) or _ZERO for line in lines), _ZERO))

    subtotal = total("taxable_amount")
    cgst, sgst, igst, cess = (total(a) for a in
                              ("cgst_amount", "sgst_amount", "igst_amount", "cess_amount"))
    discount = _money(sum(
        ((line.unit_price or _ZERO) * line.ordered_qty - (line.taxable_amount or _ZERO)
         for line in lines),
        _ZERO,
    ))
    return {
        "subtotal": subtotal,
        "discount": discount,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "cess": cess,
        "grand_total": _money(subtotal + cgst + sgst + igst + cess),
    }


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ArithmeticError, ValueError):
        return None


def _money(value: Decimal) -> Decimal:
    return value.quantize(_TWO_DP, ROUND_HALF_UP)


def _parse_date(value: Any) -> Any:
    from datetime import date, datetime

    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None
