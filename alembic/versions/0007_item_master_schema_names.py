"""0007 — material_master column names mirror the Item_master schema 1:1

Renames the legacy column names to the Item_master keys so the table is a direct
mirror of SAP B1 OITM as the master-data schema defines it:

    b1_item_code        -> item_code       (OITM.ItemCode)
    description         -> item_name       (OITM.ItemName)
    uom                 -> invntry_uom     (inventory UoM)
    sales_uom           -> sal_unit_msr    (OITM.SalUnitMsr)
    vat_group_purchase  -> vat_group_pu    (OITM.VatGroupPu)
    vat_group_sales     -> vat_group_sa    (OITM.VatGroupSa)
    gst_rate            -> tax_rate
    hsn_code            -> hsn
    ean                 -> ean_code
    is_active           -> valid_for       (OITM.validFor Y/N)

`uom_group` and `case_size` are NOT in the Item_master schema but are kept: case_size
backs CaseSizeRule (Phase 5, "ordered qty must be a whole multiple of a case") and
uom_group backs the buyer-UoM -> inventory-UoM conversion before B1 push. Neither is
derivable from the other columns.

Safe to rename in place: material_master is empty at the time this runs (the catalogue
was cleared for reload), so there is no data to migrate.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-18
"""
from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_RENAMES = [
    ("b1_item_code", "item_code"),
    ("description", "item_name"),
    ("uom", "invntry_uom"),
    ("sales_uom", "sal_unit_msr"),
    ("vat_group_purchase", "vat_group_pu"),
    ("vat_group_sales", "vat_group_sa"),
    ("gst_rate", "tax_rate"),
    ("hsn_code", "hsn"),
    ("ean", "ean_code"),
    ("is_active", "valid_for"),
]


def upgrade() -> None:
    op.drop_index("ix_material_master_ean", table_name="material_master")
    for old, new in _RENAMES:
        op.alter_column("material_master", old, new_column_name=new)
    op.create_index("ix_material_master_ean_code", "material_master", ["ean_code"])


def downgrade() -> None:
    op.drop_index("ix_material_master_ean_code", table_name="material_master")
    for old, new in reversed(_RENAMES):
        op.alter_column("material_master", new, new_column_name=old)
    op.create_index("ix_material_master_ean", "material_master", ["ean"])
