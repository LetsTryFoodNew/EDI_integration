"""
Outbound adapter registry — maps partner_code / source_channel to an adapter.

Usage:
    adapter = get_outbound_adapter(partner_code="BLINKIT", source_channel=SourceChannel.WEBHOOK)
    result = adapter.send(doc_type="PO_ACK_855", payload={...}, idempotency_key="...")
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from app.adapters.outbound.blinkit_outbound import BlinkitOutboundAdapter
from app.adapters.outbound.email_outbound import EmailOutboundAdapter
from app.adapters.outbound.zepto_outbound import ZeptoOutboundAdapter
from app.models._enums import SourceChannel

if TYPE_CHECKING:
    from app.adapters.outbound.base import BaseOutboundAdapter

log = structlog.get_logger(__name__)


class UnsupportedOutboundPartnerError(ValueError):
    """Raised when no adapter exists for a given partner / channel combo."""


# Explicit per-partner overrides (highest priority)
_PARTNER_MAP: dict[str, type[BaseOutboundAdapter]] = {
    "BLINKIT": BlinkitOutboundAdapter,
    "ZEPTO": ZeptoOutboundAdapter,
}

# Channel-level fallbacks
_CHANNEL_MAP: dict[SourceChannel, type[BaseOutboundAdapter]] = {
    SourceChannel.EMAIL: EmailOutboundAdapter,
}


def get_outbound_adapter(
    partner_code: str,
    source_channel: SourceChannel,
) -> BaseOutboundAdapter:
    """
    Return an outbound adapter instance for the given partner.

    Lookup order:
      1. Exact partner_code match
      2. source_channel fallback
      3. Raise UnsupportedOutboundPartnerError
    """
    cls = _PARTNER_MAP.get(partner_code)
    if cls is None:
        cls = _CHANNEL_MAP.get(source_channel)
    if cls is None:
        raise UnsupportedOutboundPartnerError(
            f"No outbound adapter for partner={partner_code!r}, "
            f"channel={source_channel!r}"
        )
    return cls()
