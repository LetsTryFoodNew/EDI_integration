"""
API Inbox routes — raw messages received from REST API / webhook partners.

Mirrors the email inbox but filters source_channel IN ('API', 'WEBHOOK').

GET  /api/api-inbox/partners                        — platforms with message counts
GET  /api/api-inbox/status                          — per-partner connection health (last fetch, error counts)
GET  /api/api-inbox/messages?partner_code=...       — paginated messages for a platform
GET  /api/api-inbox/messages/{id}                   — full detail + raw JSON payload
POST /api/api-inbox/messages/{id}/retry-parse       — reset & re-queue a failed parse job
POST /api/api-inbox/trigger-fetch?partner_code=...  — immediately enqueue one Zepto poll (no wait)
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.models._enums import SourceChannel
from app.schemas.api import (
    ApiMessageDetail,
    ApiPartnerStatus,
    InboxMessageItem,
    InboxPartnerSummary,
    PaginatedResponse,
    UserResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/api-inbox", tags=["API Inbox"])

_API_CHANNELS = (SourceChannel.API, SourceChannel.WEBHOOK)


@router.get("/partners", response_model=PaginatedResponse[InboxPartnerSummary])
def list_api_partners(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[InboxPartnerSummary]:
    """Return active API/webhook partners with per-partner message counts."""
    from sqlalchemy import func, select

    from app.models.master_data import TradingPartner
    from app.models.raw_messages import RawMessage

    base_q = select(TradingPartner).where(
        TradingPartner.is_active.is_(True),
        TradingPartner.deleted_at.is_(None),
        TradingPartner.source_channel.in_(_API_CHANNELS),
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


@router.get("/status", response_model=list[ApiPartnerStatus])
def get_api_partner_status(
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> list[ApiPartnerStatus]:
    """
    Per-partner connection health: last fetch time, 24-hour message counts, whether
    credentials are configured in the current environment settings.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from app.config import get_settings
    from app.models.master_data import TradingPartner
    from app.models.raw_messages import RawMessage

    settings = get_settings()
    since_24h = datetime.now(UTC) - timedelta(hours=24)

    partners = db.execute(
        select(TradingPartner).where(
            TradingPartner.is_active.is_(True),
            TradingPartner.deleted_at.is_(None),
            TradingPartner.source_channel.in_(_API_CHANNELS),
        ).order_by(TradingPartner.name)
    ).scalars().all()

    def _is_configured(code: str) -> bool:
        if code == "ZEPTO":
            return bool(settings.zepto_client_id and settings.zepto_client_secret)
        if code == "BLINKIT":
            return bool(settings.blinkit_api_key)
        return False

    def _webhook_url(partner: Any) -> str | None:
        if str(partner.source_channel) == "WEBHOOK":
            return f"/api/webhooks/{partner.code}"
        return None

    result: list[ApiPartnerStatus] = []
    for p in partners:
        counts = db.execute(
            select(
                func.count().label("total_24h"),
                func.count().filter(RawMessage.parse_status == "FAILED").label("failed_24h"),
                func.max(RawMessage.received_at).label("last_message_at"),
            ).where(
                RawMessage.trading_partner_id == p.id,
                RawMessage.received_at >= since_24h,
            )
        ).one()

        import contextlib

        api_config: dict = p.api_config or {}
        last_fetched_raw = api_config.get("last_fetched_at")
        last_fetched_at: dt.datetime | None = None
        if last_fetched_raw:
            with contextlib.suppress(Exception):
                last_fetched_at = dt.datetime.fromisoformat(str(last_fetched_raw)).replace(
                    tzinfo=dt.UTC
                )

        result.append(ApiPartnerStatus(
            code=p.code,
            name=p.name,
            source_channel=str(p.source_channel),
            last_fetched_at=last_fetched_at,
            last_message_at=counts.last_message_at,
            messages_last_24h=counts.total_24h or 0,
            failed_last_24h=counts.failed_24h or 0,
            webhook_url=_webhook_url(p),
            is_configured=_is_configured(p.code),
        ))

    return result


@router.post("/trigger-fetch")
def trigger_fetch(
    partner_code: str = Query(..., description="Partner code to fetch POs for (e.g. ZEPTO)"),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Manually enqueue an immediate API poll job for a pull-based partner (e.g. ZEPTO).
    Returns immediately; the actual fetch runs asynchronously in the RQ worker.
    Not applicable to WEBHOOK-only partners (BLINKIT) — they receive POs via push.
    """
    from sqlalchemy import select

    from app.models.master_data import TradingPartner

    partner_code = partner_code.upper()
    partner = db.execute(
        select(TradingPartner).where(
            TradingPartner.code == partner_code,
            TradingPartner.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if not partner:
        raise HTTPException(status_code=404, detail=f"Partner '{partner_code}' not found")

    if str(partner.source_channel) == "WEBHOOK":
        raise HTTPException(
            status_code=400,
            detail=f"{partner_code} is a webhook-push partner — POs arrive via POST /api/webhooks/{partner_code}. No manual fetch needed.",
        )

    job_id = _enqueue_api_fetch(partner_code)
    return {
        "status": "queued",
        "partner_code": partner_code,
        "job_id": job_id,
        "message": f"Fetch job enqueued for {partner_code}. Check API Inbox in ~30 seconds.",
    }


@router.get("/messages", response_model=PaginatedResponse[InboxMessageItem])
def list_api_messages(
    partner_code: str = Query(..., description="Partner code to filter by"),
    parse_status: str | None = Query(None),
    search: str | None = Query(None, description="Match PO number or external_id"),
    date_from: dt.date | None = Query(None),
    date_to: dt.date | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[InboxMessageItem]:
    """List API/webhook raw messages for a specific platform, newest first."""
    from sqlalchemy import exists, func, select

    from app.models.edi_po import EdiPurchaseOrder
    from app.models.master_data import TradingPartner
    from app.models.raw_messages import RawMessage

    partner = db.execute(
        select(TradingPartner).where(TradingPartner.code == partner_code)
    ).scalar_one_or_none()
    if not partner:
        raise HTTPException(status_code=404, detail=f"Partner '{partner_code}' not found")

    base_q = select(RawMessage).where(
        RawMessage.trading_partner_id == partner.id,
        RawMessage.source_channel.in_(_API_CHANNELS),
    )
    if parse_status:
        base_q = base_q.where(RawMessage.parse_status == parse_status)
    if search:
        pattern = f"%{search.strip()}%"
        po_match = exists(
            select(EdiPurchaseOrder.id).where(
                EdiPurchaseOrder.raw_message_id == RawMessage.id,
                EdiPurchaseOrder.buyer_po_number.ilike(pattern),
            )
        )
        ext_id_match = RawMessage.external_id.ilike(pattern)
        base_q = base_q.where(po_match | ext_id_match)

    ist_date = func.date(func.timezone("Asia/Kolkata", RawMessage.received_at))
    if date_from:
        base_q = base_q.where(ist_date >= date_from)
    if date_to:
        base_q = base_q.where(ist_date <= date_to)

    total = db.execute(
        select(func.count()).select_from(base_q.subquery())
    ).scalar_one()

    messages = db.execute(
        base_q.order_by(RawMessage.received_at.desc()).limit(limit).offset(offset)
    ).scalars().all()

    msg_ids = [m.id for m in messages]
    po_map: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    if msg_ids:
        po_rows = db.execute(
            select(EdiPurchaseOrder.raw_message_id, EdiPurchaseOrder.id, EdiPurchaseOrder.buyer_po_number)
            .where(EdiPurchaseOrder.raw_message_id.in_(msg_ids))
        ).all()
        for row in po_rows:
            if row.raw_message_id:
                po_map[row.raw_message_id] = (row.id, row.buyer_po_number)

    items = []
    for msg in messages:
        po_entry = po_map.get(msg.id)
        items.append(InboxMessageItem(
            id=msg.id,
            external_id=msg.external_id,
            subject=None,   # API messages have no email subject
            sender=None,
            received_at=msg.received_at,
            attachment_count=0,
            parse_status=msg.parse_status,
            processed=msg.processed,
            po_id=po_entry[0] if po_entry else None,
            po_number=po_entry[1] if po_entry else None,
        ))

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/messages/{message_id}", response_model=ApiMessageDetail)
def get_api_message(
    message_id: uuid.UUID,
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> ApiMessageDetail:
    """Full detail of one API/webhook raw message, including the raw JSON payload."""
    from sqlalchemy import select

    from app.models.edi_po import EdiPurchaseOrder
    from app.models.master_data import TradingPartner
    from app.models.raw_messages import RawMessage

    msg = db.get(RawMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    partner = db.get(TradingPartner, msg.trading_partner_id)

    po = db.execute(
        select(EdiPurchaseOrder).where(EdiPurchaseOrder.raw_message_id == msg.id)
    ).scalar_one_or_none()

    return ApiMessageDetail(
        id=msg.id,
        partner_code=partner.code if partner else "",
        partner_name=partner.name if partner else "",
        external_id=msg.external_id,
        received_at=msg.received_at,
        payload=msg.payload,
        parse_status=msg.parse_status,
        processed=msg.processed,
        po_id=po.id if po else None,
        po_number=po.buyer_po_number if po else None,
        po_status=str(po.po_status) if po else None,
        created_at=msg.created_at,
    )


@router.post("/messages/{message_id}/retry-parse")
def retry_api_parse(
    message_id: uuid.UUID,
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    """Reset parse status to PENDING and re-enqueue the parse job."""
    from app.models.raw_messages import RawMessage

    msg = db.get(RawMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.parse_status == "SUCCESS":
        raise HTTPException(status_code=400, detail="Message already parsed successfully")

    msg.parse_status = "PENDING"
    msg.processed = False
    db.commit()

    _enqueue_parse(str(message_id))
    return {"status": "queued", "message_id": str(message_id)}


def _enqueue_api_fetch(partner_code: str) -> str:
    """Enqueue one fetch_api_partner_job and return the RQ job ID."""
    try:
        from redis import Redis
        from rq import Queue

        from app.config import get_settings
        from app.workers.jobs import fetch_api_partner_job

        redis_conn = Redis.from_url(get_settings().redis_url)
        job = Queue("ingest", connection=redis_conn).enqueue(
            fetch_api_partner_job,
            partner_code,
            job_timeout=600,
            result_ttl=3600,
            failure_ttl=86400,
        )
        return job.id
    except Exception as exc:
        import structlog
        structlog.get_logger(__name__).error(
            "api_inbox.enqueue_fetch_failed", partner=partner_code, error=str(exc)
        )
        raise HTTPException(status_code=503, detail=f"Failed to enqueue fetch job: {exc}") from exc


def _enqueue_parse(message_id: str) -> None:
    try:
        from redis import Redis
        from rq import Queue

        from app.config import get_settings
        from app.workers.jobs import parse_raw_message_job

        redis_conn = Redis.from_url(get_settings().redis_url)
        Queue("ingest", connection=redis_conn).enqueue(
            parse_raw_message_job, message_id, job_timeout=300
        )
    except Exception as exc:
        import structlog
        structlog.get_logger(__name__).error(
            "api_inbox.enqueue_parse_failed", message_id=message_id, error=str(exc)
        )
