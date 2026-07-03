"""
RQ job definitions.

Each function here is enqueued via Redis Queue (RQ) and executed by a worker
process. Functions must be importable at the module level and must be
serialisable by RQ (no lambdas, no local functions).

Sync only — RQ workers are synchronous.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def ingest_label_job(partner_code: str, label_name: str) -> dict[str, object]:
    """
    RQ job: pull new emails from one Gmail label and save them as raw_messages.

    Called by the scheduler every 2 minutes per active email-based partner.
    Safe to re-enqueue — the ingest workflow is idempotent.

    Returns a dict summary so RQ can record the result in its job store.
    """
    # Import here so the module is importable without a live DB / Gmail connection
    from app.workflows.ingest_to_canonical import ingest_label

    log.info("job.ingest_label.start", extra={"partner": partner_code, "label": label_name})
    result = ingest_label(partner_code=partner_code, label_name=label_name)
    summary = {
        "partner_code": result.partner_code,
        "label": result.label,
        "fetched": result.fetched,
        "saved": result.saved,
        "skipped_duplicate": result.skipped_duplicate,
        "skipped_filter": result.skipped_filter,
        "errors": result.errors,
    }
    log.info("job.ingest_label.done", extra=summary)
    return summary


def parse_raw_message_job(raw_message_id: str) -> dict[str, object]:
    """
    RQ job: parse one raw_message into a canonical EDI850.
    Stub in Phase 2 — Phase 3 implements this fully.
    """
    log.info("job.parse.stub", extra={"raw_message_id": raw_message_id})
    return {"status": "stub", "raw_message_id": raw_message_id}
