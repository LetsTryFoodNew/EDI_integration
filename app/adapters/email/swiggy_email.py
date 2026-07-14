"""
Swiggy Instamart email adapter — targets the SWIGGY_PO Gmail label.

Source format: Swiggy sends PO emails with a PDF or Excel attachment.
Known quirks:
  - Sender domains: scootsy.com (Swiggy procurement subsidiary — HYD/BLR),
    swiggy.in, bundl.com, swiggyit.com.
  - Subject format: "{CITY} {CODE}-{PO_NUM}-EARTH CRUST PRIVATE LIMITED"
    (e.g. "HYD IM5-CMQPO17805-EARTH CRUST PRIVATE LIMITED").
    No explicit "Purchase Order" text in subject — domain+extension check
    is the primary filter.
  - Gmail assigns MIME type 'application/octet-stream' to both .pdf and .xls
    attachments for these emails. Extension-based detection is required.
  - GRN, invoice, and reconciliation emails share the same label; they are
    rejected by subject-keyword matching.

Sample subject lines (PO):
    "HYD IM5-CMQPO17805-EARTH CRUST PRIVATE LIMITED"
    "BLR IM4-MBEPO36284-EARTH CRUST PRIVATE LIMITED"

Sample subject lines (non-PO, skip):
    "GRN confirmation for PO#SWG00123"
    "Invoice for PO#SWG00123"
"""
from __future__ import annotations

from pathlib import Path

from app.adapters.email.base import BaseEmailAdapter, InboundEmail

SWIGGY_SENDER_DOMAINS: frozenset[str] = frozenset({
    "swiggy.in",
    "bundl.com",
    "swiggyit.com",
    "scootsy.com",      # Swiggy-owned procurement subsidiary (HYD/BLR POs)
})

_PO_KEYWORDS: frozenset[str] = frozenset({
    "purchase order",
    "po #",
    "po#",
    "new order",
    "order confirmation",
})

# Any of these in the subject → not a PO, skip it.
_SKIP_KEYWORDS: frozenset[str] = frozenset({
    "grn",
    "goods receipt",
    "goods received",
    "delivery note",
    "return",
    "debit note",
    "credit note",
    "invoice",
    "payment",
    "reconciliation",
    "statement",
    "remittance",
})

# Gmail often sends PO attachments as application/octet-stream — check extension too.
_PO_MIME_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",   # Gmail generic fallback for pdf/xls
})

_PO_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".xls", ".xlsx"})


def _has_po_attachment(email: InboundEmail) -> bool:
    """Return True if any attachment looks like a PO document (PDF or Excel)."""
    for att in email.attachments:
        if att.mime_type in _PO_MIME_TYPES:
            # Narrow octet-stream further by extension to avoid false positives
            if att.mime_type == "application/octet-stream":
                if Path(att.filename).suffix.lower() in _PO_EXTENSIONS:
                    return True
            else:
                return True
    return False


class SwiggyEmailAdapter(BaseEmailAdapter):
    """
    Ingests PO emails from the SWIGGY_PO Gmail label.
    GRN, invoice, delivery-note, and reconciliation emails are excluded.
    """

    def get_partner_code(self) -> str:
        return "SWIGGY"

    def get_gmail_label(self) -> str:
        return "SWIGGY_PO"

    def is_po_email(self, email: InboundEmail) -> bool:
        subject = email.subject.lower()
        sender = email.sender.lower()

        # Hard reject: known non-PO email types by subject keyword
        if any(kw in subject for kw in _SKIP_KEYWORDS):
            return False

        # Accept if subject explicitly looks like a PO
        if any(kw in subject for kw in _PO_KEYWORDS):
            return True

        # Accept if from a known Swiggy/Scootsy domain AND has a PDF or Excel attachment.
        # Note: Gmail delivers these with MIME type 'application/octet-stream';
        # _has_po_attachment() falls back to extension-based detection.
        from_swiggy = any(domain in sender for domain in SWIGGY_SENDER_DOMAINS)
        return from_swiggy and _has_po_attachment(email)
