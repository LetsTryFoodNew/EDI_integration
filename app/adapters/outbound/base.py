"""
Base interface for outbound document adapters.

An outbound adapter is responsible only for TRANSPORT — sending a pre-built
payload to the correct partner channel (HTTP API or email). Payload construction
happens in app/workflows/b1_to_outbound.py.

Concrete adapters:
  BlinkitOutboundAdapter  — HTTP POST to Blinkit partner portal
  ZeptoOutboundAdapter    — HTTP POST to Zepto Silk Route
  EmailOutboundAdapter    — Gmail send reply for email-based partners
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class OutboundResult:
    """Returned by every adapter.send() call."""

    success: bool
    external_ref: str | None = None  # asn_id, asn_number, Gmail message_id, etc.
    error: str | None = None


class BaseOutboundAdapter(ABC):
    """
    Contract for all outbound adapters.

    `send` receives the pre-built partner-specific payload from
    EdiOutboundMessage.payload (populated at message creation time by
    b1_to_outbound.py). The adapter must not look up additional data from DB.
    """

    @abstractmethod
    def send(
        self,
        doc_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> OutboundResult:
        """
        Send the document to the partner.

        doc_type — one of EdiDocType string values ("PO_ACK_855", "ASN_856", etc.)
        payload  — partner-specific payload dict (pre-built)
        idempotency_key — EdiOutboundMessage.id as str; safe to retry with same key
        """

    @property
    @abstractmethod
    def channel(self) -> str:
        """Return the channel identifier string: "API", "WEBHOOK", or "EMAIL"."""
