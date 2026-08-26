"""
Render an ASN as an email for partners with no API.

Swiggy, LOTS and the other mail partners get the same shipment information the API
partners get over the wire, only as something a person opens. The transport adapter
is deliberately dumb -- `BaseOutboundAdapter` says payload construction happens
before dispatch and the adapter must not read the DB -- so the envelope is built
here, at message creation time, which also means the exact email is visible in the
Outbound Messages tab *before* it is sent rather than materialising inside Gmail.

Without this the ASN payload reached `EmailOutboundAdapter._build_mime` as a bare
canonical body, which reads `to`, `subject` and `body_text` and found none of them:
the mail would have gone out with an empty To header and a subject of
"(no subject)". A retailer expecting a delivery note would have received nothing
usable, and our own outbound tab would have said SENT.

The body carries both a plain-text and an HTML part. Plain text is not a courtesy
here -- warehouse mailboxes and EDI mailbots frequently strip HTML, and the shipment
lines have to survive that.
"""
from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import Any

_ZERO = Decimal("0")


def build_email_asn_payload(
    po: Any,
    asn: Any,
    invoice: Any,
    partner: Any,
    seller: Any,
    body: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Wrap a canonical ASN body in an email envelope.

    Returns the envelope and any warnings worth logging. The canonical body is kept
    under `asn` so nothing is lost: the outbound tab still shows the full shipment,
    and a later formatter change can re-render from it.
    """
    warnings: list[str] = []

    to = (getattr(partner, "email_address", "") or "").strip()
    if not to:
        warnings.append(
            f"{getattr(partner, 'code', '?')} has no email_address in Master Data — "
            f"the ASN cannot be delivered until one is set."
        )

    lines = list(body.get("line_items") or [])
    invoice_number = body.get("invoice_number") or getattr(invoice, "invoice_number", "")
    po_number = body.get("po_number") or getattr(po, "buyer_po_number", "")
    asn_number = body.get("asn_number") or getattr(asn, "asn_number", "")

    subject = f"ASN {asn_number} — PO {po_number} — Invoice {invoice_number}"

    envelope = {
        "to": to,
        "subject": subject,
        "body_text": _text_body(po_number, asn_number, invoice_number, body, lines, seller),
        "body_html": _html_body(po_number, asn_number, invoice_number, body, lines, seller),
        # Kept so the outbound tab still shows the shipment and a formatter change can
        # re-render without rebuilding from the invoice.
        "asn": body,
    }
    return envelope, warnings


def _seller_name(seller: Any) -> str:
    return (getattr(seller, "name", None) or "").strip() or "Let's Try Foods"


def _rows(lines: list[dict[str, Any]]) -> list[tuple[str, str, str, str, str]]:
    out = []
    for line in lines:
        out.append((
            str(line.get("buyer_sku") or ""),
            str(line.get("b1_item_code") or ""),
            _qty(line.get("shipped_qty")),
            str(line.get("batch_number") or ""),
            str(line.get("expiry_date") or ""),
        ))
    return out


def _qty(value: Any) -> str:
    """Trim the stored scale: 15.0000 reads as 15 on a delivery note."""
    try:
        d = Decimal(str(value))
    except Exception:
        return str(value or "")
    return str(int(d)) if d == d.to_integral_value() else str(d.normalize())


def _meta(body: dict[str, Any]) -> list[tuple[str, str]]:
    pairs = [
        ("Carrier", body.get("carrier")),
        ("Tracking", body.get("tracking_number")),
        ("Shipment date", body.get("shipment_date")),
        ("IRN", body.get("irn")),
        ("E-way bill", body.get("eway_bill_number")),
    ]
    return [(k, str(v)) for k, v in pairs if v]


def _text_body(
    po_number: str,
    asn_number: str,
    invoice_number: str,
    body: dict[str, Any],
    lines: list[dict[str, Any]],
    seller: Any,
) -> str:
    rows = _rows(lines)
    width = max([len(r[0]) for r in rows] + [9])
    out = [
        f"Advance Ship Notice {asn_number}",
        "",
        f"Purchase order : {po_number}",
        f"Invoice        : {invoice_number}",
    ]
    out += [f"{k:<15}: {v}" for k, v in _meta(body)]
    out += ["", f"{'Your SKU'.ljust(width)}  {'Our code':<10} {'Qty':>8}  Batch / Expiry", "-" * (width + 42)]
    for sku, item, qty, batch, expiry in rows:
        out.append(f"{sku.ljust(width)}  {item:<10} {qty:>8}  {batch} {expiry}".rstrip())
    out += ["", f"{len(rows)} line(s) despatched.", "", f"{_seller_name(seller)}",
            "Sent automatically by the EDI middleware — please do not reply."]
    return "\n".join(out)


def _html_body(
    po_number: str,
    asn_number: str,
    invoice_number: str,
    body: dict[str, Any],
    lines: list[dict[str, Any]],
    seller: Any,
) -> str:
    meta = "".join(
        f"<tr><td style='padding:2px 12px 2px 0;color:#666'>{escape(k)}</td>"
        f"<td style='padding:2px 0'><strong>{escape(v)}</strong></td></tr>"
        for k, v in [("Purchase order", po_number), ("Invoice", invoice_number), *_meta(body)]
    )
    rows = "".join(
        "<tr>"
        f"<td style='padding:6px 12px 6px 0;border-bottom:1px solid #eee'>{escape(sku)}</td>"
        f"<td style='padding:6px 12px 6px 0;border-bottom:1px solid #eee'>{escape(item)}</td>"
        f"<td style='padding:6px 12px 6px 0;border-bottom:1px solid #eee;text-align:right'>{escape(qty)}</td>"
        f"<td style='padding:6px 12px 6px 0;border-bottom:1px solid #eee'>{escape(batch)}</td>"
        f"<td style='padding:6px 0;border-bottom:1px solid #eee'>{escape(expiry)}</td>"
        "</tr>"
        for sku, item, qty, batch, expiry in _rows(lines)
    )
    head = "".join(
        f"<th style='padding:0 12px 6px 0;text-align:{'right' if h == 'Qty' else 'left'};"
        f"border-bottom:2px solid #333;font-size:12px;color:#666'>{h}</th>"
        for h in ("Your SKU", "Our code", "Qty", "Batch", "Expiry")
    )
    return (
        "<div style=\"font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
        "font-size:14px;color:#111;max-width:720px\">"
        f"<h2 style='margin:0 0 4px'>Advance Ship Notice {escape(asn_number)}</h2>"
        f"<table style='margin:12px 0 20px;border-collapse:collapse'>{meta}</table>"
        f"<table style='border-collapse:collapse;width:100%'><thead><tr>{head}</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        f"<p style='color:#666;margin-top:20px'>{len(lines)} line(s) despatched.</p>"
        f"<p style='color:#999;font-size:12px;margin-top:24px'>{escape(_seller_name(seller))} — "
        "sent automatically by the EDI middleware, please do not reply.</p>"
        "</div>"
    )
