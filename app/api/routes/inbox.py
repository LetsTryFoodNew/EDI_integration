"""
Inbox routes — raw email/PO message view, organized by platform.

GET  /api/inbox/partners                       — platforms with message counts
GET  /api/inbox/messages?partner_code=...      — paginated raw messages for a platform
GET  /api/inbox/messages/{id}                  — full detail of one raw message
POST /api/inbox/messages/{id}/retry-parse      — reset & re-queue a failed parse job
POST /api/inbox/retry-all-failed?partner_code= — re-queue all failed jobs for a partner
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.schemas.api import (
    AttachmentInfo,
    InboxMessageDetail,
    InboxMessageItem,
    InboxPartnerSummary,
    PaginatedResponse,
    UserResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/inbox", tags=["Inbox"])


@router.get("/partners", response_model=list[InboxPartnerSummary])
def list_inbox_partners(
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> list[InboxPartnerSummary]:
    """Return all active partners that have a Gmail label, with message counts."""
    from sqlalchemy import func, select

    from app.models.master_data import TradingPartner
    from app.models.raw_messages import RawMessage

    partners = db.execute(
        select(TradingPartner).where(
            TradingPartner.is_active.is_(True),
            TradingPartner.deleted_at.is_(None),
            TradingPartner.gmail_label.isnot(None),
        ).order_by(TradingPartner.name)
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
            gmail_label=p.gmail_label,
            total=counts.total or 0,
            pending=counts.pending or 0,
            failed=counts.failed or 0,
            last_received_at=counts.last_received_at,
        ))

    return result


@router.get("/messages", response_model=PaginatedResponse[InboxMessageItem])
def list_inbox_messages(
    partner_code: str = Query(..., description="Partner code to filter by"),
    parse_status: str | None = Query(None),
    search: str | None = Query(None, description="Match PO number or email subject"),
    date_from: dt.date | None = Query(None, description="Received on/after this date (IST)"),
    date_to: dt.date | None = Query(None, description="Received on/before this date (IST)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[InboxMessageItem]:
    """List raw messages for a specific platform, newest first."""
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
        subject_match = RawMessage.headers["subject"].astext.ilike(pattern)
        base_q = base_q.where(po_match | subject_match)
    # Dates are compared in IST — the timezone the ops team sees in the UI.
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

    # Look up canonical PO IDs for these messages in one query
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
        atts = msg.attachment_paths or []
        po_entry = po_map.get(msg.id)
        subject = (msg.headers or {}).get("subject")
        sender = (msg.headers or {}).get("from")
        items.append(InboxMessageItem(
            id=msg.id,
            external_id=msg.external_id,
            subject=subject,
            sender=sender,
            received_at=msg.received_at,
            attachment_count=len(atts),
            parse_status=msg.parse_status,
            processed=msg.processed,
            po_id=po_entry[0] if po_entry else None,
            po_number=po_entry[1] if po_entry else None,
        ))

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/messages/{message_id}", response_model=InboxMessageDetail)
def get_inbox_message(
    message_id: uuid.UUID,
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> InboxMessageDetail:
    """Full detail of one raw message, including attachments and linked canonical PO."""
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

    raw_atts = msg.attachment_paths or []
    attachments = [
        AttachmentInfo(
            filename=a.get("filename", ""),
            url=a.get("url", a.get("path", "")),   # Cloudinary url or legacy disk path
            mime_type=a.get("mime_type", "application/octet-stream"),
            size_bytes=a.get("size_bytes", 0),
        )
        for a in raw_atts
    ]

    headers = msg.headers or {}
    body_preview = (msg.payload_raw or "")[:500] or None

    return InboxMessageDetail(
        id=msg.id,
        partner_code=partner.code if partner else "",
        partner_name=partner.name if partner else "",
        external_id=msg.external_id,
        subject=headers.get("subject"),
        sender=headers.get("from"),
        received_at=msg.received_at,
        attachments=attachments,
        body_preview=body_preview,
        parse_status=msg.parse_status,
        processed=msg.processed,
        po_id=po.id if po else None,
        po_number=po.buyer_po_number if po else None,
        po_status=str(po.po_status) if po else None,
        created_at=msg.created_at,
    )


@router.post("/messages/{message_id}/retry-parse")
def retry_parse(
    message_id: uuid.UUID,
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> dict:
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


@router.post("/retry-all-failed")
def retry_all_failed(
    partner_code: str = Query(..., description="Partner code whose failed messages to re-queue"),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> dict:
    """Re-queue all FAILED parse jobs for a given partner."""
    from sqlalchemy import select

    from app.models.master_data import TradingPartner
    from app.models.raw_messages import RawMessage

    partner = db.execute(
        select(TradingPartner).where(TradingPartner.code == partner_code)
    ).scalar_one_or_none()
    if not partner:
        raise HTTPException(status_code=404, detail=f"Partner '{partner_code}' not found")

    failed = db.execute(
        select(RawMessage).where(
            RawMessage.trading_partner_id == partner.id,
            RawMessage.parse_status == "FAILED",
        )
    ).scalars().all()

    for msg in failed:
        msg.parse_status = "PENDING"
        msg.processed = False

    db.commit()

    for msg in failed:
        _enqueue_parse(str(msg.id))

    return {"status": "queued", "queued_count": len(failed), "partner_code": partner_code}


@router.get("/messages/{message_id}/attachments/{index}")
def download_attachment(
    message_id: uuid.UUID,
    index: int,
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> Any:
    """
    Stream one attachment through the backend.

    Cloudinary blocks public delivery of PDF files (401), so the browser can't
    open att.url directly. We fetch the original with API credentials via a
    signed private-download URL and return it inline. Local-disk attachments
    (dev fallback) are read from disk.
    """
    import mimetypes
    from pathlib import Path

    from fastapi import Response

    from app.models.raw_messages import RawMessage

    msg = db.get(RawMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    atts = msg.attachment_paths or []
    if not isinstance(atts, list) or index < 0 or index >= len(atts):
        raise HTTPException(status_code=404, detail="Attachment not found")

    att = atts[index]
    filename = att.get("filename") or "attachment"
    url = att.get("url") or ""
    public_id = att.get("public_id") or ""

    if url.startswith("https://res.cloudinary.com"):
        content = _fetch_cloudinary_raw(public_id)
    else:
        p = Path(public_id or url)
        if not p.is_file():
            raise HTTPException(status_code=404, detail="Attachment file missing on disk")
        content = p.read_bytes()

    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_cloudinary_raw(public_id: str) -> bytes:
    """Download a raw Cloudinary asset with API credentials (bypasses PDF delivery block)."""
    import time

    import requests
    from cloudinary.utils import private_download_url

    from app.adapters.storage import _ensure_cloudinary_configured

    _ensure_cloudinary_configured()
    signed = private_download_url(
        public_id, "", resource_type="raw", type="upload",
        expires_at=int(time.time()) + 300,
    )
    resp = requests.get(signed, timeout=60)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Cloudinary download failed ({resp.status_code})",
        )
    return resp.content


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
        structlog.get_logger(__name__).error("inbox.enqueue_parse_failed", message_id=message_id, error=str(exc))
