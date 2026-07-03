"""
APScheduler entry point.

Discovers all active email-based trading partners at startup (those with a
gmail_label set), then schedules an ingest job for each one every 2 minutes.

Jobs are dispatched via RQ so the scheduler process is lightweight; actual
work happens in the `worker-ingest` container.

Run this module directly or via Docker:
    python -m app.workers.scheduler
"""
from __future__ import annotations

import logging
import time

import redis
import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from rq import Queue

from app.config import get_settings
from app.db import SyncSessionLocal
from app.models.master_data import TradingPartner
from app.models._enums import SourceChannel

log = structlog.get_logger(__name__)
settings = get_settings()

INGEST_INTERVAL_SECONDS = 120   # 2 minutes
INGEST_QUEUE_NAME = "ingest"


def _enqueue_ingest(queue: Queue, partner_code: str, label_name: str) -> None:
    """Enqueue one ingest job. Separated for easier testing."""
    from app.workers.jobs import ingest_label_job

    job = queue.enqueue(
        ingest_label_job,
        partner_code,
        label_name,
        job_timeout=300,    # 5 min max per label sweep
        result_ttl=3600,    # keep result for 1 h for debugging
        failure_ttl=86400,  # keep failures for 24 h
    )
    log.info(
        "scheduler.enqueued",
        partner=partner_code,
        label=label_name,
        job_id=job.id,
    )


def _get_email_partners() -> list[tuple[str, str]]:
    """
    Query DB for active partners with a gmail_label.
    Returns list of (partner_code, gmail_label) tuples.
    """
    with SyncSessionLocal() as session:
        rows = (
            session.query(TradingPartner.code, TradingPartner.gmail_label)
            .filter(
                TradingPartner.is_active.is_(True),
                TradingPartner.deleted_at.is_(None),
                TradingPartner.gmail_label.isnot(None),
            )
            .all()
        )
    return [(code, label) for code, label in rows if label]


def build_scheduler() -> BlockingScheduler:
    """
    Construct and configure the APScheduler instance.
    Exported so tests can build the scheduler without starting it.
    """
    redis_conn = redis.from_url(settings.redis_url)
    queue = Queue(INGEST_QUEUE_NAME, connection=redis_conn)

    partners = _get_email_partners()
    if not partners:
        log.warning("scheduler.no_email_partners_found")

    scheduler = BlockingScheduler(timezone="UTC")

    for partner_code, label_name in partners:
        # Stagger start times by 10 s per partner to avoid thundering herd
        offset = partners.index((partner_code, label_name)) * 10

        scheduler.add_job(
            _enqueue_ingest,
            trigger=IntervalTrigger(seconds=INGEST_INTERVAL_SECONDS),
            args=[queue, partner_code, label_name],
            id=f"ingest_{partner_code}",
            name=f"Gmail ingest — {partner_code}",
            replace_existing=True,
            misfire_grace_time=60,
            # Delay first run so the scheduler is fully started
            next_run_time=None,  # APScheduler fires after the first interval
        )
        log.info(
            "scheduler.registered",
            partner=partner_code,
            label=label_name,
            interval_s=INGEST_INTERVAL_SECONDS,
        )

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
