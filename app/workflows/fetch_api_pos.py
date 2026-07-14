"""
Fetch-and-store workflow for API-based (polling) partners.

Called by fetch_api_partner_job (RQ) on a schedule per partner.

Steps:
  1. Load TradingPartner + read watermark from api_config["last_fetched_at"]
  2. Call adapter.fetch_new_pos(since=watermark)
  3. For each FetchedPO: idempotency check → save RawMessage → enqueue parse job
  4. Update watermark to now on success
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

log = structlog.get_logger(__name__)

# Registry: partner_code → adapter class (lazy import)
_API_ADAPTER_REGISTRY: dict[str, type] | None = None


@dataclass
class FetchApiResult:
    partner_code: str
    fetched: int = 0
    new: int = 0
    skipped_duplicate: int = 0
    errors: list[str] = field(default_factory=list)


def fetch_and_store_api_pos(partner_code: str) -> FetchApiResult:
    """Entry point — pull new POs for one API-based partner and persist them."""
    from sqlalchemy import select

    from app.db import SyncSessionLocal
    from app.models.master_data import TradingPartner

    result = FetchApiResult(partner_code=partner_code)

    adapter_cls = _get_adapter_class(partner_code)
    if adapter_cls is None:
        result.errors.append(f"No API adapter registered for partner '{partner_code}'")
        log.error("fetch_api.no_adapter", partner=partner_code)
        return result

    with SyncSessionLocal() as session:
        partner = session.execute(
            select(TradingPartner).where(
                TradingPartner.code == partner_code,
                TradingPartner.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

        if not partner:
            result.errors.append(f"TradingPartner '{partner_code}' not found")
            log.error("fetch_api.partner_not_found", partner=partner_code)
            return result

        since = _read_watermark(partner)
        log.info("fetch_api.start", partner=partner_code, since=since)

        adapter = adapter_cls()
        try:
            fetched_pos = adapter.fetch_new_pos(since=since)
        except Exception as exc:
            result.errors.append(f"Adapter fetch failed: {exc}")
            log.exception("fetch_api.adapter_error", partner=partner_code)
            return result

        result.fetched = len(fetched_pos)

        for fetched_po in fetched_pos:
            try:
                raw_id = _save_one(session, partner, fetched_po)
                if raw_id is None:
                    result.skipped_duplicate += 1
                else:
                    result.new += 1
                    _enqueue_parse(raw_id)
            except Exception as exc:
                err = f"{fetched_po.external_id}: {exc}"
                result.errors.append(err)
                log.exception("fetch_api.save_error", partner=partner_code, external_id=fetched_po.external_id)

        # Update watermark only if we got no hard errors
        if not result.errors:
            _write_watermark(session, partner, datetime.now(UTC))
            session.commit()
            log.info(
                "fetch_api.done",
                partner=partner_code,
                fetched=result.fetched,
                new=result.new,
                skipped=result.skipped_duplicate,
            )
        else:
            session.commit()  # still persist any successfully saved messages
            log.warning(
                "fetch_api.done_with_errors",
                partner=partner_code,
                fetched=result.fetched,
                new=result.new,
                errors=result.errors,
            )

    return result


def _save_one(session: object, partner: object, fetched_po: object) -> uuid.UUID | None:
    """Save one FetchedPO as a RawMessage. Returns None if duplicate."""
    from sqlalchemy import select

    from app.models._enums import SourceChannel
    from app.models.raw_messages import RawMessage

    already = session.execute(  # type: ignore[union-attr]
        select(RawMessage).where(
            RawMessage.trading_partner_id == partner.id,  # type: ignore[union-attr]
            RawMessage.external_id == fetched_po.external_id,  # type: ignore[union-attr]
        )
    ).scalar_one_or_none()

    if already:
        return None

    raw_id = uuid.uuid4()
    source_ch = SourceChannel(partner.source_channel)  # type: ignore[union-attr]
    session.add(RawMessage(  # type: ignore[union-attr]
        id=raw_id,
        trading_partner_id=partner.id,  # type: ignore[union-attr]
        source_channel=source_ch,
        external_id=fetched_po.external_id,  # type: ignore[union-attr]
        received_at=fetched_po.received_at,  # type: ignore[union-attr]
        payload=fetched_po.payload,  # type: ignore[union-attr]
        processed=False,
        parse_status="PENDING",
    ))
    session.flush()  # type: ignore[union-attr]
    return raw_id


def _enqueue_parse(raw_id: uuid.UUID) -> None:
    try:
        from redis import Redis
        from rq import Queue

        from app.config import get_settings
        from app.workers.jobs import parse_raw_message_job

        redis_conn = Redis.from_url(get_settings().redis_url)
        Queue("ingest", connection=redis_conn).enqueue(
            parse_raw_message_job,
            str(raw_id),
            job_timeout=300,
        )
        log.debug("fetch_api.parse_enqueued", raw_id=str(raw_id))
    except Exception as exc:
        log.error("fetch_api.enqueue_error", raw_id=str(raw_id), error=str(exc))


def _read_watermark(partner: object) -> datetime | None:
    api_config = getattr(partner, "api_config", None) or {}
    val = api_config.get("last_fetched_at")
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val)).replace(tzinfo=UTC)
    except Exception:
        return None


def _write_watermark(session: object, partner: object, now: datetime) -> None:
    from sqlalchemy import select

    from app.models.master_data import TradingPartner

    db_partner = session.execute(  # type: ignore[union-attr]
        select(TradingPartner).where(TradingPartner.id == partner.id)  # type: ignore[union-attr]
    ).scalar_one()
    api_config: dict = dict(db_partner.api_config or {})
    api_config["last_fetched_at"] = now.isoformat()
    db_partner.api_config = api_config


# ── Adapter registry ──────────────────────────────────────────────────────────

def _get_adapter_class(partner_code: str) -> type | None:
    global _API_ADAPTER_REGISTRY  # noqa: PLW0603
    if _API_ADAPTER_REGISTRY is None:
        _API_ADAPTER_REGISTRY = _build_adapter_registry()
    return _API_ADAPTER_REGISTRY.get(partner_code)


def _build_adapter_registry() -> dict[str, type]:
    from app.adapters.api.zepto_api import ZeptoApiAdapter
    return {
        "ZEPTO": ZeptoApiAdapter,
        # Phase 4+: add BigBasketApiAdapter, AmazonApiAdapter, FlipkartApiAdapter
    }
