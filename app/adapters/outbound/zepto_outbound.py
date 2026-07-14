"""
Zepto outbound adapter — sends ASNs via Zepto Silk Route API.

Routes:
  ASN_856 → ZeptoApiAdapter.send_asn()

Payload schema (built by b1_to_outbound.py):
  purchaseOrderDetails.purchaseOrderNumber, invoiceNumber, invoiceDate, lineItems[]

Re-implemented from _archive/backend_old/app/services/zepto.py
"""
from __future__ import annotations

from typing import Any

import structlog

from app.adapters.outbound.base import BaseOutboundAdapter, OutboundResult

log = structlog.get_logger(__name__)


class ZeptoOutboundAdapter(BaseOutboundAdapter):
    """Transport adapter for Zepto (API-pull channel)."""

    @property
    def channel(self) -> str:
        return "API"

    def send(
        self,
        doc_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> OutboundResult:
        if doc_type == "ASN_856":
            return self._send_asn(payload, idempotency_key)

        return OutboundResult(
            success=False,
            error=f"Zepto outbound adapter: unsupported doc_type {doc_type!r}",
        )

    def _send_asn(
        self,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> OutboundResult:
        from app.adapters.api.zepto_api import ZeptoApiAdapter

        adapter = ZeptoApiAdapter()
        result = adapter.send_asn(payload=payload, idempotency_key=idempotency_key)
        if result.get("success"):
            asn_number = result.get("asn_number")
            return OutboundResult(success=True, external_ref=asn_number)
        return OutboundResult(success=False, error=result.get("error") or str(result))
