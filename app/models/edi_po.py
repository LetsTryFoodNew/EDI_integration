"""
Canonical EDI 850 Purchase Order — header, line items, status history,
and validation issues.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._enums import EdiDocType, PoStatus, ValidationStatus

if TYPE_CHECKING:
    from app.models.asn import EdiAdvanceShipNotice
    from app.models.b1_log import B1ApiLog
    from app.models.invoice import EdiInvoice
    from app.models.master_data import SellerEntity, SkuMapping, TradingPartner
    from app.models.outbound import EdiOutboundMessage
    from app.models.raw_messages import RawMessage


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EdiPurchaseOrder(Base):
    """
    Canonical representation of one inbound retailer PO.
    Maps to a SAP B1 Sales Order (ORDR) after validation.
    """

    __tablename__ = "edi_purchase_orders"
    __table_args__ = (
        UniqueConstraint("trading_partner_id", "buyer_po_number", "version", name="uq_po_partner_number_ver"),
        Index("ix_edi_po_partner", "trading_partner_id"),
        Index("ix_edi_po_status", "po_status"),
        Index("ix_edi_po_buyer_po_number", "buyer_po_number"),
        Index("ix_edi_po_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4)

    trading_partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trading_partners.id"), nullable=False
    )
    seller_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seller_entities.id"), nullable=False
    )
    raw_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_messages.id")
    )

    buyer_po_number: Mapped[str] = mapped_column(String(200), nullable=False)
    buyer_po_date: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    doc_type: Mapped[EdiDocType] = mapped_column(
        Enum(EdiDocType, name="edi_doc_type_t", create_type=False),
        nullable=False,
        default=EdiDocType.PO_850,
    )
    po_status: Mapped[PoStatus] = mapped_column(
        Enum(PoStatus, name="po_status_t", create_type=False),
        nullable=False,
        default=PoStatus.RECEIVED,
        index=True,
    )

    # ── Shipping ──────────────────────────────────────────────────────────────
    ship_to_code: Mapped[str | None] = mapped_column(String(100))
    ship_to_name: Mapped[str | None] = mapped_column(String(500))
    ship_to_address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date)

    # ── Financials (all INR) ──────────────────────────────────────────────────
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    subtotal_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    total_discount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    cgst_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    sgst_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    igst_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    cess_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    round_off: Mapped[float | None] = mapped_column(Numeric(5, 2))
    grand_total: Mapped[float | None] = mapped_column(Numeric(15, 2))

    # ── Buyer info ────────────────────────────────────────────────────────────
    buyer_gstin: Mapped[str | None] = mapped_column(String(15))
    buyer_name: Mapped[str | None] = mapped_column(String(255))

    # ── SAP B1 linkage ────────────────────────────────────────────────────────
    b1_sales_order_doc_entry: Mapped[int | None] = mapped_column(Integer)
    b1_sales_order_doc_num: Mapped[int | None] = mapped_column(Integer)
    b1_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    b1_error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Relationships ─────────────────────────────────────────────────────────
    trading_partner: Mapped[TradingPartner] = relationship("TradingPartner", back_populates="purchase_orders")
    seller_entity: Mapped[SellerEntity] = relationship("SellerEntity", back_populates="purchase_orders")
    raw_message: Mapped[RawMessage | None] = relationship("RawMessage")
    line_items: Mapped[list[EdiPoLineItem]] = relationship("EdiPoLineItem", back_populates="purchase_order", cascade="all, delete-orphan")
    status_history: Mapped[list[EdiPoStatusHistory]] = relationship("EdiPoStatusHistory", back_populates="purchase_order", cascade="all, delete-orphan")
    validation_issues: Mapped[list[EdiValidationIssue]] = relationship("EdiValidationIssue", back_populates="purchase_order", cascade="all, delete-orphan")
    outbound_messages: Mapped[list[EdiOutboundMessage]] = relationship("EdiOutboundMessage", back_populates="purchase_order")
    advance_ship_notices: Mapped[list[EdiAdvanceShipNotice]] = relationship("EdiAdvanceShipNotice", back_populates="purchase_order")
    invoices: Mapped[list[EdiInvoice]] = relationship("EdiInvoice", back_populates="purchase_order")
    b1_logs: Mapped[list[B1ApiLog]] = relationship("B1ApiLog", back_populates="purchase_order")


class EdiPoLineItem(Base):
    """One line of a canonical EDI 850 PO."""

    __tablename__ = "edi_po_line_items"
    __table_args__ = (
        UniqueConstraint("po_id", "line_number", name="uq_po_line_number"),
        Index("ix_edi_po_line_po_id", "po_id"),
        Index("ix_edi_po_line_buyer_sku", "buyer_sku"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    po_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("edi_purchase_orders.id"), nullable=False)
    sku_mapping_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sku_mapping.id"))

    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    buyer_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    buyer_sku_description: Mapped[str | None] = mapped_column(String(500))
    hsn_code: Mapped[str | None] = mapped_column(String(10))

    # ── Quantities ────────────────────────────────────────────────────────────
    ordered_qty: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    accepted_qty: Mapped[float | None] = mapped_column(Numeric(12, 4))
    shipped_qty: Mapped[float | None] = mapped_column(Numeric(12, 4))
    invoiced_qty: Mapped[float | None] = mapped_column(Numeric(12, 4))
    buyer_uom: Mapped[str | None] = mapped_column(String(20))
    # Quantity converted to inventory UoM (what we send to B1)
    inventory_qty: Mapped[float | None] = mapped_column(Numeric(12, 4))

    # ── Pricing ───────────────────────────────────────────────────────────────
    unit_price: Mapped[float | None] = mapped_column(Numeric(15, 6))
    discount_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    taxable_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    cgst_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    cgst_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    sgst_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    sgst_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    igst_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    igst_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    cess_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    cess_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    line_total: Mapped[float | None] = mapped_column(Numeric(15, 2))

    # ── SAP linkage ───────────────────────────────────────────────────────────
    sap_material_no: Mapped[str | None] = mapped_column(String(50))
    b1_whs_code: Mapped[str | None] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    purchase_order: Mapped[EdiPurchaseOrder] = relationship("EdiPurchaseOrder", back_populates="line_items")
    sku_mapping: Mapped[SkuMapping | None] = relationship("SkuMapping")
    validation_issues: Mapped[list[EdiValidationIssue]] = relationship("EdiValidationIssue", back_populates="line_item")


class EdiPoStatusHistory(Base):
    """Immutable audit log of every PO status transition."""

    __tablename__ = "edi_po_status_history"
    __table_args__ = (Index("ix_po_status_history_po_id", "po_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    po_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("edi_purchase_orders.id"), nullable=False)
    from_status: Mapped[PoStatus | None] = mapped_column(Enum(PoStatus, name="po_status_t", create_type=False))
    to_status: Mapped[PoStatus] = mapped_column(Enum(PoStatus, name="po_status_t", create_type=False), nullable=False)
    changed_by: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    purchase_order: Mapped[EdiPurchaseOrder] = relationship("EdiPurchaseOrder", back_populates="status_history")


class EdiValidationIssue(Base):
    """Every rule failure or warning on a PO, resolvable by ops team."""

    __tablename__ = "edi_validation_issues"
    __table_args__ = (
        Index("ix_validation_issues_po_id", "po_id"),
        Index("ix_validation_issues_status", "validation_status"),
        Index("ix_validation_issues_code", "issue_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    po_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("edi_purchase_orders.id"), nullable=False)
    line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("edi_po_line_items.id"))

    issue_code: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # ERROR | WARNING | INFO
    message: Mapped[str] = mapped_column(Text, nullable=False)
    field_path: Mapped[str | None] = mapped_column(String(200))

    validation_status: Mapped[ValidationStatus] = mapped_column(
        Enum(ValidationStatus, name="validation_status_t", create_type=False),
        nullable=False,
        default=ValidationStatus.OPEN,
    )
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    purchase_order: Mapped[EdiPurchaseOrder] = relationship("EdiPurchaseOrder", back_populates="validation_issues")
    line_item: Mapped[EdiPoLineItem | None] = relationship("EdiPoLineItem", back_populates="validation_issues")
