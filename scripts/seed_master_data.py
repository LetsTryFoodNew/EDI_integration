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

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import SyncSessionLocal
from app.models import (
    TradingPartner,
)
from app.models._enums import SourceChannel

PARTNERS = [
    dict(code="BLINKIT",       name="Blinkit (Grofers India Pvt Ltd)",      channel=SourceChannel.WEBHOOK,  gmail_label=None,         ack_sla=4,  asn_sla=12),
    dict(code="ZEPTO",         name="Zepto (Kiranakart Technologies)",       channel=SourceChannel.API,      gmail_label=None,         ack_sla=4,  asn_sla=12),
    dict(code="SWIGGY",        name="Swiggy Instamart",                      channel=SourceChannel.EMAIL,    gmail_label="SWIGGY_PO",  ack_sla=6,  asn_sla=24),
    dict(code="BIGBASKET",     name="BigBasket (Supermarket Grocery)",       channel=SourceChannel.EMAIL,    gmail_label="BIGBASKET_PO", ack_sla=12, asn_sla=48),
    dict(code="AMAZON",        name="Amazon Retail India Pvt Ltd",           channel=SourceChannel.API,      gmail_label=None,         ack_sla=6,  asn_sla=24),
    dict(code="FLIPKART",      name="Flipkart Internet Pvt Ltd",             channel=SourceChannel.API,      gmail_label=None,         ack_sla=6,  asn_sla=24),
    dict(code="ZOMATO_HP",      name="Zomato Hyperpure",               channel=SourceChannel.EMAIL,  gmail_label="ZOMATO_HP_PO",      ack_sla=12, asn_sla=48),
    dict(code="BB_DAILY",       name="BB Daily (BigBasket Daily)",     channel=SourceChannel.EMAIL,  gmail_label="BB_DAILY_PO",       ack_sla=12, asn_sla=24),
    dict(code="MILKBASKET",     name="Milkbasket (Reliance)",          channel=SourceChannel.EMAIL,  gmail_label="MILKBASKET_PO",     ack_sla=12, asn_sla=24),
     dict(code="BIGBAZARJIO",     name="MBIGBAZARJIO India Pvt private limited jai maa kali",          channel=SourceChannel.EMAIL,  gmail_label="MILKBASKET_PO",     ack_sla=12, asn_sla=24),
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
            # Customer-master fields — real values arrive via POST /partners/sync.
            business_type=p.get("business_type"),
            group_name=p.get("group_name"),
        )
        session.add(partner)
        partners[p["code"]] = partner
    return partners


def main() -> None:
    print("Seeding master data...")
    with SyncSessionLocal() as session:

        partners = seed_partners(session)
        print(f"  partners: {len(partners)} rows")

        session.commit()

    print("Done.")


if __name__ == "__main__":
    main()
