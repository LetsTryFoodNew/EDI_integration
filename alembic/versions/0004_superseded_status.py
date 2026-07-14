"""0004 — add SUPERSEDED to po_status_t enum (PO revision flow)

When a partner re-issues a PO with the same PO number within the revision
window (25 days), the new email becomes version N+1 and the previous version
is marked SUPERSEDED (read-only, out of the exception queue and SAP flow).

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE po_status_t ADD VALUE IF NOT EXISTS 'SUPERSEDED'")


def downgrade() -> None:
    # PostgreSQL cannot drop a value from an enum type; leaving it is harmless.
    pass
