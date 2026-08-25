"""
One-off: re-point Zepto POs at their newest event.

Zepto returns PO events newest-first, and parse_and_persist treats each arriving
document as a revision of the one before it, so consuming them in API order made the
*oldest* event win. Four POs went live with a stale revision active while the newer
ones sat SUPERSEDED -- P368998 had its 22 Aug CreatePO active (12313.18) and its
24 Aug UpdatePO superseded (7920.32).

The adapter now sorts oldest-first, so this only repairs what already landed.

Only touches POs never pushed to SAP. Re-validation is enqueued for whichever row
becomes active, since its numbers differ from the row that was active before.

    docker compose exec -T api python scripts/repair_zepto_versions.py [--apply]
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SyncSessionLocal  # noqa: E402
from app.models._enums import PoStatus  # noqa: E402
from app.models.edi_po import EdiPoStatusHistory, EdiPurchaseOrder  # noqa: E402
from app.models.master_data import TradingPartner  # noqa: E402
from app.models.raw_messages import RawMessage  # noqa: E402

APPLY = "--apply" in sys.argv


def main() -> int:
    repaired = 0
    with SyncSessionLocal() as db:
        partner = db.execute(
            select(TradingPartner).where(TradingPartner.code == "ZEPTO")
        ).scalar_one()

        rows = db.execute(
            select(EdiPurchaseOrder).where(
                EdiPurchaseOrder.trading_partner_id == partner.id,
                EdiPurchaseOrder.deleted_at.is_(None),
            )
        ).scalars().all()

        by_number: dict[str, list] = defaultdict(list)
        for po in rows:
            by_number[po.buyer_po_number].append(po)

        for number, pos in sorted(by_number.items()):
            if len(pos) < 2:
                continue
            if any(p.b1_sales_order_doc_entry for p in pos):
                print(f"SKIP  {number}: already in SAP — repair by hand")
                continue

            stamped = []
            for po in pos:
                raw = db.get(RawMessage, po.raw_message_id) if po.raw_message_id else None
                ts = str((getattr(raw, "payload", None) or {}).get("timestamp") or "")
                stamped.append((ts, po))
            stamped.sort(key=lambda t: (0 if not t[0] else 1, t[0]))

            newest_ts, newest = stamped[-1]
            if str(newest.po_status) != "SUPERSEDED":
                print(f"OK    {number}: newest event {newest_ts} is already active")
                continue

            print(f"FIX   {number}: activating {newest_ts} ({newest.grand_total})")
            for ts, po in stamped[:-1]:
                if str(po.po_status) != "SUPERSEDED":
                    print(f"        superseding {ts} ({po.grand_total})")
                    if APPLY:
                        db.add(EdiPoStatusHistory(
                            po_id=po.id, from_status=po.po_status,
                            to_status=PoStatus.SUPERSEDED, changed_by="repair",
                            notes="Superseded: an event with a later timestamp exists.",
                        ))
                        po.po_status = PoStatus.SUPERSEDED

            if APPLY:
                db.add(EdiPoStatusHistory(
                    po_id=newest.id, from_status=newest.po_status,
                    to_status=PoStatus.PARSED, changed_by="repair",
                    notes="Re-activated: newest Zepto event for this PO number.",
                ))
                newest.po_status = PoStatus.PARSED
            repaired += 1

        if APPLY:
            db.commit()

    print(f"\n{'applied' if APPLY else 'dry run'} — {repaired} PO number(s) repaired")
    if not APPLY:
        print("re-run with --apply to persist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
