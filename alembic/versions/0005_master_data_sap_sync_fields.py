"""0005 — master data: align with SAP sync schema (Customer/Item_master/SKU_Mapping/Ship_to_mapping)

Adds the fields needed so master data can be pushed FROM SAP (via POST /sync endpoints)
instead of the middleware calling SAP's Service Layer on every read. GET endpoints then
serve purely from these local tables.

trading_partners  — business_type, group_name, phone_numbers, email_address
                    (gstin already existed but was never exposed via the API — now is)
material_master   — itms_grp_cod, items_group_name, frgn_name, sales_uom,
                    vat_group_purchase, vat_group_sales, frozen_for, lot_size, grammage
sku_mapping       — unit_price, margin (customer-specific negotiated price, for
                    PriceVarianceRule in Phase 5)
ship_to_mapping   — renamed buyer_warehouse_code -> buyer_whs_code (matches the column
                    name every route/schema/frontend type already assumed — this mismatch
                    meant GET/PATCH /api/master-data/ship-to 500'd), added is_active (also
                    already assumed by the response schema but missing from the table), and
                    the structured address + GSTIN fields needed for CGST/SGST vs IGST
                    determination (see CLAUDE.md section 8 — the GST split depends on the
                    ship-to state, which we previously had nowhere to store).

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-18
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── trading_partners ────────────────────────────────────────────────────
    op.add_column("trading_partners", sa.Column("business_type", sa.String(100), nullable=True))
    op.add_column("trading_partners", sa.Column("group_name", sa.String(100), nullable=True))
    op.add_column("trading_partners", sa.Column("phone_numbers", postgresql.ARRAY(sa.String(20)), nullable=True))
    op.add_column("trading_partners", sa.Column("email_address", sa.String(255), nullable=True))

    # ── material_master ─────────────────────────────────────────────────────
    op.add_column("material_master", sa.Column("itms_grp_cod", sa.Integer(), nullable=True))
    op.add_column("material_master", sa.Column("items_group_name", sa.String(100), nullable=True))
    op.add_column("material_master", sa.Column("frgn_name", sa.String(500), nullable=True))
    op.add_column("material_master", sa.Column("sales_uom", sa.String(20), nullable=True))
    op.add_column("material_master", sa.Column("vat_group_purchase", sa.String(20), nullable=True))
    op.add_column("material_master", sa.Column("vat_group_sales", sa.String(20), nullable=True))
    op.add_column("material_master", sa.Column("frozen_for", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("material_master", sa.Column("lot_size", sa.Integer(), nullable=True))
    op.add_column("material_master", sa.Column("grammage", sa.String(50), nullable=True))

    # ── sku_mapping ──────────────────────────────────────────────────────────
    op.add_column("sku_mapping", sa.Column("unit_price", sa.Numeric(18, 6), nullable=True))
    op.add_column("sku_mapping", sa.Column("margin", sa.Numeric(9, 4), nullable=True))

    # ── ship_to_mapping ──────────────────────────────────────────────────────
    op.alter_column("ship_to_mapping", "buyer_warehouse_code", new_column_name="buyer_whs_code")
    op.add_column("ship_to_mapping", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("ship_to_mapping", sa.Column("address_line", sa.String(500), nullable=True))
    op.add_column("ship_to_mapping", sa.Column("address_type", postgresql.ARRAY(sa.String(30)), nullable=True))
    op.add_column("ship_to_mapping", sa.Column("street", sa.String(255), nullable=True))
    op.add_column("ship_to_mapping", sa.Column("block", sa.String(100), nullable=True))
    op.add_column("ship_to_mapping", sa.Column("city", sa.String(100), nullable=True))
    op.add_column("ship_to_mapping", sa.Column("zip_code", sa.String(10), nullable=True))
    op.add_column("ship_to_mapping", sa.Column("state", sa.String(100), nullable=True))
    op.add_column("ship_to_mapping", sa.Column("country", sa.String(50), nullable=True))
    op.add_column("ship_to_mapping", sa.Column("gst_registration_no", sa.String(15), nullable=True))
    op.add_column("ship_to_mapping", sa.Column("gst_type", postgresql.ARRAY(sa.String(30)), nullable=True))


def downgrade() -> None:
    op.drop_column("ship_to_mapping", "gst_type")
    op.drop_column("ship_to_mapping", "gst_registration_no")
    op.drop_column("ship_to_mapping", "country")
    op.drop_column("ship_to_mapping", "state")
    op.drop_column("ship_to_mapping", "zip_code")
    op.drop_column("ship_to_mapping", "city")
    op.drop_column("ship_to_mapping", "block")
    op.drop_column("ship_to_mapping", "street")
    op.drop_column("ship_to_mapping", "address_type")
    op.drop_column("ship_to_mapping", "address_line")
    op.drop_column("ship_to_mapping", "is_active")
    op.alter_column("ship_to_mapping", "buyer_whs_code", new_column_name="buyer_warehouse_code")

    op.drop_column("sku_mapping", "margin")
    op.drop_column("sku_mapping", "unit_price")

    op.drop_column("material_master", "grammage")
    op.drop_column("material_master", "lot_size")
    op.drop_column("material_master", "frozen_for")
    op.drop_column("material_master", "vat_group_sales")
    op.drop_column("material_master", "vat_group_purchase")
    op.drop_column("material_master", "sales_uom")
    op.drop_column("material_master", "frgn_name")
    op.drop_column("material_master", "items_group_name")
    op.drop_column("material_master", "itms_grp_cod")

    op.drop_column("trading_partners", "email_address")
    op.drop_column("trading_partners", "phone_numbers")
    op.drop_column("trading_partners", "group_name")
    op.drop_column("trading_partners", "business_type")
