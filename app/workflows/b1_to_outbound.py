"""
B1-to-outbound workflow — polls SAP B1 for new Deliveries and Invoices,
creates EdiOutboundMessage rows, and triggers ACKs for SAP-confirmed POs.

Three entry points (all called by scheduler jobs):

  trigger_acks_for_confirmed_pos(queue)
    Find SAP_CONFIRMED POs that have no PO_ACK_855 outbound message yet.
    Build the ACK payload and enqueue send_outbound_job for each.

  poll_b1_deliveries(queue)
    Query B1 for Delivery Notes linked to our Sales Orders.
    For each new delivery: create EdiAdvanceShipNotice + EdiOutboundMessage(ASN_856).

  poll_b1_invoices(queue)
    Query B1 for A/R Invoices linked to our Delivery Notes.
    For each new invoice: create EdiInvoice + EdiOutboundMessage(INVOICE_810).

Polling approach:
  We know which POs are SAP_CONFIRMED (b1_sales_order_doc_entry is set).
  B1 query for deliveries:  GET /b1s/v1/DeliveryNotes?$filter=BaseEntry eq {DocEntry} and BaseType eq 17
  B1 query for invoices:    GET /b1s/v1/Invoices?$filter=BaseEntry eq {delivery_doc_entry} and BaseType eq 15
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from rq import Queue

log = structlog.get_logger(__name__)

# B1 object type codes
_B1_OBJ_SALES_ORDER = 17
_B1_OBJ_DELIVERY_NOTE = 15


@dataclass
class OutboundTriggerResult:
    acks_created: int = 0
    asns_created: int = 0
    invoices_created: int = 0
    errors: list[str] = field(default_factory=list)


# ── ACK trigger ───────────────────────────────────────────────────────────────

def trigger_acks_for_confirmed_pos(queue: Queue) -> int:
    """
    Find all SAP_CONFIRMED POs that have no ACK outbound message.
    Create a PENDING PO_ACK_855 EdiOutboundMessage for each and enqueue a send job.
    Returns the number of ACKs created.
    """
    from sqlalchemy import select

    from app.db import SyncSessionLocal
    from app.models._enums import EdiDocType, PoStatus
    from app.models.edi_po import EdiPurchaseOrder
    from app.models.master_data import TradingPartner
    from app.models.outbound import EdiOutboundMessage
    from app.workers.jobs import send_outbound_job

    created = 0

    with SyncSessionLocal() as session:
        # SAP_CONFIRMED POs with no ACK yet
        confirmed_ids: list[Any] = session.execute(
            select(EdiPurchaseOrder.id).where(
                EdiPurchaseOrder.po_status == PoStatus.SAP_CONFIRMED,
                EdiPurchaseOrder.deleted_at.is_(None),
                ~EdiPurchaseOrder.id.in_(
                    select(EdiOutboundMessage.po_id).where(
                        EdiOutboundMessage.doc_type == EdiDocType.PO_ACK_855,
                    )
                ),
            )
        ).scalars().all()

        for po_id in confirmed_ids:
            po = session.get(EdiPurchaseOrder, po_id)
            if not po:
                continue
            partner = session.get(TradingPartner, po.trading_partner_id)
            if not partner:
                continue

            payload = _build_ack_payload(po, partner)
            msg = EdiOutboundMessage(
                po_id=po.id,
                trading_partner_id=partner.id,
                doc_type=EdiDocType.PO_ACK_855,
                payload=payload,
                channel=partner.source_channel.value,
                status="PENDING",
            )
            session.add(msg)
            session.flush()  # get msg.id before enqueue

            queue.enqueue(
                send_outbound_job,
                str(msg.id),
                job_timeout=120,
                result_ttl=3600,
                failure_ttl=86400,
            )
            created += 1

        session.commit()

    if created:
        log.info("b1_outbound.acks_created", count=created)
    return created


# ── B1 delivery polling ───────────────────────────────────────────────────────

def poll_b1_deliveries(queue: Queue) -> int:
    """
    Poll B1 for Delivery Notes linked to our confirmed Sales Orders.
    Create EdiAdvanceShipNotice + EdiOutboundMessage(ASN_856) for each new one.
    Returns number of ASNs created.
    """
    from sqlalchemy import select

    from app.db import SyncSessionLocal
    from app.models._enums import EdiDocType, PoStatus
    from app.models.asn import EdiAdvanceShipNotice
    from app.models.edi_po import EdiPoLineItem, EdiPurchaseOrder
    from app.models.master_data import SellerEntity, TradingPartner
    from app.models.outbound import EdiOutboundMessage
    from app.sap_b1.client import get_b1_client
    from app.workers.jobs import send_outbound_job

    created = 0

    with SyncSessionLocal() as session:
        # Load all SAP_CONFIRMED POs with a B1 DocEntry
        confirmed_pos: list[EdiPurchaseOrder] = session.execute(
            select(EdiPurchaseOrder).where(
                EdiPurchaseOrder.po_status == PoStatus.SAP_CONFIRMED,
                EdiPurchaseOrder.b1_sales_order_doc_entry.isnot(None),
                EdiPurchaseOrder.deleted_at.is_(None),
            )
        ).scalars().all()

        if not confirmed_pos:
            return 0

        # Already-processed B1 delivery entries
        existing_entries: set[int] = set(
            session.execute(
                select(EdiAdvanceShipNotice.b1_delivery_doc_entry).where(
                    EdiAdvanceShipNotice.b1_delivery_doc_entry.isnot(None)
                )
            ).scalars().all()
        )

        seller = session.execute(
            select(SellerEntity).where(SellerEntity.deleted_at.is_(None)).limit(1)
        ).scalar_one_or_none()

        client = get_b1_client()

        for po in confirmed_pos:
            try:
                deliveries = client.query(
                    entity="DeliveryNotes",
                    filter_expr=f"BaseEntry eq {po.b1_sales_order_doc_entry} and BaseType eq {_B1_OBJ_SALES_ORDER}",
                    select="DocEntry,DocNum,DocDate,DocumentLines",
                )
            except Exception as exc:
                log.warning(
                    "b1_outbound.delivery_poll_error",
                    po_id=str(po.id),
                    error=str(exc),
                )
                continue

            partner = session.get(TradingPartner, po.trading_partner_id)
            if not partner:
                continue

            lines = session.execute(
                select(EdiPoLineItem).where(EdiPoLineItem.po_id == po.id)
            ).scalars().all()

            for delivery in deliveries:
                doc_entry = delivery.get("DocEntry")
                if doc_entry is None or int(doc_entry) in existing_entries:
                    continue

                # Create ASN record
                asn_number = f"{partner.code}-ASN-{delivery.get('DocNum', doc_entry)}"
                ship_date_str = delivery.get("DocDate")
                try:
                    ship_date = datetime.strptime(ship_date_str, "%Y-%m-%d").date() if ship_date_str else None
                except ValueError:
                    ship_date = None

                asn = EdiAdvanceShipNotice(
                    po_id=po.id,
                    trading_partner_id=partner.id,
                    asn_number=asn_number,
                    shipment_date=ship_date,
                    b1_delivery_doc_entry=int(doc_entry),
                    b1_delivery_doc_num=delivery.get("DocNum"),
                    status="DRAFT",
                )
                session.add(asn)
                session.flush()

                # Build partner-specific payload
                payload = _build_asn_payload(
                    po=po,
                    lines=list(lines),
                    asn=asn,
                    delivery=delivery,
                    partner=partner,
                    seller=seller,
                )

                msg = EdiOutboundMessage(
                    po_id=po.id,
                    trading_partner_id=partner.id,
                    doc_type=EdiDocType.ASN_856,
                    payload=payload,
                    channel=partner.source_channel.value,
                    status="PENDING",
                )
                session.add(msg)
                session.flush()

                queue.enqueue(
                    send_outbound_job,
                    str(msg.id),
                    job_timeout=120,
                    result_ttl=3600,
                    failure_ttl=86400,
                )
                existing_entries.add(int(doc_entry))
                created += 1

        session.commit()

    if created:
        log.info("b1_outbound.asns_created", count=created)
    return created


# ── B1 invoice polling ────────────────────────────────────────────────────────

def poll_b1_invoices(queue: Queue) -> int:
    """
    Poll B1 for A/R Invoices linked to our Delivery Notes.
    Create EdiInvoice + EdiOutboundMessage(INVOICE_810) for each new one.
    Returns number of invoice notifications created.
    """
    from sqlalchemy import select

    from app.db import SyncSessionLocal
    from app.models._enums import EdiDocType
    from app.models.asn import EdiAdvanceShipNotice
    from app.models.edi_po import EdiPurchaseOrder
    from app.models.invoice import EdiInvoice
    from app.models.master_data import TradingPartner
    from app.models.outbound import EdiOutboundMessage
    from app.sap_b1.client import get_b1_client
    from app.workers.jobs import send_outbound_job

    created = 0

    with SyncSessionLocal() as session:
        # Load ASNs that have a B1 delivery entry but no invoice yet
        asns: list[EdiAdvanceShipNotice] = session.execute(
            select(EdiAdvanceShipNotice).where(
                EdiAdvanceShipNotice.b1_delivery_doc_entry.isnot(None),
                ~EdiAdvanceShipNotice.id.in_(
                    select(EdiInvoice.asn_id).where(EdiInvoice.asn_id.isnot(None))
                ),
            )
        ).scalars().all()

        if not asns:
            return 0

        client = get_b1_client()

        for asn in asns:
            po = session.get(EdiPurchaseOrder, asn.po_id)
            partner = session.get(TradingPartner, asn.trading_partner_id)
            if not po or not partner:
                continue

            try:
                invoices = client.query(
                    entity="Invoices",
                    filter_expr=(
                        f"BaseEntry eq {asn.b1_delivery_doc_entry}"
                        f" and BaseType eq {_B1_OBJ_DELIVERY_NOTE}"
                    ),
                    select="DocEntry,DocNum,DocDate,DocTotal,U_IRN",
                )
            except Exception as exc:
                log.warning(
                    "b1_outbound.invoice_poll_error",
                    asn_id=str(asn.id),
                    error=str(exc),
                )
                continue

            for inv_data in invoices:
                doc_entry = inv_data.get("DocEntry")
                if doc_entry is None:
                    continue

                inv_number = f"{partner.code}-INV-{inv_data.get('DocNum', doc_entry)}"
                inv_date_str = inv_data.get("DocDate")
                try:
                    inv_date = datetime.strptime(inv_date_str, "%Y-%m-%d").date() if inv_date_str else datetime.now(UTC).date()
                except ValueError:
                    inv_date = datetime.now(UTC).date()

                invoice = EdiInvoice(
                    po_id=po.id,
                    asn_id=asn.id,
                    trading_partner_id=partner.id,
                    invoice_number=inv_number,
                    invoice_date=inv_date,
                    b1_invoice_doc_entry=int(doc_entry),
                    b1_invoice_doc_num=inv_data.get("DocNum"),
                    irn=inv_data.get("U_IRN"),
                    grand_total=inv_data.get("DocTotal"),
                    status="DRAFT",
                )
                session.add(invoice)
                session.flush()

                payload = _build_invoice_notification(po, invoice, partner)
                msg = EdiOutboundMessage(
                    po_id=po.id,
                    trading_partner_id=partner.id,
                    doc_type=EdiDocType.INVOICE_810,
                    payload=payload,
                    channel=partner.source_channel.value,
                    status="PENDING",
                )
                session.add(msg)
                session.flush()

                queue.enqueue(
                    send_outbound_job,
                    str(msg.id),
                    job_timeout=120,
                    result_ttl=3600,
                    failure_ttl=86400,
                )
                created += 1

        session.commit()

    if created:
        log.info("b1_outbound.invoices_created", count=created)
    return created


# ── Retry pending outbound messages ──────────────────────────────────────────

def enqueue_due_retries(queue: Queue) -> int:
    """
    Find PENDING outbound messages whose next_retry_at is past due and enqueue them.
    Returns count enqueued.
    """
    from sqlalchemy import select

    from app.db import SyncSessionLocal
    from app.models.outbound import EdiOutboundMessage
    from app.workers.jobs import send_outbound_job

    now = datetime.now(UTC)
    enqueued = 0

    with SyncSessionLocal() as session:
        due: list[Any] = session.execute(
            select(EdiOutboundMessage.id).where(
                EdiOutboundMessage.status == "PENDING",
                EdiOutboundMessage.next_retry_at.isnot(None),
                EdiOutboundMessage.next_retry_at <= now,
            )
        ).scalars().all()

        for msg_id in due:
            queue.enqueue(
                send_outbound_job,
                str(msg_id),
                job_timeout=120,
                result_ttl=3600,
                failure_ttl=86400,
            )
            enqueued += 1

    if enqueued:
        log.info("b1_outbound.retries_enqueued", count=enqueued)
    return enqueued


# ── Payload builders ──────────────────────────────────────────────────────────

def _build_ack_payload(po: Any, partner: Any) -> dict[str, Any]:
    """Build Blinkit-style PO_ACK_855 payload (works for email channel too — body text)."""
    payload: dict[str, Any] = {
        "po_number": po.buyer_po_number,
        "status": "PROCESSING",
    }
    # For email-based partners, also add readable body
    from app.models._enums import SourceChannel
    if partner.source_channel == SourceChannel.EMAIL:
        payload.update({
            "to": _partner_email(partner),
            "subject": f"PO Acknowledgement — {po.buyer_po_number}",
            "body_text": (
                f"Dear {partner.name},\n\n"
                f"We have received your Purchase Order {po.buyer_po_number} "
                f"and it is currently being processed.\n\n"
                f"Thank you,\nLet's Try Foods"
            ),
        })
    return payload


def _build_asn_payload(
    po: Any,
    lines: list[Any],
    asn: Any,
    delivery: dict[str, Any],
    partner: Any,
    seller: Any,
) -> dict[str, Any]:
    """Dispatch to partner-specific ASN builder."""
    from app.models._enums import SourceChannel

    if partner.code == "BLINKIT":
        return _build_blinkit_asn(po, lines, asn, delivery, partner, seller)
    if partner.code == "ZEPTO":
        return _build_zepto_asn(po, lines, asn, delivery)
    if partner.source_channel == SourceChannel.EMAIL:
        return _build_email_asn(po, asn, partner, seller)

    # Generic fallback
    return {
        "po_number": po.buyer_po_number,
        "asn_number": asn.asn_number,
        "shipment_date": str(asn.shipment_date) if asn.shipment_date else None,
    }


def _build_blinkit_asn(
    po: Any,
    lines: list[Any],
    asn: Any,
    delivery: dict[str, Any],
    partner: Any,
    seller: Any,
) -> dict[str, Any]:
    """
    Build Blinkit ASN JSON payload.
    Re-implemented from _archive/backend_old/app/services/blinkit.py create_asn docstring.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    delivery_lines: list[dict[str, Any]] = delivery.get("DocumentLines") or []
    shipped_qty_map: dict[str, float] = {
        str(dl.get("ItemCode", "")): float(dl.get("Quantity", 0))
        for dl in delivery_lines
    }

    items: list[dict[str, Any]] = []
    for line in lines:
        shipped = shipped_qty_map.get(line.sap_material_no or "", float(line.ordered_qty or 0))
        cgst = float(line.cgst_rate or 0)
        sgst = float(line.sgst_rate or 0)
        igst = float(line.igst_rate or 0)
        items.append({
            "item_id": _blinkit_item_id(line, po),
            "sku_code": line.buyer_sku,
            "batch_number": "",
            "sku_description": line.buyer_sku_description or line.buyer_sku,
            "upc": "",
            "quantity": round(shipped, 4),
            "mrp": round(float(line.unit_price or 0) * 1.18, 2),  # estimate; ops should update
            "unit_basic_price": round(float(line.unit_price or 0), 6),
            "unit_landing_price": round(float(line.unit_price or 0), 6),
            "expiry_date": today,
            "uom": line.buyer_uom or "PC",
            "tax_distribution": {"cgst": cgst, "sgst": sgst, "igst": igst},
        })

    seller_name = getattr(seller, "name", "Let's Try Foods") if seller else "Let's Try Foods"
    seller_gstin = getattr(seller, "gstin", "") if seller else ""

    return {
        "po_number": po.buyer_po_number,
        "invoice_number": asn.asn_number,
        "invoice_date": today,
        "delivery_date": str(asn.shipment_date) if asn.shipment_date else today,
        "supplier_details": {
            "name": seller_name,
            "gstin": seller_gstin,
            "supplier_address": {},
        },
        "buyer_details": {"gstin": po.buyer_gstin or ""},
        "shipment_details": {"delivery_type": "MTO"},
        "items": items,
    }


def _build_zepto_asn(
    po: Any,
    lines: list[Any],
    asn: Any,
    delivery: dict[str, Any],
) -> dict[str, Any]:
    """
    Build Zepto ASN (Silk Route) JSON payload.
    Re-implemented from _archive/backend_old/app/services/zepto.py create_asn docstring.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    delivery_lines: list[dict[str, Any]] = delivery.get("DocumentLines") or []
    shipped_qty_map: dict[str, float] = {
        str(dl.get("ItemCode", "")): float(dl.get("Quantity", 0))
        for dl in delivery_lines
    }

    line_items: list[dict[str, Any]] = []
    for line in lines:
        shipped = shipped_qty_map.get(line.sap_material_no or "", float(line.ordered_qty or 0))
        line_items.append({
            "poLineItemId": str(line.line_number),
            "quantity": {"amount": round(shipped, 4), "uom": line.buyer_uom or "PC"},
            "batchDetails": {"batchNumber": "", "expiryDate": today},
        })

    return {
        "purchaseOrderDetails": {"purchaseOrderNumber": po.buyer_po_number},
        "invoiceNumber": asn.asn_number,
        "invoiceDate": today,
        "lineItems": line_items,
    }


def _build_email_asn(
    po: Any,
    asn: Any,
    partner: Any,
    seller: Any,
) -> dict[str, Any]:
    """Build plain-text ASN notification for email-based partners."""
    seller_name = getattr(seller, "name", "Let's Try Foods") if seller else "Let's Try Foods"
    return {
        "to": _partner_email(partner),
        "subject": f"Advance Shipment Notice — PO {po.buyer_po_number} / {asn.asn_number}",
        "body_text": (
            f"Dear {partner.name},\n\n"
            f"We have dispatched the shipment for your PO {po.buyer_po_number}.\n"
            f"ASN Number: {asn.asn_number}\n"
            f"Shipment Date: {asn.shipment_date}\n\n"
            f"Thank you,\n{seller_name}"
        ),
    }


def _build_invoice_notification(
    po: Any,
    invoice: Any,
    partner: Any,
) -> dict[str, Any]:
    """Build invoice notification payload."""
    from app.models._enums import SourceChannel
    payload: dict[str, Any] = {
        "po_number": po.buyer_po_number,
        "invoice_number": invoice.invoice_number,
        "invoice_date": str(invoice.invoice_date),
        "grand_total": float(invoice.grand_total or 0),
        "irn": invoice.irn,
    }
    if partner.source_channel == SourceChannel.EMAIL:
        payload.update({
            "to": _partner_email(partner),
            "subject": f"Invoice — {invoice.invoice_number} for PO {po.buyer_po_number}",
            "body_text": (
                f"Dear {partner.name},\n\n"
                f"Please find the invoice details for PO {po.buyer_po_number}.\n"
                f"Invoice Number: {invoice.invoice_number}\n"
                f"Invoice Date: {invoice.invoice_date}\n"
                f"Total Amount: ₹{invoice.grand_total or 0:,.2f}\n"
                f"IRN: {invoice.irn or 'Pending'}\n\n"
                f"Thank you,\nLet's Try Foods"
            ),
        })
    return payload


def _blinkit_item_id(line: Any, po: Any) -> str:
    """
    Return Blinkit's internal item_id for a line.
    Looks in SkuMapping.notes["blinkit_item_id"] first, falls back to buyer_sku.
    The item_id is Blinkit's own ID from the original PO webhook
    (stored in raw_message.payload.details.item_data[*].item_id).
    """
    # Check SkuMapping.notes for blinkit_item_id
    if hasattr(line, "sku_mapping") and line.sku_mapping:
        import contextlib
        import json
        with contextlib.suppress(Exception):
            notes = line.sku_mapping.notes
            if isinstance(notes, str):
                notes = json.loads(notes)
            if isinstance(notes, dict) and notes.get("blinkit_item_id"):
                return str(notes["blinkit_item_id"])
    return line.buyer_sku


def _partner_email(partner: Any) -> str:
    """Extract ops email from partner.api_config["ops_email"] or return empty."""
    import contextlib
    with contextlib.suppress(Exception):
        cfg = partner.api_config or {}
        return str(cfg.get("ops_email", ""))
    return ""
