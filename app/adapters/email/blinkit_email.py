"""
Blinkit email adapter — targets the BLINKIT_PO Gmail label.

Source format: Blinkit sends PO confirmation emails with a PDF attachment.
Known quirks:
  - Subject line: "Purchase Order #<po_number> from Blinkit"
  - Sender domain: @blinkit.com or @grofers.com (legacy)
  - PDF attachment named "PO_<po_number>.pdf"
  - Some emails contain only an HTML body with an embedded PO table (no PDF).
"""
from __future__ import annotations

from app.adapters.email.base import BaseEmailAdapter, InboundEmail

BLINKIT_SENDER_DOMAINS = {"blinkit.com", "grofers.com"}


class BlinkitEmailAdapter(BaseEmailAdapter):
    """
    Ingests PO emails from the BLINKIT_PO Gmail label.
    Primary channel for Blinkit is webhook (Phase 4); this adapter covers
    manually forwarded or legacy email POs.
    """

    def get_partner_code(self) -> str:
        return "BLINKIT"

    def get_gmail_label(self) -> str:
        return "BLINKIT_PO"

    def is_po_email(self, email: InboundEmail) -> bool:
        sender = email.sender.lower()
        subject = email.subject.lower()

        # Accept any email that looks like a PO from Blinkit
        from_blinkit = any(domain in sender for domain in BLINKIT_SENDER_DOMAINS)
        looks_like_po = "purchase order" in subject or "po #" in subject or "po#" in subject

        # Also accept if there's a PDF attachment (ops may forward POs manually)
        has_pdf = any(a.mime_type == "application/pdf" for a in email.attachments)

        return from_blinkit or looks_like_po or has_pdf
