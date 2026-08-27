"""
Outbound document dispatch workflow.

Responsibilities:
  1. Load EdiOutboundMessage from DB
  2. SLA breach check (log warning; does not block send)
  3. Dispatch to the correct outbound adapter (registry lookup)
  4. On success: status → SENT, store external_ref
  5. On failure:
     - attempt_count < MAX_ATTEMPTS → schedule next retry via next_retry_at
     - attempt_count >= MAX_ATTEMPTS → status → FAILED
  6. Persist outcome

Retry schedule (delays before each retry attempt):
  attempt 1 → 60s
  attempt 2 → 300s
  attempt 3 → 1800s
  attempt 4 → 7200s
  attempt 5 → FAILED (no more retries; MAX_ATTEMPTS = 5)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

import structlog

log = structlog.get_logger(__name__)

_MAX_ATTEMPTS = 5
# Delay in seconds before each retry (indexed by attempt_count after failure)
_RETRY_DELAYS_S = [60, 300, 1800, 7200, 21600]
# Gmail's hard limit is 25 MB per message; stay under it with room for the MIME
# encoding overhead, which base64 puts at roughly a third on top of the raw bytes.
_MAX_ATTACHMENT_BYTES = 18 * 1024 * 1024


@dataclass
class SendResult:
    success: bool
    outbound_msg_id: UUID
    doc_type: str
    partner_code: str
    external_ref: str | None = None
    skipped: bool = False
    skip_reason: str = ""
    error: str | None = None
    attempt_count: int = 0


def send_outbound_message(outbound_msg_id: UUID) -> SendResult:
    """
    Send one EdiOutboundMessage. Called by send_outbound_job (RQ).
    Idempotent: if status = SENT, returns a skipped result.
    """

    from app.adapters.outbound.registry import UnsupportedOutboundPartnerError, get_outbound_adapter
    from app.db import SyncSessionLocal
    from app.models.master_data import TradingPartner
    from app.models.outbound import EdiOutboundMessage

    with SyncSessionLocal() as session:
        msg = session.get(EdiOutboundMessage, outbound_msg_id)
        if not msg:
            return SendResult(
                success=False,
                outbound_msg_id=outbound_msg_id,
                doc_type="UNKNOWN",
                partner_code="UNKNOWN",
                error="OutboundMessage not found",
            )

        if msg.status == "SENT":
            return SendResult(
                success=True,
                outbound_msg_id=outbound_msg_id,
                doc_type=str(msg.doc_type),
                partner_code="",
                skipped=True,
                skip_reason="already sent",
            )

        if msg.status == "FAILED":
            return SendResult(
                success=False,
                outbound_msg_id=outbound_msg_id,
                doc_type=str(msg.doc_type),
                partner_code="",
                skipped=True,
                skip_reason="permanently failed — max attempts exhausted",
            )

        partner = session.get(TradingPartner, msg.trading_partner_id)
        if not partner:
            return SendResult(
                success=False,
                outbound_msg_id=outbound_msg_id,
                doc_type=str(msg.doc_type),
                partner_code="",
                error="TradingPartner not found",
            )

        # SLA breach check (log only; does not stop the send)
        _check_sla(msg, partner)

        # Get adapter
        try:
            adapter = get_outbound_adapter(
                partner_code=partner.code,
                source_channel=partner.source_channel,
            )
        except UnsupportedOutboundPartnerError as exc:
            _mark_skipped(session, msg, str(exc))
            session.commit()
            return SendResult(
                success=False,
                outbound_msg_id=outbound_msg_id,
                doc_type=str(msg.doc_type),
                partner_code=partner.code,
                skipped=True,
                skip_reason=str(exc),
            )

        # Increment attempt counter before the call
        msg.attempt_count = (msg.attempt_count or 0) + 1
        msg.last_attempt_at = datetime.now(UTC)
        session.commit()

        # Call adapter
        result = adapter.send(
            doc_type=str(msg.doc_type),
            payload=_with_attachments(session, msg),
            idempotency_key=str(outbound_msg_id),
        )

        with SyncSessionLocal() as s2:
            msg2 = s2.get(EdiOutboundMessage, outbound_msg_id)
            if msg2 is None:
                return SendResult(
                    success=False,
                    outbound_msg_id=outbound_msg_id,
                    doc_type=str(msg.doc_type),
                    partner_code=partner.code,
                    error="Message disappeared after send",
                )

            if result.success:
                msg2.status = "SENT"
                msg2.ack_received_at = datetime.now(UTC)
                # Their id goes in partner_reference; ours stays in external_reference.
                # Overwriting ours destroyed the only handle we had on the document at
                # the exact moment it became real.
                if result.external_ref:
                    msg2.partner_reference = result.external_ref
                msg2.error_message = None
                s2.commit()
                log.info(
                    "outbound.sent",
                    msg_id=str(outbound_msg_id),
                    doc_type=str(msg.doc_type),
                    partner=partner.code,
                    external_ref=result.external_ref,
                )
                return SendResult(
                    success=True,
                    outbound_msg_id=outbound_msg_id,
                    doc_type=str(msg.doc_type),
                    partner_code=partner.code,
                    external_ref=result.external_ref,
                    attempt_count=msg2.attempt_count,
                )
            else:
                # Failed — schedule retry or mark permanently failed
                attempt_count = msg2.attempt_count or 1
                if attempt_count < _MAX_ATTEMPTS:
                    delay_s = _RETRY_DELAYS_S[attempt_count - 1]
                    msg2.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay_s)
                    msg2.status = "PENDING"
                    msg2.error_message = result.error
                    s2.commit()
                    log.warning(
                        "outbound.retry_scheduled",
                        msg_id=str(outbound_msg_id),
                        doc_type=str(msg.doc_type),
                        partner=partner.code,
                        attempt=attempt_count,
                        retry_in_s=delay_s,
                        error=result.error,
                    )
                else:
                    msg2.status = "FAILED"
                    msg2.next_retry_at = None
                    msg2.error_message = result.error
                    s2.commit()
                    log.error(
                        "outbound.permanent_failure",
                        msg_id=str(outbound_msg_id),
                        doc_type=str(msg.doc_type),
                        partner=partner.code,
                        attempts=attempt_count,
                        error=result.error,
                    )
                return SendResult(
                    success=False,
                    outbound_msg_id=outbound_msg_id,
                    doc_type=str(msg.doc_type),
                    partner_code=partner.code,
                    error=result.error,
                    attempt_count=attempt_count,
                )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _with_attachments(session: Session, msg: Any) -> dict[str, Any]:
    """
    Render any document the payload asks to be attached, just before dispatch.

    The payload names an invoice (`attach_invoice`) rather than carrying the file,
    for two reasons. A tax invoice PDF is tens of kilobytes and base64 in a JSONB
    column is a third larger again — storing one per ASN bloats the row and makes the
    Outbound Messages tab unreadable. And the PDF is derived: regenerating it from the
    invoice always matches the invoice, whereas a stored copy silently goes stale the
    moment an IRN arrives on a re-push.

    Rendering here rather than in the adapter keeps `BaseOutboundAdapter`'s rule intact
    — the adapter transports and never touches the DB — while putting the DB access in
    the workflow layer, which already holds the session.

    A failure to render must not swallow the document: the covering email is still
    worth sending, so this logs and sends without the attachment rather than raising.
    """
    payload = dict(msg.payload or {})
    invoice_number = payload.pop("attach_invoice", None)
    want_po_source = payload.pop("attach_po_source", False)
    if not invoice_number and not want_po_source:
        return payload

    files: list[dict[str, Any]] = []
    if invoice_number:
        files += _invoice_attachment(session, invoice_number)
    if want_po_source:
        files += _po_source_attachments(session, msg)

    if not files:
        return payload

    # Gmail refuses a message over 25 MB and answers with a size error rather than a
    # partial send, so an oversized set is trimmed here instead: a delivery note that
    # arrives with one document beats one that does not arrive at all.
    kept: list[dict[str, Any]] = []
    total = 0
    for f in files:
        size = len(f["content"])
        if total + size > _MAX_ATTACHMENT_BYTES:
            log.warning(
                "outbound.attachment_dropped_too_large",
                filename=f["filename"], bytes=size, running_total=total,
            )
            continue
        kept.append(f)
        total += size

    payload["attachments"] = kept
    log.info(
        "outbound.attachments_rendered",
        files=[f["filename"] for f in kept],
        bytes=total,
    )
    return payload


def _invoice_attachment(session: Session, invoice_number: str) -> list[dict[str, Any]]:
    """The GST tax invoice, rendered fresh from the invoice record."""
    from sqlalchemy import select

    from app.models.invoice import EdiInvoice
    from app.utils.invoice_pdf import render_invoice_pdf

    invoice = session.execute(
        select(EdiInvoice).where(EdiInvoice.invoice_number == invoice_number)
    ).scalar_one_or_none()
    if invoice is None:
        log.warning("outbound.attachment_missing", invoice_number=invoice_number)
        return []

    try:
        pdf = render_invoice_pdf(session, invoice)
    except Exception as exc:
        log.exception(
            "outbound.attachment_failed", invoice_number=invoice_number, error=str(exc)
        )
        return []

    return [{
        "filename": f"Invoice-{invoice_number}.pdf",
        "mime_type": "application/pdf",
        "content": pdf,
    }]


def _po_source_attachments(session: Session, msg: Any) -> list[dict[str, Any]]:
    """
    The order exactly as the partner sent it.

    Read back from wherever ingestion stored it rather than re-rendered, because the
    point is to hand their accounts desk the same document they issued — a
    reconstruction of it would invite an argument about which one is authoritative.
    A partner whose orders arrive over an API has no such file, and a manually keyed
    order has none either; both simply contribute nothing.
    """
    from app.adapters.storage import fetch_attachment
    from app.models.edi_po import EdiPurchaseOrder
    from app.models.raw_messages import RawMessage

    po = session.get(EdiPurchaseOrder, msg.po_id)
    raw = session.get(RawMessage, po.raw_message_id) if po and po.raw_message_id else None
    atts = (getattr(raw, "attachment_paths", None) or []) if raw else []

    out: list[dict[str, Any]] = []
    seen_ext: dict[str, int] = {}
    for att in atts:
        if not isinstance(att, dict):
            continue
        source_name = str(att.get("filename") or "purchase-order")
        try:
            content = fetch_attachment(att)
        except Exception as exc:
            # Broad on purpose: this reaches Cloudinary over the network and the local
            # filesystem, so it can fail as an HTTP error, a timeout, a signing problem
            # or a missing file. One unreadable source document must not cost the
            # retailer the invoice, so every one of those is logged and skipped.
            log.warning(
                "outbound.po_source_unavailable", filename=source_name, error=str(exc)
            )
            continue

        # Renamed to the PO number. Partners generate names like
        # "DG8TMD12QLBDILJRUSF7_CREATE_OTB_PURCHASE_ORDER_ae4e21a5-814d-...xlsx",
        # which tells an accounts desk nothing and is what they will have to find
        # again later. A second file of the same type gets a suffix rather than
        # overwriting the first in someone's downloads folder.
        ext = Path(source_name).suffix.lower() or ".bin"
        seen_ext[ext] = seen_ext.get(ext, 0) + 1
        suffix = "" if seen_ext[ext] == 1 else f"-{seen_ext[ext]}"

        out.append({
            "filename": f"PO-{po.buyer_po_number}{suffix}{ext}",
            "mime_type": _mime_for(ext),
            "content": content,
        })
    return out


#: Content types for the formats partners actually send us. `mimetypes.guess_type`
#: is backed by the system mime database, and the slim container image has no entry
#: for .xlsx — a spreadsheet went out as application/octet-stream, which mail clients
#: will not preview and some label as an unknown binary.
_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".xml": "application/xml",
    ".json": "application/json",
    ".zip": "application/zip",
}


def _mime_for(ext: str) -> str:
    import mimetypes

    return _MIME_BY_EXT.get(ext) or mimetypes.guess_type(f"x{ext}")[0] or "application/octet-stream"

def _check_sla(msg: object, partner: object) -> None:
    """Log a warning if the ACK SLA deadline has passed."""
    from app.models._enums import EdiDocType
    doc_type = getattr(msg, "doc_type", None)
    if doc_type != EdiDocType.PO_ACK_855:
        return
    created_at = getattr(msg, "created_at", None)
    ack_sla_hours = getattr(partner, "ack_sla_hours", 24) or 24
    if created_at is None:
        return
    deadline = created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at
    deadline = deadline + timedelta(hours=ack_sla_hours)
    if datetime.now(UTC) > deadline:
        log.warning(
            "outbound.sla_breached",
            msg_id=str(getattr(msg, "id", "")),
            partner=getattr(partner, "code", ""),
            ack_sla_hours=ack_sla_hours,
            deadline=deadline.isoformat(),
        )


def _mark_skipped(session: object, msg: object, reason: str) -> None:
    msg.status = "SKIPPED"
    msg.error_message = reason
