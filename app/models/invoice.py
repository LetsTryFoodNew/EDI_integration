"""EDI 810 Invoice and line items (with India e-invoicing fields)."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.asn import EdiAdvanceShipNotice
    from app.models.edi_po import EdiPoLineItem, EdiPurchaseOrder
    from app.models.master_data import TradingPartner


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EdiInvoice(Base):
    """
    A/R Invoice record (EDI 810).
    IRN is populated by SAP B1 after e-invoicing submission.
    """

    __tablename__ = "edi_invoices"
    __table_args__ = (
        Index("ix_invoice_po_id", "po_id"),
        Index("ix_invoice_number", "invoice_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    po_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("edi_purchase_orders.id"), nullable=False)
    asn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("edi_advance_ship_notices.id"))
    trading_partner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading_partners.id"), nullable=False)

    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)

    b1_invoice_doc_entry: Mapped[int | None] = mapped_column(Integer)
    b1_invoice_doc_num: Mapped[int | None] = mapped_column(Integer)

    # India e-invoicing
    irn: Mapped[str | None] = mapped_column(String(200))
    eway_bill_number: Mapped[str | None] = mapped_column(String(50))
    eway_bill_date: Mapped[date | None] = mapped_column(Date)

    subtotal_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    cgst_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    sgst_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    igst_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    cess_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    round_off: Mapped[float | None] = mapped_column(Numeric(5, 2))
    grand_total: Mapped[float | None] = mapped_column(Numeric(15, 2))

    # DRAFT | SENT | ACKED | CANCELLED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    purchase_order: Mapped[EdiPurchaseOrder] = relationship("EdiPurchaseOrder", back_populates="invoices")
    asn: Mapped[EdiAdvanceShipNotice | None] = relationship("EdiAdvanceShipNotice", back_populates="invoices")
    trading_partner: Mapped[TradingPartner] = relationship("TradingPartner")
    line_items: Mapped[list[EdiInvoiceLineItem]] = relationship("EdiInvoiceLineItem", back_populates="invoice", cascade="all, delete-orphan")


class EdiInvoiceLineItem(Base):
    """One line of an EDI 810 invoice."""

    __tablename__ = "edi_invoice_line_items"
    __table_args__ = (Index("ix_invoice_line_invoice_id", "invoice_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("edi_invoices.id"), nullable=False)
    po_line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("edi_po_line_items.id"))

    b1_item_code: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(String(500))
    hsn_code: Mapped[str | None] = mapped_column(String(10))
    qty: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    uom: Mapped[str | None] = mapped_column(String(20))
    unit_price: Mapped[float | None] = mapped_column(Numeric(15, 6))
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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    invoice: Mapped[EdiInvoice] = relationship("EdiInvoice", back_populates="line_items")
    po_line: Mapped[EdiPoLineItem | None] = relationship("EdiPoLineItem")
