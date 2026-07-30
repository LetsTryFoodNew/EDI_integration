"""0006 — sku_mapping.is_active (the `status` flag from the master-data schema)

The master-data schema models SKU_Mapping.status as a boolean. We already had
`deleted_at` (soft-delete) and `mapping_status` (UNMAPPED/AUTO_MAPPED/MANUALLY_MAPPED),
but neither expresses "this mapping exists and is correct, but is currently dormant"
— e.g. a listing the retailer has delisted for a season. Those are three different
states and collapsing them loses information, so `is_active` is its own column.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-18
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sku_mapping",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("sku_mapping", "is_active")
