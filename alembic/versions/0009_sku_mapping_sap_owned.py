"""0009 — SKU_Mapping is SAP-owned: drop mapping_status / confidence_score, material_id NOT NULL

The master-data schema declares `b1ItemCode [not null]` on SKU_Mapping: every row SAP
sends is already a confirmed mapping. That makes two columns meaningless here:

  mapping_status    — UNMAPPED/AUTO_MAPPED/MANUALLY_MAPPED described a row the middleware
                      might have guessed at. SAP is now the only author, so the only
                      possible state is "mapped".
  confidence_score  — recorded how sure the fuzzy matcher was. There is no fuzzy matcher
                      any more (see app/validators/rules/sku_mapping.py, same change).

What does NOT move here: a PO line can still arrive with a buyer SKU that SAP has not
sent a mapping for. That is a property of the *document*, not of this table, and is
raised as E002_SKU_UNRESOLVED against the PO line instead.

`material_id` becomes NOT NULL to match `b1ItemCode [not null]`. Rows with a NULL
material_id are exactly the ones the old sync created without resolving an item code;
they are deleted, and any PO line pointing at one is detached first so the FK cannot
cascade into transactional data.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-18
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Detach PO lines pointing at rows we are about to delete, so the FK from
    # edi_po_line_items cannot block (or cascade through) the cleanup.
    op.execute("""
        UPDATE edi_po_line_items
        SET sku_mapping_id = NULL
        WHERE sku_mapping_id IN (SELECT id FROM sku_mapping WHERE material_id IS NULL)
    """)
    op.execute("DELETE FROM sku_mapping WHERE material_id IS NULL")

    op.alter_column("sku_mapping", "material_id", nullable=False)
    op.drop_column("sku_mapping", "mapping_status")
    op.drop_column("sku_mapping", "confidence_score")


def downgrade() -> None:
    op.add_column(
        "sku_mapping",
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
    )
    op.add_column(
        "sku_mapping",
        sa.Column(
            "mapping_status",
            postgresql.ENUM(name="mapping_status_t", create_type=False),
            nullable=False,
            server_default="MANUALLY_MAPPED",
        ),
    )
    op.alter_column("sku_mapping", "material_id", nullable=True)
