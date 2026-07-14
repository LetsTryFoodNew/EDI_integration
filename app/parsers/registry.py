"""
Parser registry — maps trading_partner.code → concrete BaseParser class.

Add a new parser by importing it here and adding it to REGISTRY.
The registry is built lazily on first call so it's importable without
triggering all parser imports at module load time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from app.parsers.base import BaseParser

log = structlog.get_logger(__name__)

# partner_code → parser class (not instance — instantiated on demand)
_REGISTRY: dict[str, type[BaseParser]] | None = None


def get_parser(partner_code: str) -> BaseParser | None:
    """
    Return a fresh parser instance for the given partner code.
    Returns None if no parser is registered for this partner.
    """
    registry = _get_registry()
    cls = registry.get(partner_code)
    if cls is None:
        log.warning("parser.not_found", partner_code=partner_code)
        return None
    return cls()


def registered_codes() -> list[str]:
    """Return the list of partner codes that have a registered parser."""
    return list(_get_registry().keys())


def _get_registry() -> dict[str, type[BaseParser]]:
    global _REGISTRY  # noqa: PLW0603
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def _build_registry() -> dict[str, type[BaseParser]]:
    from app.parsers.blinkit_parser import BlinkitParser  # noqa: PLC0415
    from app.parsers.swiggy_parser import SwiggyParser  # noqa: PLC0415
    from app.parsers.zepto_parser import ZeptoParser  # noqa: PLC0415

    parsers: list[BaseParser] = [
        BlinkitParser(),
        ZeptoParser(),
        SwiggyParser(),
    ]
    registry: dict[str, type[BaseParser]] = {}
    for p in parsers:
        registry[p.partner_code] = type(p)
        log.debug("parser.registered", partner=p.partner_code, cls=type(p).__name__)
    return registry
