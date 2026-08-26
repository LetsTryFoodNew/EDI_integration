"""
Invoice-from-SAP workflow — SAP pushes A/R Invoices, we store them and raise the ASN.

Direction of travel (decided 2026-08-10, supersedes the polling design in CLAUDE.md
Phase 7): SAP posts invoices to us rather than us polling B1 for them. Same reasoning
as master data — Service Layer sessions are licensed and capped (CLAUDE.md section 7),
so having SAP push removes a recurring read against a scarce resource and gets the
invoice to us in seconds instead of on the next poll tick.

`poll_b1_invoices` in b1_to_outbound.py stays alive on a slow cadence as a backup.
Both paths converge here and both are idempotent on invoice_number, so an invoice that
arrives twice is updated, never duplicated.

Flow per invoice:

    resolve PO  ->  upsert EdiInvoice + lines  ->  validate  ->  pass: build ASN,
                                                                       enqueue send
                                                                 fail: raise issues,
                                                                       hold dispatch

The hold-on-failure behaviour is deliberate. An ASN is outward-facing: once Zepto or
Swiggy has it, retracting means a cancel-and-recreate cycle with the partner. So a
mismatched invoice parks in the exceptions queue instead of being sent and regretted.
Everything that passes goes out immediately, which is what keeps the SLA.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import structlog

from app.models._enums import EdiDocType, ValidationStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.schemas.api import InvoicePush

log = structlog.get_logger(__name__)

# Tolerance when reconciling the sum of line totals against the invoice grand total.
# B1 rounds centrally and puts the residue in round_off, so an exact match is not a
# realistic expectation — but a gap wider than this means the lines and the header
# genuinely disagree and the invoice should not go out.
_TOTAL_TOLERANCE = Decimal("1.00")

_ZERO = Decimal("0")


class InvoiceResult:
    """Outcome of one pushed invoice, mapped to InvoicePushResultItem by the route."""

    def __init__(self, invoice_number: str) -> None:
        self.invoice_number = invoice_number
        self.outcome: str = "ERROR"
        self.invoice_id: uuid.UUID | None = None
        self.po_number: str | None = None
        self.asn_number: str | None = None
        self.asn_dispatched: bool = False
        self.issues: list[str] = []


def ingest_invoice(
    db: Session,
    payload: InvoicePush,
    *,
    enqueue: bool = True,
) -> InvoiceResult:
    """
    Store one SAP invoice and, if it validates, raise and dispatch its ASN.

    Commits nothing — the caller owns the transaction so a batch is all-or-nothing
    per request. Returns an InvoiceResult describing what happened.
    """
    from app.models.invoice import EdiInvoice

    result = InvoiceResult(payload.invoice_number)

    po = _resolve_po(db, payload)
    if po is None:
        result.outcome = "ERROR"
        result.issues.append(
            "No Sales Order found for this invoice. Send b1_sales_order_doc_entry, "
            "or partner_code + po_number matching a PO already pushed to SAP."
        )
        log.warning(
            "invoice.po_not_found",
            invoice_number=payload.invoice_number,
            doc_entry=payload.b1_sales_order_doc_entry,
            po_number=payload.po_number,
        )
        return result

    result.po_number = po.buyer_po_number

    from sqlalchemy import select
    existing = db.execute(
        select(EdiInvoice).where(EdiInvoice.invoice_number == payload.invoice_number)
    ).scalar_one_or_none()

    invoice = _upsert_invoice(db, po, payload, existing)
    result.invoice_id = invoice.id
    result.outcome = "UPDATED" if existing else "CREATED"

    problems = _validate(db, po, invoice, payload)
    if problems:
        result.issues = problems
        _raise_issues(db, po, invoice, problems)
        log.warning(
            "invoice.held",
            invoice_number=payload.invoice_number,
            po_number=po.buyer_po_number,
            issue_count=len(problems),
        )
        return result

    # An ASN already linked means this invoice was pushed before and dispatched;
    # re-pushing (e.g. to add an IRN) must not raise a second 856.
    if invoice.asn_id is not None:
        from app.models.asn import EdiAdvanceShipNotice
        prior = db.get(EdiAdvanceShipNotice, invoice.asn_id)
        result.asn_number = prior.asn_number if prior else None
        log.info(
            "invoice.asn_already_exists",
            invoice_number=payload.invoice_number,
            asn_number=result.asn_number,
        )
        return result

    asn = _build_asn(db, po, invoice, payload)
    invoice.asn_id = asn.id
    result.asn_number = asn.asn_number

    if enqueue:
        _queue_asn_dispatch(db, po, asn, invoice)
        result.asn_dispatched = True

    log.info(
        "invoice.accepted",
        invoice_number=payload.invoice_number,
        po_number=po.buyer_po_number,
        asn_number=asn.asn_number,
        dispatched=result.asn_dispatched,
    )
    return result


# ── PO resolution ─────────────────────────────────────────────────────────────

def _resolve_po(db: Session, payload: InvoicePush) -> Any:
    """
    Find the PO this invoice belongs to.

    DocEntry is tried first: it is B1's own immutable key for the Sales Order we
    created, so it cannot drift the way a hand-typed PO number can. The
    partner_code + po_number pair is the fallback for callers that only have the
    retailer-facing reference. Where several versions of a PO exist, the newest wins —
    an invoice is always raised against the current revision, not a superseded one.
    """
    from sqlalchemy import select

    from app.models.edi_po import EdiPurchaseOrder
    from app.models.master_data import TradingPartner

    if payload.b1_sales_order_doc_entry is not None:
        po = db.execute(
            select(EdiPurchaseOrder)
            .where(
                EdiPurchaseOrder.b1_sales_order_doc_entry == payload.b1_sales_order_doc_entry,
                EdiPurchaseOrder.deleted_at.is_(None),
            )
            .order_by(EdiPurchaseOrder.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        if po is not None:
            return po

    if payload.partner_code and payload.po_number:
        partner = db.execute(
            select(TradingPartner).where(
                TradingPartner.code == payload.partner_code.strip().upper(),
                TradingPartner.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if partner is None:
            return None
        return db.execute(
            select(EdiPurchaseOrder)
            .where(
                EdiPurchaseOrder.trading_partner_id == partner.id,
                EdiPurchaseOrder.buyer_po_number == payload.po_number.strip(),
                EdiPurchaseOrder.deleted_at.is_(None),
            )
            .order_by(EdiPurchaseOrder.version.desc())
            .limit(1)
        ).scalar_one_or_none()

    return None


# ── Persistence ───────────────────────────────────────────────────────────────

def _upsert_invoice(
    db: Session,
    po: Any,
    payload: InvoicePush,
    existing: Any,
) -> Any:
    """
    Create or update the invoice keyed on invoice_number.

    Re-pushes are expected and useful: B1 typically has no IRN at the moment the
    invoice is posted, and fills it in once the IRP responds. Lines are replaced
    wholesale rather than merged — SAP is authoritative, and a diff-merge would
    leave orphaned lines if a credit adjustment removed one.
    """
    from app.models.invoice import EdiInvoice, EdiInvoiceLineItem

    header = {
        "invoice_date": payload.invoice_date,
        "b1_invoice_doc_entry": payload.b1_invoice_doc_entry,
        "b1_invoice_doc_num": payload.b1_invoice_doc_num,
        "irn": payload.irn,
        "eway_bill_number": payload.eway_bill_number,
        "eway_bill_date": payload.eway_bill_date,
        "subtotal_amount": payload.subtotal_amount,
        "cgst_amount": payload.cgst_amount,
        "sgst_amount": payload.sgst_amount,
        "igst_amount": payload.igst_amount,
        "cess_amount": payload.cess_amount,
        "round_off": payload.round_off,
        "grand_total": payload.grand_total,
    }

    if existing is not None:
        invoice = existing
        for key, value in header.items():
            setattr(invoice, key, value)
        for line in list(invoice.line_items):
            db.delete(line)
        invoice.line_items = []
        db.flush()
    else:
        invoice = EdiInvoice(
            id=uuid.uuid4(),
            po_id=po.id,
            trading_partner_id=po.trading_partner_id,
            invoice_number=payload.invoice_number,
            status="DRAFT",
            **header,
        )
        db.add(invoice)
        db.flush()

    po_lines = _po_line_index(po)
    for item in payload.line_items:
        db.add(EdiInvoiceLineItem(
            id=uuid.uuid4(),
            invoice_id=invoice.id,
            po_line_id=_match_po_line(po_lines, item),
            b1_item_code=item.b1_item_code,
            description=item.description,
            hsn_code=item.hsn_code,
            qty=item.qty,
            uom=item.uom,
            unit_price=item.unit_price,
            taxable_amount=item.taxable_amount,
            cgst_rate=item.cgst_rate,
            cgst_amount=item.cgst_amount,
            sgst_rate=item.sgst_rate,
            sgst_amount=item.sgst_amount,
            igst_rate=item.igst_rate,
            igst_amount=item.igst_amount,
            cess_rate=item.cess_rate,
            cess_amount=item.cess_amount,
            line_total=item.line_total,
        ))
    db.flush()
    return invoice


def _po_line_index(po: Any) -> dict[str, Any]:
    """Index PO lines by line number and by buyer SKU, for matching invoice lines."""
    index: dict[str, Any] = {}
    for line in po.line_items:
        index[f"n:{line.line_number}"] = line
        if line.buyer_sku:
            index[f"s:{line.buyer_sku}"] = line
        if getattr(line, "sap_material_no", None):
            index[f"i:{line.sap_material_no}"] = line
    return index


def _match_po_line(index: dict[str, Any], item: Any) -> uuid.UUID | None:
    """
    Link an invoice line back to its PO line.

    Explicit line number wins, then buyer SKU, then the B1 item code. Returning None
    is acceptable — the line is still stored, it just cannot participate in the
    over-shipment check.
    """
    for key in (
        f"n:{item.po_line_number}" if item.po_line_number is not None else None,
        f"s:{item.buyer_sku}" if item.buyer_sku else None,
        f"i:{item.b1_item_code}",
    ):
        if key and key in index:
            return index[key].id
    return None


# ── Validation ────────────────────────────────────────────────────────────────

def _validate(db: Session, po: Any, invoice: Any, payload: InvoicePush) -> list[str]:
    """
    Checks that decide whether the ASN may go out unattended.

    Deliberately narrow: these catch the errors that would embarrass us with a
    retailer — billing for goods never ordered, or a header that disagrees with its
    own lines. Anything subtler is a human judgement call and belongs in review, not
    in a gate that silently blocks dispatch.
    """
    problems: list[str] = []

    total = _sum_line_totals(payload)
    if payload.grand_total is not None and total > _ZERO:
        gap = abs(Decimal(str(payload.grand_total)) - total)
        if gap > _TOTAL_TOLERANCE:
            problems.append(
                f"Header grand_total {payload.grand_total} does not reconcile with the "
                f"sum of line totals {total} (difference {gap}, tolerance {_TOTAL_TOLERANCE})."
            )

    problems.extend(_check_over_shipment(db, po, invoice, payload))
    return problems


def _sum_line_totals(payload: InvoicePush) -> Decimal:
    return sum(
        (Decimal(str(li.line_total)) for li in payload.line_items if li.line_total is not None),
        start=_ZERO,
    )


def _check_over_shipment(db: Session, po: Any, invoice: Any, payload: InvoicePush) -> list[str]:
    """
    Reject invoicing more than the PO ordered, counting every other invoice on this PO.

    Partial dispatch is normal — several invoices against one PO is the whole reason
    edi_invoices links by po_id. What is not normal is the running total exceeding the
    ordered quantity, which means either SAP double-posted or the wrong PO was matched.
    Either way it must not reach the retailer.
    """
    from sqlalchemy import select

    from app.models.invoice import EdiInvoice, EdiInvoiceLineItem

    ordered: dict[uuid.UUID, Decimal] = {
        line.id: Decimal(str(line.ordered_qty or 0)) for line in po.line_items
    }
    if not ordered:
        return []

    already: dict[uuid.UUID, Decimal] = dict.fromkeys(ordered, _ZERO)
    prior_lines = db.execute(
        select(EdiInvoiceLineItem)
        .join(EdiInvoice, EdiInvoice.id == EdiInvoiceLineItem.invoice_id)
        .where(
            EdiInvoice.po_id == po.id,
            EdiInvoice.id != invoice.id,
            EdiInvoice.status != "CANCELLED",
        )
    ).scalars().all()
    for line in prior_lines:
        if line.po_line_id in already:
            already[line.po_line_id] += Decimal(str(line.qty or 0))

    this_invoice: dict[uuid.UUID, Decimal] = dict.fromkeys(ordered, _ZERO)
    index = _po_line_index(po)
    for item in payload.line_items:
        po_line_id = _match_po_line(index, item)
        if po_line_id in this_invoice:
            this_invoice[po_line_id] += Decimal(str(item.qty))

    problems: list[str] = []
    by_id = {line.id: line for line in po.line_items}
    for po_line_id, ordered_qty in ordered.items():
        billed = already[po_line_id] + this_invoice[po_line_id]
        if billed > ordered_qty:
            line = by_id[po_line_id]
            problems.append(
                f"Line {line.line_number} ({line.buyer_sku}): invoicing {billed} against "
                f"an ordered quantity of {ordered_qty}"
                + (f", of which {already[po_line_id]} was already invoiced" if already[po_line_id] else "")
                + "."
            )
    return problems


def _raise_issues(db: Session, po: Any, invoice: Any, problems: list[str]) -> None:
    """Park the invoice in the exceptions queue instead of dispatching it."""
    from app.models.edi_po import EdiValidationIssue

    for message in problems:
        db.add(EdiValidationIssue(
            id=uuid.uuid4(),
            po_id=po.id,
            issue_code="E200_INVOICE_HELD",
            severity="ERROR",
            message=f"Invoice {invoice.invoice_number}: {message}",
            field_path=f"invoice.{invoice.invoice_number}",
            validation_status=ValidationStatus.OPEN,
        ))
    db.flush()


# ── ASN ───────────────────────────────────────────────────────────────────────

def _build_asn(db: Session, po: Any, invoice: Any, payload: InvoicePush) -> Any:
    """
    Raise the 856 for this invoice — one ASN per invoice, so a partial dispatch is
    announced as it ships rather than waiting for the PO to be fully invoiced.

    asn_number is derived from invoice_number (which is unique) so the ASN number is
    reproducible and a re-push cannot mint a second one.
    """
    from app.models.asn import EdiAdvanceShipNotice, EdiAsnLineItem

    asn = EdiAdvanceShipNotice(
        id=uuid.uuid4(),
        po_id=po.id,
        trading_partner_id=po.trading_partner_id,
        asn_number=f"ASN-{invoice.invoice_number}",
        shipment_date=payload.shipment_date or payload.invoice_date,
        carrier=payload.carrier,
        tracking_number=payload.tracking_number,
        status="DRAFT",
    )
    db.add(asn)
    db.flush()

    index = _po_line_index(po)
    for item in payload.line_items:
        db.add(EdiAsnLineItem(
            id=uuid.uuid4(),
            asn_id=asn.id,
            po_line_id=_match_po_line(index, item),
            shipped_qty=item.qty,
            buyer_sku=item.buyer_sku,
            b1_item_code=item.b1_item_code,
            batch_number=item.batch_number,
            expiry_date=item.expiry_date,
        ))
    db.flush()
    return asn


def _queue_asn_dispatch(db: Session, po: Any, asn: Any, invoice: Any) -> None:
    """
    Hand the ASN to the outbound machinery.

    Nothing here knows or cares whether the partner is API or email — the outbound
    registry resolves that from partner code and source_channel, so Zepto goes out
    over its API and Swiggy over email without a branch in this file.

    `channel` is stamped from the partner anyway. Routing does not read it (send_outbound
    re-resolves from partner.source_channel at send time), but the column defaults to
    "API", and a Swiggy email message labelled API in the outbound tab sends whoever is
    debugging it looking down the wrong path.
    """
    from app.models.master_data import TradingPartner
    from app.models.outbound import EdiOutboundMessage

    partner = db.get(TradingPartner, po.trading_partner_id)
    channel = str(partner.source_channel) if partner is not None else "API"

    db.add(EdiOutboundMessage(
        id=uuid.uuid4(),
        po_id=po.id,
        trading_partner_id=po.trading_partner_id,
        doc_type=EdiDocType.ASN_856,
        external_reference=asn.asn_number,
        payload=_partner_asn_payload(db, po, asn, invoice, partner),
        channel=channel,
        status="PENDING",
        attempt_count=0,
        next_retry_at=datetime.now(UTC),
    ))
    db.flush()


def _partner_asn_payload(
    db: Session, po: Any, asn: Any, invoice: Any, partner: Any
) -> dict[str, Any]:
    """
    Shape the ASN for whoever is receiving it.

    Partners whose contract we hold get their exact wire format built here, at creation
    time, so what will be sent is visible in the outbound tab *before* dispatch rather
    than materialising inside the adapter. Everyone else gets the partner-neutral body
    below, which their adapter reshapes.
    """
    from sqlalchemy import select

    from app.adapters.outbound.blinkit_asn import build_blinkit_asn_payload
    from app.adapters.outbound.email_asn import build_email_asn_payload
    from app.adapters.outbound.zepto_asn import build_zepto_asn_payload
    from app.models.master_data import SellerEntity

    builders = {
        "BLINKIT": build_blinkit_asn_payload,
        "ZEPTO": build_zepto_asn_payload,
    }

    code = getattr(partner, "code", "")
    seller = db.execute(
        select(SellerEntity).where(SellerEntity.deleted_at.is_(None)).limit(1)
    ).scalar_one_or_none()

    builder = builders.get(code)
    if builder is None:
        body = _asn_payload(po, asn, invoice)
        if str(getattr(partner, "source_channel", "")) != "EMAIL":
            return body
        # An email partner needs an envelope, not a shipment body. EmailOutboundAdapter
        # reads `to`, `subject` and `body_text`, so a bare canonical body was going out
        # with an empty To header and a subject of "(no subject)" — delivered nowhere,
        # recorded as SENT.
        payload, warnings = build_email_asn_payload(po, asn, invoice, partner, seller, body)
    else:
        payload, warnings = builder(db, po, asn, invoice, partner, seller)

    for w in warnings:
        log.warning(
            "asn.payload_warning", partner=code, po=po.buyer_po_number, warning=w
        )
    return payload


def _asn_payload(po: Any, asn: Any, invoice: Any) -> dict[str, Any]:
    """
    Partner-neutral ASN body. Each outbound adapter reshapes this into whatever its
    partner expects, so this stays a plain description of the shipment.
    """
    return {
        "asn_number": asn.asn_number,
        "po_number": po.buyer_po_number,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "shipment_date": asn.shipment_date.isoformat() if asn.shipment_date else None,
        "carrier": asn.carrier,
        "tracking_number": asn.tracking_number,
        "irn": invoice.irn,
        "eway_bill_number": invoice.eway_bill_number,
        "grand_total": str(invoice.grand_total) if invoice.grand_total is not None else None,
        "line_items": [
            {
                "buyer_sku": line.buyer_sku,
                "b1_item_code": line.b1_item_code,
                "shipped_qty": str(line.shipped_qty),
                "batch_number": line.batch_number,
                "expiry_date": line.expiry_date.isoformat() if line.expiry_date else None,
            }
            for line in asn.line_items
        ],
    }
