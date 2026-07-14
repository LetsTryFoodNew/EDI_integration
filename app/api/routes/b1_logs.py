"""
B1 API log routes — Phase 8.

GET /api/b1-logs          — list log entries (paginated, filterable)
GET /api/b1-logs/{log_id} — full request + response JSON for one entry
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.schemas.api import B1LogDetail, B1LogListItem, PaginatedResponse, UserResponse

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/b1-logs", tags=["B1 Logs"])


@router.get("", response_model=PaginatedResponse[B1LogListItem])
def list_b1_logs(
    po_id: uuid.UUID | None = Query(None),
    success: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[B1LogListItem]:
    from sqlalchemy import func, select

    from app.models.b1_log import B1ApiLog

    q = select(B1ApiLog).where().order_by(B1ApiLog.created_at.desc())
    if po_id is not None:
        q = q.where(B1ApiLog.po_id == po_id)
    if success is not None:
        q = q.where(B1ApiLog.success == success)

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.limit(limit).offset(offset)).scalars().all()

    return PaginatedResponse(
        items=[B1LogListItem.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{log_id}", response_model=B1LogDetail)
def get_b1_log(
    log_id: uuid.UUID,
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> B1LogDetail:
    from app.models.b1_log import B1ApiLog

    entry = db.get(B1ApiLog, log_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Log entry not found")
    return B1LogDetail.model_validate(entry)
