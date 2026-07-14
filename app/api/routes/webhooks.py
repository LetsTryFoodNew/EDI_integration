"""
Inbound webhook endpoint — partners POST PO creation events here.

POST /api/webhooks/{partner_code}

Currently supported:
  BLINKIT — Blinkit pushes PO_CREATION / PO_CANCELLATION events

Authentication:
  Blinkit passes our `api-key` in the request header.
  We compare it against TradingPartner.webhook_secret (stored at setup time).

Pipeline:
  1. Validate auth
  2. Store as raw_message (idempotent — same po_number + partner is a no-op)
  3. Enqueue parse_raw_message_job
  4. Return Blinkit-format ACK immediately (< 2s required by Blinkit contract)
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.api.deps import get_sync_db

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])


@router.post("/{partner_code}", status_code=200)
async def inbound_webhook(
    partner_code: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_sync_db),  # noqa: B008
) -> dict[str, Any]:
    """
    Receive an inbound PO webhook from a retail partner.

    Blinkit webhook URL to share: POST /api/webhooks/BLINKIT
    The endpoint returns 200 with an ACK body immediately; the actual
    RawMessage save + parse enqueue happen synchronously before the response.
    """
    partner_code = partner_code.upper()

    # 1. Load partner from DB
    from sqlalchemy import select

    from app.models.master_data import TradingPartner
    partner = db.execute(
        select(TradingPartner).where(
            TradingPartner.code == partner_code,
            TradingPartner.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if not partner:
        log.warning("webhook.unknown_partner", partner_code=partner_code)
        raise HTTPException(status_code=404, detail=f"Partner '{partner_code}' not found")

    # 2. Parse body (never crash on bad input — always return a 200 to Blinkit)
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:
        log.error("webhook.json_parse_error", partner=partner_code, error=str(exc))
        body = {}

    # 3. Validate webhook auth (api-key header vs TradingPartner.webhook_secret)
    if partner.webhook_secret:
        incoming_key = request.headers.get("api-key") or request.headers.get("x-api-key") or ""
        if incoming_key != partner.webhook_secret:
            log.warning(
                "webhook.auth_failed",
                partner=partner_code,
                source_ip=request.client.host if request.client else "?",
            )
            raise HTTPException(status_code=401, detail="Invalid api-key")

    # 4. Route to partner-specific handler
    if partner_code == "BLINKIT":
        return await _handle_blinkit(body, partner, db, background_tasks, request)

    # Generic fallback for future partners — store + enqueue, return 200
    return await _handle_generic(body, partner, db, partner_code)


# ── Partner-specific handlers ─────────────────────────────────────────────────

async def _handle_blinkit(
    body: dict[str, Any],
    partner: Any,
    db: Session,
    background_tasks: BackgroundTasks,
    request: Request,
) -> dict[str, Any]:
    """
    Store the Blinkit PO event as a RawMessage and enqueue a parse job.
    Return Blinkit's required ACK format immediately.

    Blinkit reads `data.po_status` from our response — "processing" means we got it.
    The final accepted/rejected status is sent later via BlinkitApiAdapter.acknowledge_po().
    """
    po_number = str(body.get("po_number") or "")[:200] or f"UNKNOWN_{uuid.uuid4().hex[:8].upper()}"
    event_type = str(body.get("type") or "PO_CREATION")

    log.info(
        "webhook.blinkit.received",
        po_number=po_number,
        event_type=event_type,
        source_ip=request.client.host if request.client else "?",
    )

    raw_id = _save_raw_message(
        db=db,
        partner=partner,
        external_id=po_number,
        payload=body,
        source_channel="WEBHOOK",
    )

    if raw_id:
        _enqueue_parse(raw_id, background_tasks)

    return {
        "success": True,
        "message": "PO received",
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": {
            "po_status": "processing",
            "po_number": po_number,
            "errors": [],
            "warnings": [],
        },
    }


async def _handle_generic(
    body: dict[str, Any],
    partner: Any,
    db: Session,
    partner_code: str,
) -> dict[str, Any]:
    """Fallback: store body as RawMessage, return minimal 200."""
    external_id = (
        str(body.get("po_number") or body.get("orderId") or body.get("id") or uuid.uuid4())
    )[:200]

    raw_id = _save_raw_message(
        db=db,
        partner=partner,
        external_id=external_id,
        payload=body,
        source_channel="WEBHOOK",
    )
    log.info("webhook.generic.received", partner=partner_code, external_id=external_id, raw_id=raw_id)
    return {"success": True, "received": True}


# ── Shared helpers ────────────────────────────────────────────────────────────

def _save_raw_message(
    db: Session,
    partner: Any,
    external_id: str,
    payload: dict[str, Any],
    source_channel: str,
) -> uuid.UUID | None:
    """
    Save the webhook payload as an immutable RawMessage.
    Returns None and does nothing if the same (partner, external_id) already exists.
    """
    from sqlalchemy import select

    from app.models._enums import SourceChannel
    from app.models.raw_messages import RawMessage

    already = db.execute(
        select(RawMessage).where(
            RawMessage.trading_partner_id == partner.id,
            RawMessage.external_id == external_id,
        )
    ).scalar_one_or_none()

    if already:
        log.debug("webhook.duplicate_skipped", partner=partner.code, external_id=external_id)
        return None

    raw_id = uuid.uuid4()
    channel = SourceChannel(source_channel)
    db.add(RawMessage(
        id=raw_id,
        trading_partner_id=partner.id,
        source_channel=channel,
        external_id=external_id,
        received_at=datetime.now(UTC),
        payload=payload,
        processed=False,
        parse_status="PENDING",
    ))
    db.commit()
    log.info("webhook.raw_saved", partner=partner.code, external_id=external_id, raw_id=str(raw_id))
    return raw_id


def _enqueue_parse(raw_id: uuid.UUID, background_tasks: BackgroundTasks) -> None:
    """Enqueue parse job via RQ (non-blocking — done in background task)."""
    def _enqueue() -> None:
        try:
            from redis import Redis
            from rq import Queue

            from app.config import get_settings
            from app.workers.jobs import parse_raw_message_job
            redis_conn = Redis.from_url(get_settings().redis_url)
            Queue("ingest", connection=redis_conn).enqueue(
                parse_raw_message_job,
                str(raw_id),
                job_timeout=300,
            )
            log.debug("webhook.parse_enqueued", raw_id=str(raw_id))
        except Exception as exc:
            log.error("webhook.enqueue_error", raw_id=str(raw_id), error=str(exc))

    background_tasks.add_task(_enqueue)
