"""no-op: confirm Alembic tooling works

Revision ID: 0000
Revises:
Create Date: 2026-06-29 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

revision: str = "0000"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
