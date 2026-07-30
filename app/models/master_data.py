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
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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
    # Customer-master fields synced from SAP (POST /partners/sync) — informational only.
    # Integration routing stays on source_channel/gmail_label/api_config above.
    business_type: Mapped[str | None] = mapped_column(String(100))
    group_name: Mapped[str | None] = mapped_column(String(100))
    phone_numbers: Mapped[list[str] | None] = mapped_column(ARRAY(String(20)))
    email_address: Mapped[str | None] = mapped_column(String(255))
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
    """
    Item master — a 1:1 mirror of SAP B1 OITM. Column names follow the Item_master
    schema exactly; `uom_group` and `case_size` are the only additions (case_size backs
    CaseSizeRule, uom_group backs buyer-UoM -> inventory-UoM conversion before B1 push).
    """

    __tablename__ = "material_master"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)       # OITM.ItemCode
    item_name: Mapped[str] = mapped_column(String(500), nullable=False)                   # OITM.ItemName
    frgn_name: Mapped[str | None] = mapped_column(String(500))                            # OITM.FrgnName
    hsn: Mapped[str | None] = mapped_column(String(10))
    tax_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    itms_grp_cod: Mapped[int | None] = mapped_column(Integer)                             # OITM.ItmsGrpCod
    items_group_name: Mapped[str | None] = mapped_column(String(100))
    invntry_uom: Mapped[str] = mapped_column(String(20), nullable=False)                  # inventory UoM
    uom_group: Mapped[str | None] = mapped_column(String(50))                             # (not in schema — UoM conversion)
    sal_unit_msr: Mapped[str | None] = mapped_column(String(20))                          # OITM.SalUnitMsr
    vat_group_pu: Mapped[str | None] = mapped_column(String(20))                          # OITM.VatGroupPu
    vat_group_sa: Mapped[str | None] = mapped_column(String(20))                          # OITM.VatGroupSa
    case_size: Mapped[int | None] = mapped_column(Integer)                                # (not in schema — CaseSizeRule)
    lot_size: Mapped[int | None] = mapped_column(Integer)
    grammage: Mapped[str | None] = mapped_column(String(50))
    ean_code: Mapped[str | None] = mapped_column(String(14), index=True)
    mrp: Mapped[float | None] = mapped_column(Numeric(10, 2))
    frozen_for: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)      # OITM Y/N
    valid_for: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)        # OITM Y/N
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
        Index("ix_sku_mapping_material", "material_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trading_partner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading_partners.id"), nullable=False)
    buyer_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    buyer_sku_description: Mapped[str | None] = mapped_column(String(500))
    # NOT NULL: the schema declares b1ItemCode [not null] — SAP only ever sends
    # confirmed mappings. An unresolvable buyer SKU is a PO-line exception, not a row here.
    material_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("material_master.id"), nullable=False
    )
    qty_per_buyer_uom: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=1)
    buyer_uom: Mapped[str | None] = mapped_column(String(20))
    # Customer-specific negotiated pricing synced from SAP — used by PriceVarianceRule (Phase 5).
    unit_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    margin: Mapped[float | None] = mapped_column(Numeric(9, 4))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trading_partner: Mapped[TradingPartner] = relationship("TradingPartner", back_populates="sku_mappings")
    material: Mapped[MaterialMaster] = relationship("MaterialMaster", back_populates="sku_mappings")


class ShipToMapping(Base):
    """Maps a partner's warehouse code to our SAP B1 warehouse code."""

    __tablename__ = "ship_to_mapping"
    __table_args__ = (
        UniqueConstraint("trading_partner_id", "buyer_whs_code", name="uq_ship_to_partner_whs"),
        Index("ix_ship_to_mapping_partner", "trading_partner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trading_partner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading_partners.id"), nullable=False)
    buyer_whs_code: Mapped[str] = mapped_column(String(100), nullable=False)   # buyer's DC code
    buyer_warehouse_name: Mapped[str | None] = mapped_column(String(500))
    b1_whs_code: Mapped[str | None] = mapped_column(String(20))                # our B1 WhsCode
    mapping_status: Mapped[MappingStatus] = mapped_column(
        Enum(MappingStatus, name="mapping_status_t", create_type=False),
        nullable=False,
        default=MappingStatus.UNMAPPED,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Structured address + GSTIN synced from SAP business-partner ship-to records.
    # `state` drives CGST/SGST vs IGST determination (CLAUDE.md section 8).
    address_line: Mapped[str | None] = mapped_column(String(500))
    address_type: Mapped[list[str] | None] = mapped_column(ARRAY(String(30)))
    street: Mapped[str | None] = mapped_column(String(255))
    block: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    zip_code: Mapped[str | None] = mapped_column(String(10))
    state: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(50))
    gst_registration_no: Mapped[str | None] = mapped_column(String(15))
    gst_type: Mapped[list[str] | None] = mapped_column(ARRAY(String(30)))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trading_partner: Mapped[TradingPartner] = relationship("TradingPartner", back_populates="ship_to_mappings")
