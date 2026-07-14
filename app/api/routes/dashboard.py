"""
Dashboard routes — Phase 8.

GET /api/dashboard/today        — PO counts by partner + error summary for today
GET /api/dashboard/sla-breaches — POs that missed their ACK SLA
GET /api/dashboard/unmapped-skus — SKUs that need manual mapping
GET /api/dashboard/activity     — last N events across the system
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, Query

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.schemas.api import (
    ActivityItem,
    DashboardToday,
    PartnerStat,
    SLABreachItem,
    UnmappedSkuItem,
    UserResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.get("/today", response_model=DashboardToday)
def dashboard_today(
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> DashboardToday:
    from sqlalchemy import func, select

    from app.models._enums import PoStatus
    from app.models.edi_po import EdiPurchaseOrder
    from app.models.master_data import TradingPartner

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    base_q = select(EdiPurchaseOrder).where(
        EdiPurchaseOrder.deleted_at.is_(None),
        EdiPurchaseOrder.created_at >= today_start,
    )

    total = db.execute(select(func.count()).select_from(base_q.subquery())).scalar_one()
    confirmed = db.execute(
        select(func.count()).select_from(
            base_q.where(EdiPurchaseOrder.po_status == PoStatus.SAP_CONFIRMED).subquery()
        )
    ).scalar_one()
    exceptions = db.execute(
        select(func.count()).select_from(
            base_q.where(EdiPurchaseOrder.po_status == PoStatus.EXCEPTION).subquery()
        )
    ).scalar_one()
    pending_b1 = db.execute(
        select(func.count()).select_from(
            base_q.where(EdiPurchaseOrder.po_status == PoStatus.VALIDATED).subquery()
        )
    ).scalar_one()

    # per-partner stats
    partner_rows = db.execute(
        select(
            TradingPartner.code,
            TradingPartner.name,
            func.count(EdiPurchaseOrder.id).label("po_count"),
            func.count(
                EdiPurchaseOrder.id
            ).filter(EdiPurchaseOrder.po_status == PoStatus.EXCEPTION).label("error_count"),
        )
        .join(TradingPartner, EdiPurchaseOrder.trading_partner_id == TradingPartner.id)
        .where(
            EdiPurchaseOrder.deleted_at.is_(None),
            EdiPurchaseOrder.created_at >= today_start,
        )
        .group_by(TradingPartner.code, TradingPartner.name)
    ).all()

    partner_stats = [
        PartnerStat(
            partner_code=row.code,
            partner_name=row.name,
            po_count=row.po_count,
            error_count=row.error_count,
        )
        for row in partner_rows
    ]

    return DashboardToday(
        total_pos=total,
        confirmed_pos=confirmed,
        exception_pos=exceptions,
        pending_b1_push=pending_b1,
        partner_stats=partner_stats,
        last_updated=datetime.now(UTC),
    )


@router.get("/sla-breaches", response_model=list[SLABreachItem])
def sla_breaches(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> list[SLABreachItem]:
    from sqlalchemy import select

    from app.models._enums import EdiDocType, PoStatus
    from app.models.edi_po import EdiPurchaseOrder
    from app.models.master_data import TradingPartner
    from app.models.outbound import EdiOutboundMessage

    now = datetime.now(UTC)

    # SAP_CONFIRMED POs whose ACK is still PENDING/not SENT past SLA
    rows = db.execute(
        select(EdiPurchaseOrder, TradingPartner)
        .join(TradingPartner, EdiPurchaseOrder.trading_partner_id == TradingPartner.id)
        .where(
            EdiPurchaseOrder.po_status == PoStatus.SAP_CONFIRMED,
            EdiPurchaseOrder.deleted_at.is_(None),
        )
        .order_by(EdiPurchaseOrder.created_at)
        .limit(limit * 3)  # fetch extra to filter
    ).all()

    breaches: list[SLABreachItem] = []
    for row in rows:
        po, partner = row.EdiPurchaseOrder, row.TradingPartner
        sla_hours = (partner.ack_sla_hours or 24)
        deadline = po.created_at + timedelta(hours=sla_hours)
        if now <= deadline:
            continue

        # Check if ACK was actually sent
        ack = db.execute(
            select(EdiOutboundMessage).where(
                EdiOutboundMessage.po_id == po.id,
                EdiOutboundMessage.doc_type == EdiDocType.PO_ACK_855,
                EdiOutboundMessage.status == "SENT",
            ).limit(1)
        ).scalar_one_or_none()
        if ack:
            continue

        hours_overdue = (now - deadline).total_seconds() / 3600
        breaches.append(SLABreachItem(
            po_id=po.id,
            buyer_po_number=po.buyer_po_number,
            partner_code=partner.code,
            po_status=str(po.po_status),
            hours_overdue=round(hours_overdue, 1),
            created_at=po.created_at,
        ))
        if len(breaches) >= limit:
            break

    return breaches


@router.get("/unmapped-skus", response_model=list[UnmappedSkuItem])
def unmapped_skus(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> list[UnmappedSkuItem]:
    from sqlalchemy import func, select

    from app.models._enums import MappingStatus
    from app.models.edi_po import EdiPoLineItem, EdiPurchaseOrder
    from app.models.master_data import TradingPartner

    rows = db.execute(
        select(
            EdiPoLineItem.buyer_sku,
            TradingPartner.code.label("partner_code"),
            EdiPoLineItem.description,
            func.count(EdiPoLineItem.id).label("occurrence_count"),
            func.max(EdiPoLineItem.created_at).label("last_seen"),
        )
        .join(EdiPurchaseOrder, EdiPoLineItem.po_id == EdiPurchaseOrder.id)
        .join(TradingPartner, EdiPurchaseOrder.trading_partner_id == TradingPartner.id)
        .where(
            EdiPoLineItem.mapping_status == MappingStatus.UNMAPPED,
            EdiPurchaseOrder.deleted_at.is_(None),
        )
        .group_by(EdiPoLineItem.buyer_sku, TradingPartner.code, EdiPoLineItem.description)
        .order_by(func.count(EdiPoLineItem.id).desc())
        .limit(limit)
    ).all()

    return [
        UnmappedSkuItem(
            buyer_sku=row.buyer_sku,
            partner_code=row.partner_code,
            description=row.description,
            occurrence_count=row.occurrence_count,
            last_seen=row.last_seen,
        )
        for row in rows
    ]


@router.get("/activity", response_model=list[ActivityItem])
def recent_activity(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> list[ActivityItem]:
    from sqlalchemy import select

    from app.models.edi_po import EdiPoStatusHistory, EdiPurchaseOrder

    rows = db.execute(
        select(EdiPoStatusHistory)
        .order_by(EdiPoStatusHistory.created_at.desc())
        .limit(limit)
    ).scalars().all()

    items: list[ActivityItem] = []
    for row in rows:
        po = db.get(EdiPurchaseOrder, row.po_id)
        items.append(ActivityItem(
            entity_type="EdiPurchaseOrder",
            entity_id=str(row.po_id),
            description=f"PO {po.buyer_po_number if po else str(row.po_id)} → {row.to_status}",
            status=str(row.to_status),
            created_at=row.created_at,
        ))
    return items
