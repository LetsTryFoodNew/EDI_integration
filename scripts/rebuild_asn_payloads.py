"""
Rebuild stored Blinkit ASN payloads from current data.

The ASN body is built once, when the invoice arrives, and parked in
`edi_outbound_messages.payload` so what will be sent is visible before dispatch.
That means a fix to the builder does **not** reach an ASN that is already queued --
the retry re-sends the same bytes and earns the same rejection.

Run this after any change to `build_blinkit_asn_payload`, e.g. the Go integer
encoding fix (2026-08-22), which Blinkit rejected with:

    cannot unmarshal number 360.0 into Go struct field
    Item.items.quantity of type int

Only touches BLINKIT messages that have not been sent. Prints a diff summary and
leaves `next_retry_at` alone, so a held message stays held.

    docker compose exec -T api python scripts/rebuild_asn_payloads.py [--apply]
"""
from __future__ import annotations

import json
import sys

from sqlalchemy import select

from app.adapters.outbound.blinkit_asn import build_blinkit_asn_payload
from app.db import SyncSessionLocal
from app.models._enums import EdiDocType
from app.models.asn import EdiAdvanceShipNotice
from app.models.edi_po import EdiPurchaseOrder
from app.models.invoice import EdiInvoice
from app.models.master_data import SellerEntity, TradingPartner
from app.models.outbound import EdiOutboundMessage

APPLY = "--apply" in sys.argv


def _typed(node: object, path: str = "") -> dict[str, str]:
    """Flatten a payload to {path: python_type} so an encoding change is visible."""
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(_typed(v, f"{path}.{k}" if path else k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.update(_typed(v, f"{path}[{i}]"))
    else:
        out[path] = type(node).__name__
    return out


def main() -> int:
    changed = 0
    with SyncSessionLocal() as db:
        seller = db.execute(
            select(SellerEntity).where(SellerEntity.deleted_at.is_(None)).limit(1)
        ).scalar_one_or_none()

        messages = db.execute(
            select(EdiOutboundMessage).where(
                EdiOutboundMessage.doc_type == EdiDocType.ASN_856,
                EdiOutboundMessage.status.notin_(("SENT", "ACKED")),
            )
        ).scalars().all()

        for msg in messages:
            partner = db.get(TradingPartner, msg.trading_partner_id)
            if partner is None or partner.code != "BLINKIT":
                continue

            po = db.get(EdiPurchaseOrder, msg.po_id)
            asn = db.execute(
                select(EdiAdvanceShipNotice).where(
                    EdiAdvanceShipNotice.asn_number == msg.external_reference
                )
            ).scalar_one_or_none()
            if po is None or asn is None:
                print(f"SKIP  {msg.external_reference}: no PO or ASN row")
                continue

            invoice = db.execute(
                select(EdiInvoice).where(EdiInvoice.asn_id == asn.id)
            ).scalar_one_or_none()
            if invoice is None:
                print(f"SKIP  {msg.external_reference}: no invoice linked")
                continue

            fresh, warnings = build_blinkit_asn_payload(db, po, asn, invoice, partner, seller)
            before, after = _typed(msg.payload or {}), _typed(fresh)
            retyped = {k: (before[k], after[k]) for k in after if before.get(k) not in (None, after[k])}

            if json.dumps(msg.payload, sort_keys=True, default=str) == json.dumps(
                fresh, sort_keys=True, default=str
            ) and not retyped:
                print(f"SAME  {msg.external_reference}")
                continue

            print(f"BUILD {msg.external_reference}  ({len(retyped)} field(s) re-typed)")
            for path, (was, now) in sorted(retyped.items())[:12]:
                print(f"        {path}: {was} -> {now}")
            for w in warnings:
                print(f"        warning: {w}")

            if APPLY:
                msg.payload = fresh
                msg.error_message = None
                changed += 1

        if APPLY:
            db.commit()

    print(f"\n{'applied' if APPLY else 'dry run'} — {changed} message(s) rewritten")
    if not APPLY:
        print("re-run with --apply to persist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
