"""
Seed master data: 1 seller entity, 15 trading partners, 5 items,
SKU mappings, ship-to mappings.

Usage:
    python -m scripts.seed_master_data
    # or with a running DB:
    python scripts/seed_master_data.py
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

# Allow running from repo root without installing package
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import SyncSessionLocal
from app.models import (
    MaterialMaster,
    SellerEntity,
    ShipToMapping,
    SkuMapping,
    TradingPartner,
)
from app.models._enums import MappingStatus, SourceChannel


def seed_seller(session: object) -> SellerEntity:
    existing = session.query(SellerEntity).filter_by(name="Let's Try Foods Private Limited").first()
    if existing:
        print("  seller entity already exists — skipping")
        return existing

    seller = SellerEntity(
        id=uuid.uuid4(),
        name="Let's Try Foods Private Limited",
        gstin="27AADCL9999Q1ZY",   # placeholder — replace with real GSTIN
        b1_company_db="LETSTRY",
        b1_server_url="https://sapb1.letstryfoods.com:50000",
        address_line1="Unit 5, Andheri Industrial Estate",
        city="Mumbai",
        state="Maharashtra",
        pincode="400053",
        country="India",
    )
    session.add(seller)
    return seller


PARTNERS = [
    dict(code="BLINKIT",       name="Blinkit (Grofers India Pvt Ltd)",      channel=SourceChannel.WEBHOOK,  gmail_label=None,         ack_sla=4,  asn_sla=12),
    dict(code="ZEPTO",         name="Zepto (Kiranakart Technologies)",       channel=SourceChannel.API,      gmail_label=None,         ack_sla=4,  asn_sla=12),
    dict(code="SWIGGY",        name="Swiggy Instamart",                      channel=SourceChannel.EMAIL,    gmail_label="SWIGGY_PO",  ack_sla=6,  asn_sla=24),
    dict(code="BIGBASKET",     name="BigBasket (Supermarket Grocery)",       channel=SourceChannel.EMAIL,    gmail_label="BIGBASKET_PO", ack_sla=12, asn_sla=48),
    dict(code="AMAZON",        name="Amazon Retail India Pvt Ltd",           channel=SourceChannel.API,      gmail_label=None,         ack_sla=6,  asn_sla=24),
    dict(code="FLIPKART",      name="Flipkart Internet Pvt Ltd",             channel=SourceChannel.API,      gmail_label=None,         ack_sla=6,  asn_sla=24),
    dict(code="DMART",         name="Avenue Supermarts (DMart)",             channel=SourceChannel.EMAIL,    gmail_label="DMART_PO",   ack_sla=24, asn_sla=48),
    dict(code="RELIANCE_JIO",   name="Reliance Retail / JioMart",      channel=SourceChannel.PORTAL, gmail_label=None,                ack_sla=24, asn_sla=48),
    dict(code="NATURES_BASKET", name="Nature's Basket (Godrej)",       channel=SourceChannel.EMAIL,  gmail_label="NATURES_BASKET_PO", ack_sla=24, asn_sla=48),
    dict(code="SPAR",           name="SPAR Hypermarket India",         channel=SourceChannel.EMAIL,  gmail_label="SPAR_PO",           ack_sla=24, asn_sla=48),
    dict(code="METRO_CASH",     name="Metro Cash & Carry India",       channel=SourceChannel.EMAIL,  gmail_label="METRO_PO",          ack_sla=24, asn_sla=72),
    dict(code="DUNZO",          name="Dunzo Daily",                    channel=SourceChannel.API,    gmail_label=None,                ack_sla=4,  asn_sla=12),
    dict(code="ZOMATO_HP",      name="Zomato Hyperpure",               channel=SourceChannel.EMAIL,  gmail_label="ZOMATO_HP_PO",      ack_sla=12, asn_sla=48),
    dict(code="BB_DAILY",       name="BB Daily (BigBasket Daily)",     channel=SourceChannel.EMAIL,  gmail_label="BB_DAILY_PO",       ack_sla=12, asn_sla=24),
    dict(code="MILKBASKET",     name="Milkbasket (Reliance)",          channel=SourceChannel.EMAIL,  gmail_label="MILKBASKET_PO",     ack_sla=12, asn_sla=24),
]


def seed_partners(session: object) -> dict[str, TradingPartner]:
    partners: dict[str, TradingPartner] = {}
    for p in PARTNERS:
        existing = session.query(TradingPartner).filter_by(code=p["code"]).first()
        if existing:
            partners[p["code"]] = existing
            continue

        partner = TradingPartner(
            id=uuid.uuid4(),
            code=p["code"],
            name=p["name"],
            source_channel=p["channel"],
            gmail_label=p["gmail_label"],
            ack_sla_hours=p["ack_sla"],
            asn_sla_hours=p["asn_sla"],
            is_active=True,
        )
        session.add(partner)
        partners[p["code"]] = partner
    return partners


MATERIALS = [
    dict(
        b1_item_code="LTFM001",
        description="Peri Peri Makhana 30g",
        hsn_code="20089900",
        gst_rate=12.0,
        uom="PCS",
        uom_group="MAKHANA_UOM",
    ),
    dict(
        b1_item_code="LTFM002",
        description="Classic Salted Makhana 30g",
        hsn_code="20089900",
        gst_rate=12.0,
        uom="PCS",
        uom_group="MAKHANA_UOM",
    ),
    dict(
        b1_item_code="LTFS001",
        description="Spicy Potato Chips 50g",
        hsn_code="20052000",
        gst_rate=12.0,
        uom="PCS",
        uom_group="CHIPS_UOM",
    ),
    dict(
        b1_item_code="LTFS002",
        description="Baked Multigrain Chips 50g",
        hsn_code="20052000",
        gst_rate=12.0,
        uom="PCS",
        uom_group="CHIPS_UOM",
    ),
    dict(
        b1_item_code="LTFN001",
        description="Roasted Mixed Nuts 100g",
        hsn_code="20081900",
        gst_rate=5.0,
        uom="PCS",
        uom_group="NUTS_UOM",
    ),
]


def seed_materials(session: object) -> dict[str, MaterialMaster]:
    materials: dict[str, MaterialMaster] = {}
    for m in MATERIALS:
        existing = session.query(MaterialMaster).filter_by(b1_item_code=m["b1_item_code"]).first()
        if existing:
            materials[m["b1_item_code"]] = existing
            continue

        mat = MaterialMaster(id=uuid.uuid4(), **m)
        session.add(mat)
        materials[m["b1_item_code"]] = mat
    return materials


def seed_sku_mappings(
    session: object,
    partners: dict[str, TradingPartner],
    materials: dict[str, MaterialMaster],
) -> None:
    """Seed sample SKU mappings for Blinkit and Zepto."""
    mappings = [
        # Blinkit uses EAN-style SKUs
        dict(partner="BLINKIT", buyer_sku="8901234560001", mat_code="LTFM001", qty_per=1, uom="PCS"),
        dict(partner="BLINKIT", buyer_sku="8901234560002", mat_code="LTFM002", qty_per=1, uom="PCS"),
        dict(partner="BLINKIT", buyer_sku="8901234560010", mat_code="LTFS001", qty_per=1, uom="PCS"),
        dict(partner="BLINKIT", buyer_sku="8901234560011", mat_code="LTFS002", qty_per=1, uom="PCS"),
        dict(partner="BLINKIT", buyer_sku="8901234560020", mat_code="LTFN001", qty_per=1, uom="PCS"),
        # Zepto uses numeric IDs
        dict(partner="ZEPTO",   buyer_sku="ZP-MM-001",     mat_code="LTFM001", qty_per=1, uom="PCS"),
        dict(partner="ZEPTO",   buyer_sku="ZP-MM-002",     mat_code="LTFM002", qty_per=1, uom="PCS"),
        dict(partner="ZEPTO",   buyer_sku="ZP-CS-001",     mat_code="LTFS001", qty_per=1, uom="PCS"),
    ]
    for m in mappings:
        partner = partners.get(m["partner"])
        material = materials.get(m["mat_code"])
        if not partner or not material:
            continue
        existing = (
            session.query(SkuMapping)
            .filter_by(trading_partner_id=partner.id, buyer_sku=m["buyer_sku"])
            .first()
        )
        if existing:
            continue
        session.add(SkuMapping(
            id=uuid.uuid4(),
            trading_partner_id=partner.id,
            buyer_sku=m["buyer_sku"],
            material_id=material.id,
            qty_per_buyer_uom=m["qty_per"],
            buyer_uom=m["uom"],
            mapping_status=MappingStatus.MANUALLY_MAPPED,
        ))


def seed_ship_to_mappings(session: object, partners: dict[str, TradingPartner]) -> None:
    """Seed sample ship-to warehouse mappings."""
    ship_tos = [
        dict(partner="BLINKIT", whs_code="BL-MUM-001", whs_name="Blinkit Mumbai DC",      b1_whs="WH01"),
        dict(partner="BLINKIT", whs_code="BL-DEL-001", whs_name="Blinkit Delhi DC",        b1_whs="WH02"),
        dict(partner="ZEPTO",   whs_code="ZP-MUM-001", whs_name="Zepto Mumbai Dark Store",  b1_whs="WH01"),
        dict(partner="ZEPTO",   whs_code="ZP-BLR-001", whs_name="Zepto Bengaluru DC",       b1_whs="WH03"),
        dict(partner="SWIGGY",  whs_code="SW-MUM-001", whs_name="Swiggy Mumbai Hub",        b1_whs="WH01"),
    ]
    for s in ship_tos:
        partner = partners.get(s["partner"])
        if not partner:
            continue
        existing = (
            session.query(ShipToMapping)
            .filter_by(trading_partner_id=partner.id, buyer_warehouse_code=s["whs_code"])
            .first()
        )
        if existing:
            continue
        session.add(ShipToMapping(
            id=uuid.uuid4(),
            trading_partner_id=partner.id,
            buyer_warehouse_code=s["whs_code"],
            buyer_warehouse_name=s["whs_name"],
            b1_whs_code=s["b1_whs"],
            mapping_status=MappingStatus.MANUALLY_MAPPED,
        ))


def main() -> None:
    print("Seeding master data...")
    with SyncSessionLocal() as session:
        seller = seed_seller(session)
        print(f"  seller: {seller.name}")

        partners = seed_partners(session)
        print(f"  partners: {len(partners)} rows")

        materials = seed_materials(session)
        print(f"  materials: {len(materials)} rows")

        seed_sku_mappings(session, partners, materials)
        print("  sku_mappings: done")

        seed_ship_to_mappings(session, partners)
        print("  ship_to_mappings: done")

        session.commit()

    print("Done.")


if __name__ == "__main__":
    main()
