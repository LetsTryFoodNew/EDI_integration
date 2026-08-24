"""0014 — keep the partner's own document id alongside ours

`edi_outbound_messages.external_reference` held our reference (ASN-LTF/26-27/001842)
until a send succeeded, at which point `send_outbound_message` overwrote it with
whatever the partner returned (Zepto's "JAI005MEA00972", Blinkit's asn_id). One column
meant two different things depending on a row's status, and the value we chose the
document by was destroyed the moment it was accepted.

That was survivable while nothing needed the partner's id back. Zepto's ASN
Cancellation API needs exactly it:

    DELETE /api/v1/external/asn?asnNumber=<the id Zepto returned>

and the contract is explicit that cancel-then-recreate is the *only* way to correct a
sent ASN, since there is no update endpoint. So the id has to survive.

`partner_reference` is the partner's, `external_reference` stays ours. Nullable because
nothing has one until a send is accepted, and because partners that return no id at all
never will.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-24
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "edi_outbound_messages",
        sa.Column("partner_reference", sa.String(length=200), nullable=True),
    )
    # Cancellation looks a message up by the id the partner gave it.
    op.create_index(
        "ix_outbound_partner_reference",
        "edi_outbound_messages",
        ["partner_reference"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbound_partner_reference", table_name="edi_outbound_messages")
    op.drop_column("edi_outbound_messages", "partner_reference")
