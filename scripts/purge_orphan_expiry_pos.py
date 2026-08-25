"""
Retire purchase orders that were never purchase orders.

A partner's "this PO has expired" notice arrives in the same event feed as real POs.
`parse_and_persist` used to turn each one into a fresh PO at version 1, because there
was nothing under that number to supersede -- so a notice about an order we had never
received became an order. 303 of the 540 Zepto POs on the server got there this way,
and every one of them then went through validation and landed in the exceptions queue
asking ops to map SKUs for goods nobody would ever ship. That is why a day with four
real POs read as twenty-eight.

The workflow no longer creates them (`_is_orphan_terminal_notice`). This clears out
what it already made.

Two repairs, both driven by re-running the real parser over the stored raw payload
rather than by hardcoding any partner's status vocabulary:

  ORPHAN   the PO's own raw message is a terminal notice, and no other undeleted PO
           under that (partner, number) came from a live event. Soft-deleted, and its
           raw message marked SKIPPED so the inbox stops calling it Pending.

  RESTATE  the PO's raw message is a terminal notice but a real PO does exist under
           that number -- so the notice was a genuine expiry of an order we held. The
           row stays; its status is corrected to CANCELLED, which is what the parser
           said all along before `_save_canonical_po` overwrote it with PARSED.

Nothing is touched if the PO reached SAP, carries an invoice or ASN, or has an
outbound message: at that point it is a business record whatever its origin, and the
right correction is a human one.

    docker compose exec -T api python scripts/purge_orphan_expiry_pos.py [--partner ZEPTO] [--apply]
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import SyncSessionLocal  # noqa: E402
from app.models._enums import PoStatus  # noqa: E402
from app.models.asn import EdiAdvanceShipNotice  # noqa: E402
from app.models.edi_po import EdiPoStatusHistory, EdiPurchaseOrder  # noqa: E402
from app.models.invoice import EdiInvoice  # noqa: E402
from app.models.master_data import TradingPartner  # noqa: E402
from app.models.outbound import EdiOutboundMessage  # noqa: E402
from app.models.raw_messages import RawMessage  # noqa: E402
from app.workflows.parse_and_persist import _TERMINAL_PO_STATUSES, _run_parser  # noqa: E402

APPLY = "--apply" in sys.argv
PARTNER = None
if "--partner" in sys.argv:
    PARTNER = sys.argv[sys.argv.index("--partner") + 1].upper()


def _is_terminal_notice(session, po, partner) -> bool:
    """Ask the partner's own parser whether this PO's source event declared it dead."""
    if po.raw_message_id is None:
        return False
    raw = session.get(RawMessage, po.raw_message_id)
    if raw is None:
        return False
    result = _run_parser(raw, partner)
    if not (result.success and result.doc):
        return False
    return result.doc.po_status in _TERMINAL_PO_STATUSES


def _is_business_record(session, po) -> str | None:
    """Reasons this row must survive regardless of how it was born."""
    if po.b1_sales_order_doc_entry:
        return f"in SAP (DocEntry {po.b1_sales_order_doc_entry})"
    for model, label in (
        (EdiInvoice, "carries an invoice"),
        (EdiAdvanceShipNotice, "carries an ASN"),
        (EdiOutboundMessage, "has an outbound message"),
    ):
        if session.execute(
            select(func.count()).select_from(model).where(model.po_id == po.id)
        ).scalar_one():
            return label
    return None


def main() -> int:
    counts: Counter[str] = Counter()

    with SyncSessionLocal() as db:
        partners = db.execute(
            select(TradingPartner).where(TradingPartner.deleted_at.is_(None))
        ).scalars().all()
        if PARTNER:
            partners = [p for p in partners if p.code == PARTNER]
            if not partners:
                print(f"no trading partner with code {PARTNER!r}")
                return 1

        for partner in partners:
            pos = db.execute(
                select(EdiPurchaseOrder).where(
                    EdiPurchaseOrder.trading_partner_id == partner.id,
                    EdiPurchaseOrder.deleted_at.is_(None),
                ).order_by(EdiPurchaseOrder.buyer_po_number, EdiPurchaseOrder.version)
            ).scalars().all()

            terminal = {po.id: _is_terminal_notice(db, po, partner) for po in pos}

            # A number is "real" if any undeleted row under it came from a live event.
            live_numbers = {po.buyer_po_number for po in pos if not terminal[po.id]}

            for po in pos:
                if not terminal[po.id]:
                    continue

                keep = _is_business_record(db, po)
                if keep:
                    print(f"KEEP     {partner.code:8} {po.buyer_po_number:12} — {keep}")
                    counts[f"{partner.code} kept"] += 1
                    continue

                if po.buyer_po_number in live_numbers:
                    if po.po_status is PoStatus.CANCELLED:
                        continue
                    print(f"RESTATE  {partner.code:8} {po.buyer_po_number:12} "
                          f"{po.po_status} → CANCELLED")
                    counts[f"{partner.code} restated"] += 1
                    if APPLY:
                        db.add(EdiPoStatusHistory(
                            po_id=po.id, from_status=po.po_status,
                            to_status=PoStatus.CANCELLED, changed_by="purge_orphan_expiry_pos",
                            notes="Partner reported this PO terminal; status was stored as "
                                  "PARSED before _save_canonical_po honoured the parser.",
                        ))
                        po.po_status = PoStatus.CANCELLED
                    continue

                print(f"ORPHAN   {partner.code:8} {po.buyer_po_number:12} "
                      f"{po.po_status} — expiry notice for an order we never held")
                counts[f"{partner.code} orphans"] += 1
                if APPLY:
                    from datetime import UTC, datetime

                    po.deleted_at = datetime.now(UTC)
                    if po.raw_message_id:
                        raw = db.get(RawMessage, po.raw_message_id)
                        if raw is not None:
                            raw.parse_status = "SKIPPED"
                            raw.processed = True

        if APPLY:
            db.commit()

    if not counts:
        print("nothing to repair")
        return 0

    print()
    for key, n in sorted(counts.items()):
        print(f"  {key:24} {n}")
    print(f"\n{'applied' if APPLY else 'dry run'}")
    if not APPLY:
        print("re-run with --apply to persist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
