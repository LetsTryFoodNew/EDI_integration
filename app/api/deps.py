"""FastAPI dependency injectors shared across routes."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.db import SyncSessionLocal

if TYPE_CHECKING:
    from collections.abc import Generator

    from sqlalchemy.orm import Session


def get_sync_db() -> Generator[Session, None, None]:
    """Yield a synchronous SQLAlchemy session; close it on exit."""
    with SyncSessionLocal() as session:
        yield session
