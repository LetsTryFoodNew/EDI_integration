"""
Create dummy invoices for testing the SAP invoice-push flow end to end.

Builds one invoice per requested partner, deriving quantities, prices and tax from the
PO's own line items so the result reconciles and clears validation. A hand-typed invoice
would trip the total-reconciliation gate and be held, which tests the gate rather than
the happy path.

Covers both dispatch channels, because that is the part worth seeing work:

    ZEPTO   (API)   -> ZeptoOutboundAdapter  -> Zepto's ASN API
    SWIGGY  (EMAIL) -> EmailOutboundAdapter  -> email to the partner

Neither is special-cased here. The outbound registry resolves the adapter from the
partner's source_channel, so the same payload takes different routes on its own.

Usage:
    python scripts/create_dummy_invoices.py                    # both partners, writes to DB
    python scripts/create_dummy_invoices.py --partner ZEPTO    # one partner
    python scripts/create_dummy_invoices.py --dry-run          # print JSON, touch nothing
    python scripts/create_dummy_invoices.py --json-only DIR    # write Postman payloads
    python scripts/create_dummy_invoices.py --cleanup          # remove what this created

Every invoice this creates is numbered DUMMY-<PARTNER>-<n>, which is what --cleanup
matches on. Nothing else is touched.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import SyncSessionLocal  # noqa: E402
from app.models.edi_po import EdiPurchaseOrder  # noqa: E402
from app.models.master_data import TradingPartner  # noqa: E402

_TWO_DP = Decimal("0.01")
_PREFIX = "DUMMY-"

# Partners to build for by default — one per dispatch channel, so a single run
# exercises both the API and the email path.
_DEFAULT_PARTNERS = ("ZEPTO", "SWIGGY")


def _q(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(_TWO_DP, ROUND_HALF_UP)


def _pick_po(session: Any, partner_code: str, po_number: str | None = None) -> Any:
    """
    Pick the PO to invoice.

    With `po_number`, that exact PO -- needed once a partner has hundreds of POs and
    the one you want to test is not the newest. Without it, the newest PO for this
    partner that has line items, which is the one a tester recognises in the UI.
    """
    from sqlalchemy import select

    partner = session.execute(
        select(TradingPartner).where(
            TradingPartner.code == partner_code.upper(),
            TradingPartner.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if partner is None:
        return None, None

    pos = session.execute(
        select(EdiPurchaseOrder)
        .where(
            EdiPurchaseOrder.trading_partner_id == partner.id,
            EdiPurchaseOrder.deleted_at.is_(None),
        )
        .order_by(EdiPurchaseOrder.created_at.desc())
        .limit(50 if not po_number else 2000)
    ).scalars().all()

    if po_number:
        for po in pos:
            if po.buyer_po_number == po_number and po.line_items:
                return partner, po
        return partner, None

    for po in pos:
        if po.line_items:
            return partner, po
    return partner, None


def _build_payload(partner: Any, po: Any, seq: int) -> dict[str, Any]:
    """
    Turn a PO into a plausible invoice for it.

    Ships half of each ordered quantity (minimum 1) so the result is a *partial*
    dispatch — that is the interesting case, since it proves several invoices can hang
    off one PO and that the cumulative over-invoicing check tolerates it.

    Tax is copied from the PO line rather than recomputed: the PO already carries the
    CGST/SGST-vs-IGST split the partner decided on, and re-deriving it here would risk
    disagreeing with the retailer's own arithmetic.
    """
    today = date.today()
    lines: list[dict[str, Any]] = []
    subtotal = cgst_total = sgst_total = igst_total = Decimal("0")

    for line in po.line_items:
        ordered = Decimal(str(line.ordered_qty or 0))
        if ordered <= 0:
            continue
        qty = max(Decimal("1"), (ordered / 2).quantize(Decimal("1"), ROUND_HALF_UP))
        qty = min(qty, ordered)

        unit_price = _q(line.unit_price)
        taxable = (qty * unit_price).quantize(_TWO_DP, ROUND_HALF_UP)

        cgst_rate = _q(line.cgst_rate)
        sgst_rate = _q(line.sgst_rate)
        igst_rate = _q(line.igst_rate)
        cgst_amt = (taxable * cgst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP)
        sgst_amt = (taxable * sgst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP)
        igst_amt = (taxable * igst_rate / 100).quantize(_TWO_DP, ROUND_HALF_UP)
        line_total = taxable + cgst_amt + sgst_amt + igst_amt

        subtotal += taxable
        cgst_total += cgst_amt
        sgst_total += sgst_amt
        igst_total += igst_amt

        lines.append({
            "b1_item_code": line.sap_material_no or f"FG-{line.buyer_sku or 'UNMAPPED'}"[:50],
            "buyer_sku": line.buyer_sku,
            "po_line_number": line.line_number,
            "description": line.buyer_sku_description,
            "hsn_code": line.hsn_code,
            "qty": str(qty),
            "uom": line.buyer_uom or "PCS",
            "unit_price": str(unit_price),
            "taxable_amount": str(taxable),
            "cgst_rate": str(cgst_rate), "cgst_amount": str(cgst_amt),
            "sgst_rate": str(sgst_rate), "sgst_amount": str(sgst_amt),
            "igst_rate": str(igst_rate), "igst_amount": str(igst_amt),
            "line_total": str(line_total),
            "batch_number": f"LTF-{today:%Y%m}-{seq}",
            "expiry_date": str(today + timedelta(days=365)),
        })

    grand_total = subtotal + cgst_total + sgst_total + igst_total

    return {
        "invoice_number": f"{_PREFIX}{partner.code}-{seq}",
        "invoice_date": str(today),
        # Real SAP pushes send b1_sales_order_doc_entry. These POs were never pushed to
        # B1, so the partner_code + po_number fallback is used instead — which also
        # exercises that resolution path.
        "partner_code": partner.code,
        "po_number": po.buyer_po_number,
        "b1_invoice_doc_entry": 900000 + seq,
        "b1_invoice_doc_num": 500000 + seq,
        "subtotal_amount": str(subtotal),
        "cgst_amount": str(cgst_total),
        "sgst_amount": str(sgst_total),
        "igst_amount": str(igst_total),
        "round_off": "0.00",
        "grand_total": str(grand_total),
        "shipment_date": str(today),
        "carrier": "Delhivery" if partner.code == "ZEPTO" else "Blue Dart",
        "tracking_number": f"TRK-{partner.code}-{seq:04d}",
        "line_items": lines,
    }


def _expected_adapter(partner: Any) -> str:
    """Name the adapter the outbound registry will pick, so the run is self-explaining."""
    try:
        from app.adapters.outbound.registry import get_outbound_adapter
        return type(get_outbound_adapter(
            partner_code=partner.code, source_channel=partner.source_channel
        )).__name__
    except Exception as exc:  # noqa: BLE001 - informational only
        return f"(unresolved: {exc})"


def _cleanup(session: Any) -> int:
    """Remove every DUMMY- invoice and the ASNs / outbound rows they produced."""
    from sqlalchemy import select

    from app.models.asn import EdiAdvanceShipNotice, EdiAsnLineItem
    from app.models.edi_po import EdiValidationIssue
    from app.models.invoice import EdiInvoice, EdiInvoiceLineItem
    from app.models.outbound import EdiOutboundMessage

    invoices = session.execute(
        select(EdiInvoice).where(EdiInvoice.invoice_number.like(f"{_PREFIX}%"))
    ).scalars().all()
    if not invoices:
        return 0

    asn_numbers = [f"ASN-{inv.invoice_number}" for inv in invoices]
    invoice_ids = [inv.id for inv in invoices]

    asns = session.execute(
        select(EdiAdvanceShipNotice).where(EdiAdvanceShipNotice.asn_number.in_(asn_numbers))
    ).scalars().all()

    for line in session.execute(
        select(EdiAsnLineItem).where(EdiAsnLineItem.asn_id.in_([a.id for a in asns] or [uuid.uuid4()]))
    ).scalars().all():
        session.delete(line)
    for line in session.execute(
        select(EdiInvoiceLineItem).where(EdiInvoiceLineItem.invoice_id.in_(invoice_ids))
    ).scalars().all():
        session.delete(line)
    for msg in session.execute(
        select(EdiOutboundMessage).where(EdiOutboundMessage.external_reference.in_(asn_numbers))
    ).scalars().all():
        session.delete(msg)
    for issue in session.execute(
        select(EdiValidationIssue).where(
            EdiValidationIssue.issue_code == "E200_INVOICE_HELD",
            EdiValidationIssue.message.like(f"%{_PREFIX}%"),
        )
    ).scalars().all():
        session.delete(issue)

    # Break the invoice -> ASN reference before deleting the ASN rows it points at.
    for inv in invoices:
        inv.asn_id = None
    session.flush()
    for asn in asns:
        session.delete(asn)
    for inv in invoices:
        session.delete(inv)

    session.commit()
    return len(invoices)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partner", action="append",
                    help="partner code (repeatable). Default: ZEPTO and SWIGGY")
    ap.add_argument("--dry-run", action="store_true", help="print payloads, write nothing")
    ap.add_argument("--json-only", metavar="DIR",
                    help="write payloads as .json for Postman and exit")
    ap.add_argument("--cleanup", action="store_true", help="delete every DUMMY- invoice")
    ap.add_argument("--po", metavar="PO_NUMBER",
                    help="invoice this exact PO instead of the newest one")
    args = ap.parse_args()

    from app.schemas.api import InvoicePush
    from app.workflows.invoice_from_sap import ingest_invoice

    with SyncSessionLocal() as session:
        if args.cleanup:
            removed = _cleanup(session)
            print(f"removed {removed} dummy invoice(s) and their ASNs / outbound rows")
            return 0

        codes = [c.upper() for c in (args.partner or _DEFAULT_PARTNERS)]
        stamp = datetime.now(UTC).strftime("%H%M%S")
        out_dir = Path(args.json_only) if args.json_only else None
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)

        for idx, code in enumerate(codes, start=1):
            partner, po = _pick_po(session, code, args.po)
            if partner is None:
                print(f"{code:8} partner not found — skipped")
                continue
            if po is None:
                hint = f" matching --po {args.po}" if args.po else ""
                print(f"{code:8} no PO with line items{hint} — skipped")
                continue

            payload = _build_payload(partner, po, seq=int(f"{stamp[-4:]}{idx}"))

            print(f"\n{'=' * 68}")
            print(f"{code}  ({partner.source_channel})  ->  {_expected_adapter(partner)}")
            print(f"  PO           : {po.buyer_po_number}")
            print(f"  Invoice      : {payload['invoice_number']}")
            print(f"  Lines        : {len(payload['line_items'])} (half of each ordered qty)")
            print(f"  Grand total  : {payload['grand_total']}")

            if out_dir:
                path = out_dir / f"invoice_{code.lower()}.json"
                path.write_text(json.dumps({"invoices": [payload]}, indent=2))
                print(f"  written      : {path}")
                continue

            if args.dry_run:
                print(json.dumps({"invoices": [payload]}, indent=2))
                continue

            result = ingest_invoice(session, InvoicePush(**payload))
            session.commit()
            print(f"  outcome      : {result.outcome}")
            print(f"  ASN          : {result.asn_number or '(none — held)'}")
            print(f"  dispatched   : {result.asn_dispatched}")
            for issue in result.issues:
                print(f"  HELD         : {issue}")

        print(f"\n{'=' * 68}")
        if not args.dry_run and not out_dir:
            print("Undo with:  python scripts/create_dummy_invoices.py --cleanup")
    return 0


if __name__ == "__main__":
    sys.exit(main())
