"""
Permanently erase a partner's purchase orders and everything hanging off them.

This is the destructive sibling of purge_partner_pos.py, which only soft-deletes.
Soft-delete hides a PO from every screen but leaves `raw_messages` on the API Inbox,
because the inbox lists payloads rather than orders and that table has no `deleted_at`
by design -- it is the immutable record of what arrived. Clearing the inbox therefore
means removing rows, and a retired PO still holds a foreign key to its raw message, so
the orders have to go first.

CLAUDE.md says never hard-delete, and that rule is right for business records. This
exists for the one situation it does not cover: discarding pre-prod test data during a
cutover to live partner endpoints, where leaving hundreds of fake orders behind makes
the first real ones impossible to pick out. Every row is written to JSONL before it
goes, so the operation stays reversible from the dump.

Order matters -- all fourteen foreign keys are NO ACTION, so nothing cascades:

    invoice lines -> asn lines -> invoices -> ASNs -> validation issues ->
    outbound messages -> status history -> b1 api log -> po lines -> POs -> raw messages

    docker exec -w /app edi-integration-api-1 python scripts/erase_partner_pos.py \
        --partner ZEPTO --partner BLINKIT --keep-today BLINKIT [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import bindparam, text  # noqa: E402

from app.db import SyncSessionLocal  # noqa: E402

#: (label, table, WHERE clause) in the order they must be deleted. Every clause is
#: anchored on the doomed PO ids so a mistake cannot reach beyond them.
CHILDREN: list[tuple[str, str, str]] = [
    ("invoice lines", "edi_invoice_line_items",
     "invoice_id in (select id from edi_invoices where po_id in :ids)"),
    ("asn lines", "edi_asn_line_items",
     "asn_id in (select id from edi_advance_ship_notices where po_id in :ids)"),
    ("invoices", "edi_invoices", "po_id in :ids"),
    ("ASNs", "edi_advance_ship_notices", "po_id in :ids"),
    ("validation issues", "edi_validation_issues", "po_id in :ids"),
    ("outbound messages", "edi_outbound_messages", "po_id in :ids"),
    ("status history", "edi_po_status_history", "po_id in :ids"),
    ("b1 api log", "b1_api_log", "po_id in :ids"),
    ("po lines", "edi_po_line_items", "po_id in :ids"),
    ("purchase orders", "edi_purchase_orders", "id in :ids"),
]

SELECT_POS = text("""
    select po.id, po.buyer_po_number, po.raw_message_id, p.code
    from edi_purchase_orders po
    join trading_partners p on p.id = po.trading_partner_id
    where p.code in :partners
      and not (p.code = any(:keep_today)
               and (po.created_at at time zone 'Asia/Kolkata')::date = :today)
""").bindparams(bindparam("partners", expanding=True))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partner", action="append", required=True,
                    help="partner code to erase; repeatable")
    ap.add_argument("--keep-today", action="append", default=[],
                    help="partner code whose orders from today (IST) are spared")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dump", default="/app/data/erased_pos_backup.jsonl")
    args = ap.parse_args()

    partners = [c.upper() for c in args.partner]
    keep_today = [c.upper() for c in args.keep_today]
    today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Kolkata")).date()

    with SyncSessionLocal() as db:
        rows = db.execute(SELECT_POS, {
            "partners": partners, "keep_today": keep_today, "today": today,
        }).all()
        ids = [r.id for r in rows]

        by_partner: dict[str, int] = {}
        for r in rows:
            by_partner[r.code] = by_partner.get(r.code, 0) + 1
        for code, n in sorted(by_partner.items()):
            print(f"  {code:<10} {n} purchase order(s)")
        if not ids:
            print("\nnothing to erase")
            return 0

        params = {"ids": tuple(ids)}
        for label, table, where in CHILDREN:
            n = db.execute(
                text(f"select count(*) from {table} where {where}"), params  # noqa: S608
            ).scalar_one()
            print(f"    {label:<20} {n}")

        # Raw messages go only when no surviving PO still points at one. A message can
        # carry several orders, and one live PO among them keeps the whole row -- the
        # PO detail page reads it for the Raw Source tab.
        still_referenced = db.execute(text(
            "select distinct raw_message_id from edi_purchase_orders "
            "where raw_message_id is not null and id not in :ids"
        ), params).scalars().all()
        doomed_raw = sorted(
            {r.raw_message_id for r in rows if r.raw_message_id} - set(still_referenced)
        )
        print(f"    {'raw messages':<20} {len(doomed_raw)}")

        if not args.apply:
            print(f"\ntoday (IST) = {today}\ndry run — re-run with --apply")
            return 0

        dump = Path(args.dump)
        dump.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with dump.open("a") as fh:
            for table, col in (("edi_purchase_orders", "id"),
                               ("edi_po_line_items", "po_id"),
                               ("edi_validation_issues", "po_id"),
                               ("edi_outbound_messages", "po_id")):
                for row in db.execute(text(
                    f"select row_to_json(t) from {table} t where t.{col} in :ids"  # noqa: S608
                ), params).scalars():
                    fh.write(json.dumps({"table": table, "row": row}, default=str) + "\n")
                    written += 1
        print(f"  backed up {written} row(s) to {dump}")

        for _label, table, where in CHILDREN:
            result = db.execute(text(f"delete from {table} where {where}"), params)  # noqa: S608
            print(f"    deleted {result.rowcount:>5}  {table}")
        if doomed_raw:
            result = db.execute(
                text("delete from raw_messages where id in :raw"),
                {"raw": tuple(doomed_raw)},
            )
            print(f"    deleted {result.rowcount:>5}  raw_messages")
        db.commit()

    print(f"\ntoday (IST) = {today}\napplied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
