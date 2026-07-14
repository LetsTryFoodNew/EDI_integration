"""
Master data routes — Phase 8.

Partners:      GET /api/master-data/partners, PATCH /api/master-data/partners/{id}
Materials:     GET /api/master-data/materials, POST, PATCH /api/master-data/materials/{id}
SKU mappings:  GET /api/master-data/sku-mappings, PATCH /api/master-data/sku-mappings/{id}
Ship-to:       GET /api/master-data/ship-to, PATCH /api/master-data/ship-to/{id}
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.schemas.api import (
    MaterialMasterCreate,
    MaterialMasterResponse,
    PaginatedResponse,
    ShipToMappingResponse,
    ShipToMappingUpdate,
    SkuMappingResponse,
    SkuMappingUpdate,
    TradingPartnerResponse,
    TradingPartnerUpdate,
    UserResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/master-data", tags=["Master Data"])


# ── Trading Partners ──────────────────────────────────────────────────────────

@router.get("/partners", response_model=PaginatedResponse[TradingPartnerResponse])
def list_partners(
    is_active: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[TradingPartnerResponse]:
    from sqlalchemy import func, select

    from app.models.master_data import TradingPartner

    q = select(TradingPartner).where(TradingPartner.deleted_at.is_(None))
    if is_active is not None:
        q = q.where(TradingPartner.is_active == is_active)
    q = q.order_by(TradingPartner.name)

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.limit(limit).offset(offset)).scalars().all()
    return PaginatedResponse(
        items=[TradingPartnerResponse.model_validate(r) for r in rows],
        total=total, limit=limit, offset=offset,
    )


@router.patch("/partners/{partner_id}", response_model=TradingPartnerResponse)
def update_partner(
    partner_id: uuid.UUID,
    body: TradingPartnerUpdate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> TradingPartnerResponse:
    from app.models.audit_log import AuditLog
    from app.models.master_data import TradingPartner

    partner = db.get(TradingPartner, partner_id)
    if not partner or partner.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Partner not found")

    update_data = body.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(partner, field, value)

    db.add(AuditLog(
        user_email=current_user.email,
        action="update_partner",
        entity_type="TradingPartner",
        entity_id=str(partner_id),
        payload=update_data,
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    db.refresh(partner)
    return TradingPartnerResponse.model_validate(partner)


# ── Material Master ───────────────────────────────────────────────────────────

@router.get("/materials", response_model=PaginatedResponse[MaterialMasterResponse])
def list_materials(
    search: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[MaterialMasterResponse]:
    from sqlalchemy import func, select

    from app.models.master_data import MaterialMaster

    q = select(MaterialMaster).where(MaterialMaster.deleted_at.is_(None))
    if search:
        q = q.where(
            MaterialMaster.b1_item_code.ilike(f"%{search}%") |
            MaterialMaster.description.ilike(f"%{search}%")
        )
    q = q.order_by(MaterialMaster.b1_item_code)

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.limit(limit).offset(offset)).scalars().all()
    return PaginatedResponse(
        items=[MaterialMasterResponse.model_validate(r) for r in rows],
        total=total, limit=limit, offset=offset,
    )


@router.post("/materials", response_model=MaterialMasterResponse, status_code=201)
def create_material(
    body: MaterialMasterCreate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MaterialMasterResponse:
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import MaterialMaster

    existing = db.execute(
        select(MaterialMaster).where(MaterialMaster.b1_item_code == body.b1_item_code)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Item code already exists")

    material = MaterialMaster(**body.model_dump())
    db.add(material)
    db.add(AuditLog(
        user_email=current_user.email,
        action="create_material",
        entity_type="MaterialMaster",
        payload=body.model_dump(),
        ip_address=request.client.host if request.client else None,
    ))
    db.flush()
    db.commit()
    db.refresh(material)
    return MaterialMasterResponse.model_validate(material)


# ── SKU Mappings ──────────────────────────────────────────────────────────────

@router.get("/sku-mappings", response_model=PaginatedResponse[SkuMappingResponse])
def list_sku_mappings(
    partner_code: str | None = Query(None),
    mapping_status: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[SkuMappingResponse]:
    from sqlalchemy import func, select

    from app.models.master_data import MaterialMaster, SkuMapping, TradingPartner

    q = (
        select(SkuMapping, TradingPartner.code.label("partner_code"), MaterialMaster.b1_item_code)
        .join(TradingPartner, SkuMapping.trading_partner_id == TradingPartner.id)
        .outerjoin(MaterialMaster, SkuMapping.material_id == MaterialMaster.id)
        .where(TradingPartner.deleted_at.is_(None))
        .where(SkuMapping.deleted_at.is_(None))
        .order_by(TradingPartner.code, SkuMapping.buyer_sku)
    )
    if partner_code:
        q = q.where(TradingPartner.code == partner_code)
    if mapping_status:
        q = q.where(SkuMapping.mapping_status == mapping_status)
    if search:
        q = q.where(SkuMapping.buyer_sku.ilike(f"%{search}%"))

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.limit(limit).offset(offset)).all()

    items = [
        SkuMappingResponse(
            id=row.SkuMapping.id,
            trading_partner_id=row.SkuMapping.trading_partner_id,
            partner_code=row.partner_code,
            buyer_sku=row.SkuMapping.buyer_sku,
            material_id=row.SkuMapping.material_id,
            b1_item_code=row.b1_item_code,
            qty_per_buyer_uom=row.SkuMapping.qty_per_buyer_uom,
            mapping_status=str(row.SkuMapping.mapping_status),
            confidence_score=row.SkuMapping.confidence_score,
            notes=row.SkuMapping.notes,
            created_at=row.SkuMapping.created_at,
        )
        for row in rows
    ]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.patch("/sku-mappings/{mapping_id}", response_model=SkuMappingResponse)
def update_sku_mapping(
    mapping_id: uuid.UUID,
    body: SkuMappingUpdate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> SkuMappingResponse:
    from sqlalchemy import select

    from app.models._enums import MappingStatus
    from app.models.audit_log import AuditLog
    from app.models.master_data import MaterialMaster, SkuMapping, TradingPartner

    mapping = db.get(SkuMapping, mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="SKU mapping not found")

    material = db.execute(
        select(MaterialMaster).where(
            MaterialMaster.b1_item_code == body.b1_item_code.strip().upper(),
            MaterialMaster.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not material:
        raise HTTPException(
            status_code=404,
            detail=f"Material '{body.b1_item_code}' not found in Material Master",
        )

    mapping.material_id = material.id
    mapping.qty_per_buyer_uom = body.qty_per_buyer_uom
    mapping.mapping_status = MappingStatus.MANUALLY_MAPPED
    mapping.confidence_score = 1.0
    if body.notes:
        mapping.notes = body.notes

    db.add(AuditLog(
        user_email=current_user.email,
        action="update_sku_mapping",
        entity_type="SkuMapping",
        entity_id=str(mapping_id),
        payload={"b1_item_code": material.b1_item_code, "qty": str(body.qty_per_buyer_uom)},
        ip_address=request.client.host if request.client else None,
    ))
    db.flush()
    db.commit()

    partner = db.get(TradingPartner, mapping.trading_partner_id)
    return SkuMappingResponse(
        id=mapping.id,
        trading_partner_id=mapping.trading_partner_id,
        partner_code=partner.code if partner else "",
        buyer_sku=mapping.buyer_sku,
        material_id=mapping.material_id,
        b1_item_code=material.b1_item_code,
        qty_per_buyer_uom=mapping.qty_per_buyer_uom,
        mapping_status=str(mapping.mapping_status),
        confidence_score=mapping.confidence_score,
        notes=mapping.notes,
        created_at=mapping.created_at,
    )


# ── Ship-to Mappings ──────────────────────────────────────────────────────────

@router.get("/ship-to", response_model=PaginatedResponse[ShipToMappingResponse])
def list_ship_to(
    partner_code: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[ShipToMappingResponse]:
    from sqlalchemy import func, select

    from app.models.master_data import ShipToMapping, TradingPartner

    q = (
        select(ShipToMapping, TradingPartner.code.label("partner_code"))
        .join(TradingPartner, ShipToMapping.trading_partner_id == TradingPartner.id)
        .where(TradingPartner.deleted_at.is_(None))
        .order_by(TradingPartner.code, ShipToMapping.buyer_whs_code)
    )
    if partner_code:
        q = q.where(TradingPartner.code == partner_code)

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.limit(limit).offset(offset)).all()

    items = [
        ShipToMappingResponse(
            id=row.ShipToMapping.id,
            trading_partner_id=row.ShipToMapping.trading_partner_id,
            partner_code=row.partner_code,
            buyer_whs_code=row.ShipToMapping.buyer_whs_code,
            b1_whs_code=row.ShipToMapping.b1_whs_code,
            is_active=row.ShipToMapping.is_active,
        )
        for row in rows
    ]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.patch("/ship-to/{mapping_id}", response_model=ShipToMappingResponse)
def update_ship_to(
    mapping_id: uuid.UUID,
    body: ShipToMappingUpdate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> ShipToMappingResponse:
    from app.models.audit_log import AuditLog
    from app.models.master_data import ShipToMapping, TradingPartner

    mapping = db.get(ShipToMapping, mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="Ship-to mapping not found")

    mapping.b1_whs_code = body.b1_whs_code
    db.add(AuditLog(
        user_email=current_user.email,
        action="update_ship_to_mapping",
        entity_type="ShipToMapping",
        entity_id=str(mapping_id),
        payload={"b1_whs_code": body.b1_whs_code},
        ip_address=request.client.host if request.client else None,
    ))
    db.flush()
    db.commit()

    partner = db.get(TradingPartner, mapping.trading_partner_id)
    return ShipToMappingResponse(
        id=mapping.id,
        trading_partner_id=mapping.trading_partner_id,
        partner_code=partner.code if partner else "",
        buyer_whs_code=mapping.buyer_whs_code,
        b1_whs_code=mapping.b1_whs_code,
        is_active=mapping.is_active,
    )
