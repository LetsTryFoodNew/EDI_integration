"""
Blinkit outbound adapter — sends ACKs and ASNs via Blinkit's partner portal API.

Routes:
  PO_ACK_855 → BlinkitApiAdapter.acknowledge_po()
  ASN_856    → BlinkitApiAdapter.send_asn()

Payload schemas (built by b1_to_outbound.py):

  PO_ACK_855 payload keys: po_number, status ("PROCESSING" | "ACCEPTED" | "REJECTED")
  ASN_856 payload keys:    (full Blinkit ASN JSON — see blinkit_api.py docstring)

Re-implemented from _archive/backend_old/app/services/blinkit.py
"""
from __future__ import annotations

from typing import Any

import structlog

from app.adapters.outbound.base import BaseOutboundAdapter, OutboundResult

log = structlog.get_logger(__name__)


class BlinkitOutboundAdapter(BaseOutboundAdapter):
    """Transport adapter for Blinkit (webhook-push channel)."""

    @property
    def channel(self) -> str:
        return "WEBHOOK"

    def send(
        self,
        doc_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> OutboundResult:
        from app.adapters.api.blinkit_api import BlinkitApiAdapter

        adapter = BlinkitApiAdapter()

        if doc_type == "PO_ACK_855":
            return self._send_ack(adapter, payload, idempotency_key)
        if doc_type == "ASN_856":
            return self._send_asn(adapter, payload, idempotency_key)

        return OutboundResult(
            success=False,
            error=f"Blinkit outbound adapter: unsupported doc_type {doc_type!r}",
        )

    def _send_ack(
        self,
        adapter: Any,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> OutboundResult:
        po_number = payload.get("po_number", "")
        status = payload.get("status", "PROCESSING").lower()
        result = adapter.acknowledge_po(
            po_number=po_number,
            status=status,
            errors=payload.get("errors"),
            warnings=payload.get("warnings"),
            idempotency_key=idempotency_key,
        )
        if result.get("success"):
            return OutboundResult(success=True, external_ref=po_number)
        return OutboundResult(success=False, error=result.get("error") or str(result))

    def _send_asn(
        self,
        adapter: Any,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> OutboundResult:
        result = adapter.send_asn(payload=payload, idempotency_key=idempotency_key)
        if result.get("success"):
            asn_id = result.get("asn_id") or result.get("data", {}).get("asn_id")
            return OutboundResult(success=True, external_ref=str(asn_id) if asn_id else None)
        return OutboundResult(success=False, error=result.get("error") or str(result))
