"""
BaseApiAdapter — interface for partners whose POs arrive via REST API polling.

Blinkit does NOT implement this interface (it is webhook/push only).
ZeptoApiAdapter and future partners (BigBasket, Amazon SP-API, Flipkart) do.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class FetchedPO:
    """
    One raw PO returned by a polling adapter.
    The adapter stores the full API response in `payload`.
    The workflow converts this to a RawMessage row.
    """
    external_id: str          # idempotency key — eventId (Zepto), order_id (etc.)
    payload: dict[str, Any]   # full PO JSON from partner API
    received_at: datetime
    po_number: str = ""       # best-effort pre-extraction for logging
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    """Summary returned by fetch_new_pos after one polling run."""
    partner_code: str
    fetched: int = 0
    new: int = 0
    skipped_duplicate: int = 0
    errors: list[str] = field(default_factory=list)
    watermark: datetime | None = None  # updated last_fetched_at to persist


class BaseApiAdapter(ABC):
    """
    Interface for poll-based partner adapters.

    fetch_new_pos() is called by the RQ worker (sync context) on a schedule.
    It returns a list of FetchedPO objects; the caller (workflow) is responsible
    for writing them to raw_messages and enqueuing parse jobs.
    """

    @property
    @abstractmethod
    def partner_code(self) -> str:
        """The trading_partners.code this adapter handles (e.g. 'ZEPTO')."""

    @abstractmethod
    def fetch_new_pos(
        self,
        since: datetime | None = None,
        max_pages: int = 10,
    ) -> list[FetchedPO]:
        """
        Pull all new POs since `since` (watermark datetime).
        Paginates internally until no more results or max_pages is hit.
        Returns one FetchedPO per PO (de-duplicated by external_id).
        """

    def fetch_po_detail(self, po_id: str) -> dict[str, Any] | None:
        """
        Optional: fetch the full detail of a specific PO by partner ID.
        Raises NotImplementedError if the partner has no single-PO endpoint.
        """
        raise NotImplementedError(f"{type(self).__name__} has no single-PO detail endpoint")
