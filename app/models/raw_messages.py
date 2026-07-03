"""
Immutable store of every inbound message — email, API response, webhook payload,
portal scrape. Once written, never modified.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._enums import SourceChannel

if TYPE_CHECKING:
    from app.models.master_data import TradingPartner


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RawMessage(Base):
    """
    One row per inbound event. Attachment files are on disk/S3;
    paths stored in attachment_paths JSONB array.
    """

    __tablename__ = "raw_messages"
    __table_args__ = (
        UniqueConstraint("trading_partner_id", "external_id", name="uq_raw_message_partner_ext"),
        Index("ix_raw_messages_partner", "trading_partner_id"),
        Index("ix_raw_messages_received_at", "received_at"),
        Index("ix_raw_messages_parse_status", "parse_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trading_partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trading_partners.id"), nullable=False
    )
    source_channel: Mapped[SourceChannel] = mapped_column(
        Enum(SourceChannel, name="source_channel_t", create_type=False), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    # Email headers / HTTP request headers
    headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Parsed JSON body for API/webhook messages
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Raw text for non-JSON (email body, HTML scrape)
    payload_raw: Mapped[str | None] = mapped_column(Text)
    # [{filename, path, mime_type, size_bytes}]
    attachment_paths: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    processed: Mapped[bool] = mapped_column(nullable=False, default=False)
    # PENDING | SUCCESS | FAILED
    parse_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    trading_partner: Mapped[TradingPartner] = relationship("TradingPartner", back_populates="raw_messages")
