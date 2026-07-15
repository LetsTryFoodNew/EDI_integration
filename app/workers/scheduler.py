"""
APScheduler entry point.

Schedules two types of jobs:
  1. Email ingest — every 2 min per active partner with a gmail_label
  2. API polling  — every 5 min per active API-based partner (Zepto, BigBasket, etc.)

Jobs are dispatched via RQ so the scheduler process is lightweight.

Run this module directly or via Docker:
    python -m app.workers.scheduler
"""
from __future__ import annotations

import redis
import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from rq import Queue

from app.config import get_settings
from app.db import SyncSessionLocal
from app.models._enums import SourceChannel
from app.models.master_data import TradingPartner

log = structlog.get_logger(__name__)
settings = get_settings()

INGEST_INTERVAL_SECONDS = 120    # email ingest: every 2 minutes
API_POLL_INTERVAL_SECONDS = 300  # API polling:  every 5 minutes
SAP_PUSH_INTERVAL_SECONDS = 60   # SAP push:     every 1 minute
B1_OUTBOUND_INTERVAL_SECONDS = 300  # B1 delivery/invoice poll: every 5 minutes
OUTBOUND_RETRY_INTERVAL_SECONDS = 120  # retry pending outbound: every 2 minutes
OUTBOUND_ACK_INTERVAL_SECONDS = 60    # ACK trigger: every 1 minute
INGEST_QUEUE_NAME = "ingest"
SAP_PUSH_QUEUE_NAME = "sap_push"
OUTBOUND_QUEUE_NAME = "outbound"


def _enqueue_ingest(queue: Queue, partner_code: str, label_name: str) -> None:
    """Enqueue one Gmail ingest job."""
    from app.workers.jobs import ingest_label_job

    job = queue.enqueue(
        ingest_label_job,
        partner_code,
        label_name,
        job_timeout=300,
        result_ttl=3600,
        failure_ttl=86400,
    )
    log.info("scheduler.enqueued", partner=partner_code, label=label_name, job_id=job.id)


def _enqueue_api_fetch(queue: Queue, partner_code: str) -> None:
    """Enqueue one API-poll fetch job."""
    from app.workers.jobs import fetch_api_partner_job

    job = queue.enqueue(
        fetch_api_partner_job,
        partner_code,
        job_timeout=600,
        result_ttl=3600,
        failure_ttl=86400,
    )
    log.info("scheduler.api_enqueued", partner=partner_code, job_id=job.id)


def _get_email_partners() -> list[tuple[str, str]]:
    """Return (partner_code, gmail_label) for active email partners."""
    from sqlalchemy import select
    with SyncSessionLocal() as session:
        rows = session.execute(
            select(TradingPartner.code, TradingPartner.gmail_label).where(
                TradingPartner.is_active.is_(True),
                TradingPartner.deleted_at.is_(None),
                TradingPartner.gmail_label.isnot(None),
            )
        ).all()
    return [(code, label) for code, label in rows if label]



def _get_api_partners() -> list[str]:
    """Return partner_codes for active API/WEBHOOK partners that need polling."""
    from sqlalchemy import select

    # Only partners whose source_channel is API (not WEBHOOK — those are push-only)
    with SyncSessionLocal() as session:
        rows = session.execute(
            select(TradingPartner.code).where(
                TradingPartner.is_active.is_(True),
                TradingPartner.deleted_at.is_(None),
                TradingPartner.source_channel == SourceChannel.API,
            )
        ).all()
    return [code for (code,) in rows]


def _enqueue_sap_push(sap_queue: Queue) -> None:
    """Enqueue push jobs for all VALIDATED POs."""
    from sqlalchemy import select

    from app.models._enums import PoStatus
    from app.models.edi_po import EdiPurchaseOrder
    from app.workers.jobs import push_po_to_b1_job

    with SyncSessionLocal() as session:
        po_ids = session.execute(
            select(EdiPurchaseOrder.id).where(
                EdiPurchaseOrder.po_status == PoStatus.VALIDATED,
                EdiPurchaseOrder.deleted_at.is_(None),
            )
        ).scalars().all()

    for po_id in po_ids:
        sap_queue.enqueue(
            push_po_to_b1_job,
            str(po_id),
            job_timeout=300,
            result_ttl=3600,
            failure_ttl=86400,
        )

    if po_ids:
        log.info("scheduler.sap_push_enqueued", count=len(po_ids))


def _enqueue_poll_b1_outbound(outbound_queue: Queue) -> None:
    """Enqueue the B1 outbound poll job (ACKs, ASNs, Invoices)."""
    from app.workers.jobs import poll_b1_outbound_job

    job = outbound_queue.enqueue(
        poll_b1_outbound_job,
        job_timeout=600,
        result_ttl=3600,
        failure_ttl=86400,
    )
    log.info("scheduler.poll_b1_outbound_enqueued", job_id=job.id)


def _enqueue_retry_pending_outbound(outbound_queue: Queue) -> None:
    """Re-enqueue outbound messages whose next_retry_at has passed."""
    from app.workers.jobs import retry_pending_outbound_job

    job = outbound_queue.enqueue(
        retry_pending_outbound_job,
        job_timeout=120,
        result_ttl=3600,
        failure_ttl=86400,
    )
    log.info("scheduler.retry_pending_outbound_enqueued", job_id=job.id)



def build_scheduler() -> BlockingScheduler:
    """Construct and configure the APScheduler instance."""
    redis_conn = redis.from_url(settings.redis_url)
    queue = Queue(INGEST_QUEUE_NAME, connection=redis_conn)
    sap_queue = Queue(SAP_PUSH_QUEUE_NAME, connection=redis_conn)
    outbound_queue = Queue(OUTBOUND_QUEUE_NAME, connection=redis_conn)

    scheduler = BlockingScheduler(timezone="UTC")

    # ── Email partners ────────────────────────────────────────────────────────
    email_partners = _get_email_partners()
    if not email_partners:
        log.warning("scheduler.no_email_partners_found")

    for partner_code, label_name in email_partners:
        scheduler.add_job(
            _enqueue_ingest,
            trigger=IntervalTrigger(seconds=INGEST_INTERVAL_SECONDS),
            args=[queue, partner_code, label_name],
            id=f"ingest_{partner_code}",
            name=f"Gmail ingest — {partner_code}",
            replace_existing=True,
            misfire_grace_time=60,
        )
        log.info(
            "scheduler.email_registered",
            partner=partner_code,
            label=label_name,
            interval_s=INGEST_INTERVAL_SECONDS,
        )

    # ── API polling partners ──────────────────────────────────────────────────
    api_partners = _get_api_partners()
    if not api_partners:
        log.warning("scheduler.no_api_partners_found")

    for partner_code in api_partners:
        scheduler.add_job(
            _enqueue_api_fetch,
            trigger=IntervalTrigger(seconds=API_POLL_INTERVAL_SECONDS),
            args=[queue, partner_code],
            id=f"api_fetch_{partner_code}",
            name=f"API poll — {partner_code}",
            replace_existing=True,
            misfire_grace_time=120,
        )
        log.info(
            "scheduler.api_registered",
            partner=partner_code,
            interval_s=API_POLL_INTERVAL_SECONDS,
        )

    # ── SAP push ──────────────────────────────────────────────────────────────
    scheduler.add_job(
        _enqueue_sap_push,
        trigger=IntervalTrigger(seconds=SAP_PUSH_INTERVAL_SECONDS),
        args=[sap_queue],
        id="sap_push_validated_pos",
        name="SAP B1 push — all VALIDATED POs",
        replace_existing=True,
        misfire_grace_time=30,
    )
    log.info("scheduler.sap_push_registered", interval_s=SAP_PUSH_INTERVAL_SECONDS)

    # ── Outbound: ACK + ASN + Invoice polling ─────────────────────────────────
    scheduler.add_job(
        _enqueue_poll_b1_outbound,
        trigger=IntervalTrigger(seconds=B1_OUTBOUND_INTERVAL_SECONDS),
        args=[outbound_queue],
        id="poll_b1_outbound",
        name="B1 outbound poll — ACKs, ASNs, Invoices",
        replace_existing=True,
        misfire_grace_time=60,
    )
    log.info("scheduler.b1_outbound_registered", interval_s=B1_OUTBOUND_INTERVAL_SECONDS)

    # ── Outbound: retry pending messages ──────────────────────────────────────
    scheduler.add_job(
        _enqueue_retry_pending_outbound,
        trigger=IntervalTrigger(seconds=OUTBOUND_RETRY_INTERVAL_SECONDS),
        args=[outbound_queue],
        id="retry_pending_outbound",
        name="Retry pending outbound messages",
        replace_existing=True,
        misfire_grace_time=60,
    )
    log.info("scheduler.outbound_retry_registered", interval_s=OUTBOUND_RETRY_INTERVAL_SECONDS)

    return scheduler


def main() -> None:
    log.info("scheduler.starting", redis_url=settings.redis_url)
    scheduler = build_scheduler()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler.stopped")


if __name__ == "__main__":
    main()
