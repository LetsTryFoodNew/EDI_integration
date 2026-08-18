"""0011 — bill_to_mapping table

Mirrors ship_to_mapping for the *invoicing* entity, which in Indian retail is routinely
a different address from the delivery point: goods go to a distribution centre, the
invoice goes to the retailer's registered office.

Keeping them apart matters for tax. The ship-to state decides CGST/SGST vs IGST (place
of supply, CLAUDE.md section 8) while the bill-to GSTIN is what prints on the invoice as
the buyer's registration. One combined row cannot express an order where those two sit
in different states, which is the common interstate case.

The B1 target differs from ship-to as well: a delivery address resolves to a warehouse
(WhsCode); a billing address resolves to an address name on the Business Partner. Hence
b1_bill_to_code here rather than b1_whs_code.

Reuses the existing mapping_status_t enum (create_type=False on the model) — no new
enum type is created or dropped by this migration.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-11
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bill_to_mapping",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("trading_partner_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("trading_partners.id"), nullable=False),
        sa.Column("buyer_bill_to_code", sa.String(100), nullable=False),
        sa.Column("buyer_entity_name", sa.String(500)),
        sa.Column("b1_bill_to_code", sa.String(50)),
        sa.Column(
            "mapping_status",
            postgresql.ENUM(name="mapping_status_t", create_type=False),
            nullable=False,
            server_default="UNMAPPED",
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("address_line", sa.String(500)),
        sa.Column("address_type", postgresql.ARRAY(sa.String(30))),
        sa.Column("street", sa.String(255)),
        sa.Column("block", sa.String(100)),
        sa.Column("city", sa.String(100)),
        sa.Column("zip_code", sa.String(10)),
        sa.Column("state", sa.String(100)),
        sa.Column("country", sa.String(50)),
        sa.Column("gst_registration_no", sa.String(15)),
        sa.Column("gst_type", postgresql.ARRAY(sa.String(30))),
        sa.Column("poc_name", sa.String(255)),
        sa.Column("poc_email", sa.String(255)),
        sa.Column("poc_phone", sa.String(20)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("trading_partner_id", "buyer_bill_to_code",
                            name="uq_bill_to_partner_code"),
    )
    op.create_index("ix_bill_to_mapping_partner", "bill_to_mapping", ["trading_partner_id"])


def downgrade() -> None:
    op.drop_index("ix_bill_to_mapping_partner", table_name="bill_to_mapping")
    op.drop_table("bill_to_mapping")
