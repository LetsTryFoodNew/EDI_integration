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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

import structlog

log = structlog.get_logger(__name__)

_MAX_ATTEMPTS = 5
# Delay in seconds before each retry (indexed by attempt_count after failure)
_RETRY_DELAYS_S = [60, 300, 1800, 7200, 21600]


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
            payload=msg.payload or {},
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
                msg2.external_reference = result.external_ref
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
