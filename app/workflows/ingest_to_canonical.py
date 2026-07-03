"""
Ingest workflow — phase 1 of the processing pipeline:
  Gmail message → raw_messages row + attachment files on disk.

Does NOT parse or validate. Enqueues a parse job after save (stub for now;
Phase 3 will replace the stub with real parser dispatch).

Idempotency: the raw_messages table has a UNIQUE constraint on
(trading_partner_id, external_id). A pre-check + that constraint together
guarantee the same Gmail message is never stored twice.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any

import structlog

from app.adapters.email.base import BaseEmailAdapter, InboundEmail
from app.adapters.email.gmail_client import GmailClient
from app.config import get_settings
from app.db import SyncSessionLocal
from app.models import RawMessage, TradingPartner
from app.models._enums import SourceChannel

log = structlog.get_logger(__name__)
settings = get_settings()


@dataclass
class IngestResult:
    partner_code: str
    label: str
    fetched: int = 0
    saved: int = 0
    skipped_duplicate: int = 0
    skipped_filter: int = 0
    errors: list[str] = field(default_factory=list)


def ingest_label(partner_code: str, label_name: str) -> IngestResult:
    """
    Pull new messages from a Gmail label and persist them as RawMessage rows.
    Attachments are saved to disk under:
      <attachment_base_path>/<partner_code>/<yyyy-mm-dd>/<message_id>/<filename>

    This is the entry point called by the RQ job.
    """
    result = IngestResult(partner_code=partner_code, label=label_name)
    log.info("ingest.start", partner=partner_code, label=label_name)

    gmail = GmailClient(
        credentials_path=settings.gmail_credentials_path,
        token_path=settings.gmail_token_path,
    )

    with SyncSessionLocal() as session:
        partner = session.query(TradingPartner).filter_by(code=partner_code).first()
        if not partner:
            log.error("ingest.partner_not_found", partner_code=partner_code)
            result.errors.append(f"TradingPartner '{partner_code}' not in DB")
            return result

        message_ids = gmail.list_message_ids(label_name)
        result.fetched = len(message_ids)
        log.info("ingest.fetched", partner=partner_code, count=len(message_ids))

        for msg_id in message_ids:
            try:
                _process_one(session, gmail, partner, msg_id, result)
            except Exception as exc:
                log.exception("ingest.message_error", message_id=msg_id, error=str(exc))
                result.errors.append(f"{msg_id}: {exc}")

        session.commit()

    log.info("ingest.done", **{k: v for k, v in vars(result).items() if k != "errors"})
    return result


def _process_one(
    session: Any,
    gmail: GmailClient,
    partner: TradingPartner,
    msg_id: str,
    result: IngestResult,
) -> None:
    # Idempotency pre-check — avoids fetching the full message body on duplicates
    already = (
        session.query(RawMessage)
        .filter_by(trading_partner_id=partner.id, external_id=msg_id)
        .first()
    )
    if already:
        result.skipped_duplicate += 1
        return

    email = gmail.get_message(msg_id)

    # Let the adapter apply its secondary filter
    # (the adapter instance is looked up from the registry)
    adapter = _get_adapter(partner.code)
    if adapter and not adapter.is_po_email(email):
        result.skipped_filter += 1
        log.debug("ingest.filtered", message_id=msg_id, partner=partner.code)
        return

    attachment_paths = _save_attachments(gmail, email, partner.code)

    raw = RawMessage(
        id=uuid.uuid4(),
        trading_partner_id=partner.id,
        source_channel=SourceChannel.EMAIL,
        external_id=msg_id,
        received_at=email.received_at,
        headers=dict(email.headers),
        payload=None,
        payload_raw=email.body_text,
        attachment_paths=attachment_paths,
        processed=False,
        parse_status="PENDING",
    )
    session.add(raw)
    result.saved += 1

    log.info(
        "ingest.saved",
        partner=partner.code,
        message_id=msg_id,
        attachments=len(attachment_paths),
    )

    # Phase 3 will replace this stub with real parser dispatch
    _enqueue_parse_stub(raw.id)


def _save_attachments(
    gmail: GmailClient,
    email: InboundEmail,
    partner_code: str,
) -> list[dict[str, Any]]:
    """
    Download all attachments for an email and write them to disk.
    Returns the attachment_paths list for the RawMessage row.
    """
    saved: list[dict[str, Any]] = []
    date_str = email.received_at.strftime("%Y-%m-%d")
    base = Path(settings.attachment_base_path) / partner_code / date_str / email.message_id
    base.mkdir(parents=True, exist_ok=True)

    for att in email.attachments:
        try:
            if att.attachment_id:
                data = gmail.download_attachment(email.message_id, att.attachment_id)
            elif att.size_bytes == 0:
                continue  # empty attachment — skip
            else:
                log.warning(
                    "ingest.attachment_no_id",
                    filename=att.filename,
                    message_id=email.message_id,
                )
                continue

            dest = base / att.filename
            dest.write_bytes(data)
            saved.append({
                "filename": att.filename,
                "path": str(dest),
                "mime_type": att.mime_type,
                "size_bytes": len(data),
            })
            log.debug("ingest.attachment_saved", path=str(dest))
        except Exception as exc:
            log.error(
                "ingest.attachment_error",
                filename=att.filename,
                message_id=email.message_id,
                error=str(exc),
            )

    return saved


def _enqueue_parse_stub(raw_message_id: uuid.UUID) -> None:
    """
    Stub: Phase 3 will enqueue a real parse job here.
    For now, just log that a job would be queued.
    """
    log.debug("ingest.parse_stub", raw_message_id=str(raw_message_id))


# ── Adapter registry ──────────────────────────────────────────────────────────
# Partner code → adapter instance. Adapters are stateless so one instance each.

_ADAPTER_REGISTRY: dict[str, BaseEmailAdapter] | None = None


def _get_adapter(partner_code: str) -> BaseEmailAdapter | None:
    global _ADAPTER_REGISTRY  # noqa: PLW0603
    if _ADAPTER_REGISTRY is None:
        _ADAPTER_REGISTRY = _build_registry()
    return _ADAPTER_REGISTRY.get(partner_code)


def _build_registry() -> dict[str, BaseEmailAdapter]:
    from app.adapters.email.blinkit_email import BlinkitEmailAdapter

    adapters: list[BaseEmailAdapter] = [
        BlinkitEmailAdapter(),
        # Phase 2+: add SwiggyEmailAdapter, BigBasketEmailAdapter, etc.
    ]
    return {a.get_partner_code(): a for a in adapters}
