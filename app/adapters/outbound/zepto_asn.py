"""
Zepto ASN (856) payload builder for the Silk Route API.

Built against Zepto's **"API Externalisation Contracts v12"** and the ASN Creation
curl in `_archive/backend_old/assets/API Curls.csv`, which carries a full worked
example of the request body.

  Endpoint  POST {base}/api/v1/external/asn
  Pre-prod  https://silkroute.zeptonow.dev
  Prod      https://silkroute.zepto.co.in

Three rules from the contract shape this module:

**Quantities go in pieces, not cases** (contract rule 4). `sku_mapping.qty_per_buyer_uom`
converts at PO ingest, so the invoice already holds inventory-UoM quantities and nothing
further is needed here -- but a case-pack quantity slipping through would silently
understate a shipment by a factor of the case size, so the unit is stated explicitly on
every line rather than left implied.

**There is no update API** (rule 6). A wrong ASN has to be cancelled and re-created
under a *different* invoice number. That makes a bad send expensive to walk back, which
is why the builder warns rather than guesses on missing batch and expiry data.

**Every write needs an X-Idempotency-Key** (rule 5), supplied by the outbound adapter
from the outbound message id so a retry cannot double-book a shipment.

The shape Zepto validates against is camelCase, but its error messages name the
properties in PascalCase -- a rejection reading ``Field 'ItemDetails' is required``
means the JSON key ``itemDetails`` was missing, not that the casing was wrong.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)

_TWO_DP = Decimal("0.01")
_ZERO = Decimal("0")

# Contract sample uses "sales"; the field is mandatory and this is the only value
# the document shows.
INVOICE_TYPE_SALES = "sales"
CURRENCY_INR = "INR"
# Zepto's sample sends "EA" (each). Our inventory UoM is the source of truth when set.
DEFAULT_UOM = "EA"
# invoiceDetails.dueDate is mandatory. Nothing upstream carries payment terms yet, so it
# is derived and flagged rather than omitted -- an absent mandatory field is a hard
# rejection, a derived one is visible and correctable.
DEFAULT_PAYMENT_TERMS_DAYS = 30


def _d(v: Any) -> Decimal:
    try:
        return Decimal(str(v)) if v is not None else _ZERO
    except (ArithmeticError, ValueError):
        return _ZERO


def _money(v: Any) -> Decimal:
    return _d(v).quantize(_TWO_DP, ROUND_HALF_UP)


def _num(v: Any) -> int | float:
    """
    Integral values as ints, everything else as floats.

    Same reasoning as the Blinkit builder: a strict decoder that types a count as an
    integer rejects `100.0`, while one that types an amount as a float accepts `100`.
    The narrower encoding is the one both accept.
    """
    d = _d(v)
    return int(d) if d == d.to_integral_value() else float(d)


def _iso(d: date | datetime | None) -> str | None:
    if d is None:
        return None
    return (d.date() if isinstance(d, datetime) else d).isoformat()


def build_zepto_asn_payload(
    session: Session,
    po: Any,
    asn: Any,
    invoice: Any,
    partner: Any,
    seller: Any,
) -> tuple[dict[str, Any], list[str]]:
    """
    Build the Silk Route ASN body for one invoice.

    Returns ``(payload, warnings)``. Warnings name assumptions worth a human's eye --
    a derived due date, a missing batch number -- without refusing to ship over them.
    """
    warnings: list[str] = []

    inv_lines = list(invoice.line_items or [])
    asn_by_item = {(line.b1_item_code or ""): line for line in (asn.line_items or [])}

    items: list[dict[str, Any]] = []
    taxable_total = _ZERO

    for seq, inv_line in enumerate(inv_lines, start=1):
        material = _material_for(session, inv_line.b1_item_code)
        po_line = getattr(inv_line, "po_line", None)
        asn_line = asn_by_item.get(inv_line.b1_item_code or "")

        qty = _d(inv_line.qty)
        taxable_total += _money(inv_line.taxable_amount or qty * _d(inv_line.unit_price))

        batch = getattr(asn_line, "batch_number", None) or ""
        expiry = getattr(asn_line, "expiry_date", None)
        if not batch:
            warnings.append(
                f"Item {inv_line.b1_item_code}: no batch_number. Zepto has no ASN update "
                f"API, so a rejected line has to be cancelled and re-sent under a new "
                f"invoice number."
            )
        if expiry is None:
            warnings.append(f"Item {inv_line.b1_item_code}: no expiry_date on the ASN line.")

        ean = getattr(material, "ean_code", None) or ""
        if not ean:
            warnings.append(
                f"Item {inv_line.b1_item_code}: no EAN in the item master; sending the "
                f"item code as the seller identifier instead."
            )

        buyer_sku = getattr(po_line, "buyer_sku", None) or ""
        item: dict[str, Any] = {
            "itemSequenceNumber": seq,
            "productIdentifier": {
                "buyerProductIdentifier": {
                    "articleSequenceNumber": seq,
                    "skuCode": buyer_sku,
                    "materialCode": getattr(po_line, "buyer_material_code", None) or buyer_sku,
                    "articleName": getattr(po_line, "buyer_sku_description", None)
                    or inv_line.description
                    or "",
                },
                "sellerProductIdentifier": {
                    "identifier": {
                        "identifierType": "EAN",
                        "identifierValue": ean,
                    },
                    "itemCode": inv_line.b1_item_code or "",
                    "itemName": inv_line.description
                    or getattr(material, "item_name", None)
                    or "",
                },
            },
            "batchDetails": {
                "batchNumber": batch,
                "manufacturingDate": _iso(getattr(asn_line, "manufacturing_date", None)),
                "expiryDate": _iso(expiry),
            },
            # Rule 4: pieces, never cases.
            "quantity": {
                "invoicedQuantity": {
                    "amount": _num(qty),
                    "unitOfMeasure": _uom(inv_line, material),
                },
                "freeQuantity": {"amount": 0, "unitOfMeasure": _uom(inv_line, material)},
            },
        }
        items.append(item)

    if not items:
        warnings.append("Invoice has no line items — Zepto requires at least one.")

    due_date = _due_date(invoice, warnings)
    vendor_id, vendor_name = _seller_identity(session, po, partner, seller, warnings)

    payload: dict[str, Any] = {
        "purchaseOrderDetails": {
            "purchaseOrderNumber": po.buyer_po_number,
            "purchaseOrderDate": _iso(getattr(po, "buyer_po_date", None)),
            "expiryDate": _iso(getattr(po, "po_expiry_date", None)),
        },
        "invoiceDetails": {
            "invoiceNumber": invoice.invoice_number,
            "invoiceType": INVOICE_TYPE_SALES,
            "invoiceDate": _iso(invoice.invoice_date),
            "shippingDate": _iso(getattr(asn, "shipment_date", None) or invoice.invoice_date),
            "deliveryDate": _iso(
                getattr(po, "requested_delivery_date", None)
                or getattr(asn, "shipment_date", None)
                or invoice.invoice_date
            ),
            "dueDate": _iso(due_date),
        },
        "invoiceTotals": {
            "currencyCode": CURRENCY_INR,
            "discountDetails": {"totalDiscountAmount": _num(_money(_discount(invoice)))},
            "taxableAmount": _num(_money(invoice.subtotal_amount or taxable_total)),
            "grandTotalAmount": _num(_money(invoice.grand_total)),
        },
        "itemDetails": items,
        "seller": {"soldFrom": {"id": vendor_id, "name": vendor_name}},
    }
    return payload, warnings


def _uom(inv_line: Any, material: Any) -> str:
    return (
        getattr(inv_line, "uom", None)
        or getattr(material, "invntry_uom", None)
        or DEFAULT_UOM
    )


def _discount(invoice: Any) -> Decimal:
    """totalDiscountAmount is mandatory; 0 is a legitimate value, absence is not."""
    return _d(getattr(invoice, "discount_amount", None))


def _due_date(invoice: Any, warnings: list[str]) -> date | None:
    """
    Mandatory in the contract, and nothing upstream carries payment terms yet.

    Derived from the invoice date so the field is present and plainly wrong-if-wrong,
    rather than omitted and rejected outright.
    """
    inv_date = invoice.invoice_date
    if inv_date is None:
        return None
    base = inv_date.date() if isinstance(inv_date, datetime) else inv_date
    warnings.append(
        f"invoiceDetails.dueDate derived as invoice_date + {DEFAULT_PAYMENT_TERMS_DAYS}d "
        f"— no payment terms are stored against the partner yet."
    )
    return base + timedelta(days=DEFAULT_PAYMENT_TERMS_DAYS)


def _seller_identity(
    session: Session, po: Any, partner: Any, seller: Any, warnings: list[str]
) -> tuple[str, str]:
    """
    `seller.soldFrom.id` is the vendor code Zepto knows us by (e.g. "KK-1102").

    It arrives on every PO Zepto sends, so it is read back off the originating raw
    message rather than stored twice. api_config["vendor_code"] overrides when set.
    """
    cfg = getattr(partner, "api_config", None) or {}
    seller_name = getattr(seller, "name", None) or "Let's Try Foods"

    configured = cfg.get("vendor_code")
    if configured:
        return str(configured), str(cfg.get("vendor_name") or seller_name)

    raw_vendor = _vendor_code_from_raw(session, po)
    if raw_vendor:
        return raw_vendor[0], raw_vendor[1] or seller_name

    warnings.append(
        "No Zepto vendor code found on the PO or in partner.api_config['vendor_code'] "
        "— seller.soldFrom.id sent empty, which Zepto will reject."
    )
    return "", seller_name


def _vendor_code_from_raw(session: Session, po: Any) -> tuple[str, str] | None:
    from sqlalchemy import select

    from app.models.raw_messages import RawMessage

    raw_id = getattr(po, "raw_message_id", None)
    if raw_id is None:
        return None
    raw = session.execute(
        select(RawMessage).where(RawMessage.id == raw_id)
    ).scalar_one_or_none()
    payload = getattr(raw, "payload", None) or {}
    code = payload.get("vendorCode")
    if not code:
        return None
    return str(code), str(payload.get("vendorName") or "")


def _material_for(session: Session, item_code: str | None) -> Any:
    if not item_code:
        return None
    from sqlalchemy import select

    from app.models.master_data import MaterialMaster

    return session.execute(
        select(MaterialMaster).where(MaterialMaster.item_code == item_code)
    ).scalar_one_or_none()
