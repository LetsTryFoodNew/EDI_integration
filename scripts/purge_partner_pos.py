"""
Retire purchase orders for a partner, keeping an optional recent window.

Written for the cutover from pre-prod to production partner endpoints: the server had
accumulated 607 Zepto and Blinkit orders from testing, and leaving them in place meant
their queued acknowledgements and ASNs would fire at the *real* retailer APIs on the
next scheduler tick, carrying PO numbers those retailers have never issued.

Soft-delete, per CLAUDE.md: `deleted_at` is set and nothing is dropped. The rows leave
every list in the app, and the decision stays reversible -- which matters here because
some of these orders have real SAP Sales Orders behind them, and a hard delete would
destroy the only local record of what was posted.

The queued outbound messages are the part that actually has to stop. Soft-deleting a
PO does not silence them: `send_outbound_message` loads a message by its own id, so a
PENDING acknowledgement for a deleted order still goes out. Those are marked FAILED
with a reason, which is what the retry loop reads as "do not send".

    docker exec -w /app edi-integration-api-1 python scripts/purge_partner_pos.py \
        --partner ZEPTO --partner BLINKIT --keep-today BLINKIT [--apply]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SyncSessionLocal  # noqa: E402
from app.models.edi_po import EdiPurchaseOrder  # noqa: E402
from app.models.master_data import TradingPartner  # noqa: E402
from app.models.outbound import EdiOutboundMessage  # noqa: E402

#: Statuses that mean the document has not reached the partner yet, so stopping it
#: costs nothing. SENT/ACKED/CANCELLED are history and are never touched.
STOPPABLE = ("PENDING", "QUEUED", "RETRY")


def ist_date(dt: datetime):
    """The Indian business day a timestamp falls on."""
    from zoneinfo import ZoneInfo

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ZoneInfo("Asia/Kolkata")).date()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partner", action="append", required=True,
                    help="partner code to purge; repeatable")
    ap.add_argument("--keep-today", action="append", default=[],
                    help="partner code whose orders from today (IST) are kept")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    partners = [c.upper() for c in args.partner]
    keep_today = {c.upper() for c in args.keep_today}
    today = ist_date(datetime.now(UTC))
    now = datetime.now(UTC)

    counts: Counter[str] = Counter()
    with SyncSessionLocal() as db:
        rows = db.execute(
            select(EdiPurchaseOrder, TradingPartner)
            .join(TradingPartner, TradingPartner.id == EdiPurchaseOrder.trading_partner_id)
            .where(TradingPartner.code.in_(partners))
            .order_by(TradingPartner.code, EdiPurchaseOrder.created_at)
        ).all()

        for po, partner in rows:
            code = partner.code
            if code in keep_today and ist_date(po.created_at) == today:
                counts[f"{code} kept (today)"] += 1
                continue

            if po.deleted_at is None:
                counts[f"{code} retired"] += 1
                if po.b1_sales_order_doc_num:
                    counts[f"{code} retired (had SAP order)"] += 1
                if args.apply:
                    po.deleted_at = now
            else:
                counts[f"{code} already retired"] += 1

            # Stop anything still queued for it, whether or not the PO was already
            # soft-deleted -- a previous purge would have left these live.
            for msg in db.execute(
                select(EdiOutboundMessage).where(
                    EdiOutboundMessage.po_id == po.id,
                    EdiOutboundMessage.status.in_(STOPPABLE),
                )
            ).scalars():
                counts[f"{code} outbound stopped"] += 1
                if args.apply:
                    msg.status = "FAILED"
                    msg.next_retry_at = None
                    msg.error_message = (
                        "Not sent: the purchase order was retired during the "
                        "pre-prod to production cutover."
                    )

        if args.apply:
            db.commit()

    width = max((len(k) for k in counts), default=0)
    for key, n in sorted(counts.items()):
        print(f"  {key:<{width}}  {n}")
    print(f"\ntoday (IST) = {today}")
    print("applied" if args.apply else "dry run — re-run with --apply to persist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
