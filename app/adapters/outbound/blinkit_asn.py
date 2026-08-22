"""
Blinkit ASN (856) payload builder and response reader.

Built against Blinkit's **"POVMS - ASN Sync API Contracts"** (rev 100226-093807),
archived at `_archive/backend_old/assets/POVMS-ASN Sync API Contracts-*.txt` — a PDF
despite the `.txt` extension. Section numbers below refer to that document's
"Request Payload Details" and "Response Payload Details" tables.

  Endpoint  POST {base}/webhook/public/v1/asn
  Prod      https://api.partnersbiz.com
  Pre-prod  https://dev.partnersbiz.com

Two things in this contract are easy to get wrong and expensive when you do.

**A 2xx does not mean accepted.** The contract states plainly that full acceptance,
partial acceptance *and rejection* all return 2xx; rejection is signalled in the body.
Its own example response pairs ``"successful": true`` with
``"asn_sync_status": "REJECTED"`` — because §1 defines ``successful`` as "operation
executed; does not mean all items succeeded". Treating HTTP 200 as success would mark a
rejected ASN as delivered, and nobody would find out until a truck was turned away at
the retailer's gate. `interpret_asn_response` reads the body, not the status line.

**One asn-level error rejects the whole ASN.** Item-level errors can accompany a
rejection, but any single ``level: "asn"`` error means nothing was accepted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)

_TWO_DP = Decimal("0.01")
_ZERO = Decimal("0")


class BlinkitDeliveryType(StrEnum):
    """§11.2. `delivery_partner` and `delivery_tracking_code` are mandatory for COURIER."""

    COURIER = "COURIER"
    SELF = "SELF"


class BlinkitPoStatus(StrEnum):
    """§8 — only meaningful for multi-GRN (several invoices against one PO)."""

    FULFILLED = "PO_FULFILLED"
    PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED"


class BlinkitGstType(StrEnum):
    """§2.1.1 — header-level tax summary rows."""

    CGST = "CGST"
    SGST = "SGST"
    IGST = "IGST"
    CESS = "CESS"
    ADDITIONAL_CESS = "AdditionalCESS"


class BlinkitCaseLevel(StrEnum):
    """§12.23.1."""

    OUTER = "outer_case"
    INNER = "inner_case"


class BlinkitCaseType(StrEnum):
    """§12.23.2 — the contract names exactly these two."""

    CRATE = "CRATE"
    PACKETS = "PACKETS"


class BlinkitAsnStatus(StrEnum):
    """§2 of the response table."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED"


class BlinkitAsnErrorCode(StrEnum):
    """
    Contract "Error Codes" table. The document states these are immutable — more may be
    added in the EXXX range, but existing meanings will not change.
    """

    # Header — HTTP 400, status FAILED
    PO_ALREADY_PROCESSED = "E106"
    INVOICE_NUMBER_EXISTS = "E107"
    INVOICE_DATE_BEFORE_PO = "E108"
    SUPPLIER_GSTIN_MISMATCH = "E109"
    BUYER_GSTIN_MISMATCH = "E110"
    # SKU level
    ITEM_ID_INCORRECT = "E112"
    CODE_CATEGORY_INCORRECT = "E113"
    CODES_MANDATORY = "E114"


class BlinkitAsnWarningCode(StrEnum):
    """Contract "SKU Level : Warnings"."""

    NEAR_OR_PAST_EXPIRY = "W102"
    VARIANT_NOT_FOUND = "W103"


@dataclass
class AsnAck:
    """What Blinkit actually decided, as opposed to what HTTP said."""

    accepted: bool
    status: str
    asn_id: str | None = None
    message: str = ""
    success_count: int = 0
    error_count: int = 0
    asn_errors: list[dict[str, Any]] = field(default_factory=list)
    item_errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"{self.status}"]
        if self.message:
            parts.append(self.message)
        for e in (*self.asn_errors, *self.item_errors):
            parts.append(f"[{e.get('code')}] {e.get('message') or e.get('description') or ''}")
        return " | ".join(p for p in parts if p)


def interpret_asn_response(data: Any) -> AsnAck:
    """
    Decide acceptance from the response **body**.

    A rejected ASN comes back 2xx, so the transport layer cannot tell success from
    failure — only `asn_sync_status` and the errors array can. A single error at
    `level: "asn"` rejects the whole submission even when item-level rows succeeded.
    """
    if not isinstance(data, dict):
        return AsnAck(accepted=False, status="UNKNOWN",
                      message=f"Unreadable ASN response: {data!r}")

    status = str(data.get("asn_sync_status") or "").upper()
    errors = (data.get("data") or {}).get("errors") or []
    if not isinstance(errors, list):
        errors = []

    asn_errors = [e for e in errors if isinstance(e, dict) and str(e.get("level")) == "asn"]
    item_errors = [e for e in errors if isinstance(e, dict) and str(e.get("level")) == "item"]

    warnings = data.get("warnings") or (data.get("data") or {}).get("warnings") or []
    if not isinstance(warnings, list):
        warnings = []

    # Any asn-level error rejects the submission, whatever the status field says.
    accepted = status in {BlinkitAsnStatus.ACCEPTED, BlinkitAsnStatus.PARTIALLY_ACCEPTED} and not asn_errors
    if not status:
        # No status field at all — fall back to `successful`, but only when the errors
        # array is empty. Assuming success from a missing field is how a rejection
        # becomes invisible.
        accepted = bool(data.get("successful")) and not errors
        status = "ACCEPTED" if accepted else "UNKNOWN"

    return AsnAck(
        accepted=accepted,
        status=status,
        asn_id=str(data["asn_id"]) if data.get("asn_id") else None,
        message=str(data.get("message") or ""),
        success_count=int(data.get("success_count") or 0),
        error_count=int(data.get("error_count") or 0),
        asn_errors=asn_errors,
        item_errors=item_errors,
        warnings=warnings,
    )


# ── Payload construction ──────────────────────────────────────────────────────

def _d(v: Any) -> Decimal:
    try:
        return Decimal(str(v)) if v is not None else _ZERO
    except (ArithmeticError, ValueError):
        return _ZERO


def _money(v: Any) -> Decimal:
    return _d(v).quantize(_TWO_DP, ROUND_HALF_UP)


def _num_str(v: Any) -> str:
    """
    Money and counts go over the wire as strings (§3, §4, §6, §7 and the item-level
    price fields), so they are normalised here rather than left to json.dumps.
    """
    return f"{_money(v):f}"


def _num(v: Any) -> int | float:
    """
    Encode an integral value as an int, everything else as a float.

    Blinkit's API is Go. `json.Unmarshal` accepts the literal `360` into either an
    `int` or a `float64` field, but refuses `360.0` for an `int` one:

        cannot unmarshal number 360.0 into Go struct field
        Item.items.quantity of type int

    The contract calls these fields "number" without saying which, so the only
    encoding safe against both is the narrower one. Genuinely fractional values
    (gst_percentage 2.5, gst_total 1087.71) still go out as floats -- an `int`
    field would reject those whatever we did.
    """
    d = _d(v)
    return int(d) if d == d.to_integral_value() else float(d)


def _qty(v: Any, label: str, warnings: list[str]) -> int:
    """
    Item quantity, which Blinkit types as a Go `int`.

    A fractional quantity cannot be represented on their side at all, so it is
    rounded and called out rather than silently truncated -- a shipment notice
    that understates what is on the truck is worse than one that fails to send.
    """
    d = _d(v)
    rounded = int(d.to_integral_value(ROUND_HALF_UP))
    if d != d.to_integral_value():
        warnings.append(
            f"{label}: quantity {d} is not a whole number; Blinkit types this field "
            f"as an integer, so {rounded} was sent."
        )
    return rounded


def _iso(d: date | datetime | None) -> str | None:
    if d is None:
        return None
    return (d.date() if isinstance(d, datetime) else d).isoformat()


def build_blinkit_asn_payload(
    session: Session,
    po: Any,
    asn: Any,
    invoice: Any,
    partner: Any,
    seller: Any,
) -> tuple[dict[str, Any], list[str]]:
    """
    Build the ASN Sync body for one invoice.

    Returns ``(payload, warnings)``. Warnings name assumptions a human should check —
    a missing batch number, a defaulted UPC — that are worth seeing but not worth
    refusing to ship over. Blinkit's own validation is the backstop, and its rejections
    come back as error codes we surface verbatim.
    """
    warnings: list[str] = []

    inv_lines = list(invoice.line_items or [])
    asn_by_item = {
        (line.b1_item_code or ""): line for line in (asn.line_items or [])
    }

    items: list[dict[str, Any]] = []
    total_qty = _ZERO
    basic_total = _ZERO

    for inv_line in inv_lines:
        material = _material_for(session, inv_line.b1_item_code)
        po_line = inv_line.po_line
        asn_line = asn_by_item.get(inv_line.b1_item_code or "")

        qty = _d(inv_line.qty)
        total_qty += qty
        taxable = _money(inv_line.taxable_amount or qty * _d(inv_line.unit_price))
        basic_total += taxable

        batch = getattr(asn_line, "batch_number", None) or ""
        expiry = getattr(asn_line, "expiry_date", None)
        if not batch:
            warnings.append(
                f"Item {inv_line.b1_item_code}: no batch_number — the contract marks it "
                f"mandatory (§12.3) and Blinkit may reject the line."
            )
        if expiry is None:
            warnings.append(
                f"Item {inv_line.b1_item_code}: no expiry_date and no mfg_date/shelf_life "
                f"— §12.16-12.18 require one of the two forms."
            )

        upc = getattr(material, "ean_code", None) or ""
        if not upc:
            warnings.append(
                f"Item {inv_line.b1_item_code}: no EAN/UPC in the item master (§12.5 "
                f"mandatory)."
            )

        case_size = int(getattr(material, "case_size", 0) or 0)
        unit, uom_value = _uom(material, inv_line)

        item: dict[str, Any] = {
            "item_id": str(_blinkit_item_id(po_line, inv_line)),
            "sku_code": getattr(po_line, "buyer_sku", None) or "",
            "batch_number": batch,
            "sku_description": (
                inv_line.description or getattr(material, "item_name", None) or ""
            ),
            "upc": upc,
            "case_config": case_size,
            "quantity": _qty(qty, f"Item {inv_line.b1_item_code}", warnings),
            "mrp": _num(_money(getattr(material, "mrp", None))),
            "hsn_code": inv_line.hsn_code or getattr(material, "hsn", None) or "",
            "total_additional_cess_value": _num(_money(inv_line.cess_amount)),
            # §12.11 — every percentage present, zeros included. Omitting a key is not
            # the same as sending 0, and all six are marked mandatory.
            "tax_distribution": {
                "cgst_percentage": _num_str(inv_line.cgst_rate),
                "sgst_percentage": _num_str(inv_line.sgst_rate),
                "igst_percentage": _num_str(inv_line.igst_rate),
                "ugst_percentage": _num_str(_ZERO),
                "cess_percentage": _num_str(inv_line.cess_rate),
                "additional_cess_value": _num_str(inv_line.cess_amount),
            },
            "unit_basic_price": _num_str(inv_line.unit_price),
            "unit_landing_price": _num_str(_landing_price(inv_line, qty)),
            "uom": {"unit": unit, "value": uom_value},
        }
        if expiry is not None:
            item["expiry_date"] = _iso(expiry)
        if case_size > 0:
            item["case_configuration"] = [{
                "level": BlinkitCaseLevel.OUTER.value,
                "type": BlinkitCaseType.CRATE.value,
                "value": case_size,
            }]
        items.append(item)

    po_status = _po_status(session, po)
    delivery_type = (
        BlinkitDeliveryType.COURIER if asn.carrier else BlinkitDeliveryType.SELF
    )

    shipment: dict[str, Any] = {"delivery_type": delivery_type.value}
    if invoice.eway_bill_number:
        shipment["e_way_bill_number"] = str(invoice.eway_bill_number)
    if delivery_type is BlinkitDeliveryType.COURIER:
        shipment["delivery_partner"] = asn.carrier
        shipment["delivery_tracking_code"] = asn.tracking_number or ""
        if not asn.tracking_number:
            warnings.append(
                "delivery_type is COURIER but no tracking number is set — §11.4 makes "
                "delivery_tracking_code mandatory for courier shipments."
            )

    payload: dict[str, Any] = {
        "po_number": po.buyer_po_number,
        "invoice_number": invoice.invoice_number,
        "invoice_date": _iso(invoice.invoice_date),
        "delivery_date": _iso(asn.shipment_date or invoice.invoice_date),
        "total_additional_cess_value": _num(_money(invoice.cess_amount)),
        "tax_distribution": _header_tax_distribution(inv_lines),
        "basic_price": _num_str(invoice.subtotal_amount or basic_total),
        "landing_price": _num_str(invoice.grand_total),
        "quantity": _num_str(total_qty),
        "item_count": str(len(items)),
        "po_status": po_status.value,
        "supplier_details": _supplier_details(seller),
        "buyer_details": {"gstin": po.buyer_gstin or ""},
        "shipment_details": shipment,
        "items": items,
    }
    return payload, warnings


def _header_tax_distribution(inv_lines: list[Any]) -> list[dict[str, Any]]:
    """
    §2 — one row per (gst_type, rate) pair, with the tax and taxable value summed.

    Grouped by rate rather than emitted per line: a five-line invoice all at 5% is two
    rows (CGST 2.5, SGST 2.5), which is what the retailer reconciles against. Mixed-rate
    invoices produce a row per rate, which is also correct.
    """
    buckets: dict[tuple[str, str], dict[str, Decimal]] = {}

    def add(gst_type: BlinkitGstType, rate: Any, amount: Any, taxable: Any) -> None:
        rate_d, amount_d = _d(rate), _d(amount)
        if rate_d <= _ZERO and amount_d <= _ZERO:
            return
        key = (gst_type.value, f"{rate_d.normalize():f}")
        b = buckets.setdefault(key, {"total": _ZERO, "taxable": _ZERO})
        b["total"] += amount_d
        b["taxable"] += _d(taxable)

    for line in inv_lines:
        taxable = line.taxable_amount
        add(BlinkitGstType.CGST, line.cgst_rate, line.cgst_amount, taxable)
        add(BlinkitGstType.SGST, line.sgst_rate, line.sgst_amount, taxable)
        add(BlinkitGstType.IGST, line.igst_rate, line.igst_amount, taxable)
        add(BlinkitGstType.CESS, line.cess_rate, line.cess_amount, taxable)

    return [
        {
            "gst_type": gst_type,
            "gst_percentage": _num(Decimal(rate)),
            "gst_total": _num(_money(v["total"])),
            "taxable_value": _num_str(v["taxable"]),
        }
        for (gst_type, rate), v in buckets.items()
    ]


def _supplier_details(seller: Any) -> dict[str, Any]:
    """
    §9. The field-detail table spells the address keys `addressLine1`/`addressLine2`
    while the contract's own JSON and XML examples use `address_line_1`/`address_line_2`.
    The examples are the wire format, so those are what we send.
    """
    addr = {
        "address_line_1": getattr(seller, "address_line1", None) or "",
        "address_line_2": getattr(seller, "address_line2", None) or "",
        "city": getattr(seller, "city", None) or "",
        "country": getattr(seller, "country", None) or "India",
        "postal_code": getattr(seller, "pincode", None) or "",
        "state": getattr(seller, "state", None) or "",
    }
    return {
        "name": getattr(seller, "name", None) or "",
        "gstin": getattr(seller, "gstin", None) or "",
        "supplier_address": addr,
    }


def _material_for(session: Session, item_code: str | None) -> Any:
    if not item_code:
        return None
    from sqlalchemy import select

    from app.models.master_data import MaterialMaster

    return session.execute(
        select(MaterialMaster).where(
            MaterialMaster.item_code == item_code,
            MaterialMaster.deleted_at.is_(None),
        )
    ).scalar_one_or_none()


def _uom(material: Any, inv_line: Any) -> tuple[str, int | float]:
    """
    §12.19 — `uom` is an object of unit and volume-per-unit ("ml"/12), not a bare code.

    `material.grammage` carries values like "57g" or "173g"; splitting it gives both
    halves. Falling back to the invoice UoM code with value 1 is honest: it says one
    selling unit without inventing a volume.
    """
    grammage = (getattr(material, "grammage", None) or "").strip()
    if grammage:
        digits = "".join(c for c in grammage if c.isdigit() or c == ".")
        unit = "".join(c for c in grammage if c.isalpha()).lower()
        if digits and unit:
            try:
                return unit, _num(Decimal(digits))
            except ArithmeticError:
                pass
    return (inv_line.uom or getattr(material, "invntry_uom", None) or "PCS"), 1


def _landing_price(inv_line: Any, qty: Decimal) -> Decimal:
    """
    §12.15 — price per unit after discount and taxes. Derived from the line total rather
    than assumed, so it always agrees with what is actually invoiced.
    """
    if inv_line.line_total is not None and qty > _ZERO:
        return _money(_d(inv_line.line_total) / qty)
    return _money(inv_line.unit_price)


def _blinkit_item_id(po_line: Any, inv_line: Any) -> str:
    """
    §12.1 — Blinkit's own item identifier. The PO carried it as `item_id`, which the
    parser stores as the buyer SKU when `sku_code` was empty; otherwise the buyer SKU
    is the mapped code and is what Blinkit matches on.
    """
    return getattr(po_line, "buyer_sku", None) or inv_line.b1_item_code or ""


def _po_status(session: Session, po: Any) -> BlinkitPoStatus:
    """
    §8 — only meaningful for multi-GRN. Compares cumulative invoiced quantity across
    every invoice on this PO against what was ordered, so the last of several partial
    shipments correctly reports PO_FULFILLED.
    """
    from sqlalchemy import func, select

    from app.models.edi_po import EdiPoLineItem
    from app.models.invoice import EdiInvoice, EdiInvoiceLineItem

    ordered = session.execute(
        select(func.coalesce(func.sum(EdiPoLineItem.ordered_qty), 0)).where(
            EdiPoLineItem.po_id == po.id
        )
    ).scalar_one()

    invoiced = session.execute(
        select(func.coalesce(func.sum(EdiInvoiceLineItem.qty), 0))
        .join(EdiInvoice, EdiInvoice.id == EdiInvoiceLineItem.invoice_id)
        .where(EdiInvoice.po_id == po.id)
    ).scalar_one()

    if _d(invoiced) >= _d(ordered) > _ZERO:
        return BlinkitPoStatus.FULFILLED
    return BlinkitPoStatus.PARTIALLY_FULFILLED

