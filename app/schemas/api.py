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

class TradingPartnerResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    source_channel: str
    is_active: bool
    gmail_label: str | None
    b1_card_code: str | None
    ack_sla_hours: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TradingPartnerUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    b1_card_code: str | None = None
    ack_sla_hours: int | None = None
    gmail_label: str | None = None


class MaterialMasterResponse(BaseModel):
    id: uuid.UUID
    b1_item_code: str
    description: str | None
    hsn_code: str | None
    uom: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class MaterialMasterCreate(BaseModel):
    b1_item_code: str = Field(min_length=1, max_length=100)
    description: str | None = None
    hsn_code: str | None = None
    uom: str | None = None


class SkuMappingResponse(BaseModel):
    id: uuid.UUID
    trading_partner_id: uuid.UUID
    partner_code: str
    buyer_sku: str
    material_id: uuid.UUID | None
    b1_item_code: str | None
    qty_per_buyer_uom: Decimal | None
    mapping_status: str
    confidence_score: float | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SkuMappingUpdate(BaseModel):
    b1_item_code: str
    qty_per_buyer_uom: Decimal = Field(default=Decimal("1"))
    notes: str | None = None


class ShipToMappingResponse(BaseModel):
    id: uuid.UUID
    trading_partner_id: uuid.UUID
    partner_code: str
    buyer_whs_code: str
    b1_whs_code: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class ShipToMappingUpdate(BaseModel):
    b1_whs_code: str = Field(min_length=1, max_length=50)


# ── Inbox (raw messages / email PO view) ──────────────────────────────────────

class InboxPartnerSummary(BaseModel):
    code: str
    name: str
    source_channel: str
    gmail_label: str | None
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
