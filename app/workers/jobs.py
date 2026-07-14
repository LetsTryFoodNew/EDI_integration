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


def fetch_api_partner_job(partner_code: str) -> dict[str, object]:
    """
    RQ job: poll one API-based partner for new POs, save as raw_messages, enqueue parses.

    Called by the scheduler every N minutes per active API-based partner.
    Watermark (last_fetched_at) is read from and written back to
    TradingPartner.api_config["last_fetched_at"] inside the workflow.
    """
    from app.workflows.fetch_api_pos import fetch_and_store_api_pos

    log.info("job.fetch_api.start", extra={"partner": partner_code})
    result = fetch_and_store_api_pos(partner_code=partner_code)
    summary: dict[str, object] = {
        "partner_code": result.partner_code,
        "fetched": result.fetched,
        "new": result.new,
        "skipped_duplicate": result.skipped_duplicate,
        "errors": result.errors,
    }
    if result.errors:
        log.warning("job.fetch_api.done_with_errors", extra=summary)
    else:
        log.info("job.fetch_api.done", extra=summary)
    return summary


def validate_po_job(po_id: str) -> dict[str, object]:
    """
    RQ job: run validation engine on one parsed PO.

    po_id is passed as a str (RQ JSON serialisation). Enqueued automatically
    after parse_raw_message_job succeeds.
    """
    import uuid

    from app.workflows.validate_po import validate_po

    result = validate_po(uuid.UUID(po_id))
    summary: dict[str, object] = {
        "success": result.success,
        "po_id": po_id,
        "status": result.status,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "errors": result.errors,
    }
    if result.errors:
        log.warning("job.validate.failed", extra=summary)
    else:
        log.info("job.validate.done", extra=summary)
    return summary


def parse_raw_message_job(raw_message_id: str) -> dict[str, object]:
    """
    RQ job: parse one raw_message into a canonical EDI850, then persist.

    raw_message_id is passed as a str (RQ serialises everything to JSON).
    Converts to UUID before calling the workflow.
    """
    import uuid

    from app.workflows.parse_and_persist import parse_and_persist

    raw_id = uuid.UUID(raw_message_id)
    log.info("job.parse.start", extra={"raw_message_id": raw_message_id})
    result = parse_and_persist(raw_id)
    summary: dict[str, object] = {
        "success": result.success,
        "raw_message_id": raw_message_id,
        "po_id": str(result.po_id) if result.po_id else None,
        "partner_code": result.partner_code,
        "buyer_po_number": result.buyer_po_number,
        "error": result.error,
    }
    if result.success:
        log.info("job.parse.done", extra=summary)
    else:
        log.warning("job.parse.failed", extra=summary)
    return summary


def push_po_to_b1_job(po_id: str) -> dict[str, object]:
    """
    RQ job: push one VALIDATED PO to SAP B1 as a Sales Order.

    Runs on the dedicated "sap_push" queue (concurrency = pool_size, default 2).
    Always writes a B1ApiLog entry. Sets PO status to SAP_CONFIRMED or SAP_REJECTED.
    """
    import uuid

    from app.workflows.canonical_to_b1 import push_po_to_b1

    log.info("job.push_b1.start", extra={"po_id": po_id})
    result = push_po_to_b1(uuid.UUID(po_id))
    summary: dict[str, object] = {
        "success": result.success,
        "po_id": po_id,
        "b1_doc_entry": result.b1_doc_entry,
        "b1_doc_num": result.b1_doc_num,
        "skipped": result.skipped,
        "skip_reason": result.skip_reason,
        "error": result.error,
    }
    if result.skipped:
        log.info("job.push_b1.skipped", extra=summary)
    elif result.success:
        log.info("job.push_b1.done", extra=summary)
    else:
        log.error("job.push_b1.failed", extra=summary)
    return summary


def send_outbound_job(outbound_msg_id: str) -> dict[str, object]:
    """
    RQ job: send one EdiOutboundMessage to the partner.

    Handles ACK (855), ASN (856), Invoice (810), and Credit Note outbound docs.
    Retry schedule is managed inside send_outbound_message; this job does
    one attempt and persists the result.
    """
    import uuid

    from app.workflows.send_outbound import send_outbound_message

    log.info("job.send_outbound.start", extra={"outbound_msg_id": outbound_msg_id})
    result = send_outbound_message(uuid.UUID(outbound_msg_id))
    summary: dict[str, object] = {
        "success": result.success,
        "outbound_msg_id": outbound_msg_id,
        "doc_type": result.doc_type,
        "partner_code": result.partner_code,
        "external_ref": result.external_ref,
        "skipped": result.skipped,
        "skip_reason": result.skip_reason,
        "error": result.error,
        "attempt_count": result.attempt_count,
    }
    if result.skipped:
        log.info("job.send_outbound.skipped", extra=summary)
    elif result.success:
        log.info("job.send_outbound.done", extra=summary)
    else:
        log.warning("job.send_outbound.failed", extra=summary)
    return summary


def poll_b1_outbound_job() -> dict[str, object]:
    """
    RQ job: poll B1 for new Deliveries and Invoices, trigger ACKs for confirmed POs.

    Enqueued by the scheduler every 5 minutes. Calls three workflow functions:
      1. trigger_acks_for_confirmed_pos — queue ACKs for SAP_CONFIRMED POs
      2. poll_b1_deliveries             — create ASNs for new B1 Delivery Notes
      3. poll_b1_invoices               — create Invoice notifications for new B1 Invoices
    """
    import redis
    from rq import Queue

    from app.config import get_settings
    from app.workflows.b1_to_outbound import (
        poll_b1_deliveries,
        poll_b1_invoices,
        trigger_acks_for_confirmed_pos,
    )

    settings = get_settings()
    redis_conn = redis.from_url(settings.redis_url)
    outbound_queue = Queue("outbound", connection=redis_conn)

    acks = trigger_acks_for_confirmed_pos(outbound_queue)
    deliveries = poll_b1_deliveries(outbound_queue)
    invoices = poll_b1_invoices(outbound_queue)

    summary: dict[str, object] = {
        "acks_enqueued": acks,
        "deliveries_processed": deliveries,
        "invoices_processed": invoices,
    }
    log.info("job.poll_b1_outbound.done", extra=summary)
    return summary


def retry_pending_outbound_job() -> dict[str, object]:
    """
    RQ job: enqueue pending outbound messages whose next_retry_at has passed.

    Runs every 2 minutes. Does not send directly — it re-enqueues via
    send_outbound_job so retry attempts share the same concurrency controls.
    """
    import redis
    from rq import Queue

    from app.config import get_settings
    from app.workflows.b1_to_outbound import enqueue_due_retries

    settings = get_settings()
    redis_conn = redis.from_url(settings.redis_url)
    outbound_queue = Queue("outbound", connection=redis_conn)

    enqueued = enqueue_due_retries(outbound_queue)
    summary: dict[str, object] = {"retries_enqueued": enqueued}
    log.info("job.retry_pending_outbound.done", extra=summary)
    return summary


def process_rtv_job(raw_message_id: str) -> dict[str, object]:
    """
    RQ job: process one inbound RTV raw_message.

    Matches to an existing PO, creates a B1 Return, and enqueues a credit
    note notification outbound message.
    """
    import uuid

    from app.workflows.rtv_flow import process_rtv

    log.info("job.process_rtv.start", extra={"raw_message_id": raw_message_id})
    result = process_rtv(uuid.UUID(raw_message_id))
    summary: dict[str, object] = {
        "success": result.success,
        "raw_message_id": raw_message_id,
        "po_id": str(result.po_id) if result.po_id else None,
        "b1_return_doc_entry": result.b1_return_doc_entry,
        "b1_return_doc_num": result.b1_return_doc_num,
        "skipped": result.skipped,
        "skip_reason": result.skip_reason,
        "error": result.error,
    }
    if result.skipped:
        log.info("job.process_rtv.skipped", extra=summary)
    elif result.success:
        log.info("job.process_rtv.done", extra=summary)
    else:
        log.error("job.process_rtv.failed", extra=summary)
    return summary
