"""Outbound document delivery state — 855 Ack, 856 ASN, 810 Invoice, Credit Note."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._enums import EdiDocType

if TYPE_CHECKING:
    from app.models.edi_po import EdiPurchaseOrder
    from app.models.master_data import TradingPartner


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EdiOutboundMessage(Base):
    """
    Tracks every document we send back to a partner.
    Retry policy: 5 attempts with exponential backoff (1m, 5m, 30m, 2h, 6h).
    """

    __tablename__ = "edi_outbound_messages"
    __table_args__ = (
        Index("ix_outbound_po_id", "po_id"),
        Index("ix_outbound_status", "status"),
        Index("ix_outbound_next_retry", "next_retry_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    po_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("edi_purchase_orders.id"), nullable=False)
    trading_partner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading_partners.id"), nullable=False)

    doc_type: Mapped[EdiDocType] = mapped_column(
        Enum(EdiDocType, name="edi_doc_type_t", create_type=False), nullable=False
    )
    external_reference: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # API | EMAIL
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="API")
    # PENDING | SENT | ACKED | FAILED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ack_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    purchase_order: Mapped[EdiPurchaseOrder] = relationship("EdiPurchaseOrder", back_populates="outbound_messages")
    trading_partner: Mapped[TradingPartner] = relationship("TradingPartner")
