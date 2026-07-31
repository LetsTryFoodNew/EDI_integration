"""
Manual Inbox routes — partners whose orders arrive by hand rather than by wire.

Covers two channels:
  MANUAL — no integration at all; orders arrive by phone / WhatsApp / paper and
           will be keyed in through the manual sales-order form (next phase).
  PORTAL — partners whose portal scraping (Phase 9) is not built yet. Until it
           is, their orders are effectively manual too, so they live here rather
           than being invisible in every inbox.

Only the partner list is defined here. Message listing, detail, retry-parse and
attachment download reuse the /api/inbox/messages* routes, which are
partner-scoped and channel-agnostic — a third copy of that logic would drift.

GET /api/manual-inbox/partners — MANUAL + PORTAL partners with message counts
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.models._enums import SourceChannel
from app.schemas.api import InboxPartnerSummary, PaginatedResponse, UserResponse

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

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
