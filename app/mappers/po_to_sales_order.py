"""
Mapper: canonical EdiPurchaseOrder + lines → SAP B1 Sales Order JSON (ORDR / RDR1).

The payload shape here was derived from **posted documents in the live company**, not
from the generic Service Layer reference — B1 installations differ enormously in which
UDFs exist and what tax codes are called, and a field that does not exist is a hard
rejection rather than a warning. Ground truth: `GET /Orders?$filter=CardCode eq 'D00086'`
on TESTECPL260422, e.g. DocEntry 1764 / DocNum 3000043.

Header fields
  CardCode / CardName        ← TradingPartner.b1_card_code / .name
  DocDate / TaxDate          ← buyer_po_date (the retailer's PO date)
  DocDueDate                 ← requested_delivery_date
  BPL_IDAssignedToInvoice    ← the branch chosen at push time (po.b1_bpl_id)
  NumAtCard                  ← buyer_po_number — the retailer's own PO number
  ShipToCode / PayToCode     ← B1 BP address names chosen at push time
  DocCurrency                ← po.currency

User-defined fields
  U_OrdType   db_Alpha(10)   order type; "N" (normal) unless overridden
  U_POEXP_DT  db_Date        PO expiry — when the retailer's window closes
  U_DC_TAT    db_Numeric     turnaround days, PO date → requested delivery

  These three were verified present on ORDR. `U_MWOrderID` from the draft integration
  spec is **not** defined in this company, so it is not sent — Service Layer rejects
  the whole document for an unknown property. Add it via UDF_HEADER_EXTRA once the
  field exists in B1.

Line fields
  ItemCode, Quantity, WarehouseCode, Price, VatGroup, DiscountPercent,
  Currency, ShipDate

  No UoM fields: items here have UoMGroupEntry -1, and posted lines carry
  UoMCode "Manual" / UoMEntry -1. Sending a UoM would be rejected or silently
  reinterpreted.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from app.utils.gst import is_interstate, resolve_state, vat_group

if TYPE_CHECKING:
    from app.models.edi_po import EdiPoLineItem, EdiPurchaseOrder
    from app.models.master_data import BranchMaster, SkuMapping, TradingPartner

# Header UDFs confirmed to exist on ORDR in this company.
UDF_ORDER_TYPE = "U_OrdType"
UDF_PO_EXPIRY = "U_POEXP_DT"
UDF_DC_TAT = "U_DC_TAT"

DEFAULT_ORDER_TYPE = "N"

# Fallback when a line carries no GST rate at all. Every item traded so far is 5%,
# but a wrong guess is a wrong tax code, so this is only used when nothing better
# exists and it is reported as a warning rather than applied silently.
FALLBACK_GST_RATE = Decimal("5")


class MappingError(ValueError):
    """Raised when the PO cannot produce a valid Sales Order payload."""


def build_sales_order_payload(
    po: EdiPurchaseOrder,
    lines: list[EdiPoLineItem],
    partner: TradingPartner,
    branch: BranchMaster,
    sku_mappings: dict[str, SkuMapping] | None = None,
    *,
    warehouse_code: str,
    ship_to_code: str | None = None,
    pay_to_code: str | None = None,
    order_type: str = DEFAULT_ORDER_TYPE,
) -> tuple[dict[str, Any], list[str]]:
    """
    Build the payload for ``POST /b1s/v2/Orders``.

    Returns ``(payload, warnings)``. Warnings describe assumptions a human should look
    at — a defaulted tax rate, a missing GSTIN — that are not severe enough to block a
    push but would be invisible otherwise.

    Raises MappingError when the order genuinely cannot be built.
    """
    warnings: list[str] = []

    if not partner.b1_card_code:
        raise MappingError(
            f"Partner '{partner.code}' has no b1_card_code — a Sales Order cannot be "
            f"created without a CardCode. Set it in Master Data → Customers."
        )
    if not lines:
        raise MappingError("A Sales Order needs at least one line; this PO has none.")
    if not warehouse_code:
        raise MappingError("No warehouse selected — pick one before pushing.")

    # ── Place of supply ──────────────────────────────────────────────────────
    # From-state is the *branch*, not the seller entity: a company registered in four
    # states bills from whichever branch ships. To-state comes from the ship-to GSTIN
    # where we have one, since its prefix is unambiguous.
    from_state = resolve_state(gstin=branch.gstin, state=branch.state)
    to_state = resolve_state(gstin=po.buyer_gstin, state=_ship_to_state(po))
    interstate = is_interstate(from_state, to_state)

    if interstate is None:
        raise MappingError(
            f"Cannot determine the place of supply: branch state "
            f"{from_state or branch.state or '(unknown)'!r} vs ship-to state "
            f"{to_state or _ship_to_state(po) or '(unknown)'!r}. That decides CGST+SGST "
            f"versus IGST, so the order is not pushed rather than taxed on a guess."
        )
    if not po.buyer_gstin:
        warnings.append(
            "PO carries no buyer GSTIN; place of supply was taken from the ship-to "
            "address instead."
        )

    doc_date = po.buyer_po_date or _today()
    due_date = po.requested_delivery_date or doc_date

    payload: dict[str, Any] = {
        "CardCode": partner.b1_card_code,
        "CardName": partner.name,
        "DocDate": _fmt_date(doc_date),
        "DocDueDate": _fmt_date(due_date),
        "TaxDate": _fmt_date(doc_date),
        "BPL_IDAssignedToInvoice": branch.bpl_id,
        "NumAtCard": po.buyer_po_number,
        "Comments": f"EDI {partner.code} PO {po.buyer_po_number} via middleware",
        "DocCurrency": po.currency or "INR",
        UDF_ORDER_TYPE: order_type,
        UDF_DC_TAT: max((due_date - doc_date).days, 0),
    }

    expiry = getattr(po, "po_expiry_date", None)
    if expiry:
        payload[UDF_PO_EXPIRY] = _fmt_date(expiry)

    if ship_to_code:
        payload["ShipToCode"] = ship_to_code
    else:
        warnings.append(
            "No ShipToCode selected — B1 will apply the customer's default ship-to "
            "address, which may not be the DC this PO names."
        )
    if pay_to_code:
        payload["PayToCode"] = pay_to_code

    doc_lines = []
    for line in lines:
        mapping = (sku_mappings or {}).get(line.buyer_sku)
        doc_lines.append(
            _build_line(
                line, mapping,
                warehouse_code=warehouse_code,
                interstate=interstate,
                currency=payload["DocCurrency"],
                ship_date=due_date,
                warnings=warnings,
            )
        )
    payload["DocumentLines"] = doc_lines
    return payload, warnings


def _build_line(
    line: EdiPoLineItem,
    mapping: SkuMapping | None,
    *,
    warehouse_code: str,
    interstate: bool,
    currency: str,
    ship_date: date,
    warnings: list[str],
) -> dict[str, Any]:
    if not line.sap_material_no:
        raise MappingError(
            f"Line {line.line_number} ({line.buyer_sku}) has no B1 item code. "
            f"Add the mapping in SAP and re-sync, then retry."
        )

    qty = _inventory_qty(line, mapping)
    if qty <= 0:
        raise MappingError(
            f"Line {line.line_number} ({line.buyer_sku}) has quantity {qty} — "
            f"B1 rejects a Sales Order line with no quantity."
        )

    rate = _gst_rate(line)
    if rate is None:
        rate = FALLBACK_GST_RATE
        warnings.append(
            f"Line {line.line_number} ({line.buyer_sku}) carried no GST rate; "
            f"defaulted to {FALLBACK_GST_RATE}% → {vat_group(rate, interstate=interstate)}. "
            f"Verify before invoicing."
        )

    # The operator's dialog choice wins over the warehouse cached on the line by
    # ShipToMappingRule. That mapping is a standing default for a retailer DC; the
    # selection is a decision made for this push, against the branch just chosen. If
    # the line's default silently won, picking a warehouse would do nothing — and
    # the pair could disagree with the branch, which B1 rejects.
    if line.b1_whs_code and line.b1_whs_code != warehouse_code:
        warnings.append(
            f"Line {line.line_number}: ship-to mapping suggests warehouse "
            f"{line.b1_whs_code}, but {warehouse_code} was selected. Using the "
            f"selection. Update the ship-to mapping if it is out of date."
        )

    return {
        "ItemCode": line.sap_material_no,
        "Quantity": float(qty),
        "WarehouseCode": warehouse_code,
        "Price": float(_money(line.unit_price)),
        "VatGroup": vat_group(rate, interstate=interstate),
        "DiscountPercent": float(_money(line.discount_pct or 0)),
        "Currency": currency,
        "ShipDate": _fmt_date(ship_date),
    }


def _inventory_qty(line: EdiPoLineItem, mapping: SkuMapping | None) -> Decimal:
    """
    Quantity in inventory UoM. `inventory_qty` is written by validation when a UoM
    conversion applies; falling back to the raw ordered quantity is correct only
    because these items have no UoM group (conversion factor 1).
    """
    if line.inventory_qty is not None:
        return _qty(line.inventory_qty)
    factor = Decimal(str(getattr(mapping, "qty_per_buyer_uom", None) or 1))
    return _qty(Decimal(str(line.ordered_qty or 0)) * factor)


def _gst_rate(line: EdiPoLineItem) -> Decimal | None:
    """
    Combined GST rate for the line. B1's tax code carries the total, so an intra-state
    line split 2.5 + 2.5 is ``CSGST@5``, not ``CSGST@2.5``.
    """
    cgst = Decimal(str(line.cgst_rate or 0))
    sgst = Decimal(str(line.sgst_rate or 0))
    igst = Decimal(str(line.igst_rate or 0))
    total = igst if igst > 0 else cgst + sgst
    return total if total > 0 else None


def _ship_to_state(po: EdiPurchaseOrder) -> str | None:
    addr = po.ship_to_address
    if isinstance(addr, dict):
        for key in ("state", "State", "state_name"):
            if addr.get(key):
                return str(addr[key])
    return None


def _money(v: Any) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _qty(v: Any) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _today() -> date:
    return datetime.now(UTC).date()


def _fmt_date(d: date | datetime | None) -> str:
    if d is None:
        return _today().strftime("%Y-%m-%d")
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%Y-%m-%d")
