"""
Manual Inbox routes — partners whose orders arrive by hand rather than by wire.

Covers two channels:
  MANUAL — no integration at all; orders arrive by phone / WhatsApp / paper and
           will be keyed in through the manual sales-order form (next phase).
  PORTAL — partners whose portal scraping (Phase 9) is not built yet. Until it
           is, their orders are effectively manual too, so they live here rather
           than being invisible in every inbox.

Message listing, detail, retry-parse and attachment download reuse the
/api/inbox/messages* routes, which are partner-scoped and channel-agnostic — a third
copy of that logic would drift.

GET  /api/manual-inbox/partners — MANUAL + PORTAL partners with message counts
POST /api/manual-inbox/entries  — key in one purchase order by hand

A keyed-in order is stored as a raw_message exactly like a webhook body, then parsed,
validated, mapped and pushed by the same pipeline. Nothing downstream can tell it
apart from a Blinkit webhook, which is the whole point: manual partners get SKU
mapping, the SAP push and the outbound 855/856 for free.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.models._enums import SourceChannel
from app.schemas.api import (
    InboxPartnerSummary,
    ManualPoEntryRequest,
    ManualPoEntryResponse,
    PaginatedResponse,
    UserResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/manual-inbox", tags=["Manual Inbox"])

_MANUAL_CHANNELS = (SourceChannel.MANUAL, SourceChannel.PORTAL)


@router.get("/partners", response_model=PaginatedResponse[InboxPartnerSummary])
def list_manual_partners(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[InboxPartnerSummary]:
    """Return active MANUAL/PORTAL partners with per-partner message counts."""
    from sqlalchemy import func, select

    from app.models.master_data import TradingPartner
    from app.models.raw_messages import RawMessage

    base_q = select(TradingPartner).where(
        TradingPartner.is_active.is_(True),
        TradingPartner.deleted_at.is_(None),
        TradingPartner.source_channel.in_(_MANUAL_CHANNELS),
    )
    total = db.execute(select(func.count()).select_from(base_q.subquery())).scalar_one()
    partners = db.execute(
        base_q.order_by(TradingPartner.name).limit(limit).offset(offset)
    ).scalars().all()

    result: list[InboxPartnerSummary] = []
    for p in partners:
        counts = db.execute(
            select(
                func.count().label("total"),
                func.count().filter(RawMessage.parse_status == "PENDING").label("pending"),
                func.count().filter(RawMessage.parse_status == "FAILED").label("failed"),
                func.max(RawMessage.received_at).label("last_received_at"),
            ).where(RawMessage.trading_partner_id == p.id)
        ).one()

        result.append(InboxPartnerSummary(
            code=p.code,
            name=p.name,
            source_channel=str(p.source_channel),
            total=counts.total or 0,
            pending=counts.pending or 0,
            failed=counts.failed or 0,
            last_received_at=counts.last_received_at,
        ))

    return PaginatedResponse(items=result, total=total, limit=limit, offset=offset)


@router.post("/entries", response_model=ManualPoEntryResponse, status_code=201)
def create_manual_entry(
    entry: ManualPoEntryRequest,
    db: Session = Depends(get_sync_db),  # noqa: B008
    current_user: UserResponse = Depends(get_current_user),  # noqa: B008
) -> ManualPoEntryResponse:
    """
    Key in one purchase order for a partner with no integration.

    Stored verbatim as a raw_message, then handed to the same parse pipeline every
    other channel uses. The operator's keystrokes are never rewritten: raw_messages is
    the immutable record of what arrived, and the arithmetic — taxable amounts, the
    GST split, the totals — is derived in ManualEntryParser, so re-parsing the entry
    re-derives it rather than trusting a stored figure.
    """
    from sqlalchemy import func, select

    from app.models.master_data import SellerEntity, TradingPartner
    from app.models.raw_messages import RawMessage

    code = entry.partner_code.upper()
    partner = db.execute(
        select(TradingPartner).where(
            TradingPartner.code == code,
            TradingPartner.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if partner is None:
        raise HTTPException(status_code=404, detail=f"No trading partner {code!r}")
    if partner.source_channel not in _MANUAL_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{code} receives orders over {partner.source_channel}. Keying one in "
                f"by hand would create a second copy of an order that also arrives on "
                f"its own, so this is only allowed for manual and portal partners."
            ),
        )

    # The GST split needs both ends. Refusing here gives the operator the message on
    # the form they are looking at, rather than as a parse failure minutes later.
    if not (entry.ship_to.gstin or entry.ship_to.state or entry.buyer_gstin):
        raise HTTPException(
            status_code=422,
            detail=(
                "Enter the delivery state or a GSTIN — without one the CGST+SGST vs "
                "IGST split cannot be decided."
            ),
        )

    seller = db.execute(
        select(SellerEntity).where(SellerEntity.deleted_at.is_(None)).limit(1)
    ).scalar_one_or_none()
    if seller is None:
        raise HTTPException(
            status_code=409, detail="No seller entity configured — run seed_master_data.py"
        )

    # autoescape, because a PO number is free text and an underscore or percent in one
    # would otherwise match every other order.
    prior = db.execute(
        select(func.count())
        .select_from(RawMessage)
        .where(
            RawMessage.trading_partner_id == partner.id,
            RawMessage.external_id.startswith(
                _revision_prefix(entry.buyer_po_number), autoescape=True
            ),
        )
    ).scalar_one()
    if prior and not entry.replace_existing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"PO {entry.buyer_po_number} has already been entered for {code}. "
                f"Re-submit with 'replace_existing' to file this as a revision, which "
                f"supersedes the previous one."
            ),
        )
    revision = prior + 1

    raw_id = uuid.uuid4()
    db.add(RawMessage(
        id=raw_id,
        trading_partner_id=partner.id,
        source_channel=partner.source_channel,
        external_id=_external_id(entry.buyer_po_number, revision),
        received_at=datetime.now(UTC),
        payload=_entry_payload(entry, code, seller, current_user, revision),
        processed=False,
        parse_status="PENDING",
    ))
    db.commit()

    queued = _enqueue_parse(raw_id)
    log.info(
        "manual.entry_created",
        partner=code,
        po_number=entry.buyer_po_number,
        revision=revision,
        lines=len(entry.line_items),
        raw_id=str(raw_id),
        by=current_user.email,
        queued=queued,
    )

    return ManualPoEntryResponse(
        raw_message_id=raw_id,
        partner_code=code,
        buyer_po_number=entry.buyer_po_number,
        revision=revision,
        queued=queued,
        message=(
            f"PO {entry.buyer_po_number} recorded"
            + (f" as revision {revision}" if revision > 1 else "")
            + (". Parsing now." if queued else
               ". The parse queue is unreachable — retry from the message when it is back.")
        ),
    )


def _revision_prefix(po_number: str) -> str:
    """Everything in the natural key that is the same across revisions of one order."""
    return f"manual:{po_number}:"


def _external_id(po_number: str, revision: int) -> str:
    """
    Natural key for a keyed-in order.

    raw_messages is unique on (partner, external_id), so the revision has to be part
    of it or a corrected order could never be entered. The `manual:` prefix keeps
    these from ever colliding with a partner's own message ids.
    """
    return f"{_revision_prefix(po_number)}{revision}"


def _entry_payload(
    entry: ManualPoEntryRequest,
    partner_code: str,
    seller: object,
    user: UserResponse,
    revision: int,
) -> dict:
    """
    The stored payload: what the operator typed, plus who typed it and the seller's
    place of supply.

    The seller's GSTIN and state are copied in rather than looked up at parse time
    on purpose — they decide the tax split, and reading them later would silently
    re-tax an old order if the seller entity were ever edited.
    """
    payload = entry.model_dump(mode="json", exclude={"replace_existing"})
    payload.update({
        "_entry_type": "MANUAL_PO",
        "partner_code": partner_code,
        "revision": revision,
        "seller_gstin": getattr(seller, "gstin", None),
        "seller_state": getattr(seller, "state", None),
        "entered_by": user.email,
        "entered_at": datetime.now(UTC).isoformat(),
    })
    return payload


def _enqueue_parse(raw_id: uuid.UUID) -> bool:
    """Hand the entry to the parse worker. False if the queue is unreachable."""
    try:
        from redis import Redis
        from rq import Queue

        from app.config import get_settings
        from app.workers.jobs import parse_raw_message_job

        queue = Queue("ingest", connection=Redis.from_url(get_settings().redis_url))
        queue.enqueue(parse_raw_message_job, str(raw_id), job_timeout=300)
        return True
    except Exception as exc:  # queue down must not lose the entry — it is already saved
        log.error("manual.enqueue_failed", raw_id=str(raw_id), error=str(exc))
        return False
