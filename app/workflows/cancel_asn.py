"""
Cancel an ASN with the partner that accepted it.

Only some partners can do this, and pretending otherwise is worse than saying no.
Where a partner has no cancellation endpoint, an ASN they have accepted is final as
far as their API is concerned and the correction has to happen by phone or portal --
so this refuses with that reason rather than flipping our own status and leaving the
retailer expecting a delivery.

Support today:

    ZEPTO     DELETE /api/v1/external/asn?asnNumber=...   (contract v12 §2.b)
    BLINKIT   no endpoint. The POVMS ASN Sync contract defines creation only.

Zepto's contract is explicit that this is half of the *only* correction path: there is
no update API, so a wrong ASN is cancelled and re-created under a different
invoiceNumber. Re-using the invoice number is rejected as a duplicate (E107 on
Blinkit's side, a duplicate check on Zepto's), which is why the caller is told to
re-invoice rather than retry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)

# Partner code -> human reason it cannot be cancelled through an API.
NO_CANCEL_API: dict[str, str] = {
    "BLINKIT": (
        "Blinkit's POVMS ASN Sync contract defines ASN creation only — there is no "
        "cancellation endpoint. A sent ASN has to be withdrawn with Blinkit directly."
    ),
}


@dataclass
class CancelResult:
    success: bool
    asn_number: str
    partner_code: str = ""
    partner_reference: str | None = None
    error: str | None = None
    already_cancelled: bool = False


def cancel_asn(db: Session, asn_id: uuid.UUID, *, cancelled_by: str) -> CancelResult:
    """
    Cancel one ASN with its partner and mark it cancelled here.

    Nothing local changes unless the partner confirms. An ASN marked cancelled on our
    side while the retailer still holds it is the failure this ordering exists to
    avoid — their warehouse is the one expecting the truck.
    """
    from sqlalchemy import select

    from app.models._enums import EdiDocType
    from app.models.asn import EdiAdvanceShipNotice
    from app.models.master_data import TradingPartner
    from app.models.outbound import EdiOutboundMessage

    asn = db.get(EdiAdvanceShipNotice, asn_id)
    if asn is None:
        return CancelResult(success=False, asn_number="", error="ASN not found")

    if str(asn.status) == "CANCELLED":
        return CancelResult(
            success=True,
            asn_number=asn.asn_number,
            already_cancelled=True,
        )

    partner = db.get(TradingPartner, asn.trading_partner_id)
    code = getattr(partner, "code", "")

    blocked = NO_CANCEL_API.get(code)
    if blocked:
        return CancelResult(success=False, asn_number=asn.asn_number, partner_code=code, error=blocked)

    msg = db.execute(
        select(EdiOutboundMessage)
        .where(
            EdiOutboundMessage.doc_type == EdiDocType.ASN_856,
            EdiOutboundMessage.external_reference == asn.asn_number,
        )
        .order_by(EdiOutboundMessage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if msg is None or str(msg.status) != "SENT":
        # Never accepted, so there is nothing at the partner to cancel. Marking it
        # cancelled here is honest and needs no call.
        asn.status = "CANCELLED"
        if msg is not None and str(msg.status) == "PENDING":
            msg.status = "FAILED"
            msg.next_retry_at = None
            msg.error_message = f"Cancelled locally by {cancelled_by} before dispatch."
        log.info("asn.cancelled_before_dispatch", asn_number=asn.asn_number, by=cancelled_by)
        return CancelResult(success=True, asn_number=asn.asn_number, partner_code=code)

    partner_ref = msg.partner_reference
    if not partner_ref:
        return CancelResult(
            success=False,
            asn_number=asn.asn_number,
            partner_code=code,
            error=(
                "This ASN was sent before the partner's own id was being stored, so "
                "there is nothing to address the cancellation to. Cancel it with "
                f"{code} directly."
            ),
        )

    result = _call_partner(code, partner_ref, _cancel_key(msg.id))
    if not result.get("success"):
        return CancelResult(
            success=False,
            asn_number=asn.asn_number,
            partner_code=code,
            partner_reference=partner_ref,
            error=str(result.get("error") or "Cancellation refused"),
        )

    asn.status = "CANCELLED"
    msg.status = "CANCELLED"
    msg.next_retry_at = None
    log.info(
        "asn.cancelled",
        asn_number=asn.asn_number,
        partner=code,
        partner_reference=partner_ref,
        by=cancelled_by,
    )
    return CancelResult(
        success=True,
        asn_number=asn.asn_number,
        partner_code=code,
        partner_reference=partner_ref,
    )


def _cancel_key(message_id: Any) -> str:
    """
    Idempotency key for the cancellation, distinct from the one used to create.

    The outbound message id was being sent for both, and Zepto matched the cancel
    against the create it had already processed:

        Past interaction found. Skipping duplicate event
        (requestId: 5b35d5ed-92a0-4a3d-a78f-b2a2394043b4)

    The contract says the key identifies a *request*, and create and cancel are two.
    Derived rather than random so a retried cancellation is still idempotent -- the
    point of the header -- while never colliding with the send.
    """
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"asn-cancel:{message_id}"))


def _call_partner(code: str, partner_reference: str, idempotency_key: str) -> dict[str, Any]:
    if code == "ZEPTO":
        from app.adapters.api.zepto_api import ZeptoApiAdapter

        return ZeptoApiAdapter().cancel_asn(partner_reference, idempotency_key=idempotency_key)

    return {
        "success": False,
        "error": f"No ASN cancellation is implemented for partner {code!r}.",
    }
