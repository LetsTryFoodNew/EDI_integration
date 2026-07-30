"""0008 — index sku_mapping.material_id (the SKU_Mapping.b1ItemCode index)

The master-data schema specifies an index on SKU_Mapping.b1ItemCode. We model that
reference as a UUID FK (`material_id` -> material_master.id) rather than a varchar
item code, so the equivalent index goes on material_id.

It backs a hot join: the customer drill-down (GET /api/master-data/partners/{id})
outer-joins sku_mapping -> material_master on every row expand, and CLAUDE.md section 4
requires an index on every column used in a hot JOIN.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-18
"""
from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_sku_mapping_material", "sku_mapping", ["material_id"])


def downgrade() -> None:
    op.drop_index("ix_sku_mapping_material", table_name="sku_mapping")
