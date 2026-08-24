"""
Invoice routes — EDI 810.

  POST /api/invoices          — SAP pushes A/R Invoices raised against our Sales Orders
  GET  /api/invoices          — list, filterable by PO / partner / status
  GET  /api/pos/{po_id}/invoices — invoices for one PO (the PO-detail Invoices tab)

Direction of travel matches master data: SAP posts, we store, we never call B1 to read
back. See app/workflows/invoice_from_sap.py for why, and for the validation gate that
decides whether an invoice's ASN dispatches unattended.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.schemas.api import (
    InvoiceAsnActionResponse,
    InvoiceAsnCancelResponse,
    InvoicePushRequest,
    InvoicePushResult,
    InvoicePushResultItem,
    InvoiceResponse,
    PaginatedResponse,
    UserResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Invoices"])


@router.post("/invoices", response_model=InvoicePushResult)
def push_invoices(
    body: InvoicePushRequest,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> InvoicePushResult:
    """
    Receive one or more A/R Invoices from SAP.

    Idempotent on `invoice_number`: re-pushing updates the existing record rather than
    duplicating it, which is what lets SAP send the invoice immediately and follow up
    with the IRN once the IRP responds.

    Each invoice reports its own outcome in `results`, so one bad row in a batch of
    fifty is actionable without guessing which one failed. An invoice that fails
    validation is still stored — it is held from dispatch and raised in the exceptions
    queue, never silently dropped.
    """
    from app.models.audit_log import AuditLog
    from app.workflows.invoice_from_sap import ingest_invoice

    created = updated = skipped = 0
    errors: list[str] = []
    results: list[InvoicePushResultItem] = []

    for payload in body.invoices:
        outcome = ingest_invoice(db, payload)

        if outcome.outcome == "CREATED":
            created += 1
        elif outcome.outcome == "UPDATED":
            updated += 1
        else:
            skipped += 1
            errors.extend(f"{payload.invoice_number}: {issue}" for issue in outcome.issues)

        # A stored-but-held invoice counts as created/updated, yet its problems still
        # belong in `errors` — otherwise a caller checking only the counters would
        # believe the ASN went out.
        if outcome.outcome in ("CREATED", "UPDATED") and outcome.issues:
            errors.extend(f"{payload.invoice_number}: {issue}" for issue in outcome.issues)

        results.append(InvoicePushResultItem(
            invoice_number=outcome.invoice_number,
            outcome=outcome.outcome,
            invoice_id=outcome.invoice_id,
            po_number=outcome.po_number,
            asn_number=outcome.asn_number,
            asn_dispatched=outcome.asn_dispatched,
            issues=outcome.issues,
        ))

    db.add(AuditLog(
        user_email=current_user.email,
        action="push_invoices",
        entity_type="EdiInvoice",
        payload={"created": created, "updated": updated, "skipped": skipped},
    ))
    db.commit()

    log.info(
        "invoices.pushed",
        created=created, updated=updated, skipped=skipped,
        dispatched=sum(1 for r in results if r.asn_dispatched),
    )
    return InvoicePushResult(
        created=created, updated=updated, skipped=skipped,
        errors=errors, results=results,
    )


@router.get("/invoices", response_model=PaginatedResponse[InvoiceResponse])
def list_invoices(
    po_id: uuid.UUID | None = Query(None),
    partner_code: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[InvoiceResponse]:
    """List invoices, newest first."""
    from sqlalchemy import func, select

    from app.models.invoice import EdiInvoice
    from app.models.master_data import TradingPartner

    stmt = select(EdiInvoice)
    count_stmt = select(func.count()).select_from(EdiInvoice)

    if po_id is not None:
        stmt = stmt.where(EdiInvoice.po_id == po_id)
        count_stmt = count_stmt.where(EdiInvoice.po_id == po_id)
    if status:
        stmt = stmt.where(EdiInvoice.status == status.upper())
        count_stmt = count_stmt.where(EdiInvoice.status == status.upper())
    if partner_code:
        partner = db.execute(
            select(TradingPartner).where(TradingPartner.code == partner_code.strip().upper())
        ).scalar_one_or_none()
        if partner is None:
            return PaginatedResponse(items=[], total=0, limit=limit, offset=offset)
        stmt = stmt.where(EdiInvoice.trading_partner_id == partner.id)
        count_stmt = count_stmt.where(EdiInvoice.trading_partner_id == partner.id)

    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(
        stmt.order_by(EdiInvoice.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()

    return PaginatedResponse(
        items=[_to_response(db, row) for row in rows],
        total=total, limit=limit, offset=offset,
    )


@router.get("/pos/{po_id}/invoices", response_model=list[InvoiceResponse])
def list_po_invoices(
    po_id: uuid.UUID,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> list[InvoiceResponse]:
    """
    Invoices raised against one PO — backs the Invoices tab on PO detail.

    Returns a bare list rather than a paginated envelope: a PO has a handful of
    invoices, not a page's worth, and the tab shows all of them at once.
    """
    from sqlalchemy import select

    from app.models.edi_po import EdiPurchaseOrder
    from app.models.invoice import EdiInvoice

    if db.get(EdiPurchaseOrder, po_id) is None:
        raise HTTPException(status_code=404, detail=f"PO '{po_id}' not found")

    rows = db.execute(
        select(EdiInvoice)
        .where(EdiInvoice.po_id == po_id)
        .order_by(EdiInvoice.invoice_date.desc(), EdiInvoice.created_at.desc())
    ).scalars().all()
    return [_to_response(db, row) for row in rows]


@router.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> InvoiceResponse:
    """One invoice with its line items, ASN and dispatch state."""
    from app.models.invoice import EdiInvoice

    invoice = db.get(EdiInvoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"Invoice '{invoice_id}' not found")
    return _to_response(db, invoice)


@router.get("/invoices/{invoice_id}/pdf")
def download_invoice_pdf(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> Response:
    """
    Render the invoice as a GST tax invoice PDF.

    Generated on demand rather than stored: the invoice can be re-pushed by SAP (to add
    an IRN, typically), and a cached PDF would then show stale references with no
    obvious signal that it had gone out of date.
    """
    from app.models.invoice import EdiInvoice
    from app.utils.invoice_pdf import render_invoice_pdf

    invoice = db.get(EdiInvoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"Invoice '{invoice_id}' not found")

    pdf = render_invoice_pdf(db, invoice)
    # Slashes are ordinary in Indian invoice numbers (INV/2026/00871) but would be read
    # as path separators by a browser saving the file.
    safe_name = invoice.invoice_number.replace("/", "-").replace("\\", "-")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="invoice-{safe_name}.pdf"'},
    )


@router.post("/invoices/{invoice_id}/cancel-asn", response_model=InvoiceAsnCancelResponse)
def cancel_invoice_asn(
    invoice_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> InvoiceAsnCancelResponse:
    """
    Withdraw this invoice's ASN from the partner that accepted it.

    Only Zepto exposes a cancellation endpoint (contract v12 §2.b). Blinkit's ASN Sync
    contract defines creation only, so this returns 400 with that reason rather than
    marking anything cancelled — an ASN cancelled in our database while the retailer
    still holds it is worse than one that was never cancelled, because their warehouse
    is still expecting the truck and nobody here is watching for it any more.

    Nothing local changes until the partner confirms. Afterwards the invoice needs a
    **new invoice number** to re-ship: Zepto has no update API and rejects a re-used
    invoiceNumber as a duplicate.
    """
    from app.models.asn import EdiAdvanceShipNotice
    from app.models.audit_log import AuditLog
    from app.models.invoice import EdiInvoice
    from app.workflows.cancel_asn import cancel_asn as run_cancel

    invoice = db.get(EdiInvoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"Invoice '{invoice_id}' not found")
    if invoice.asn_id is None:
        raise HTTPException(status_code=400, detail="This invoice has no ASN to cancel.")

    asn = db.get(EdiAdvanceShipNotice, invoice.asn_id)
    if asn is None:
        raise HTTPException(status_code=404, detail="ASN row no longer exists.")

    result = run_cancel(db, asn.id, cancelled_by=current_user.email)

    if not result.success:
        db.rollback()
        raise HTTPException(status_code=400, detail=result.error or "Cancellation failed")

    db.add(AuditLog(
        user_email=current_user.email,
        action="cancel_invoice_asn",
        entity_type="EdiInvoice",
        entity_id=str(invoice_id),
        payload={
            "invoice_number": invoice.invoice_number,
            "asn_number": result.asn_number,
            "partner_reference": result.partner_reference,
        },
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()

    message = (
        "ASN was already cancelled."
        if result.already_cancelled
        else f"ASN cancelled with {result.partner_code}. Re-ship under a new invoice "
             f"number — the partner rejects a re-used one as a duplicate."
    )
    return InvoiceAsnCancelResponse(
        invoice_id=invoice_id,
        asn_number=result.asn_number,
        cancelled=True,
        partner_code=result.partner_code,
        partner_reference=result.partner_reference,
        already_cancelled=result.already_cancelled,
        message=message,
    )


@router.post("/invoices/{invoice_id}/send-asn", response_model=InvoiceAsnActionResponse)
def send_invoice_asn(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> InvoiceAsnActionResponse:
    """
    Manually raise and/or re-queue this invoice's ASN.

    Two jobs, because ops hit this from two different dead ends:

      - The invoice was **held** by validation and has no ASN. This builds it and sends
        it, which is an intentional override — the operator has looked at the numbers
        and accepted them. The override is recorded in the audit log, and the held
        validation issues are resolved so the exception queue stops showing work that
        someone has already dealt with.

      - The ASN exists but its dispatch **failed** (partner API down, mail bounce).
        This resets the retry counter and re-queues it rather than waiting out the
        remaining backoff.

    Refuses when the ASN is already sent and acknowledged: re-sending a delivered 856
    creates a duplicate shipment notice on the retailer's side, which is worse than the
    problem it would be trying to fix.
    """
    from sqlalchemy import select

    from app.models._enums import ValidationStatus
    from app.models.asn import EdiAdvanceShipNotice
    from app.models.audit_log import AuditLog
    from app.models.edi_po import EdiPurchaseOrder, EdiValidationIssue
    from app.models.invoice import EdiInvoice
    from app.models.outbound import EdiOutboundMessage
    from app.workflows.invoice_from_sap import _build_asn, _queue_asn_dispatch

    invoice = db.get(EdiInvoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"Invoice '{invoice_id}' not found")

    po = db.get(EdiPurchaseOrder, invoice.po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="PO for this invoice no longer exists")

    overridden = False

    if invoice.asn_id is None:
        # Held by validation — the operator is choosing to send anyway.
        asn = _build_asn(db, po, invoice, _payload_from_stored(invoice))
        invoice.asn_id = asn.id
        _queue_asn_dispatch(db, po, asn, invoice)
        overridden = True

        for issue in db.execute(
            select(EdiValidationIssue).where(
                EdiValidationIssue.po_id == po.id,
                EdiValidationIssue.issue_code == "E200_INVOICE_HELD",
                EdiValidationIssue.validation_status == ValidationStatus.OPEN,
                EdiValidationIssue.message.like(f"%{invoice.invoice_number}%"),
            )
        ).scalars().all():
            issue.validation_status = ValidationStatus.RESOLVED
            issue.resolved_by = current_user.email
            issue.resolved_at = datetime.now(UTC)
        message = "Validation hold overridden — ASN raised and queued."
    else:
        asn = db.get(EdiAdvanceShipNotice, invoice.asn_id)
        outbound = db.execute(
            select(EdiOutboundMessage)
            .where(EdiOutboundMessage.external_reference == asn.asn_number)
            .order_by(EdiOutboundMessage.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if outbound is not None and outbound.ack_received_at is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"ASN {asn.asn_number} was already delivered and acknowledged. "
                    "Re-sending would raise a duplicate shipment notice with the partner."
                ),
            )

        if outbound is None:
            _queue_asn_dispatch(db, po, asn, invoice)
            message = "ASN re-queued for dispatch."
        else:
            outbound.status = "PENDING"
            outbound.attempt_count = 0
            outbound.next_retry_at = datetime.now(UTC)
            outbound.error_message = None
            message = "ASN dispatch retried — retry counter reset."

    db.add(AuditLog(
        user_email=current_user.email,
        action="send_invoice_asn",
        entity_type="EdiInvoice",
        entity_id=str(invoice.id),
        payload={
            "invoice_number": invoice.invoice_number,
            "asn_number": asn.asn_number,
            "validation_override": overridden,
        },
    ))
    db.commit()

    log.info(
        "invoice.asn_manual_send",
        invoice_number=invoice.invoice_number,
        asn_number=asn.asn_number,
        override=overridden,
        user=current_user.email,
    )
    return InvoiceAsnActionResponse(
        invoice_id=invoice.id,
        asn_number=asn.asn_number,
        queued=True,
        validation_override=overridden,
        message=message,
    )


def _payload_from_stored(invoice: object) -> object:
    """
    Rebuild the InvoicePush shape from stored rows so _build_asn can be reused.

    The original SAP payload is not retained — only the normalised invoice — so the ASN
    is reconstructed from what we hold. That is the authoritative record anyway: it is
    what the PDF and the retailer-facing documents are already built from.
    """
    from app.schemas.api import InvoiceLineItemPush, InvoicePush

    return InvoicePush(
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        b1_invoice_doc_entry=invoice.b1_invoice_doc_entry,
        b1_invoice_doc_num=invoice.b1_invoice_doc_num,
        irn=invoice.irn,
        eway_bill_number=invoice.eway_bill_number,
        grand_total=invoice.grand_total,
        line_items=[
            InvoiceLineItemPush(
                b1_item_code=li.b1_item_code or "UNKNOWN",
                qty=li.qty,
                description=li.description,
                hsn_code=li.hsn_code,
                uom=li.uom,
                unit_price=li.unit_price,
                taxable_amount=li.taxable_amount,
                line_total=li.line_total,
            )
            for li in invoice.line_items
        ],
    )


def _to_response(db: Session, invoice: object) -> InvoiceResponse:
    """
    Build the wire shape, folding in the ASN and its dispatch state.

    The tab's whole purpose is answering "did the retailer get this?", which needs the
    outbound status alongside the invoice — so it is resolved here rather than leaving
    the UI to make a second call per row.
    """
    from sqlalchemy import select

    from app.models.asn import EdiAdvanceShipNotice
    from app.models.outbound import EdiOutboundMessage

    resp = InvoiceResponse.model_validate(invoice)

    asn_id = getattr(invoice, "asn_id", None)
    if asn_id is not None:
        asn = db.get(EdiAdvanceShipNotice, asn_id)
        if asn is not None:
            resp.asn_number = asn.asn_number
            resp.asn_status = asn.status
            outbound = db.execute(
                select(EdiOutboundMessage)
                .where(EdiOutboundMessage.external_reference == asn.asn_number)
                .order_by(EdiOutboundMessage.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if outbound is not None:
                resp.outbound_status = outbound.status
    return resp
