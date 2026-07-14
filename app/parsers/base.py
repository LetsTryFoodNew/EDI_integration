"""
BaseParser and ParseResult — the contract every parser must implement.

A parser:
  - declares which partner it handles (partner_code)
  - accepts a RawMessage and returns a ParseResult
  - does NOT write to the DB; that is the caller's responsibility

Lifecycle per raw_message:
  RawMessage.parse_status: PENDING → (SUCCESS | FAILED)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.raw_messages import RawMessage
    from app.schemas.canonical import EDI850


@dataclass
class ParseResult:
    success: bool
    doc: EDI850 | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parser_name: str = ""
    # Extracted plain text kept for debugging and LLM fallback
    extracted_text: str | None = None


class BaseParser(ABC):
    """Abstract base for all PO parsers."""

    @property
    @abstractmethod
    def partner_code(self) -> str:
        """TradingPartner.code this parser handles (e.g. 'BLINKIT')."""

    @abstractmethod
    def can_parse(self, raw_message: RawMessage) -> bool:
        """
        Return True if this parser is the right handler for this raw_message.
        Quick check only — no heavy I/O here.
        """

    @abstractmethod
    def parse(self, raw_message: RawMessage) -> ParseResult:
        """
        Parse the raw_message into a canonical EDI850.
        Must never raise — catch all exceptions and return a failed ParseResult.
        """
