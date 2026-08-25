"""
Delete outbound messages whose partner has no endpoint to receive them.

trigger_acks_for_confirmed_pos used to create a PO_ACK_855 for every SAP_CONFIRMED
PO regardless of partner. Zepto has no acknowledgement API -- their PO moves to
CONFIRMED on receiving an ASN, which is why no separate 855 exists -- so each of
those retried five times and ended FAILED. The trigger now checks capability first,
but the rows it already made are still on the PO page, sitting next to an ASN that
really was delivered and implying something went wrong.

Deletes rather than soft-deletes because edi_outbound_messages has no deleted_at and
these are not business records: nothing was ever transmitted, and nothing ever could
be. A row describing a document the partner cannot receive is misinformation, not
history.

Safety: only removes messages that were never sent AND whose partner's adapter
declares it cannot carry that doc_type. A message that could have gone out -- one
failing because a PO is missing at the partner, say -- is left alone, because that
one is telling the truth.

    docker compose exec -T api python scripts/purge_unsupported_outbound.py [--apply]
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select  # noqa: E402

from app.adapters.outbound.registry import (  # noqa: E402
    UnsupportedOutboundPartnerError,
    get_outbound_adapter,
)
from app.db import SyncSessionLocal  # noqa: E402
from app.models.master_data import TradingPartner  # noqa: E402
from app.models.outbound import EdiOutboundMessage  # noqa: E402

APPLY = "--apply" in sys.argv

# A message in one of these states reached the partner; never touch it.
DELIVERED = ("SENT", "ACKED", "CANCELLED")


def main() -> int:
    removed: Counter[str] = Counter()

    with SyncSessionLocal() as db:
        messages = db.execute(
            select(EdiOutboundMessage).where(
                EdiOutboundMessage.status.notin_(DELIVERED)
            )
        ).scalars().all()

        for msg in messages:
            partner = db.get(TradingPartner, msg.trading_partner_id)
            if partner is None:
                continue

            try:
                adapter = get_outbound_adapter(
                    partner_code=partner.code, source_channel=partner.source_channel
                )
            except UnsupportedOutboundPartnerError:
                continue

            doc_type = str(getattr(msg.doc_type, "value", msg.doc_type))
            if adapter.supports(doc_type):
                continue

            key = f"{partner.code} {doc_type}"
            removed[key] += 1
            print(f"DELETE  {key:26} {msg.external_reference or msg.id} ({msg.status})")
            if APPLY:
                db.delete(msg)

        if APPLY:
            db.commit()

    if not removed:
        print("nothing to remove — every queued message can reach its partner")
        return 0

    print()
    for key, n in sorted(removed.items()):
        print(f"  {key:26} {n}")
    print(f"\n{'deleted' if APPLY else 'dry run'} — {sum(removed.values())} message(s)")
    if not APPLY:
        print("re-run with --apply to persist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
