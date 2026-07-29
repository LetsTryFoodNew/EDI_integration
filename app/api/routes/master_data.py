"""
Master data routes — Phase 8.

REST convention (see app/schemas/api.py header comment for the full rationale):
  POST .../sync   — bulk upsert pushed FROM SAP. Keeps the middleware from calling
                    SAP's Service Layer on every read (Service Layer sessions are a
                    limited, licensed resource — CLAUDE.md section 7).
  GET  ...        — reads from our local tables only, never SAP live.
  PUT  .../{id}   — ops-side manual correction (single record).

Partners:      GET /api/master-data/partners, PUT .../{id}, POST .../sync
Materials:     GET /api/master-data/materials, POST (single manual add), POST .../sync
SKU mappings:  GET /api/master-data/sku-mappings, PUT .../{id}, POST .../sync
Ship-to:       GET /api/master-data/ship-to, PUT .../{id}, POST .../sync
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.schemas.api import (
    MasterDataSyncResult,
    MaterialMasterCreate,
    MaterialMasterResponse,
    MaterialMasterSyncRequest,
    PaginatedResponse,
    ShipToMappingResponse,
    ShipToMappingSyncRequest,
    ShipToMappingUpdate,
    SkuMappingResponse,
    SkuMappingSyncRequest,
    SkuMappingUpdate,
    TradingPartnerResponse,
    TradingPartnerSyncRequest,
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


@router.put("/partners/{partner_id}", response_model=TradingPartnerResponse)
def update_partner(
    partner_id: uuid.UUID,
    body: TradingPartnerUpdate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> TradingPartnerResponse:
    """Ops-side manual correction of a single partner (e.g. fixing its B1 CardCode)."""
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


@router.post("/partners/sync", response_model=MasterDataSyncResult)
def sync_partners(
    body: TradingPartnerSyncRequest,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MasterDataSyncResult:
    """
    Bulk upsert Business Partner / Customer master data pushed from SAP.

    Update-only by design: a partner code that doesn't exist yet is SKIPPED, not
    created. Onboarding a brand-new retail partner requires deciding its
    source_channel (API/WEBHOOK/EMAIL) and wiring credentials — that's a middleware
    config decision, not something SAP's Business Partner record can express, and
    CLAUDE.md is explicit: "Never invent partners, labels, or codes." Existing
    integration config (source_channel, gmail_label, webhook_secret, api_config,
    ack/asn SLA hours) is never touched by sync — only the SAP-sourced fields below.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import TradingPartner

    created = updated = skipped = 0
    errors: list[str] = []

    for item in body.partners:
        partner = db.execute(
            select(TradingPartner).where(
                TradingPartner.code == item.code.upper(),
                TradingPartner.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

        if not partner:
            skipped += 1
            errors.append(f"{item.code}: no existing partner — onboard manually first")
            continue

        partner.name = item.name
        if item.b1_card_code is not None:
            partner.b1_card_code = item.b1_card_code
        if item.gstin is not None:
            partner.gstin = item.gstin
        if item.business_type is not None:
            partner.business_type = item.business_type
        if item.group_name is not None:
            partner.group_name = item.group_name
        if item.phone_numbers is not None:
            partner.phone_numbers = item.phone_numbers
        if item.email_address is not None:
            partner.email_address = item.email_address
        updated += 1

    db.add(AuditLog(
        user_email=current_user.email,
        action="sync_partners",
        entity_type="TradingPartner",
        payload={"created": created, "updated": updated, "skipped": skipped},
    ))
    db.commit()
    return MasterDataSyncResult(created=created, updated=updated, skipped=skipped, errors=errors)


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
    """Manual single-item add (e.g. before SAP has the item yet)."""
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


@router.post("/materials/sync", response_model=MasterDataSyncResult)
def sync_materials(
    body: MaterialMasterSyncRequest,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MasterDataSyncResult:
    """
    Bulk upsert Item Master (OITM) records pushed from SAP, keyed by b1_item_code.

    Safe to create here (unlike partners): a new item code carries no integration
    config, just catalogue data. `frozen_for`/`is_active` (valid_for) are always
    overwritten from SAP — those are SAP's authoritative item-status flags, and a
    stale frozen item must stop blocking pushes the moment SAP unfreezes it (and
    vice versa).
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import MaterialMaster

    created = updated = skipped = 0
    errors: list[str] = []

    for item in body.items:
        code = item.b1_item_code.strip().upper()
        material = db.execute(
            select(MaterialMaster).where(MaterialMaster.b1_item_code == code)
        ).scalar_one_or_none()

        fields = item.model_dump(exclude={"b1_item_code", "valid_for"})
        fields["is_active"] = item.valid_for

        if material:
            if material.deleted_at is not None:
                skipped += 1
                errors.append(f"{code}: soft-deleted — restore manually before syncing")
                continue
            for k, v in fields.items():
                setattr(material, k, v)
            updated += 1
        else:
            db.add(MaterialMaster(b1_item_code=code, **fields))
            created += 1

    db.add(AuditLog(
        user_email=current_user.email,
        action="sync_materials",
        entity_type="MaterialMaster",
        payload={"created": created, "updated": updated, "skipped": skipped},
    ))
    db.commit()
    return MasterDataSyncResult(created=created, updated=updated, skipped=skipped, errors=errors)


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
            unit_price=row.SkuMapping.unit_price,
            margin=row.SkuMapping.margin,
            mapping_status=str(row.SkuMapping.mapping_status),
            confidence_score=row.SkuMapping.confidence_score,
            notes=row.SkuMapping.notes,
            created_at=row.SkuMapping.created_at,
        )
        for row in rows
    ]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.put("/sku-mappings/{mapping_id}", response_model=SkuMappingResponse)
def update_sku_mapping(
    mapping_id: uuid.UUID,
    body: SkuMappingUpdate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> SkuMappingResponse:
    """Ops-side manual mapping of an unmapped buyer SKU to a B1 item code."""
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
        unit_price=mapping.unit_price,
        margin=mapping.margin,
        mapping_status=str(mapping.mapping_status),
        confidence_score=mapping.confidence_score,
        notes=mapping.notes,
        created_at=mapping.created_at,
    )


@router.post("/sku-mappings/sync", response_model=MasterDataSyncResult)
def sync_sku_mappings(
    body: SkuMappingSyncRequest,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MasterDataSyncResult:
    """
    Bulk upsert customer-specific SKU pricing (unit_price/margin) pushed from SAP,
    e.g. from a price list or contract sync.

    Only touches unit_price/margin/buyer_sku_description — never material_id or
    mapping_status. Which internal item a buyer SKU maps to is an ops decision
    (PUT /sku-mappings/{id} above); a price sync must never silently remap or
    unmap a SKU ops has already resolved.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import SkuMapping, TradingPartner

    created = updated = skipped = 0
    errors: list[str] = []

    for item in body.mappings:
        partner = db.execute(
            select(TradingPartner).where(
                TradingPartner.code == item.partner_code.upper(),
                TradingPartner.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if not partner:
            skipped += 1
            errors.append(f"{item.partner_code}/{item.buyer_sku}: unknown partner code")
            continue

        mapping = db.execute(
            select(SkuMapping).where(
                SkuMapping.trading_partner_id == partner.id,
                SkuMapping.buyer_sku == item.buyer_sku,
            )
        ).scalar_one_or_none()

        if mapping:
            if item.unit_price is not None:
                mapping.unit_price = item.unit_price
            if item.margin is not None:
                mapping.margin = item.margin
            if item.item_name is not None:
                mapping.buyer_sku_description = item.item_name
            updated += 1
        else:
            db.add(SkuMapping(
                trading_partner_id=partner.id,
                buyer_sku=item.buyer_sku,
                buyer_sku_description=item.item_name,
                unit_price=item.unit_price,
                margin=item.margin,
            ))
            created += 1

    db.add(AuditLog(
        user_email=current_user.email,
        action="sync_sku_mappings",
        entity_type="SkuMapping",
        payload={"created": created, "updated": updated, "skipped": skipped},
    ))
    db.commit()
    return MasterDataSyncResult(created=created, updated=updated, skipped=skipped, errors=errors)


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
            buyer_warehouse_name=row.ShipToMapping.buyer_warehouse_name,
            b1_whs_code=row.ShipToMapping.b1_whs_code,
            mapping_status=str(row.ShipToMapping.mapping_status),
            is_active=row.ShipToMapping.is_active,
            address_line=row.ShipToMapping.address_line,
            address_type=row.ShipToMapping.address_type,
            street=row.ShipToMapping.street,
            block=row.ShipToMapping.block,
            city=row.ShipToMapping.city,
            zip_code=row.ShipToMapping.zip_code,
            state=row.ShipToMapping.state,
            country=row.ShipToMapping.country,
            gst_registration_no=row.ShipToMapping.gst_registration_no,
            gst_type=row.ShipToMapping.gst_type,
        )
        for row in rows
    ]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.put("/ship-to/{mapping_id}", response_model=ShipToMappingResponse)
def update_ship_to(
    mapping_id: uuid.UUID,
    body: ShipToMappingUpdate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> ShipToMappingResponse:
    """Ops-side manual mapping of a buyer warehouse to a B1 WhsCode."""
    from app.models._enums import MappingStatus
    from app.models.audit_log import AuditLog
    from app.models.master_data import ShipToMapping, TradingPartner

    mapping = db.get(ShipToMapping, mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="Ship-to mapping not found")

    mapping.b1_whs_code = body.b1_whs_code
    mapping.mapping_status = MappingStatus.MANUALLY_MAPPED
    if body.is_active is not None:
        mapping.is_active = body.is_active

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
        buyer_warehouse_name=mapping.buyer_warehouse_name,
        b1_whs_code=mapping.b1_whs_code,
        mapping_status=str(mapping.mapping_status),
        is_active=mapping.is_active,
        address_line=mapping.address_line,
        address_type=mapping.address_type,
        street=mapping.street,
        block=mapping.block,
        city=mapping.city,
        zip_code=mapping.zip_code,
        state=mapping.state,
        country=mapping.country,
        gst_registration_no=mapping.gst_registration_no,
        gst_type=mapping.gst_type,
    )


@router.post("/ship-to/sync", response_model=MasterDataSyncResult)
def sync_ship_to(
    body: ShipToMappingSyncRequest,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MasterDataSyncResult:
    """
    Bulk upsert ship-to / delivery-address records pushed from SAP (e.g. Business
    Partner ship-to addresses). Safe to create here — a new address carries no B1
    warehouse mapping yet, so it's created with mapping_status=UNMAPPED and queued
    for ops to map to a b1_whs_code, same as the SKU auto-mapping flow (Phase 5).

    Only touches address/GSTIN fields — never b1_whs_code or mapping_status once
    an address already exists, so re-syncing never undoes an ops mapping.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import ShipToMapping, TradingPartner

    created = updated = skipped = 0
    errors: list[str] = []

    for item in body.mappings:
        partner = db.execute(
            select(TradingPartner).where(
                TradingPartner.code == item.partner_code.upper(),
                TradingPartner.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if not partner:
            skipped += 1
            errors.append(f"{item.partner_code}/{item.buyer_whs_code}: unknown partner code")
            continue

        mapping = db.execute(
            select(ShipToMapping).where(
                ShipToMapping.trading_partner_id == partner.id,
                ShipToMapping.buyer_whs_code == item.buyer_whs_code,
            )
        ).scalar_one_or_none()

        address_fields = item.model_dump(exclude={"partner_code", "buyer_whs_code"})

        if mapping:
            for k, v in address_fields.items():
                if v is not None:
                    setattr(mapping, k, v)
            updated += 1
        else:
            db.add(ShipToMapping(
                trading_partner_id=partner.id,
                buyer_whs_code=item.buyer_whs_code,
                **address_fields,
            ))
            created += 1

    db.add(AuditLog(
        user_email=current_user.email,
        action="sync_ship_to",
        entity_type="ShipToMapping",
        payload={"created": created, "updated": updated, "skipped": skipped},
    ))
    db.commit()
    return MasterDataSyncResult(created=created, updated=updated, skipped=skipped, errors=errors)
