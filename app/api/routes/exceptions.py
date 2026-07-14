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


class SkuMappingIn(BaseModel):
    trading_partner_id: uuid.UUID
    buyer_sku: str
    buyer_sku_description: str | None = None
    material_id: uuid.UUID | None = None
    b1_item_code: str | None = None     # alternative to material_id — looked up
    qty_per_buyer_uom: float = 1.0
    buyer_uom: str | None = None
    notes: str | None = None


class SkuMappingOut(BaseModel):
    id: uuid.UUID
    trading_partner_id: uuid.UUID
    buyer_sku: str
    buyer_sku_description: str | None
    material_id: uuid.UUID | None
    b1_item_code: str | None
    qty_per_buyer_uom: float
    mapping_status: str
    confidence_score: float | None
    notes: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}


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


@router.post("/sku-mapping", response_model=SkuMappingOut, status_code=201)
def upsert_sku_mapping(
    body: SkuMappingIn,
    db: Session = Depends(get_sync_db),  # noqa: B008
) -> Any:
    """
    Create or update a manual SKU mapping.

    If a mapping for (trading_partner_id, buyer_sku) already exists it is updated
    in-place and its status is set to MANUALLY_MAPPED.
    """
    from sqlalchemy import select

    from app.models._enums import MappingStatus
    from app.models.master_data import MaterialMaster, SkuMapping

    # Resolve material_id from b1_item_code if provided
    material_id = body.material_id
    if material_id is None and body.b1_item_code:
        mat = db.execute(
            select(MaterialMaster).where(MaterialMaster.b1_item_code == body.b1_item_code)
        ).scalar_one_or_none()
        if not mat:
            raise HTTPException(
                status_code=422,
                detail=f"MaterialMaster with b1_item_code='{body.b1_item_code}' not found",
            )
        material_id = mat.id

    existing = db.execute(
        select(SkuMapping).where(
            SkuMapping.trading_partner_id == body.trading_partner_id,
            SkuMapping.buyer_sku == body.buyer_sku,
            SkuMapping.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if existing:
        existing.buyer_sku_description = body.buyer_sku_description or existing.buyer_sku_description
        existing.material_id = material_id
        existing.qty_per_buyer_uom = body.qty_per_buyer_uom
        existing.buyer_uom = body.buyer_uom or existing.buyer_uom
        existing.mapping_status = MappingStatus.MANUALLY_MAPPED
        existing.confidence_score = None
        existing.notes = body.notes or existing.notes
        db.commit()
        db.refresh(existing)
        mapping = existing
    else:
        mapping = SkuMapping(
            trading_partner_id=body.trading_partner_id,
            buyer_sku=body.buyer_sku,
            buyer_sku_description=body.buyer_sku_description,
            material_id=material_id,
            qty_per_buyer_uom=body.qty_per_buyer_uom,
            buyer_uom=body.buyer_uom,
            mapping_status=MappingStatus.MANUALLY_MAPPED,
            notes=body.notes,
        )
        db.add(mapping)
        db.commit()
        db.refresh(mapping)

    # Resolve b1_item_code for the response
    b1_code: str | None = None
    if mapping.material_id:
        mat_row = db.get(MaterialMaster, mapping.material_id)
        if mat_row:
            b1_code = mat_row.b1_item_code

    log.info(
        "sku_mapping.upserted",
        buyer_sku=body.buyer_sku,
        partner_id=str(body.trading_partner_id),
        material_id=str(material_id),
    )

    return SkuMappingOut(
        id=mapping.id,
        trading_partner_id=mapping.trading_partner_id,
        buyer_sku=mapping.buyer_sku,
        buyer_sku_description=mapping.buyer_sku_description,
        material_id=mapping.material_id,
        b1_item_code=b1_code,
        qty_per_buyer_uom=float(mapping.qty_per_buyer_uom),
        mapping_status=str(mapping.mapping_status),
        confidence_score=float(mapping.confidence_score) if mapping.confidence_score else None,
        notes=mapping.notes,
        updated_at=mapping.updated_at,
    )


@router.get("/sku-mapping", response_model=list[SkuMappingOut])
def list_sku_mappings(
    partner_code: str | None = Query(None),
    status: str | None = Query(None, description="UNMAPPED / AUTO_MAPPED / MANUALLY_MAPPED"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),  # noqa: B008
) -> Any:
    """List SKU mappings, optionally filtered by partner or mapping_status."""
    from sqlalchemy import select

    from app.models._enums import MappingStatus
    from app.models.master_data import MaterialMaster, SkuMapping, TradingPartner

    q = select(SkuMapping).where(SkuMapping.deleted_at.is_(None))

    if partner_code:
        q = (
            q.join(TradingPartner, SkuMapping.trading_partner_id == TradingPartner.id)
            .where(TradingPartner.code == partner_code.upper())
        )

    if status:
        try:
            q = q.where(SkuMapping.mapping_status == MappingStatus(status.upper()))
        except ValueError as err:
            raise HTTPException(status_code=422, detail=f"Invalid status value: {status}") from err

    rows = db.execute(q.offset(offset).limit(limit)).scalars().all()

    out: list[SkuMappingOut] = []
    for m in rows:
        b1_code: str | None = None
        if m.material_id:
            mat = db.get(MaterialMaster, m.material_id)
            if mat:
                b1_code = mat.b1_item_code
        out.append(SkuMappingOut(
            id=m.id,
            trading_partner_id=m.trading_partner_id,
            buyer_sku=m.buyer_sku,
            buyer_sku_description=m.buyer_sku_description,
            material_id=m.material_id,
            b1_item_code=b1_code,
            qty_per_buyer_uom=float(m.qty_per_buyer_uom),
            mapping_status=str(m.mapping_status),
            confidence_score=float(m.confidence_score) if m.confidence_score else None,
            notes=m.notes,
            updated_at=m.updated_at,
        ))
    return out
