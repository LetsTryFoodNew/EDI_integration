"""
Exceptions & SKU-mapping API — Phase 5 (JSON-only; full UI comes in Phase 8).

GET  /api/exceptions          — list OPEN validation issues (paginated, filterable by severity)
POST /api/exceptions/{id}/resolve — mark one issue resolved with a note
POST /api/sku-mapping          — create or update a manual SKU mapping
GET  /api/sku-mapping          — list all SKU mappings (filterable by partner, status)
"""
from __future__ import annotations

import uuid  # noqa: TC003 — needed at runtime for Pydantic model fields and FastAPI params
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_sync_db

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["Exceptions"])


# ── Pydantic response/request models ─────────────────────────────────────────

class ValidationIssueOut(BaseModel):
    id: uuid.UUID
    po_id: uuid.UUID
    line_id: uuid.UUID | None
    issue_code: str
    severity: str
    message: str
    field_path: str | None
    validation_status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ExceptionsPage(BaseModel):
    items: list[ValidationIssueOut]
    total: int
    offset: int
    limit: int


class ResolveIssueRequest(BaseModel):
    resolution_notes: str = ""


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/exceptions", response_model=ExceptionsPage)
def list_exceptions(
    severity: str | None = Query(None, description="Filter by ERROR / WARNING / INFO"),
    partner_code: str | None = Query(None, description="Filter by trading partner code"),
    po_id: uuid.UUID | None = Query(None, description="Filter by specific PO"),  # noqa: B008
    offset: int = Query(0, ge=0),  # noqa: B008
    limit: int = Query(50, ge=1, le=200),  # noqa: B008
    db: Session = Depends(get_sync_db),  # noqa: B008
) -> dict[str, Any]:
    """Return OPEN validation issues, newest first."""
    from sqlalchemy import func, select

    from app.models._enums import PoStatus, ValidationStatus
    from app.models.edi_po import EdiPurchaseOrder, EdiValidationIssue
    from app.models.master_data import TradingPartner

    query = (
        select(EdiValidationIssue)
        .join(EdiPurchaseOrder, EdiValidationIssue.po_id == EdiPurchaseOrder.id)
        .where(
            EdiValidationIssue.validation_status == ValidationStatus.OPEN,
            EdiPurchaseOrder.deleted_at.is_(None),
            # Superseded/cancelled POs are out of the workflow — hide their issues
            EdiPurchaseOrder.po_status.notin_([PoStatus.SUPERSEDED, PoStatus.CANCELLED]),
        )
        .order_by(EdiValidationIssue.created_at.desc())
    )

    if severity:
        query = query.where(EdiValidationIssue.severity == severity.upper())

    if po_id:
        query = query.where(EdiValidationIssue.po_id == po_id)

    if partner_code:
        query = (
            query
            .join(TradingPartner, EdiPurchaseOrder.trading_partner_id == TradingPartner.id)
            .where(TradingPartner.code == partner_code.upper())
        )

    count_q = select(func.count()).select_from(query.subquery())
    total = db.execute(count_q).scalar_one()

    items = db.execute(query.offset(offset).limit(limit)).scalars().all()

    return {
        "items": [ValidationIssueOut.model_validate(i) for i in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("/exceptions/{issue_id}/resolve", response_model=ValidationIssueOut)
def resolve_exception(
    issue_id: uuid.UUID,
    body: ResolveIssueRequest,
    db: Session = Depends(get_sync_db),  # noqa: B008
) -> Any:
    """Mark a validation issue as RESOLVED."""
    from app.models._enums import ValidationStatus
    from app.models.edi_po import EdiValidationIssue

    issue = db.get(EdiValidationIssue, issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Validation issue not found")

    issue.validation_status = ValidationStatus.RESOLVED
    issue.resolved_by = "ops"
    issue.resolved_at = datetime.now(UTC)
    issue.resolution_notes = body.resolution_notes or None
    db.commit()
    db.refresh(issue)

    log.info("exceptions.resolved", issue_id=str(issue_id), code=issue.issue_code)
    return ValidationIssueOut.model_validate(issue)


# NOTE: POST /sku-mapping and GET /sku-mapping were removed here.
#
# SAP is the sole author of SKU_Mapping — b1ItemCode is not-null, so every row is a
# confirmed mapping and there is nothing for ops to create or correct locally. Letting
# ops write a mapping the next sync would overwrite (or silently preserve) is exactly
# the drift this design avoids.
#
# A PO line with no mapping surfaces as E002_SKU_UNRESOLVED in the exception list above;
# the fix is to add it in SAP, re-sync master data, then retry the PO.
# Read-only listing now lives at GET /api/master-data/sku-mappings.
