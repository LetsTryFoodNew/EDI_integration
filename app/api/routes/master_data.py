"""
Master data routes — Phase 8.

REST convention (rationale in the app/schemas/api.py Master Data header):
  POST .../sync   — bulk upsert pushed FROM SAP. Keeps the middleware from calling
                    SAP's Service Layer on every read (sessions are licensed and
                    capped — CLAUDE.md section 7).
  GET  ...        — reads local tables only, never SAP live.
  PUT  .../{id}   — ops-side manual correction of a single record.

Partners:      GET /api/master-data/partners,     PUT .../{id}, POST .../sync
Materials:     GET /api/master-data/materials,    POST (manual add), POST .../sync
SKU mappings:  GET /api/master-data/sku-mappings, PUT .../{id}, POST .../sync
Ship-to:       GET /api/master-data/ship-to,      PUT .../{id}, POST .../sync
Bill-to:       GET /api/master-data/bill-to,      PUT .../{id}, POST .../sync
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.schemas.api import (
    BillToMappingResponse,
    BillToMappingSyncRequest,
    BillToMappingUpdate,
    CustomerBillToItem,
    CustomerDetailResponse,
    CustomerShipToItem,
    CustomerSkuMappingItem,
    MasterDataSyncResult,
    MaterialMasterCreate,
    MaterialMasterResponse,
    MaterialMasterSyncRequest,
    MaterialMasterUpdate,
    PaginatedResponse,
    ShipToMappingResponse,
    ShipToMappingSyncRequest,
    ShipToMappingUpdate,
    SkuMappingResponse,
    SkuMappingSyncRequest,
    TradingPartnerCreate,
    TradingPartnerResponse,
    TradingPartnerSyncRequest,
    TradingPartnerUpdate,
    TradingPartnerWriteResponse,
    UserResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/master-data", tags=["Master Data"])


# Keys the sync schemas accept only so a GET response can be posted straight back.
# They are dropped before any write — see the round-trip note in app/schemas/api.py.
_SYNC_READ_ONLY = {"id", "trading_partner_id", "created_at", "updated_at", "is_active"}
_SHIP_TO_READ_ONLY = _SYNC_READ_ONLY | {"b1_whs_code", "mapping_status"}
_BILL_TO_READ_ONLY = _SYNC_READ_ONLY | {"b1_bill_to_code", "mapping_status"}


def _partner_write_response(partner: object) -> TradingPartnerWriteResponse:
    """
    Build the create/update response, warning about anything standing between this
    row and a PO actually reaching SAP. Shared so create and update never drift.
    """
    from app.models._enums import SourceChannel
    from app.parsers.registry import registered_codes

    channel = partner.source_channel
    warnings: list[str] = []

    if partner.code not in registered_codes():
        warnings.append(
            f"No parser is registered for '{partner.code}', so incoming documents "
            f"cannot be read yet. Master data sync works; PO ingestion needs a parser "
            f"in code."
        )
    if channel is SourceChannel.EMAIL and not partner.gmail_label:
        warnings.append(
            "source_channel is EMAIL but gmail_label is empty — this partner will not "
            "be polled until a label is set."
        )
    if channel is SourceChannel.WEBHOOK and not partner.webhook_secret:
        warnings.append(
            "source_channel is WEBHOOK but webhook_secret is empty — inbound pushes "
            "cannot be authenticated until it is set."
        )
    if channel is SourceChannel.MANUAL:
        warnings.append(
            "source_channel is MANUAL, so nothing is polled or received automatically. "
            "Set it to API / WEBHOOK / EMAIL once the integration is built."
        )

    response = TradingPartnerWriteResponse.model_validate(partner, from_attributes=True)
    response.warnings = warnings
    return response


def _active_flag(item: object, *, default: bool = True) -> bool:
    """
    `status` is the documented field; `is_active` is accepted as an alias so a record
    read from GET can be posted straight back. `status` wins when both are present.
    """
    status = getattr(item, "status", None)
    if status is not None:
        return bool(status)
    alias = getattr(item, "is_active", None)
    if alias is not None:
        return bool(alias)
    return default


def _reject_immutable_changes(
    body: object,
    record: object,
    fields: dict[str, str],
    *,
    reason: str,
) -> None:
    """
    Identity and sync-owned fields are accepted on PUT so a client can GET a record,
    change one value, and PUT the whole object back. They are ignored when they match
    what is already stored, and rejected with an explanation when they differ — never
    silently dropped, which would look like a successful edit that did nothing.

    `fields` maps the request field name -> the model attribute it corresponds to.
    """
    conflicts = []
    for req_field, attr in fields.items():
        sent = getattr(body, req_field, None)
        if sent is None:
            continue
        current = getattr(record, attr, None)
        if str(sent) != str(current):
            conflicts.append({
                "field": req_field,
                "problem": reason,
                "sent": str(sent),
                "current": str(current),
            })
    if conflicts:
        raise HTTPException(status_code=409, detail={"immutable_fields": conflicts})



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


def _update_partner_from_push(
    db: object,
    partner: object,
    body: object,
    request: object,
    current_user: object,
    response: object,
) -> TradingPartnerWriteResponse:
    """
    Apply a repeat push to a partner that already exists.

    Master data is always taken. Integration config is applied only where the caller
    sent a value *and* nothing is set locally, so a Business Partner refresh can fill a
    gap but never overwrite a working setting -- switching a live API partner to MANUAL
    would stop its ingestion with no error anywhere.
    """
    from app.models._enums import SourceChannel
    from app.models.audit_log import AuditLog

    partner.name = body.name
    partner.is_active = _active_flag(body)
    for field in ("b1_card_code", "gstin", "pan_card", "business_type",
                  "group_name", "phone_numbers", "email_address"):
        value = getattr(body, field, None)
        if value is not None:
            setattr(partner, field, value)

    filled: list[str] = []
    for field in _PARTNER_INTEGRATION_FIELDS:
        sent = getattr(body, field, None)
        if sent is None:
            continue
        if field == "source_channel":
            # The schema defaults this to MANUAL, so "MANUAL" on an update is
            # indistinguishable from "not supplied" and must never demote a live
            # partner. Only an explicit non-MANUAL value on an unset row is applied.
            if str(sent).strip().upper() == SourceChannel.MANUAL.value:
                continue
            try:
                sent = SourceChannel(str(sent).strip().upper())
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"source_channel '{getattr(body, field)}' is not valid. "
                        f"Allowed: {', '.join(c.value for c in SourceChannel)}."
                    ),
                ) from None
            if partner.source_channel is not SourceChannel.MANUAL:
                continue
        elif field == "asn_sla_hours":
            # Has a schema default too; only meaningful when the row has none.
            if getattr(partner, field, None) is not None:
                continue
        elif getattr(partner, field, None):
            continue
        setattr(partner, field, sent)
        filled.append(field)

    db.add(AuditLog(
        user_email=current_user.email,
        action="update_partner",
        entity_type="TradingPartner",
        entity_id=str(partner.id),
        payload={"source": "upsert", "integration_fields_filled": filled,
                 **body.model_dump(mode="json")},
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    db.refresh(partner)
    log.info("partner.updated", code=partner.code, filled=filled)
    response.status_code = 200
    return _partner_write_response(partner)


#: Set only when the partner is created. A push from SAP describes a Business Partner,
#: which has nothing to say about how we fetch that partner's orders -- overwriting
#: these on every sync would silently unwire a working integration.
_PARTNER_INTEGRATION_FIELDS = ("source_channel", "gmail_label", "webhook_secret",
                               "asn_sla_hours")


@router.post("/partners", response_model=TradingPartnerWriteResponse)
def upsert_partner(
    body: TradingPartnerCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> TradingPartnerWriteResponse:
    """
    Add or update one trading partner, keyed on `code` (= SAP `CardCode`).

    One endpoint for both, because the caller is SAP and it does not track what we
    already hold. **201 on create, 200 on update.**

    Master data is taken from every push. Integration config -- source_channel,
    gmail_label, webhook_secret, asn_sla_hours -- is written **only on create**, and on
    an update is applied only where the field is explicitly sent and currently unset.
    A SAP Business Partner record cannot express how we poll that partner's orders, so
    a routine master-data refresh must not be able to switch a live API partner to
    MANUAL and stop its ingestion.

    Creating the row does not by itself make POs flow: ingestion also needs an adapter
    (how to fetch) and a parser (how to read their format) registered in code for this
    partner code. The response warns when those are missing.
    """
    from sqlalchemy import select

    from app.models._enums import SourceChannel
    from app.models.audit_log import AuditLog
    from app.models.master_data import TradingPartner

    code = body.code.strip().upper()

    existing = db.execute(
        select(TradingPartner).where(TradingPartner.code == code)
    ).scalar_one_or_none()

    if existing is not None and existing.deleted_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Partner '{code}' is soft-deleted here. Restore it before syncing, so "
                f"an automated push cannot undo a deliberate removal."
            ),
        )

    if existing is not None:
        return _update_partner_from_push(db, existing, body, request, current_user, response)

    try:
        channel = SourceChannel(body.source_channel.strip().upper())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"source_channel '{body.source_channel}' is not valid. "
                f"Allowed: {', '.join(c.value for c in SourceChannel)}."
            ),
        ) from None

    partner = TradingPartner(
        code=code,
        name=body.name,
        source_channel=channel,
        gmail_label=body.gmail_label,
        webhook_secret=body.webhook_secret,
        asn_sla_hours=body.asn_sla_hours,
        b1_card_code=body.b1_card_code,
        gstin=body.gstin,
        pan_card=body.pan_card,
        business_type=body.business_type,
        group_name=body.group_name,
        phone_numbers=body.phone_numbers,
        email_address=body.email_address,
        is_active=body.is_active,
    )
    db.add(partner)
    db.add(AuditLog(
        user_email=current_user.email,
        action="create_partner",
        entity_type="TradingPartner",
        payload=body.model_dump(mode="json"),
        ip_address=request.client.host if request.client else None,
    ))
    db.flush()
    db.commit()
    db.refresh(partner)

    log.info("partner.created", code=code, channel=str(channel))
    response.status_code = 201
    return _partner_write_response(partner)


@router.get("/partners/{partner_id}", response_model=CustomerDetailResponse)
def get_partner_detail(
    partner_id: uuid.UUID,
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> CustomerDetailResponse:
    """
    One customer plus its SKU mappings, ship-to and bill-to addresses, in one round trip.
    Backs the expandable customer row on the Master Data screen.
    """
    from sqlalchemy import select

    from app.models.master_data import (
        BillToMapping,
        MaterialMaster,
        ShipToMapping,
        SkuMapping,
        TradingPartner,
    )

    partner = db.get(TradingPartner, partner_id)
    if not partner or partner.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Inner join: material_id is NOT NULL, so every mapping resolves to an item.
    # MRP comes from Item_master — it is item data, not customer-specific.
    sku_rows = db.execute(
        select(
            SkuMapping,
            MaterialMaster.item_code,
            MaterialMaster.mrp,
            MaterialMaster.ean_code,
            MaterialMaster.case_size,
            MaterialMaster.grammage,
        )
        .join(MaterialMaster, SkuMapping.material_id == MaterialMaster.id)
        .where(
            SkuMapping.trading_partner_id == partner.id,
            SkuMapping.deleted_at.is_(None),
        )
        .order_by(SkuMapping.buyer_sku)
    ).all()

    ship_to_rows = db.execute(
        select(ShipToMapping)
        .where(
            ShipToMapping.trading_partner_id == partner.id,
            ShipToMapping.deleted_at.is_(None),
        )
        .order_by(ShipToMapping.buyer_whs_code)
    ).scalars().all()

    bill_to_rows = db.execute(
        select(BillToMapping)
        .where(
            BillToMapping.trading_partner_id == partner.id,
            BillToMapping.deleted_at.is_(None),
        )
        .order_by(BillToMapping.buyer_bill_to_code)
    ).scalars().all()

    return CustomerDetailResponse(
        id=partner.id,
        code=partner.code,
        name=partner.name,
        source_channel=str(partner.source_channel),
        is_active=partner.is_active,
        gmail_label=partner.gmail_label,
        b1_card_code=partner.b1_card_code,
        gstin=partner.gstin,
        business_type=partner.business_type,
        group_name=partner.group_name,
        phone_numbers=partner.phone_numbers,
        email_address=partner.email_address,
        pan_card=partner.pan_card,
        created_at=partner.created_at,
        sku_mappings=[
            CustomerSkuMappingItem(
                id=row.SkuMapping.id,
                buyer_sku=row.SkuMapping.buyer_sku,
                item_name=row.SkuMapping.buyer_sku_description,
                b1_item_code=row.item_code,
                unit_price=row.SkuMapping.unit_price,
                margin=row.SkuMapping.margin,
                mrp=row.mrp,
                ean_code=row.ean_code,
                case_size=row.case_size,
                grammage=row.grammage,
                qty_per_buyer_uom=row.SkuMapping.qty_per_buyer_uom,
                is_active=row.SkuMapping.is_active,
                created_at=row.SkuMapping.created_at,
                updated_at=row.SkuMapping.updated_at,
            )
            for row in sku_rows
        ],
        ship_to_mappings=[
            CustomerShipToItem(
                id=s.id,
                dc_code=s.buyer_whs_code,
                warehouse_name=s.buyer_warehouse_name,
                b1_whs_code=s.b1_whs_code,
                address=s.address_line,
                address_type=s.address_type,
                street=s.street,
                block=s.block,
                city=s.city,
                zip_code=s.zip_code,
                state=s.state,
                country=s.country,
                gst_regn_no=s.gst_registration_no,
                gst_type=s.gst_type,
                poc_name=s.poc_name,
                poc_email=s.poc_email,
                poc_phone=s.poc_phone,
                mapping_status=str(s.mapping_status),
                is_active=s.is_active,
            )
            for s in ship_to_rows
        ],
        bill_to_mappings=[
            CustomerBillToItem(
                id=b.id,
                bill_to_code=b.buyer_bill_to_code,
                entity_name=b.buyer_entity_name,
                b1_bill_to_code=b.b1_bill_to_code,
                address=b.address_line,
                address_type=b.address_type,
                street=b.street,
                block=b.block,
                city=b.city,
                zip_code=b.zip_code,
                state=b.state,
                country=b.country,
                gst_regn_no=b.gst_registration_no,
                gst_type=b.gst_type,
                poc_name=b.poc_name,
                poc_email=b.poc_email,
                poc_phone=b.poc_phone,
                mapping_status=str(b.mapping_status),
                is_active=b.is_active,
            )
            for b in bill_to_rows
        ],
    )


@router.put("/partners/{partner_id}", response_model=TradingPartnerWriteResponse)
def update_partner(
    partner_id: uuid.UUID,
    body: TradingPartnerUpdate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> TradingPartnerWriteResponse:
    """
    Ops-side edit of one partner — including `source_channel`, which is how a partner
    graduates from MANUAL to a live ingestion route once its adapter and parser exist.
    The response warns about anything still blocking PO flow.
    """
    from app.models.audit_log import AuditLog
    from app.models.master_data import TradingPartner

    partner = db.get(TradingPartner, partner_id)
    if not partner or partner.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Partner not found")

    _reject_immutable_changes(
        body, partner, {"id": "id", "code": "code"},
        reason=(
            "`code` is the partner's identity: it is embedded in the webhook URL, is the "
            "key SAP's sync matches on, and every stored PO and raw message is filed "
            "under it. Retire this partner and onboard a new one instead."
        ),
    )

    # source_channel IS editable — it governs future routing only. Existing
    # raw_messages and POs carry their own source_channel stamped at ingestion, so
    # changing it rewrites no history. MANUAL -> API/EMAIL/WEBHOOK is the normal
    # step once a partner's adapter and parser exist.
    if body.source_channel is not None:
        from app.models._enums import SourceChannel
        try:
            partner.source_channel = SourceChannel(body.source_channel.strip().upper())
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"source_channel '{body.source_channel}' is not valid. "
                    f"Allowed: {', '.join(c.value for c in SourceChannel)}."
                ),
            ) from None

    _READ_ONLY = {"id", "code", "created_at", "source_channel"}
    update_data = body.model_dump(exclude_none=True, exclude=_READ_ONLY)
    if not update_data and body.source_channel is None:
        raise HTTPException(status_code=422, detail="No fields to update")
    for field, value in update_data.items():
        setattr(partner, field, value)
    # audit_log.payload is JSONB — serialise Decimal/UUID/date to JSON-safe types.
    audit_payload = body.model_dump(exclude_none=True, exclude=_READ_ONLY, mode="json")

    db.add(AuditLog(
        user_email=current_user.email,
        action="update_partner",
        entity_type="TradingPartner",
        entity_id=str(partner_id),
        payload=audit_payload,
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    db.refresh(partner)
    return _partner_write_response(partner)


@router.post("/partners/sync", response_model=MasterDataSyncResult)
def sync_partners(
    body: TradingPartnerSyncRequest,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MasterDataSyncResult:
    """
    Bulk upsert Business Partner / Customer master data pushed from SAP.

    Update-only by design: an unknown partner code is SKIPPED, not created.
    Onboarding a new retail partner requires choosing its source_channel
    (API/WEBHOOK/EMAIL) and wiring credentials — a middleware config decision SAP's
    Business Partner record cannot express, and CLAUDE.md is explicit: "Never invent
    partners, labels, or codes." Existing integration config (source_channel,
    gmail_label, webhook_secret, api_config, SLA hours) is never touched by sync.
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
        partner.is_active = _active_flag(item)
        for field in ("b1_card_code", "gstin", "pan_card", "business_type",
                      "group_name", "phone_numbers", "email_address"):
            value = getattr(item, field)
            if value is not None:
                setattr(partner, field, value)
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
    valid_for: int | None = Query(None, ge=0, le=1),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[MaterialMasterResponse]:
    from sqlalchemy import func, select

    from app.models.master_data import MaterialMaster

    q = select(MaterialMaster).where(MaterialMaster.deleted_at.is_(None))
    if search:
        pattern = f"%{search}%"
        q = q.where(
            MaterialMaster.item_code.ilike(pattern) |
            MaterialMaster.item_name.ilike(pattern) |
            MaterialMaster.ean_code.ilike(pattern)
        )
    if valid_for is not None:
        q = q.where(MaterialMaster.valid_for == valid_for)
    q = q.order_by(MaterialMaster.item_code)

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.limit(limit).offset(offset)).scalars().all()
    return PaginatedResponse(
        items=[MaterialMasterResponse.model_validate(r) for r in rows],
        total=total, limit=limit, offset=offset,
    )


@router.post("/materials", response_model=MaterialMasterResponse)
def upsert_material(
    body: MaterialMasterCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MaterialMasterResponse:
    """
    Add or update one item, keyed on `item_code` (= SAP `ItemCode`).

    One endpoint for both, because the caller is SAP and SAP does not track what we
    already hold. Making it ask first — POST, read 409, switch to PUT — turns every
    item into two round trips and a race, and the 409 told it nothing it could act on.

    Existence is decided on `item_code` alone: present means update, absent means
    create. **201 on create, 200 on update**, so the caller can tell which happened
    without diffing.

    A soft-deleted item is not resurrected silently -- it is refused, because
    `deleted_at` was set by a person and an automated push should not undo that.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import MaterialMaster

    code = body.item_code.strip().upper()
    material = db.execute(
        select(MaterialMaster).where(MaterialMaster.item_code == code)
    ).scalar_one_or_none()

    if material is not None and material.deleted_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Item '{code}' is soft-deleted here. Restore it before syncing, so an "
                f"automated push cannot undo a deliberate removal."
            ),
        )

    created = material is None
    fields = body.model_dump(exclude={"item_code"})
    if created:
        material = MaterialMaster(item_code=code, **fields)
        db.add(material)
    else:
        for key, value in fields.items():
            setattr(material, key, value)

    db.add(AuditLog(
        user_email=current_user.email,
        action="create_material" if created else "update_material",
        entity_type="MaterialMaster",
        payload=body.model_dump(mode="json"),
        ip_address=request.client.host if request.client else None,
    ))
    db.flush()
    db.commit()
    db.refresh(material)
    response.status_code = 201 if created else 200
    return MaterialMasterResponse.model_validate(material)


@router.put("/materials/{material_id}", response_model=MaterialMasterResponse)
def update_material(
    material_id: uuid.UUID,
    body: MaterialMasterUpdate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MaterialMasterResponse:
    """
    Ops-side edit of one item. Only the fields present in the body are written.

    `item_code` is intentionally not editable — it is the natural key SAP syncs on, and
    changing it would orphan every SKU mapping pointing at this item. To replace an
    item, retire it (`valid_for: 0`) and create the new one.

    SAP remains the source of truth: a later POST /materials/sync overwrites these
    fields. Use this for correction and testing, not as a substitute for fixing SAP.
    """
    from app.models.audit_log import AuditLog
    from app.models.master_data import MaterialMaster

    material = db.get(MaterialMaster, material_id)
    if not material or material.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Item not found")

    _reject_immutable_changes(
        body, material, {"id": "id", "item_code": "item_code"},
        reason=(
            "item_code is the key SAP syncs on and the target of every SKU mapping's "
            "b1ItemCode — changing it would orphan those mappings. Retire this item "
            "with valid_for=0 and create the replacement instead."
        ),
    )

    update_data = body.model_dump(exclude_none=True, exclude={"id", "item_code"})
    if not update_data:
        raise HTTPException(status_code=422, detail="No fields to update")
    for field, value in update_data.items():
        setattr(material, field, value)

    db.add(AuditLog(
        user_email=current_user.email,
        action="update_material",
        entity_type="MaterialMaster",
        entity_id=str(material_id),
        # audit_log.payload is JSONB — mode="json" keeps Decimal serialisable.
        payload=body.model_dump(exclude_none=True, exclude={"id", "item_code"}, mode="json"),
        ip_address=request.client.host if request.client else None,
    ))
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
    config, only catalogue data. `frozen_for` / `is_active` (valid_for) are always
    overwritten from SAP — they're SAP's authoritative item-status flags, and a
    stale frozen item must stop blocking pushes the moment SAP unfreezes it.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import MaterialMaster

    created = updated = skipped = 0
    errors: list[str] = []

    for item in body.items:
        code = item.item_code.strip().upper()
        material = db.execute(
            select(MaterialMaster).where(MaterialMaster.item_code == code)
        ).scalar_one_or_none()

        # is_active is writable for items (it is a real column now, distinct from
        # SAP's valid_for) — drop it from the shared read-only set for this endpoint.
        fields = item.model_dump(exclude={"item_code"} | (_SYNC_READ_ONLY - {"is_active"}))

        if material:
            if material.deleted_at is not None:
                skipped += 1
                errors.append(f"{code}: soft-deleted — restore manually before syncing")
                continue
            for k, v in fields.items():
                setattr(material, k, v)
            updated += 1
        else:
            db.add(MaterialMaster(item_code=code, **fields))
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
def _sku_row_to_response(
    m: object, partner_code: str, item_code: str, mrp,
    ean_code=None, case_size=None, grammage=None,
) -> SkuMappingResponse:
    return SkuMappingResponse(
        id=m.id,
        trading_partner_id=m.trading_partner_id,
        partner_code=partner_code,
        buyer_sku=m.buyer_sku,
        item_name=m.buyer_sku_description,
        b1_item_code=item_code,
        unit_price=m.unit_price,
        margin=m.margin,
        mrp=mrp,
        ean_code=ean_code,
        case_size=case_size,
        grammage=grammage,
        qty_per_buyer_uom=m.qty_per_buyer_uom,
        is_active=m.is_active,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.get("/sku-mappings", response_model=PaginatedResponse[SkuMappingResponse])
def list_sku_mappings(
    partner_code: str | None = Query(None),
    is_active: bool | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[SkuMappingResponse]:
    """
    SKU mappings with the item's MRP joined from Item_master. Inner join on
    material_master — material_id is NOT NULL, so every mapping has an item.
    """
    from sqlalchemy import func, select

    from app.models.master_data import MaterialMaster, SkuMapping, TradingPartner

    q = (
        select(
            SkuMapping,
            TradingPartner.code.label("partner_code"),
            MaterialMaster.item_code,
            MaterialMaster.mrp,
            MaterialMaster.ean_code,
            MaterialMaster.case_size,
            MaterialMaster.grammage,
        )
        .join(TradingPartner, SkuMapping.trading_partner_id == TradingPartner.id)
        .join(MaterialMaster, SkuMapping.material_id == MaterialMaster.id)
        .where(TradingPartner.deleted_at.is_(None))
        .where(SkuMapping.deleted_at.is_(None))
        .order_by(TradingPartner.code, SkuMapping.buyer_sku)
    )
    if partner_code:
        q = q.where(TradingPartner.code == partner_code)
    if is_active is not None:
        q = q.where(SkuMapping.is_active == is_active)
    if search:
        pattern = f"%{search}%"
        q = q.where(
            SkuMapping.buyer_sku.ilike(pattern) |
            SkuMapping.buyer_sku_description.ilike(pattern) |
            MaterialMaster.item_code.ilike(pattern)
        )

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.limit(limit).offset(offset)).all()

    items = [
        _sku_row_to_response(
            row.SkuMapping, row.partner_code, row.item_code, row.mrp,
            row.ean_code, row.case_size, row.grammage,
        )
        for row in rows
    ]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


# NOTE: there is deliberately no PUT /sku-mappings/{id}. SAP is the sole author of
# these rows (b1ItemCode is not-null, so every row is already a confirmed mapping).
# A PO line whose buyer SKU has no mapping raises E002_SKU_UNRESOLVED against the PO;
# it is fixed by adding the mapping in SAP and re-syncing, not by editing here — that
# way a later sync can never silently overwrite a local edit.


@router.post("/sku-mappings/sync", response_model=MasterDataSyncResult)
def sync_sku_mappings(
    body: SkuMappingSyncRequest,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MasterDataSyncResult:
    """
    Bulk upsert customer-SKU mappings pushed from SAP, keyed by (partner, buyer_sku).

    b1_item_code is required and must already exist in Item_master. If it does not,
    the row is REJECTED rather than stored half-resolved — the schema marks that
    reference "no cascade — fail loud on unmapped item", and a mapping pointing at a
    non-existent item would fail later at B1 push with a far less obvious error.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import MaterialMaster, SkuMapping, TradingPartner

    created = updated = skipped = 0
    errors: list[str] = []

    # Resolve lookups once rather than per row.
    partners = {
        p.code: p for p in db.execute(
            select(TradingPartner).where(TradingPartner.deleted_at.is_(None))
        ).scalars().all()
    }
    materials = {
        m.item_code: m for m in db.execute(
            select(MaterialMaster).where(MaterialMaster.deleted_at.is_(None))
        ).scalars().all()
    }

    for item in body.mappings:
        partner = partners.get(item.partner_code.upper())
        if not partner:
            skipped += 1
            errors.append(f"{item.partner_code}/{item.buyer_sku}: unknown partner code")
            continue

        material = materials.get(item.b1_item_code.strip().upper())
        if not material:
            skipped += 1
            errors.append(
                f"{item.partner_code}/{item.buyer_sku}: "
                f"item '{item.b1_item_code}' not in Item_master"
            )
            continue

        mapping = db.execute(
            select(SkuMapping).where(
                SkuMapping.trading_partner_id == partner.id,
                SkuMapping.buyer_sku == item.buyer_sku,
            )
        ).scalar_one_or_none()

        if mapping:
            mapping.material_id = material.id
            if item.item_name is not None:
                mapping.buyer_sku_description = item.item_name
            if item.unit_price is not None:
                mapping.unit_price = item.unit_price
            if item.margin is not None:
                mapping.margin = item.margin
            if item.qty_per_buyer_uom is not None:
                mapping.qty_per_buyer_uom = item.qty_per_buyer_uom
            mapping.is_active = _active_flag(item)
            mapping.deleted_at = None
            updated += 1
        else:
            db.add(SkuMapping(
                trading_partner_id=partner.id,
                buyer_sku=item.buyer_sku,
                buyer_sku_description=item.item_name,
                material_id=material.id,
                unit_price=item.unit_price,
                margin=item.margin,
                qty_per_buyer_uom=item.qty_per_buyer_uom or 1,
                is_active=_active_flag(item),
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

def _ship_to_row_to_response(m: object, partner_code: str) -> ShipToMappingResponse:
    return ShipToMappingResponse(
        id=m.id,
        trading_partner_id=m.trading_partner_id,
        partner_code=partner_code,
        buyer_whs_code=m.buyer_whs_code,
        buyer_warehouse_name=m.buyer_warehouse_name,
        b1_whs_code=m.b1_whs_code,
        mapping_status=str(m.mapping_status),
        is_active=m.is_active,
        address_line=m.address_line,
        address_type=m.address_type,
        street=m.street,
        block=m.block,
        city=m.city,
        zip_code=m.zip_code,
        state=m.state,
        country=m.country,
        gst_registration_no=m.gst_registration_no,
        gst_type=m.gst_type,
        poc_name=m.poc_name,
        poc_email=m.poc_email,
        poc_phone=m.poc_phone,
    )


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
        .where(ShipToMapping.deleted_at.is_(None))
        .order_by(TradingPartner.code, ShipToMapping.buyer_whs_code)
    )
    if partner_code:
        q = q.where(TradingPartner.code == partner_code)

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.limit(limit).offset(offset)).all()

    items = [_ship_to_row_to_response(row.ShipToMapping, row.partner_code) for row in rows]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.put("/ship-to/{mapping_id}", response_model=ShipToMappingResponse)
def update_ship_to(
    mapping_id: uuid.UUID,
    body: ShipToMappingUpdate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> ShipToMappingResponse:
    """Ops-side manual mapping of a buyer warehouse / DC code to a B1 WhsCode."""
    from app.models._enums import MappingStatus
    from app.models.audit_log import AuditLog
    from app.models.master_data import ShipToMapping, TradingPartner

    mapping = db.get(ShipToMapping, mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="Ship-to mapping not found")

    _reject_immutable_changes(
        body, mapping,
        {"id": "id", "buyer_whs_code": "buyer_whs_code"},
        reason="This field identifies the ship-to location and cannot be changed here.",
    )
    # Address and GST fields are owned by POST /ship-to/sync. Accepting them on a
    # round-trip PUT is fine, but silently discarding a *changed* value would look
    # like a successful edit that did nothing.
    _reject_immutable_changes(
        body, mapping,
        {
            "buyer_warehouse_name": "buyer_warehouse_name",
            "address_line": "address_line",
            "street": "street", "block": "block", "city": "city",
            "zip_code": "zip_code", "state": "state", "country": "country",
            "gst_registration_no": "gst_registration_no",
        },
        reason=(
            "Address and GST fields come from SAP via POST /api/master-data/ship-to/sync. "
            "Change them in SAP and re-sync."
        ),
    )

    _WRITABLE = ("b1_whs_code", "is_active", "poc_name", "poc_email", "poc_phone")
    if all(getattr(body, f) is None for f in _WRITABLE):
        raise HTTPException(status_code=422, detail="No fields to update")

    if body.b1_whs_code is not None:
        mapping.b1_whs_code = body.b1_whs_code
        mapping.mapping_status = MappingStatus.MANUALLY_MAPPED
    if body.is_active is not None:
        mapping.is_active = body.is_active
    # POC contact info is writable both here and by sync — it drifts, ops may fix it.
    for f in ("poc_name", "poc_email", "poc_phone"):
        v = getattr(body, f)
        if v is not None:
            setattr(mapping, f, v)

    db.add(AuditLog(
        user_email=current_user.email,
        action="update_ship_to_mapping",
        entity_type="ShipToMapping",
        entity_id=str(mapping_id),
        payload=body.model_dump(
            include={"b1_whs_code", "is_active", "poc_name", "poc_email", "poc_phone"},
            exclude_none=True, mode="json",
        ),
        ip_address=request.client.host if request.client else None,
    ))
    db.flush()
    db.commit()

    partner = db.get(TradingPartner, mapping.trading_partner_id)
    return _ship_to_row_to_response(mapping, partner.code if partner else "")


@router.post("/ship-to/sync", response_model=MasterDataSyncResult)
def sync_ship_to(
    body: ShipToMappingSyncRequest,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MasterDataSyncResult:
    """
    Bulk upsert ship-to / delivery-address records pushed from SAP.

    Safe to create here — a new address carries no B1 warehouse mapping yet, so it
    lands as mapping_status=UNMAPPED and queues for ops to map, mirroring the SKU
    auto-mapping flow (Phase 5). Only touches address / GSTIN fields; never
    b1_whs_code or mapping_status, so re-syncing cannot undo an ops mapping.
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

        address_fields = item.model_dump(
            exclude={"partner_code", "buyer_whs_code"} | _SHIP_TO_READ_ONLY
        )

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


# ── Bill-to Mappings ──────────────────────────────────────────────────────────
# Deliberately a parallel set of endpoints rather than a `type` filter on ship-to.
# The two resolve to different B1 objects (warehouse vs business-partner address) and
# carry different tax roles, so sharing a route would mean branching on a discriminator
# in every handler for no gain.

def _bill_to_row_to_response(m: object, partner_code: str) -> BillToMappingResponse:
    return BillToMappingResponse(
        id=m.id,
        trading_partner_id=m.trading_partner_id,
        partner_code=partner_code,
        buyer_bill_to_code=m.buyer_bill_to_code,
        buyer_entity_name=m.buyer_entity_name,
        b1_bill_to_code=m.b1_bill_to_code,
        mapping_status=str(m.mapping_status),
        is_active=m.is_active,
        address_line=m.address_line,
        address_type=m.address_type,
        street=m.street,
        block=m.block,
        city=m.city,
        zip_code=m.zip_code,
        state=m.state,
        country=m.country,
        gst_registration_no=m.gst_registration_no,
        gst_type=m.gst_type,
        poc_name=m.poc_name,
        poc_email=m.poc_email,
        poc_phone=m.poc_phone,
    )


@router.get("/bill-to", response_model=PaginatedResponse[BillToMappingResponse])
def list_bill_to(
    partner_code: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[BillToMappingResponse]:
    from sqlalchemy import func, select

    from app.models.master_data import BillToMapping, TradingPartner

    q = (
        select(BillToMapping, TradingPartner.code.label("partner_code"))
        .join(TradingPartner, BillToMapping.trading_partner_id == TradingPartner.id)
        .where(TradingPartner.deleted_at.is_(None))
        .where(BillToMapping.deleted_at.is_(None))
        .order_by(TradingPartner.code, BillToMapping.buyer_bill_to_code)
    )
    if partner_code:
        q = q.where(TradingPartner.code == partner_code)

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.limit(limit).offset(offset)).all()

    items = [_bill_to_row_to_response(row.BillToMapping, row.partner_code) for row in rows]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.put("/bill-to/{mapping_id}", response_model=BillToMappingResponse)
def update_bill_to(
    mapping_id: uuid.UUID,
    body: BillToMappingUpdate,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> BillToMappingResponse:
    """Ops-side manual mapping of a buyer billing entity to a B1 BP address name."""
    from app.models._enums import MappingStatus
    from app.models.audit_log import AuditLog
    from app.models.master_data import BillToMapping, TradingPartner

    mapping = db.get(BillToMapping, mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="Bill-to mapping not found")

    _reject_immutable_changes(
        body, mapping,
        {"id": "id", "buyer_bill_to_code": "buyer_bill_to_code"},
        reason="This field identifies the bill-to entity and cannot be changed here.",
    )
    # Address and GST fields are owned by POST /bill-to/sync. Accepting them on a
    # round-trip PUT is fine, but silently discarding a *changed* value would look
    # like a successful edit that did nothing.
    _reject_immutable_changes(
        body, mapping,
        {
            "buyer_entity_name": "buyer_entity_name",
            "address_line": "address_line",
            "street": "street", "block": "block", "city": "city",
            "zip_code": "zip_code", "state": "state", "country": "country",
            "gst_registration_no": "gst_registration_no",
        },
        reason=(
            "Address and GST fields come from SAP via POST /api/master-data/bill-to/sync. "
            "Change them in SAP and re-sync."
        ),
    )

    _WRITABLE = ("b1_bill_to_code", "is_active", "poc_name", "poc_email", "poc_phone")
    if all(getattr(body, f) is None for f in _WRITABLE):
        raise HTTPException(status_code=422, detail="No fields to update")

    if body.b1_bill_to_code is not None:
        mapping.b1_bill_to_code = body.b1_bill_to_code
        mapping.mapping_status = MappingStatus.MANUALLY_MAPPED
    if body.is_active is not None:
        mapping.is_active = body.is_active
    # POC contact info is writable both here and by sync — it drifts, ops may fix it.
    for f in ("poc_name", "poc_email", "poc_phone"):
        v = getattr(body, f)
        if v is not None:
            setattr(mapping, f, v)

    db.add(AuditLog(
        user_email=current_user.email,
        action="update_bill_to_mapping",
        entity_type="BillToMapping",
        entity_id=str(mapping_id),
        payload=body.model_dump(
            include={"b1_bill_to_code", "is_active", "poc_name", "poc_email", "poc_phone"},
            exclude_none=True, mode="json",
        ),
        ip_address=request.client.host if request.client else None,
    ))
    db.flush()
    db.commit()

    partner = db.get(TradingPartner, mapping.trading_partner_id)
    return _bill_to_row_to_response(mapping, partner.code if partner else "")


@router.post("/bill-to/sync", response_model=MasterDataSyncResult)
def sync_bill_to(
    body: BillToMappingSyncRequest,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> MasterDataSyncResult:
    """
    Bulk upsert bill-to / invoicing-address records pushed from SAP.

    Safe to create here — a new address carries no B1 mapping yet, so it lands as
    mapping_status=UNMAPPED and queues for ops. Only touches address / GSTIN fields;
    never b1_bill_to_code or mapping_status, so re-syncing cannot undo an ops mapping.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.master_data import BillToMapping, TradingPartner

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
            errors.append(f"{item.partner_code}/{item.buyer_bill_to_code}: unknown partner code")
            continue

        mapping = db.execute(
            select(BillToMapping).where(
                BillToMapping.trading_partner_id == partner.id,
                BillToMapping.buyer_bill_to_code == item.buyer_bill_to_code,
            )
        ).scalar_one_or_none()

        address_fields = item.model_dump(
            exclude={"partner_code", "buyer_bill_to_code"} | _BILL_TO_READ_ONLY
        )

        if mapping:
            for k, v in address_fields.items():
                if v is not None:
                    setattr(mapping, k, v)
            updated += 1
        else:
            db.add(BillToMapping(
                trading_partner_id=partner.id,
                buyer_bill_to_code=item.buyer_bill_to_code,
                **address_fields,
            ))
            created += 1

    db.add(AuditLog(
        user_email=current_user.email,
        action="sync_bill_to",
        entity_type="BillToMapping",
        payload={"created": created, "updated": updated, "skipped": skipped},
    ))
    db.commit()
    return MasterDataSyncResult(created=created, updated=updated, skipped=skipped, errors=errors)
