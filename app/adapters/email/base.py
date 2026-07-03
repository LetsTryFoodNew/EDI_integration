"""
BaseEmailAdapter and shared data classes for email-based PO ingestion.

Source format: Gmail message via Google API Python client.
Each concrete subclass targets one Gmail label (= one trading partner).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AttachmentMeta:
    """Metadata about one attachment in an email — no data, just the pointer."""

    filename: str
    mime_type: str
    size_bytes: int
    # Gmail part ID used to download the attachment body separately
    part_id: str
    # Gmail attachment ID — present when body.attachmentId is set
    attachment_id: str | None = None


@dataclass
class InboundEmail:
    """
    Parsed representation of one Gmail message.
    Produced by GmailClient.get_message(); consumed by the ingestion workflow.
    """

    message_id: str          # Gmail message ID (our external_id / idempotency key)
    thread_id: str
    subject: str
    sender: str
    received_at: datetime    # UTC
    headers: dict[str, str]  # All headers, lowercased keys
    body_text: str | None    # text/plain part
    body_html: str | None    # text/html part (optional)
    label_ids: list[str]
    attachments: list[AttachmentMeta] = field(default_factory=list)


class BaseEmailAdapter(ABC):
    """
    Abstract base for one Gmail-label ↔ one trading-partner mapping.

    Subclasses override:
      - get_partner_code()  — must match TradingPartner.code in DB
      - get_gmail_label()   — the Gmail label name to poll
      - is_po_email()       — optional additional filter (subject, sender, etc.)
    """

    @abstractmethod
    def get_partner_code(self) -> str:
        """TradingPartner.code this adapter handles (e.g. 'BLINKIT')."""

    @abstractmethod
    def get_gmail_label(self) -> str:
        """Gmail label name to poll (e.g. 'BLINKIT_PO')."""

    def is_po_email(self, email: InboundEmail) -> bool:
        """
        Secondary filter applied after label fetch.
        Return False to silently skip a message (it will not be saved to raw_messages).
        Default: accept everything in the label.
        """
        return True
