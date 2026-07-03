"""Full request/response audit log for every SAP B1 Service Layer call."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.edi_po import EdiPurchaseOrder


def _utcnow() -> datetime:
    return datetime.now(UTC)


class B1ApiLog(Base):
    """Immutable log of every SAP B1 Service Layer HTTP call."""

    __tablename__ = "b1_api_log"
    __table_args__ = (
        Index("ix_b1_log_po_id", "po_id"),
        Index("ix_b1_log_created_at", "created_at"),
        Index("ix_b1_log_response_status", "response_status"),
        Index("ix_b1_log_operation", "operation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("edi_purchase_orders.id"))

    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    http_method: Mapped[str] = mapped_column(String(10), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    request_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    b1_session_id: Mapped[str | None] = mapped_column(String(200))
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    purchase_order: Mapped[EdiPurchaseOrder | None] = relationship("EdiPurchaseOrder", back_populates="b1_logs")
