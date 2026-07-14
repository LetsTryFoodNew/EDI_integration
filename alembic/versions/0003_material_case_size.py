"""0003 — material_master: case_size, ean, mrp columns (Phase 5)

case_size = units per case (from sku master.xlsx CASE SIZE column).
Used by CaseSizeRule: ordered qty must be a whole multiple of case_size,
otherwise the platform must reissue the PO.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("material_master", sa.Column("case_size", sa.Integer(), nullable=True))
    op.add_column("material_master", sa.Column("ean", sa.String(14), nullable=True))
    op.add_column("material_master", sa.Column("mrp", sa.Numeric(10, 2), nullable=True))
    op.create_index("ix_material_master_ean", "material_master", ["ean"])


def downgrade() -> None:
    op.drop_index("ix_material_master_ean", table_name="material_master")
    op.drop_column("material_master", "mrp")
    op.drop_column("material_master", "ean")
    op.drop_column("material_master", "case_size")
