"""0010 — master-data field changes requested against the SAP contract

material_master
  - valid_for: boolean -> integer (SAP B1 sends OITM.validFor as 0/1). true->1, false->0.
  - + is_active boolean NOT NULL default true — our operational flag, restored as its
    own column now that valid_for is plain SAP data. (Supersedes the earlier decision
    to collapse the two: valid_for stopped being a boolean, so it can no longer double
    as the active flag.)

trading_partners
  - + pan_card varchar(10) — PAN of the customer entity (10 chars, e.g. AAECG1234K).
    ack_sla_hours is REMOVED FROM THE API SURFACE in this change but the column stays:
    send_outbound.py and dashboard.py read it for SLA monitoring (default 24).

ship_to_mapping
  - + poc_name / poc_email / poc_phone — point of contact at the delivery location.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-18
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The boolean default can't be cast automatically — drop it, convert, re-default.
    op.execute("ALTER TABLE material_master ALTER COLUMN valid_for DROP DEFAULT")
    op.alter_column(
        "material_master", "valid_for",
        type_=sa.Integer(),
        postgresql_using="CASE WHEN valid_for THEN 1 ELSE 0 END",
    )
    op.execute("ALTER TABLE material_master ALTER COLUMN valid_for SET DEFAULT 1")
    op.add_column(
        "material_master",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.add_column("trading_partners", sa.Column("pan_card", sa.String(10), nullable=True))

    op.add_column("ship_to_mapping", sa.Column("poc_name", sa.String(255), nullable=True))
    op.add_column("ship_to_mapping", sa.Column("poc_email", sa.String(255), nullable=True))
    op.add_column("ship_to_mapping", sa.Column("poc_phone", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("ship_to_mapping", "poc_phone")
    op.drop_column("ship_to_mapping", "poc_email")
    op.drop_column("ship_to_mapping", "poc_name")

    op.drop_column("trading_partners", "pan_card")

    op.drop_column("material_master", "is_active")
    op.execute("ALTER TABLE material_master ALTER COLUMN valid_for DROP DEFAULT")
    op.alter_column(
        "material_master", "valid_for",
        type_=sa.Boolean(),
        postgresql_using="valid_for <> 0",
    )
    op.execute("ALTER TABLE material_master ALTER COLUMN valid_for SET DEFAULT true")
