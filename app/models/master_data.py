"""
Master data tables: seller entities, trading partners, material master,
SKU mapping, ship-to mapping.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._enums import MappingStatus, SourceChannel

if TYPE_CHECKING:
    from app.models.edi_po import EdiPurchaseOrder
    from app.models.raw_messages import RawMessage


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SellerEntity(Base):
    """Our company — Let's Try Foods."""

    __tablename__ = "seller_entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15))
    b1_company_db: Mapped[str | None] = mapped_column(String(100))
    b1_server_url: Mapped[str | None] = mapped_column(String(500))
    address_line1: Mapped[str | None] = mapped_column(String(500))
    address_line2: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    pincode: Mapped[str | None] = mapped_column(String(10))
    country: Mapped[str] = mapped_column(String(50), nullable=False, default="India")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    purchase_orders: Mapped[list[EdiPurchaseOrder]] = relationship("EdiPurchaseOrder", back_populates="seller_entity")


class TradingPartner(Base):
    """Retail partners: Blinkit, Zepto, Swiggy, etc."""

    __tablename__ = "trading_partners"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    b1_card_code: Mapped[str | None] = mapped_column(String(50))
    gstin: Mapped[str | None] = mapped_column(String(15))
    source_channel: Mapped[SourceChannel] = mapped_column(
        Enum(SourceChannel, name="source_channel_t", create_type=False),
        nullable=False,
    )
    gmail_label: Mapped[str | None] = mapped_column(String(200))
    webhook_secret: Mapped[str | None] = mapped_column(String(500))
    api_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ack_sla_hours: Mapped[int] = mapped_column(nullable=False, default=24)
    asn_sla_hours: Mapped[int] = mapped_column(nullable=False, default=48)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sku_mappings: Mapped[list[SkuMapping]] = relationship("SkuMapping", back_populates="trading_partner")
    ship_to_mappings: Mapped[list[ShipToMapping]] = relationship("ShipToMapping", back_populates="trading_partner")
    raw_messages: Mapped[list[RawMessage]] = relationship("RawMessage", back_populates="trading_partner")
    purchase_orders: Mapped[list[EdiPurchaseOrder]] = relationship("EdiPurchaseOrder", back_populates="trading_partner")


class MaterialMaster(Base):
    """Our internal product catalogue, synced with SAP B1 Item Master."""

    __tablename__ = "material_master"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    b1_item_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    hsn_code: Mapped[str | None] = mapped_column(String(10))
    gst_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    uom: Mapped[str] = mapped_column(String(20), nullable=False)
    uom_group: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sku_mappings: Mapped[list[SkuMapping]] = relationship("SkuMapping", back_populates="material")


class SkuMapping(Base):
    """Maps a partner's buyer SKU / EAN to our internal material_master."""

    __tablename__ = "sku_mapping"
    __table_args__ = (
        UniqueConstraint("trading_partner_id", "buyer_sku", name="uq_sku_mapping_partner_sku"),
        Index("ix_sku_mapping_buyer_sku", "buyer_sku"),
        Index("ix_sku_mapping_partner", "trading_partner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trading_partner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading_partners.id"), nullable=False)
    buyer_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    buyer_sku_description: Mapped[str | None] = mapped_column(String(500))
    material_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("material_master.id"))
    qty_per_buyer_uom: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=1)
    buyer_uom: Mapped[str | None] = mapped_column(String(20))
    mapping_status: Mapped[MappingStatus] = mapped_column(
        Enum(MappingStatus, name="mapping_status_t", create_type=False),
        nullable=False,
        default=MappingStatus.UNMAPPED,
    )
    confidence_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trading_partner: Mapped[TradingPartner] = relationship("TradingPartner", back_populates="sku_mappings")
    material: Mapped[MaterialMaster | None] = relationship("MaterialMaster", back_populates="sku_mappings")


class ShipToMapping(Base):
    """Maps a partner's warehouse code to our SAP B1 warehouse code."""

    __tablename__ = "ship_to_mapping"
    __table_args__ = (
        UniqueConstraint("trading_partner_id", "buyer_warehouse_code", name="uq_ship_to_partner_whs"),
        Index("ix_ship_to_mapping_partner", "trading_partner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trading_partner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading_partners.id"), nullable=False)
    buyer_warehouse_code: Mapped[str] = mapped_column(String(100), nullable=False)
    buyer_warehouse_name: Mapped[str | None] = mapped_column(String(500))
    b1_whs_code: Mapped[str | None] = mapped_column(String(20))
    mapping_status: Mapped[MappingStatus] = mapped_column(
        Enum(MappingStatus, name="mapping_status_t", create_type=False),
        nullable=False,
        default=MappingStatus.UNMAPPED,
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trading_partner: Mapped[TradingPartner] = relationship("TradingPartner", back_populates="ship_to_mappings")
