"""0012 — branch_master and warehouse_master

Mirrors of SAP B1 OBPL (branch / business place) and OWHS (warehouse). Unlike
ship_to_mapping and bill_to_mapping — which describe the *retailer's* locations and
need an ops-side mapping decision — these two describe our own SAP org structure.
SAP is the sole author of every business field; nothing here is ever mapped by hand.

Why they matter to this middleware: a B1 Sales Order line carries both a WhsCode and a
BPLId, and in the India localization the branch is the GST registration point that
decides CGST/SGST vs IGST. Holding both locally means po_to_sales_order can resolve
and validate them without a Service Layer round trip on every push (sessions are
licensed and capped — CLAUDE.md section 7).

warehouse_master.branch_id is a real FK rather than a loose BPLId integer because
OWHS.BPLid is mandatory in SAP and B1 rejects a document whose warehouse and branch
disagree. That makes branches sync-before-warehouses, the same ordering rule that
already makes SKU mapping depend on Item Master.

SAP sends Disabled / Inactive as NVARCHAR 'Y'/'N'; they are stored as booleans
(Pydantic coerces the Y/N strings on the way in), matching material_master.frozen_for.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-19
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "branch_master",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("bpl_id", sa.Integer, nullable=False),
        sa.Column("bpl_name", sa.String(255), nullable=False),
        sa.Column("disabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("address", sa.String(500)),
        sa.Column("street", sa.String(255)),
        sa.Column("block", sa.String(100)),
        sa.Column("city", sa.String(100)),
        sa.Column("zip_code", sa.String(10)),
        sa.Column("state", sa.String(100)),
        sa.Column("country", sa.String(50)),
        sa.Column("gstin", sa.String(15)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_branch_master_bpl_id", "branch_master", ["bpl_id"], unique=True)

    op.create_table(
        "warehouse_master",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("whs_code", sa.String(20), nullable=False),
        sa.Column("whs_name", sa.String(255), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("branch_master.id"), nullable=False),
        sa.Column("inactive", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("location", sa.Integer),
        sa.Column("street", sa.String(255)),
        sa.Column("block", sa.String(100)),
        sa.Column("city", sa.String(100)),
        sa.Column("zip_code", sa.String(10)),
        sa.Column("state", sa.String(100)),
        sa.Column("country", sa.String(50)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_warehouse_master_whs_code", "warehouse_master", ["whs_code"], unique=True)
    op.create_index("ix_warehouse_master_branch", "warehouse_master", ["branch_id"])


def downgrade() -> None:
    op.drop_index("ix_warehouse_master_branch", table_name="warehouse_master")
    op.drop_index("ix_warehouse_master_whs_code", table_name="warehouse_master")
    op.drop_table("warehouse_master")
    op.drop_index("ix_branch_master_bpl_id", table_name="branch_master")
    op.drop_table("branch_master")
