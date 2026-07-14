"""
Unit-test conftest.

asyncpg is not installed in the local dev environment (it requires a running
PostgreSQL to be useful, and unit tests never make real DB connections).
SQLAlchemy 2.x imports asyncpg eagerly when create_async_engine() is called,
so we stub it out here before any app module is imported.

This does NOT affect the sync engine (psycopg2) or integration tests.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

for _driver in ("asyncpg", "psycopg2", "psycopg2.extensions", "psycopg2.extras"):
    if _driver not in sys.modules:
        sys.modules[_driver] = MagicMock()
