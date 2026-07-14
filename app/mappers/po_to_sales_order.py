"""
Mapper: canonical EdiPurchaseOrder + lines → SAP B1 Sales Order JSON.

B1 Sales Order = object type 17 (ORDR / RDR1).
Key mapping decisions:
  - CardCode          ← TradingPartner.b1_card_code
  - DocDate           ← today (UTC) unless buyer_po_date is available
  - DocDueDate        ← requested_delivery_date or DocDate
  - BPL_IDAssignedToInvoice ← SellerEntity.b1_bpl_id (default 1 if not set)
  - Quantities sent in inventory UoM (after sku_mapping.qty_per_buyer_uom conversion)
  - UDFs: U_EDI_SOURCE, U_EDI_DOC_UUID, U_EDI_RECEIVED_AT, U_BUYER_GSTIN,
          U_EDI_PO_NUMBER, U_EDI_LINE_NO (per line), U_BUYER_SKU (per line)

References:
  _archive/backend_old/app/services/blinkit.py   — original field names studied
  _archive/backend_old/app/services/zepto.py     — original field names studied
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.edi_po import EdiPoLineItem, EdiPurchaseOrder
    from app.models.master_data import SellerEntity, SkuMapping, TradingPartner

# UDF field names on ORDR (header)
_UDF_SOURCE = "U_EDI_SOURCE"
_UDF_UUID = "U_EDI_DOC_UUID"
_UDF_RECEIVED_AT = "U_EDI_RECEIVED_AT"
_UDF_BUYER_GSTIN = "U_BUYER_GSTIN"
_UDF_PO_NUMBER = "U_EDI_PO_NUMBER"

# UDF field names on RDR1 (lines)
_UDF_LINE_NO = "U_EDI_LINE_NO"
_UDF_BUYER_SKU = "U_BUYER_SKU"


def build_sales_order_payload(
    po: EdiPurchaseOrder,
    lines: list[EdiPoLineItem],
    partner: TradingPartner,
    seller: SellerEntity,
    sku_mappings: dict[str, SkuMapping],  # buyer_sku → SkuMapping
) -> dict[str, Any]:
    """
    Build the JSON payload for POST /b1s/v1/Orders.

    Raises ValueError if mandatory fields (CardCode, DocumentLines) cannot be
    populated — the caller should surface this as a SAP_REJECTED failure.
    """
    if not partner.b1_card_code:
        raise ValueError(
            f"TradingPartner '{partner.code}' has no b1_card_code — "
            "cannot create Sales Order without a CardCode."
        )

    today = date.today()
    doc_date = _format_date(po.buyer_po_date or today)
    due_date = _format_date(po.requested_delivery_date or po.buyer_po_date or today)
    bpl_id = getattr(seller, "b1_bpl_id", None) or 1

    payload: dict[str, Any] = {
        "CardCode": partner.b1_card_code,
        "DocDate": doc_date,
        "DocDueDate": due_date,
        "BPL_IDAssignedToInvoice": bpl_id,
        "Comments": f"EDI PO {po.buyer_po_number} from {partner.name}",
        # UDFs
        _UDF_SOURCE: partner.code,
        _UDF_UUID: str(po.correlation_id),
        _UDF_RECEIVED_AT: _format_datetime(po.created_at),
        _UDF_BUYER_GSTIN: po.buyer_gstin or "",
        _UDF_PO_NUMBER: po.buyer_po_number,
        "DocumentLines": [],
    }

    doc_lines: list[dict[str, Any]] = []
    for line in lines:
        doc_line = _build_line(line, sku_mappings.get(line.buyer_sku))
        doc_lines.append(doc_line)

    if not doc_lines:
        raise ValueError("Sales Order must have at least one DocumentLine.")

    payload["DocumentLines"] = doc_lines
    return payload


def _build_line(
    line: EdiPoLineItem,
    mapping: SkuMapping | None,
) -> dict[str, Any]:
    item_code = line.sap_material_no
    if not item_code:
        raise ValueError(
            f"Line {line.line_number}: sap_material_no is empty — "
            "run validation/SKU-mapping before pushing to B1."
        )

    # Quantity: convert buyer UoM → inventory UoM using sku_mapping.qty_per_buyer_uom
    qty_factor = float(getattr(mapping, "qty_per_buyer_uom", 1) or 1)
    inventory_qty = float(line.ordered_qty or 0) * qty_factor

    doc_line: dict[str, Any] = {
        "ItemCode": item_code,
        "Quantity": round(inventory_qty, 4),
        "Price": round(float(line.unit_price or 0), 6),
        _UDF_LINE_NO: str(line.line_number),
        _UDF_BUYER_SKU: line.buyer_sku,
    }

    if line.b1_whs_code:
        doc_line["WarehouseCode"] = line.b1_whs_code

    if line.buyer_uom:
        doc_line["UnitOfMeasureCode"] = line.buyer_uom

    if line.hsn_code:
        doc_line["HSNOrSACCode"] = line.hsn_code  # B1 India localization field

    return doc_line


def _format_date(d: date | None) -> str:
    if d is None:
        return datetime.now(UTC).strftime("%Y-%m-%d")
    return d.strftime("%Y-%m-%d")


def _format_datetime(dt: datetime | None) -> str:
    if dt is None:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
