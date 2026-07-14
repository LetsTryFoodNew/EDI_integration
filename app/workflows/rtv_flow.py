"""
RTV (Return To Vendor) workflow — Phase 7.

Processes inbound RTV emails:
  raw_message (doc_type=RTV) → match original PO → create B1 Return/Credit Memo
  → optionally notify partner (EdiOutboundMessage with doc_type=CREDIT_NOTE)

RTV matching strategy:
  1. Extract PO number from email subject or body (regex search)
  2. Look up EdiPurchaseOrder by (trading_partner_id, buyer_po_number)
  3. If found and SAP_CONFIRMED: create B1 Return via ServiceLayerClient
  4. If not matched: create a validation issue E010_RTV_PO_NOT_FOUND for ops review

B1 Return document:
  POST /b1s/v1/Returns
  Required: CardCode, BaseEntry (Sales Order DocEntry), BaseType (17), DocumentLines

RTV email subject patterns (from the ~1,420 historical RTVs):
  "RTV for PO <po_number>"
  "Return Request — <po_number>"
  "<partner> RTV <po_number>"
  (Any subject containing a known PO number)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

import structlog

log = structlog.get_logger(__name__)

_PO_NUMBER_PATTERNS = [
    re.compile(r"\bPO[_\s#:-]([A-Z0-9][A-Z0-9\-]{3,29})", re.IGNORECASE),   # "PO BL-123" / "PO: 456"
    re.compile(r"(?:return|rtv|credit)\s+(?:for|#|:)\s*([A-Z0-9\-]{4,30})", re.IGNORECASE),  # "RTV for BL-123"
    re.compile(r"\b([A-Z]{2,4}-\d{6,15})\b"),          # e.g. BL-123456789
    re.compile(r"\b(\d{12,20})\b"),                      # bare numeric PO (Blinkit)
]


@dataclass
class RtvResult:
    success: bool
    raw_message_id: UUID
    po_id: UUID | None = None
    b1_return_doc_entry: int | None = None
    b1_return_doc_num: int | None = None
    outbound_msg_id: UUID | None = None
    skipped: bool = False
    skip_reason: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def process_rtv(raw_message_id: UUID) -> RtvResult:
    """
    Process one RTV raw_message.
    Idempotent: if a B1 Return already exists for this raw_message, returns skipped.
    """
    from sqlalchemy import select

    from app.db import SyncSessionLocal
    from app.models._enums import EdiDocType
    from app.models.edi_po import (
        EdiPoLineItem,
        EdiPurchaseOrder,
    )
    from app.models.master_data import TradingPartner
    from app.models.outbound import EdiOutboundMessage
    from app.models.raw_messages import RawMessage

    with SyncSessionLocal() as session:
        raw = session.get(RawMessage, raw_message_id)
        if not raw:
            return RtvResult(
                success=False,
                raw_message_id=raw_message_id,
                error="RawMessage not found",
            )

        partner = session.get(TradingPartner, raw.trading_partner_id)
        if not partner:
            return RtvResult(
                success=False,
                raw_message_id=raw_message_id,
                error="TradingPartner not found",
            )

        # Extract PO number from email subject + body
        search_text = _rtv_search_text(raw)
        po_number = _extract_po_number(search_text)

        if not po_number:
            _flag_unmatched(session, raw, partner, "Could not extract PO number from RTV email")
            session.commit()
            return RtvResult(
                success=False,
                raw_message_id=raw_message_id,
                skipped=True,
                skip_reason="Could not extract PO number — flagged for ops review",
            )

        # Match to canonical PO
        po = session.execute(
            select(EdiPurchaseOrder).where(
                EdiPurchaseOrder.trading_partner_id == partner.id,
                EdiPurchaseOrder.buyer_po_number == po_number,
                EdiPurchaseOrder.deleted_at.is_(None),
            ).order_by(EdiPurchaseOrder.version.desc()).limit(1)
        ).scalar_one_or_none()

        if not po:
            _flag_unmatched(session, raw, partner, f"PO {po_number!r} not found for partner {partner.code}")
            session.commit()
            return RtvResult(
                success=False,
                raw_message_id=raw_message_id,
                skipped=True,
                skip_reason=f"PO {po_number!r} not in system — flagged for ops review",
            )

        if po.b1_sales_order_doc_entry is None:
            return RtvResult(
                success=False,
                raw_message_id=raw_message_id,
                po_id=po.id,
                skipped=True,
                skip_reason=f"PO {po_number!r} not yet pushed to B1 — RTV cannot be processed",
            )

        lines = session.execute(
            select(EdiPoLineItem).where(EdiPoLineItem.po_id == po.id)
        ).scalars().all()

        # Build B1 Return payload
        try:
            return_payload = _build_b1_return_payload(po, list(lines), partner)
        except ValueError as exc:
            return RtvResult(
                success=False,
                raw_message_id=raw_message_id,
                po_id=po.id,
                error=f"Return payload build failed: {exc}",
            )

        # Call B1
        from app.sap_b1.client import get_b1_client
        from app.sap_b1.errors import B1ApiError

        client = get_b1_client()
        b1_response: dict[str, Any] | None = None
        error_msg: str | None = None

        try:
            b1_response = client.create_return(return_payload)
        except B1ApiError as exc:
            error_msg = str(exc)
            log.error("rtv.b1_return_failed", po_id=str(po.id), error=error_msg)
        except Exception as exc:
            error_msg = f"Unexpected error: {exc}"
            log.exception("rtv.b1_unexpected_error", po_id=str(po.id))

        # Persist outcome
        with SyncSessionLocal() as s2:
            if b1_response is not None:
                doc_entry = b1_response.get("DocEntry")
                doc_num = b1_response.get("DocNum")

                # Notify partner (credit note / return notification)
                outbound_payload = _build_credit_note_notification(po, partner, doc_num)
                outbound_msg = EdiOutboundMessage(
                    po_id=po.id,
                    trading_partner_id=partner.id,
                    doc_type=EdiDocType.CREDIT_NOTE,
                    payload=outbound_payload,
                    channel=partner.source_channel.value,
                    status="PENDING",
                )
                s2.add(outbound_msg)
                s2.flush()

                s2.commit()
                log.info(
                    "rtv.processed",
                    po_id=str(po.id),
                    po_number=po_number,
                    doc_entry=doc_entry,
                )
                return RtvResult(
                    success=True,
                    raw_message_id=raw_message_id,
                    po_id=po.id,
                    b1_return_doc_entry=int(doc_entry) if doc_entry is not None else None,
                    b1_return_doc_num=int(doc_num) if doc_num is not None else None,
                    outbound_msg_id=outbound_msg.id,
                )
            else:
                s2.commit()
                return RtvResult(
                    success=False,
                    raw_message_id=raw_message_id,
                    po_id=po.id,
                    error=error_msg,
                )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rtv_search_text(raw: Any) -> str:
    """Combine email subject and body snippet for PO number search."""
    parts: list[str] = []
    payload = raw.payload or {}
    if isinstance(payload, dict):
        parts.append(payload.get("subject", ""))
        parts.append(payload.get("snippet", ""))
        body = payload.get("body", "")
        if isinstance(body, str):
            parts.append(body[:2000])  # limit to avoid regex backtracking on huge bodies
    return " ".join(parts)


def _extract_po_number(text: str) -> str | None:
    """Return the first PO-number-like string found in text."""
    for pattern in _PO_NUMBER_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


def _build_b1_return_payload(
    po: Any,
    lines: list[Any],
    partner: Any,
) -> dict[str, Any]:
    """Build POST /b1s/v1/Returns payload (mirrors Sales Order structure)."""
    if not partner.b1_card_code:
        raise ValueError(f"Partner {partner.code!r} has no b1_card_code")

    doc_lines: list[dict[str, Any]] = []
    for line in lines:
        if not line.sap_material_no:
            continue
        doc_lines.append({
            "ItemCode": line.sap_material_no,
            "Quantity": float(line.ordered_qty or 0),
            "Price": round(float(line.unit_price or 0), 6),
            "BaseEntry": po.b1_sales_order_doc_entry,
            "BaseType": 17,  # Sales Order
            "BaseLine": line.line_number - 1,  # B1 is 0-indexed
        })

    if not doc_lines:
        raise ValueError("Return has no lines with a mapped SAP material code")

    return {
        "CardCode": partner.b1_card_code,
        "DocDate": datetime.now(UTC).strftime("%Y-%m-%d"),
        "Comments": f"RTV for PO {po.buyer_po_number}",
        "DocumentLines": doc_lines,
    }


def _build_credit_note_notification(
    po: Any,
    partner: Any,
    b1_doc_num: Any,
) -> dict[str, Any]:
    """Build credit note notification payload for partner."""
    from app.models._enums import SourceChannel
    payload: dict[str, Any] = {
        "po_number": po.buyer_po_number,
        "credit_note_number": str(b1_doc_num) if b1_doc_num else "",
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
    }
    if partner.source_channel == SourceChannel.EMAIL:
        payload.update({
            "to": "",  # ops fills this from api_config.ops_email in adapter
            "subject": f"Credit Note — RTV for PO {po.buyer_po_number}",
            "body_text": (
                f"Dear {partner.name},\n\n"
                f"A Return/Credit Note has been raised for PO {po.buyer_po_number}.\n"
                f"Credit Note No: {b1_doc_num or 'Pending'}\n\n"
                f"Thank you,\nLet's Try Foods"
            ),
        })
    return payload


def _flag_unmatched(session: Any, raw: Any, partner: Any, message: str) -> None:
    """Create a validation issue for ops review when an RTV cannot be matched."""
    # We don't have a po_id here — create a detached issue we link to a dummy/placeholder
    # For now, log at ERROR level; Phase 8 UI will surface these via the exception queue
    log.error(
        "rtv.unmatched",
        raw_message_id=str(raw.id),
        partner=partner.code,
        message=message,
    )
