"""0013 — branch / warehouse / address selection on the PO

A B1 Sales Order needs four routing values our canonical PO does not carry, because
they are decisions about *our* side of the trade rather than facts the retailer sent:

  b1_bpl_id        BPL_IDAssignedToInvoice — which of our GST branches books the order
  b1_whs_code      WarehouseCode           — which of that branch's warehouses ships it
  b1_ship_to_code  ShipToCode              — the B1 BP address name goods are sent to
  b1_pay_to_code   PayToCode               — the B1 BP address name the invoice bills to

The branch is not cosmetic: under the India localization it is the "from" state for
place-of-supply, so it decides CGST+SGST versus IGST. The same PO booked against the
Haryana branch instead of Maharashtra produces a different tax code, a different ledger
and a different GST return. It is also why the warehouse cannot be chosen independently
— B1 rejects a document whose warehouse and branch disagree.

Nullable, because they are filled in at push time (by the operator, or by the partner's
saved default) rather than at parse time. A PO that has never been pushed has none.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-19
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("edi_purchase_orders", sa.Column("b1_bpl_id", sa.Integer))
    op.add_column("edi_purchase_orders", sa.Column("b1_whs_code", sa.String(20)))
    op.add_column("edi_purchase_orders", sa.Column("b1_ship_to_code", sa.String(100)))
    op.add_column("edi_purchase_orders", sa.Column("b1_pay_to_code", sa.String(100)))


def downgrade() -> None:
    op.drop_column("edi_purchase_orders", "b1_pay_to_code")
    op.drop_column("edi_purchase_orders", "b1_ship_to_code")
    op.drop_column("edi_purchase_orders", "b1_whs_code")
    op.drop_column("edi_purchase_orders", "b1_bpl_id")
