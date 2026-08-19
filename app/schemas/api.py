"""
Pydantic request/response models for all Phase 8 API endpoints.

These are separate from canonical.py (which models the EDI business objects).
These are the wire shapes for the ops dashboard API.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, EmailStr, Field

T = TypeVar("T")


# ── Common ─────────────────────────────────────────────────────────────────────

class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ── Auth ───────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    """
    Login result. The same JWT is returned two ways so both client styles work:
      - `access_token` in the body -> server-to-server callers send it as
        `Authorization: Bearer <token>` (e.g. the SAP master-data push).
      - an httpOnly `edi_token` cookie is also set -> used by the browser SPA.
    """
    user: UserResponse
    access_token: str
    token_type: str = "bearer"
    expires_in: int          # seconds until the token expires


# ── POs ────────────────────────────────────────────────────────────────────────

class POListItem(BaseModel):
    id: uuid.UUID
    partner_code: str
    partner_name: str
    buyer_po_number: str
    version: int
    po_status: str
    issue_date: date | None
    grand_total: Decimal | None
    currency: str
    line_count: int
    b1_sales_order_doc_num: int | None
    received_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class POLineItemResponse(BaseModel):
    id: uuid.UUID
    line_number: int
    buyer_sku: str
    description: str | None
    ordered_qty: Decimal | None
    uom: str | None
    unit_price: Decimal | None
    line_total: Decimal | None
    taxable_amount: Decimal | None
    cgst_amount: Decimal | None
    sgst_amount: Decimal | None
    igst_amount: Decimal | None
    hsn_code: str | None
    sap_material_no: str | None
    mapping_status: str | None

    model_config = {"from_attributes": True}


class ValidationIssueResponse(BaseModel):
    id: uuid.UUID
    issue_code: str
    severity: str
    field_name: str | None
    message: str
    resolution_note: str | None
    resolved_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class B1PushHistoryItem(BaseModel):
    id: uuid.UUID
    http_method: str
    endpoint: str
    http_status: int | None
    success: bool
    error_code: str | None
    error_message: str | None
    duration_ms: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class OutboundMessageResponse(BaseModel):
    id: uuid.UUID
    doc_type: str
    status: str
    channel: str
    attempt_count: int
    external_reference: str | None
    ack_received_at: datetime | None
    next_retry_at: datetime | None
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PODetail(BaseModel):
    id: uuid.UUID
    partner_code: str
    partner_name: str
    buyer_po_number: str
    version: int
    po_status: str
    source_channel: str
    issue_date: date | None
    delivery_date: date | None
    ship_to_code: str | None
    ship_to_name: str | None
    buyer_gstin: str | None
    seller_gstin: str | None
    grand_total: Decimal | None
    currency: str
    b1_sales_order_doc_entry: int | None
    b1_sales_order_doc_num: int | None
    raw_message_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    # Set only when a newer version of this PO exists. A SUPERSEDED PO is history —
    # every action is correctly disabled on it — but without a pointer to the live
    # version the page looks broken rather than archived.
    current_version_id: uuid.UUID | None = None
    current_version: int | None = None
    lines: list[POLineItemResponse]
    validation_issues: list[ValidationIssueResponse]
    b1_push_history: list[B1PushHistoryItem]
    outbound_messages: list[OutboundMessageResponse]

    model_config = {"from_attributes": True}


class POActionResponse(BaseModel):
    success: bool
    message: str
    po_id: uuid.UUID


class POUpdateRequest(BaseModel):
    """Fields the ops team can manually correct before pushing to SAP."""
    buyer_po_number: str | None = None
    buyer_po_date: str | None = None          # ISO date string YYYY-MM-DD
    buyer_name: str | None = None
    buyer_gstin: str | None = None
    ship_to_name: str | None = None
    ship_to_code: str | None = None
    requested_delivery_date: str | None = None  # ISO date string YYYY-MM-DD
    grand_total: Decimal | None = None
    currency: str | None = None


# ── Dashboard ──────────────────────────────────────────────────────────────────

class PartnerStat(BaseModel):
    partner_code: str
    partner_name: str
    po_count: int
    error_count: int


class DashboardToday(BaseModel):
    total_pos: int
    confirmed_pos: int
    exception_pos: int
    pending_b1_push: int
    partner_stats: list[PartnerStat]
    last_updated: datetime


class SLABreachItem(BaseModel):
    po_id: uuid.UUID
    buyer_po_number: str
    partner_code: str
    po_status: str
    hours_overdue: float
    created_at: datetime


class UnmappedSkuItem(BaseModel):
    buyer_sku: str
    partner_code: str
    description: str | None
    occurrence_count: int
    last_seen: datetime


class ActivityItem(BaseModel):
    entity_type: str
    entity_id: str
    description: str
    status: str
    created_at: datetime


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ExceptionItem(BaseModel):
    id: uuid.UUID
    po_id: uuid.UUID
    buyer_po_number: str
    partner_code: str
    issue_code: str
    severity: str
    field_name: str | None
    message: str
    resolution_note: str | None
    resolved_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ResolveExceptionRequest(BaseModel):
    resolution_note: str = Field(min_length=1, max_length=1000)


# ── Master Data ────────────────────────────────────────────────────────────────
#
# REST convention across all master-data resources:
#   POST .../sync   — bulk upsert pushed FROM SAP. SAP-side changes land here; the
#                     middleware never re-queries SAP's Service Layer just to read
#                     master data (Service Layer sessions are licensed and capped —
#                     CLAUDE.md section 7). Idempotent, keyed by natural key.
#   GET  ...        — reads OUR tables (never calls SAP live).
#   PUT  .../{id}   — ops-side manual correction (partners and ship-to only).
#
# SKU mappings have no PUT: SAP is their sole author (b1ItemCode is not-null, so every
# row is a confirmed mapping). A PO line whose buyer SKU has no mapping is raised as
# E002_SKU_UNRESOLVED against the PO, and is fixed in SAP, not here.
# Ship-to sync still never touches b1_whs_code — that mapping remains an ops decision.

class TradingPartnerResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    source_channel: str
    is_active: bool
    gmail_label: str | None
    b1_card_code: str | None
    gstin: str | None
    pan_card: str | None
    business_type: str | None
    group_name: str | None
    phone_numbers: list[str] | None
    email_address: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TradingPartnerUpdate(BaseModel):
    # Accepted-but-immutable, so GET -> edit -> PUT round-trips cleanly.
    id: uuid.UUID | None = None
    code: str | None = None
    created_at: datetime | None = None

    # Editable: governs future routing only. Existing raw_messages / POs carry their
    # own source_channel stamped at ingestion, so changing this rewrites no history.
    # This is the normal onboarding step: MANUAL -> API / WEBHOOK / EMAIL.
    source_channel: str | None = None
    webhook_secret: str | None = None
    asn_sla_hours: int | None = None

    name: str | None = None
    is_active: bool | None = None
    b1_card_code: str | None = None
    gmail_label: str | None = None
    gstin: str | None = None
    pan_card: str | None = None
    business_type: str | None = None
    group_name: str | None = None
    phone_numbers: list[str] | None = None
    email_address: str | None = None

    model_config = {"extra": "forbid"}


class TradingPartnerCreate(BaseModel):
    """
    Onboard a new trading partner.

    Deliberately separate from `POST /partners/sync`, which stays update-only: a bulk
    push of SAP's full customer list must never mass-create partners here, because most
    SAP business partners are not EDI trading partners. Creating one is an explicit act.

    `source_channel` defaults to MANUAL — the safe state. The scheduler only polls
    partners whose channel is API, or EMAIL *with* a gmail_label, so a MANUAL partner
    accepts master data and sits inert until its integration is wired up.
    """
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    source_channel: str = "MANUAL"

    # Integration config — only meaningful once an adapter/parser exists for this code.
    gmail_label: str | None = None
    webhook_secret: str | None = None
    asn_sla_hours: int = 48

    # Master data — normally supplied later by POST /partners/sync.
    b1_card_code: str | None = None
    gstin: str | None = None
    pan_card: str | None = None
    business_type: str | None = None
    group_name: str | None = None
    phone_numbers: list[str] | None = None
    email_address: str | None = None
    is_active: bool = True

    model_config = {"extra": "forbid"}


class TradingPartnerWriteResponse(TradingPartnerResponse):
    """
    Partner after a create or update, plus anything the caller should know before
    expecting POs to flow (missing parser, unset gmail_label, inert MANUAL channel).
    """
    warnings: list[str] = []


class TradingPartnerSyncItem(BaseModel):
    """One Business Partner / Customer record as SAP represents it."""
    # ── Accepted for GET -> sync round-trips; ignored, never written ──────────
    id: uuid.UUID | None = None
    source_channel: str | None = None
    gmail_label: str | None = None
    ack_sla_hours: int | None = None
    asn_sla_hours: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    is_active: bool | None = None      # alias of `status`; `status` wins if both sent

    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    b1_card_code: str | None = None
    gstin: str | None = None
    pan_card: str | None = None
    business_type: str | None = None
    group_name: str | None = None
    phone_numbers: list[str] | None = None
    email_address: str | None = None
    status: bool | None = None


    model_config = {"extra": "forbid"}

class TradingPartnerSyncRequest(BaseModel):
    partners: list[TradingPartnerSyncItem] = Field(min_length=1, max_length=2000)


class MaterialMasterResponse(BaseModel):
    """Item_master — column names mirror SAP B1 OITM 1:1."""
    id: uuid.UUID
    item_code: str
    item_name: str | None
    frgn_name: str | None
    hsn: str | None
    tax_rate: Decimal | None
    itms_grp_cod: int | None
    items_group_name: str | None
    invntry_uom: str | None
    sal_unit_msr: str | None
    vat_group_pu: str | None
    vat_group_sa: str | None
    case_size: int | None
    lot_size: int | None
    grammage: str | None
    ean_code: str | None
    mrp: Decimal | None
    frozen_for: bool
    valid_for: int          # OITM validFor, 0/1 as SAP sends it
    is_active: bool         # our operational flag

    model_config = {"from_attributes": True}


class MaterialMasterCreate(BaseModel):
    """
    Manual single-item add. Accepts the full Item_master field set — a partial schema
    here would silently drop tax_rate/mrp/ean_code and create a half-populated item.
    """
    item_code: str = Field(min_length=1, max_length=50)
    item_name: str = Field(min_length=1, max_length=500)
    frgn_name: str | None = None
    hsn: str | None = None
    tax_rate: Decimal | None = None
    itms_grp_cod: int | None = None
    items_group_name: str | None = None
    # NOT NULL on the table — default rather than let a None reach the insert.
    invntry_uom: str = Field(default="PCS", min_length=1, max_length=20)
    sal_unit_msr: str | None = None
    vat_group_pu: str | None = None
    vat_group_sa: str | None = None
    case_size: int | None = None
    lot_size: int | None = None
    grammage: str | None = None
    ean_code: str | None = None
    mrp: Decimal | None = None
    frozen_for: bool = False
    valid_for: int = Field(default=1, ge=0, le=1)
    is_active: bool = True

    model_config = {"extra": "forbid"}


class MaterialMasterUpdate(BaseModel):
    """
    Ops-side edit of a single item. All fields optional — only the keys you send are
    written. Note that a later `POST /materials/sync` from SAP overwrites these, since
    SAP remains the source of truth for Item_master.
    """
    # Identity fields are accepted so a client can GET, edit one value, and PUT the
    # whole object back. They are ignored when unchanged and rejected when changed —
    # see the immutability note in the route.
    id: uuid.UUID | None = None
    item_code: str | None = None

    item_name: str | None = Field(default=None, min_length=1, max_length=500)
    frgn_name: str | None = None
    hsn: str | None = None
    tax_rate: Decimal | None = None
    itms_grp_cod: int | None = None
    items_group_name: str | None = None
    invntry_uom: str | None = Field(default=None, min_length=1, max_length=20)
    sal_unit_msr: str | None = None
    vat_group_pu: str | None = None
    vat_group_sa: str | None = None
    case_size: int | None = None
    lot_size: int | None = None
    grammage: str | None = None
    ean_code: str | None = None
    mrp: Decimal | None = None
    frozen_for: bool | None = None
    valid_for: int | None = Field(default=None, ge=0, le=1)
    is_active: bool | None = None

    model_config = {"extra": "forbid"}


class MaterialMasterSyncItem(BaseModel):
    """One SAP B1 OITM (Item Master) record."""
    # ── Accepted for GET -> sync round-trips; ignored, never written ──────────
    id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    uom_group: str | None = None

    item_code: str = Field(min_length=1, max_length=50)
    item_name: str = Field(min_length=1, max_length=500)
    frgn_name: str | None = None
    hsn: str | None = None
    tax_rate: Decimal | None = None
    itms_grp_cod: int | None = None
    items_group_name: str | None = None
    invntry_uom: str = Field(default="PCS", min_length=1, max_length=20)  # NOT NULL on table
    sal_unit_msr: str | None = None
    vat_group_pu: str | None = None
    vat_group_sa: str | None = None
    case_size: int | None = None
    lot_size: int | None = None
    grammage: str | None = None
    ean_code: str | None = None
    mrp: Decimal | None = None
    frozen_for: bool = False
    valid_for: int = Field(default=1, ge=0, le=1)
    is_active: bool = True


    model_config = {"extra": "forbid"}

class MaterialMasterSyncRequest(BaseModel):
    items: list[MaterialMasterSyncItem] = Field(min_length=1, max_length=2000)


class SkuMappingResponse(BaseModel):
    """
    SKU_Mapping row. No mapping_status/confidence_score: SAP is the only author and
    b1ItemCode is not-null, so every row here is a confirmed mapping. `mrp` is joined
    from Item_master via the item code — it is item data, not customer-specific.
    """
    id: uuid.UUID
    trading_partner_id: uuid.UUID
    partner_code: str
    buyer_sku: str
    item_name: str | None
    b1_item_code: str
    unit_price: Decimal | None
    margin: Decimal | None
    mrp: Decimal | None            # joined from Item_master
    ean_code: str | None           # joined from Item_master
    case_size: int | None          # joined from Item_master
    grammage: str | None           # joined from Item_master
    qty_per_buyer_uom: Decimal | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkuMappingSyncItem(BaseModel):
    """
    One customer-SKU record from SAP. b1_item_code is required — it is the whole point
    of the row. Sync fails the row loudly if that item code is absent from Item_master
    (the schema marks that ref "no cascade — fail loud on unmapped item").
    """
    # ── Accepted for GET -> sync round-trips; ignored, never written ──────────
    id: uuid.UUID | None = None
    trading_partner_id: uuid.UUID | None = None
    mrp: Decimal | None = None         # lives on Item_master, shown here for convenience
    created_at: datetime | None = None
    updated_at: datetime | None = None
    is_active: bool | None = None      # alias of `status`; `status` wins if both sent

    partner_code: str = Field(min_length=1, max_length=50)
    buyer_sku: str = Field(min_length=1, max_length=100)
    b1_item_code: str = Field(min_length=1, max_length=50)
    item_name: str | None = None
    unit_price: Decimal | None = None
    margin: Decimal | None = None
    qty_per_buyer_uom: Decimal | None = None
    status: bool | None = None


    model_config = {"extra": "forbid"}

class SkuMappingSyncRequest(BaseModel):
    mappings: list[SkuMappingSyncItem] = Field(min_length=1, max_length=2000)


class ShipToMappingResponse(BaseModel):
    id: uuid.UUID
    trading_partner_id: uuid.UUID
    partner_code: str
    buyer_whs_code: str
    buyer_warehouse_name: str | None
    b1_whs_code: str | None
    mapping_status: str
    is_active: bool
    address_line: str | None
    address_type: list[str] | None
    street: str | None
    block: str | None
    city: str | None
    zip_code: str | None
    state: str | None
    country: str | None
    gst_registration_no: str | None
    gst_type: list[str] | None
    poc_name: str | None
    poc_email: str | None
    poc_phone: str | None

    model_config = {"from_attributes": True}


class ShipToMappingUpdate(BaseModel):
    """
    Ops edit of one ship-to. Only `b1_whs_code` and `is_active` are writable — the
    address and GST fields are owned by `POST /ship-to/sync`. Those, plus the identity
    fields, are accepted here so a GET -> edit -> PUT round-trip works, but changing
    them is rejected rather than silently dropped.
    """
    id: uuid.UUID | None = None
    trading_partner_id: uuid.UUID | None = None
    partner_code: str | None = None
    buyer_whs_code: str | None = None
    mapping_status: str | None = None
    # Sync-owned; accepted for round-trip, not writable here.
    buyer_warehouse_name: str | None = None
    address_line: str | None = None
    address_type: list[str] | None = None
    street: str | None = None
    block: str | None = None
    city: str | None = None
    zip_code: str | None = None
    state: str | None = None
    country: str | None = None
    gst_registration_no: str | None = None
    gst_type: list[str] | None = None

    # Writable here AND by sync — contact info drifts and ops may fix it locally.
    poc_name: str | None = None
    poc_email: str | None = None
    poc_phone: str | None = None

    b1_whs_code: str | None = Field(default=None, min_length=1, max_length=50)
    is_active: bool | None = None

    model_config = {"extra": "forbid"}


class ShipToMappingSyncItem(BaseModel):
    """One ship-to / delivery-address record (SAP CRD1 business-partner addresses)."""
    # ── Accepted for GET -> sync round-trips; ignored, never written ──────────
    id: uuid.UUID | None = None
    trading_partner_id: uuid.UUID | None = None
    b1_whs_code: str | None = None     # ops-owned mapping — set via PUT /ship-to/{id}
    mapping_status: str | None = None
    is_active: bool | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    partner_code: str = Field(min_length=1, max_length=50)
    buyer_whs_code: str = Field(min_length=1, max_length=100)
    buyer_warehouse_name: str | None = None
    address_line: str | None = None
    address_type: list[str] | None = None
    street: str | None = None
    block: str | None = None
    city: str | None = None
    zip_code: str | None = None
    state: str | None = None
    country: str | None = None
    gst_registration_no: str | None = None
    gst_type: list[str] | None = None
    poc_name: str | None = None
    poc_email: str | None = None
    poc_phone: str | None = None


    model_config = {"extra": "forbid"}

class ShipToMappingSyncRequest(BaseModel):
    mappings: list[ShipToMappingSyncItem] = Field(min_length=1, max_length=2000)


class MasterDataSyncResult(BaseModel):
    created: int
    updated: int
    skipped: int
    errors: list[str] = []


# ── Customer detail (drill-down) ───────────────────────────────────────────────
# The Master Data UI is two tabs: Customers and Item Master. Expanding a customer
# row loads this payload — its SKU mappings and ship-to addresses in one round trip,
# rather than the ops user hunting across separate screens.

class CustomerSkuMappingItem(BaseModel):
    """SKU_Mapping row as shown under its parent customer."""
    id: uuid.UUID
    buyer_sku: str                       # buyerSKUCode
    item_name: str | None                # itemName
    b1_item_code: str                    # b1ItemCode -> Item_master.itemCode
    unit_price: Decimal | None           # customer-specific negotiated price
    margin: Decimal | None               # customer-specific
    mrp: Decimal | None                  # joined from Item_master (item data, not per-customer)
    ean_code: str | None                 # joined from Item_master
    case_size: int | None                # joined from Item_master
    grammage: str | None                 # joined from Item_master
    qty_per_buyer_uom: Decimal | None
    is_active: bool                      # status
    created_at: datetime
    updated_at: datetime


class CustomerShipToItem(BaseModel):
    """Ship_to_mapping row as shown under its parent customer."""
    id: uuid.UUID
    dc_code: str                         # dcCode / buyer_whs_code
    warehouse_name: str | None
    b1_whs_code: str | None
    address: str | None                  # address
    address_type: list[str] | None       # addressType[]
    street: str | None
    block: str | None
    city: str | None
    zip_code: str | None
    state: str | None
    country: str | None
    gst_regn_no: str | None              # GSTRegnNO
    gst_type: list[str] | None           # gstType[]
    poc_name: str | None
    poc_email: str | None
    poc_phone: str | None
    mapping_status: str
    is_active: bool


# ── Bill-to Mappings ──────────────────────────────────────────────────────────
# Mirrors the ship-to shapes. Kept as a separate set rather than a `type` flag on
# ship-to because the two carry different B1 targets (warehouse vs BP address) and
# different tax roles: ship-to state decides CGST/SGST vs IGST, bill-to GSTIN is what
# prints on the invoice.

class BillToMappingResponse(BaseModel):
    id: uuid.UUID
    trading_partner_id: uuid.UUID
    partner_code: str
    buyer_bill_to_code: str
    buyer_entity_name: str | None
    b1_bill_to_code: str | None
    mapping_status: str
    is_active: bool
    address_line: str | None
    address_type: list[str] | None
    street: str | None
    block: str | None
    city: str | None
    zip_code: str | None
    state: str | None
    country: str | None
    gst_registration_no: str | None
    gst_type: list[str] | None
    poc_name: str | None
    poc_email: str | None
    poc_phone: str | None

    model_config = {"from_attributes": True}


class BillToMappingUpdate(BaseModel):
    """
    Ops edit of one bill-to. Only `b1_bill_to_code`, `is_active` and the POC fields are
    writable — address and GST data is owned by `POST /bill-to/sync`. Identity and
    sync-owned fields are accepted so a GET -> edit -> PUT round-trip works, but
    *changing* them is rejected rather than silently dropped.
    """
    id: uuid.UUID | None = None
    trading_partner_id: uuid.UUID | None = None
    partner_code: str | None = None
    buyer_bill_to_code: str | None = None
    mapping_status: str | None = None
    # Sync-owned; accepted for round-trip, not writable here.
    buyer_entity_name: str | None = None
    address_line: str | None = None
    address_type: list[str] | None = None
    street: str | None = None
    block: str | None = None
    city: str | None = None
    zip_code: str | None = None
    state: str | None = None
    country: str | None = None
    gst_registration_no: str | None = None
    gst_type: list[str] | None = None

    # Writable here AND by sync — contact info drifts and ops may fix it locally.
    poc_name: str | None = None
    poc_email: str | None = None
    poc_phone: str | None = None

    b1_bill_to_code: str | None = Field(default=None, min_length=1, max_length=50)
    is_active: bool | None = None

    model_config = {"extra": "forbid"}


class BillToMappingSyncItem(BaseModel):
    """One bill-to / invoicing-address record (SAP CRD1 business-partner addresses)."""
    # ── Accepted for GET -> sync round-trips; ignored, never written ──────────
    id: uuid.UUID | None = None
    trading_partner_id: uuid.UUID | None = None
    b1_bill_to_code: str | None = None   # ops-owned mapping — set via PUT /bill-to/{id}
    mapping_status: str | None = None
    is_active: bool | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    partner_code: str = Field(min_length=1, max_length=50)
    buyer_bill_to_code: str = Field(min_length=1, max_length=100)
    buyer_entity_name: str | None = None
    address_line: str | None = None
    address_type: list[str] | None = None
    street: str | None = None
    block: str | None = None
    city: str | None = None
    zip_code: str | None = None
    state: str | None = None
    country: str | None = None
    gst_registration_no: str | None = None
    gst_type: list[str] | None = None
    poc_name: str | None = None
    poc_email: str | None = None
    poc_phone: str | None = None

    model_config = {"extra": "forbid"}


class BillToMappingSyncRequest(BaseModel):
    mappings: list[BillToMappingSyncItem] = Field(min_length=1, max_length=2000)


# ── Branch Master (SAP OBPL) ──────────────────────────────────────────────────
# Our own org structure, not a retailer's. There is no mapping decision to make here:
# SAP authors every business field, so `is_active` (ours) is the only writable one.
# A B1 Sales Order line carries both WhsCode and BPLId, and under the India
# localization the branch is the GST registration point that decides CGST/SGST vs IGST.

class BranchMasterResponse(BaseModel):
    id: uuid.UUID
    bpl_id: int
    bpl_name: str
    disabled: bool
    address: str | None
    street: str | None
    block: str | None
    city: str | None
    zip_code: str | None
    state: str | None
    country: str | None
    gstin: str | None
    is_active: bool
    notes: str | None
    warehouse_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BranchMasterUpdate(BaseModel):
    """
    Ops edit of one branch. Only `is_active` and `notes` are writable — every business
    field is owned by `POST /branches/sync`. The SAP-owned fields are accepted so a
    GET -> edit -> PUT round-trip works, but *changing* one is rejected rather than
    silently dropped.
    """
    id: uuid.UUID | None = None
    bpl_id: int | None = None
    # Sync-owned; accepted for round-trip, not writable here.
    bpl_name: str | None = None
    disabled: bool | None = None
    address: str | None = None
    street: str | None = None
    block: str | None = None
    city: str | None = None
    zip_code: str | None = None
    state: str | None = None
    country: str | None = None
    gstin: str | None = None
    warehouse_count: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    is_active: bool | None = None
    notes: str | None = None

    model_config = {"extra": "forbid"}


class BranchMasterSyncItem(BaseModel):
    """
    One SAP B1 OBPL (branch / business place) record.

    `disabled` is SAP's NVARCHAR Y/N flag — send `"Y"`/`"N"`, `true`/`false`, or `1`/`0`;
    all three are accepted and stored as a boolean.
    """
    # ── Accepted for GET -> sync round-trips; ignored, never written ──────────
    id: uuid.UUID | None = None
    is_active: bool | None = None        # ops-owned — set via PUT /branches/{id}
    notes: str | None = None             # ops-owned
    warehouse_count: int | None = None   # derived
    created_at: datetime | None = None
    updated_at: datetime | None = None

    bpl_id: int = Field(ge=1)
    bpl_name: str = Field(min_length=1, max_length=255)
    disabled: bool = False
    address: str | None = None
    street: str | None = None
    block: str | None = None
    city: str | None = None
    zip_code: str | None = None
    state: str | None = None
    country: str | None = None
    gstin: str | None = None

    model_config = {"extra": "forbid"}


class BranchMasterSyncRequest(BaseModel):
    branches: list[BranchMasterSyncItem] = Field(min_length=1, max_length=2000)


# ── Warehouse Master (SAP OWHS) ───────────────────────────────────────────────

class WarehouseMasterResponse(BaseModel):
    id: uuid.UUID
    whs_code: str
    whs_name: str
    bpl_id: int            # joined from branch_master — the SAP-facing branch key
    branch_name: str | None
    inactive: bool
    location: int | None
    street: str | None
    block: str | None
    city: str | None
    zip_code: str | None
    state: str | None
    country: str | None
    is_active: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WarehouseMasterUpdate(BaseModel):
    """
    Ops edit of one warehouse. Only `is_active` and `notes` are writable — everything
    else, including the branch link, is owned by `POST /warehouses/sync`. Re-parenting
    a warehouse to a different branch is a SAP change, not a local one: B1 rejects a
    document whose warehouse and branch disagree.
    """
    id: uuid.UUID | None = None
    whs_code: str | None = None
    # Sync-owned; accepted for round-trip, not writable here.
    whs_name: str | None = None
    bpl_id: int | None = None
    branch_name: str | None = None
    inactive: bool | None = None
    location: int | None = None
    street: str | None = None
    block: str | None = None
    city: str | None = None
    zip_code: str | None = None
    state: str | None = None
    country: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    is_active: bool | None = None
    notes: str | None = None

    model_config = {"extra": "forbid"}


class WarehouseMasterSyncItem(BaseModel):
    """
    One SAP B1 OWHS (warehouse) record.

    `bpl_id` must already exist in branch_master — push branches before warehouses.
    `inactive` is SAP's NVARCHAR Y/N flag; `"Y"`/`"N"`, `true`/`false` and `1`/`0` are
    all accepted.
    """
    # ── Accepted for GET -> sync round-trips; ignored, never written ──────────
    id: uuid.UUID | None = None
    branch_name: str | None = None       # derived from the branch
    is_active: bool | None = None        # ops-owned — set via PUT /warehouses/{id}
    notes: str | None = None             # ops-owned
    created_at: datetime | None = None
    updated_at: datetime | None = None

    whs_code: str = Field(min_length=1, max_length=20)
    whs_name: str = Field(min_length=1, max_length=255)
    bpl_id: int = Field(ge=1)
    inactive: bool = False
    location: int | None = None
    street: str | None = None
    block: str | None = None
    city: str | None = None
    zip_code: str | None = None
    state: str | None = None
    country: str | None = None

    model_config = {"extra": "forbid"}


class WarehouseMasterSyncRequest(BaseModel):
    warehouses: list[WarehouseMasterSyncItem] = Field(min_length=1, max_length=2000)


class CustomerBillToItem(BaseModel):
    """Bill_to_mapping row as shown under its parent customer."""
    id: uuid.UUID
    bill_to_code: str
    entity_name: str | None
    b1_bill_to_code: str | None
    address: str | None
    address_type: list[str] | None
    street: str | None
    block: str | None
    city: str | None
    zip_code: str | None
    state: str | None
    country: str | None
    gst_regn_no: str | None
    gst_type: list[str] | None
    poc_name: str | None
    poc_email: str | None
    poc_phone: str | None
    mapping_status: str
    is_active: bool


class CustomerDetailResponse(BaseModel):
    """One customer plus its full SKU-mapping, ship-to and bill-to arrays."""
    id: uuid.UUID
    code: str
    name: str
    source_channel: str
    is_active: bool
    gmail_label: str | None
    b1_card_code: str | None
    gstin: str | None
    pan_card: str | None
    business_type: str | None
    group_name: str | None
    phone_numbers: list[str] | None
    email_address: str | None
    created_at: datetime
    sku_mappings: list[CustomerSkuMappingItem]
    ship_to_mappings: list[CustomerShipToItem]
    bill_to_mappings: list[CustomerBillToItem]


# ── Inbox (raw messages / email PO view) ──────────────────────────────────────

class InboxPartnerSummary(BaseModel):
    """Shared by all three inbox partner lists (email / api / manual)."""
    code: str
    name: str
    source_channel: str
    gmail_label: str | None = None     # only meaningful for EMAIL partners
    total: int
    pending: int
    failed: int
    last_received_at: datetime | None


class InboxMessageItem(BaseModel):
    id: uuid.UUID
    external_id: str
    subject: str | None
    sender: str | None
    received_at: datetime
    attachment_count: int
    parse_status: str
    processed: bool
    po_id: uuid.UUID | None
    po_number: str | None


class AttachmentInfo(BaseModel):
    filename: str
    url: str
    mime_type: str
    size_bytes: int


class InboxMessageDetail(BaseModel):
    id: uuid.UUID
    partner_code: str
    partner_name: str
    external_id: str
    subject: str | None
    sender: str | None
    received_at: datetime
    attachments: list[AttachmentInfo]
    body_preview: str | None
    parse_status: str
    processed: bool
    po_id: uuid.UUID | None
    po_number: str | None
    po_status: str | None
    created_at: datetime


# ── API Inbox (API/webhook raw messages) ──────────────────────────────────────

class ApiPartnerStatus(BaseModel):
    code: str
    name: str
    source_channel: str
    last_fetched_at: datetime | None        # watermark from api_config (poll partners only)
    last_message_at: datetime | None        # most recent raw_message.received_at
    messages_last_24h: int
    failed_last_24h: int
    webhook_url: str | None                 # e.g. /api/webhooks/BLINKIT (webhook partners only)
    is_configured: bool                     # True if credentials are present in settings


class ApiMessageDetail(BaseModel):
    id: uuid.UUID
    partner_code: str
    partner_name: str
    external_id: str
    received_at: datetime
    payload: dict | None
    parse_status: str
    processed: bool
    po_id: uuid.UUID | None
    po_number: str | None
    po_status: str | None
    created_at: datetime


# ── B1 Logs ────────────────────────────────────────────────────────────────────

class B1LogListItem(BaseModel):
    id: uuid.UUID
    po_id: uuid.UUID | None
    http_method: str
    endpoint: str
    http_status: int | None
    success: bool
    error_code: str | None
    error_message: str | None
    duration_ms: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class B1LogDetail(BaseModel):
    id: uuid.UUID
    po_id: uuid.UUID | None
    http_method: str
    endpoint: str
    request_payload: dict[str, Any] | None
    response_payload: dict[str, Any] | None
    http_status: int | None
    success: bool
    error_code: str | None
    error_message: str | None
    duration_ms: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Invoices (EDI 810) — pushed to us by SAP B1 ────────────────────────────────
# SAP owns invoice creation: it posts A/R Invoices against the Sales Order we
# created from the retailer PO, then pushes them here. This mirrors the master-data
# direction of travel (SAP POSTs, we store) and replaces polling B1 as the primary
# path — polling remains as a slow backup so a failed push is never silent.
#
# One PO can carry many invoices (partial dispatches), which is why the link is
# EdiInvoice.po_id -> edi_purchase_orders and not the other way round.

class InvoiceLineItemPush(BaseModel):
    """
    One line of an A/R Invoice as SAP sends it.

    `buyer_sku` and `po_line_number` are both optional but one of them makes the line
    traceable back to the originating PO line (po_line_id). Without either we still
    store the line, but quantity-vs-ordered validation cannot run on it.
    """
    b1_item_code: str = Field(min_length=1, max_length=50)
    qty: Decimal = Field(gt=0)

    buyer_sku: str | None = Field(default=None, max_length=100)
    po_line_number: int | None = None

    description: str | None = Field(default=None, max_length=500)
    hsn_code: str | None = Field(default=None, max_length=10)
    uom: str | None = Field(default=None, max_length=20)
    unit_price: Decimal | None = None
    taxable_amount: Decimal | None = None
    cgst_rate: Decimal | None = None
    cgst_amount: Decimal | None = None
    sgst_rate: Decimal | None = None
    sgst_amount: Decimal | None = None
    igst_rate: Decimal | None = None
    igst_amount: Decimal | None = None
    cess_rate: Decimal | None = None
    cess_amount: Decimal | None = None
    line_total: Decimal | None = None

    # Carried onto the ASN line when the 856 is built from this invoice.
    batch_number: str | None = Field(default=None, max_length=100)
    expiry_date: date | None = None

    model_config = {"extra": "forbid"}


class InvoicePush(BaseModel):
    """
    One A/R Invoice from SAP.

    Identifying the PO: send either `b1_sales_order_doc_entry` (B1's own key for the
    Sales Order we created) or `partner_code` + `po_number` (the retailer's PO number).
    DocEntry is preferred — it cannot drift. At least one form is required; the route
    rejects an invoice that carries neither.
    """
    invoice_number: str = Field(min_length=1, max_length=100)
    invoice_date: date
    line_items: list[InvoiceLineItemPush] = Field(min_length=1, max_length=500)

    # ── PO identification (at least one form required) ────────────────────────
    b1_sales_order_doc_entry: int | None = None
    partner_code: str | None = Field(default=None, max_length=50)
    po_number: str | None = Field(default=None, max_length=200)

    b1_invoice_doc_entry: int | None = None
    b1_invoice_doc_num: int | None = None

    # India e-invoicing — populated by B1 after IRP submission. Usually absent on the
    # first push and filled in by a later re-push of the same invoice_number.
    irn: str | None = Field(default=None, max_length=200)
    eway_bill_number: str | None = Field(default=None, max_length=50)
    eway_bill_date: date | None = None

    subtotal_amount: Decimal | None = None
    cgst_amount: Decimal | None = None
    sgst_amount: Decimal | None = None
    igst_amount: Decimal | None = None
    cess_amount: Decimal | None = None
    round_off: Decimal | None = None
    grand_total: Decimal | None = None

    # ── Shipment details, carried onto the ASN ────────────────────────────────
    shipment_date: date | None = None
    carrier: str | None = Field(default=None, max_length=100)
    tracking_number: str | None = Field(default=None, max_length=200)

    model_config = {"extra": "forbid"}


class InvoicePushRequest(BaseModel):
    invoices: list[InvoicePush] = Field(min_length=1, max_length=500)


class InvoicePushResultItem(BaseModel):
    """Per-invoice outcome, so a partial batch failure is actionable."""
    invoice_number: str
    outcome: str          # CREATED | UPDATED | SKIPPED | ERROR
    invoice_id: uuid.UUID | None = None
    po_number: str | None = None
    asn_number: str | None = None
    asn_dispatched: bool = False
    issues: list[str] = []


class InvoicePushResult(BaseModel):
    created: int
    updated: int
    skipped: int
    errors: list[str] = []
    results: list[InvoicePushResultItem] = []


class InvoiceAsnActionResponse(BaseModel):
    """Result of a manual ASN send from the Invoices tab."""
    invoice_id: uuid.UUID
    asn_number: str
    queued: bool
    validation_override: bool
    message: str


class InvoiceLineItemResponse(BaseModel):
    id: uuid.UUID
    b1_item_code: str | None
    description: str | None
    hsn_code: str | None
    qty: Decimal
    uom: str | None
    unit_price: Decimal | None
    taxable_amount: Decimal | None
    cgst_amount: Decimal | None
    sgst_amount: Decimal | None
    igst_amount: Decimal | None
    line_total: Decimal | None

    model_config = {"from_attributes": True}


class InvoiceResponse(BaseModel):
    id: uuid.UUID
    po_id: uuid.UUID
    asn_id: uuid.UUID | None
    invoice_number: str
    invoice_date: date
    b1_invoice_doc_entry: int | None
    b1_invoice_doc_num: int | None
    irn: str | None
    eway_bill_number: str | None
    subtotal_amount: Decimal | None
    cgst_amount: Decimal | None
    sgst_amount: Decimal | None
    igst_amount: Decimal | None
    round_off: Decimal | None
    grand_total: Decimal | None
    status: str
    created_at: datetime

    # Denormalised for the PO-detail Invoices tab, so it needs no extra calls.
    asn_number: str | None = None
    asn_status: str | None = None
    outbound_status: str | None = None
    line_items: list[InvoiceLineItemResponse] = []

    model_config = {"from_attributes": True}


# ── SAP push: branch / warehouse selection ────────────────────────────────────
# A B1 Sales Order needs routing our canonical PO does not carry. The branch is the
# from-state for place of supply, so it decides CGST+SGST vs IGST, and the warehouse
# must belong to it or B1 rejects the document. Both are chosen by the operator.

class B1AddressOption(BaseModel):
    """One BP address on the customer in B1 — a candidate ShipToCode / PayToCode."""
    address_name: str
    address_type: str                    # bo_ShipTo | bo_BillTo
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    gstin: str | None = None
    # True when this address's PIN or state matches what the retailer put on the PO.
    matches_po: bool = False


class WarehouseOption(BaseModel):
    whs_code: str
    whs_name: str
    bpl_id: int


class BranchOption(BaseModel):
    bpl_id: int
    bpl_name: str
    state: str | None = None
    gstin: str | None = None
    warehouses: list[WarehouseOption] = []


class DispatchOptionsResponse(BaseModel):
    """
    Everything the push dialog needs: what may be chosen, what is already chosen, and
    what the tax outcome would be. `interstate` is precomputed per branch so the
    operator sees the consequence of the choice rather than discovering it on the
    posted document.
    """
    po_id: uuid.UUID
    buyer_po_number: str
    partner_code: str
    b1_card_code: str | None
    ship_to_state: str | None            # resolved from the PO (GSTIN first)
    ship_to_pincode: str | None
    branches: list[BranchOption] = []
    addresses: list[B1AddressOption] = []
    address_lookup_error: str | None = None   # B1 unreachable — selection still works
    # Current selection, from a previous attempt or a saved default.
    selected_bpl_id: int | None = None
    selected_whs_code: str | None = None
    selected_ship_to_code: str | None = None
    selected_pay_to_code: str | None = None
    # bpl_id -> "CSGST" | "IGST", so the UI can label each branch with its tax effect.
    tax_by_branch: dict[str, str] = {}


class SapPushRequest(BaseModel):
    """Operator's selection. bpl_id and whs_code are required — never guessed."""
    bpl_id: int = Field(ge=1)
    whs_code: str = Field(min_length=1, max_length=20)
    ship_to_code: str | None = Field(default=None, max_length=100)
    pay_to_code: str | None = Field(default=None, max_length=100)

    model_config = {"extra": "forbid"}


class SapPreviewResponse(BaseModel):
    """The exact JSON that would be POSTed to /Orders, plus anything worth a second look."""
    po_id: uuid.UUID
    endpoint: str
    payload: dict[str, Any]
    warnings: list[str] = []
