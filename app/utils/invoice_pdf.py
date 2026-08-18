"""
Render an EdiInvoice as a GST tax invoice PDF.

Used for the ops "Download PDF" action and as the attachment on outbound email to
mail-based partners, so the layout has to satisfy both an internal reader and a
retailer's accounts payable desk.

Deliberately reportlab rather than an HTML-to-PDF route: weasyprint needs cairo and
pango installed in the image, and a tax invoice is a fixed grid, not a web page. What
it does need is correct pagination when a Swiggy PO runs to forty lines — which is
what platypus Table gives us with `repeatRows=1`.

India-localisation notes:
  - CGST+SGST and IGST are mutually exclusive per invoice; only the columns actually
    carrying tax are drawn, otherwise every intrastate invoice wastes a third of the
    page on a zeroed IGST column.
  - `round_off` is shown as its own line. B1 rounds centrally, so the residue is
    expected and an auditor will look for it explicitly.
  - Amounts print with Indian digit grouping (1,00,000.00) via _inr().
"""
from __future__ import annotations

import io
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_TWO_DP = Decimal("0.01")
_GREY = colors.HexColor("#6b7280")
_LINE = colors.HexColor("#d1d5db")
_HEAD_BG = colors.HexColor("#f3f4f6")


def _d(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(_TWO_DP, ROUND_HALF_UP)


def _inr(value: Any) -> str:
    """
    Indian digit grouping: 1234567.5 -> '12,34,567.50'.

    Python's own thousands separator groups in threes throughout, which is wrong for
    an Indian tax invoice — the last group is three digits and every group before it
    is two.
    """
    amount = _d(value)
    sign = "-" if amount < 0 else ""
    whole, _, frac = f"{abs(amount):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join([*parts, tail])
    return f"{sign}{whole}.{frac}"


def render_invoice_pdf(db: Session, invoice: Any) -> bytes:
    """
    Build the PDF for one invoice and return the raw bytes.

    Kept synchronous and in-memory: an invoice is a few kilobytes, and streaming it
    straight back from the route avoids a temp file we would then have to clean up.
    """
    from app.models.edi_po import EdiPurchaseOrder
    from app.models.master_data import SellerEntity, TradingPartner

    po = db.get(EdiPurchaseOrder, invoice.po_id)
    partner = db.get(TradingPartner, invoice.trading_partner_id)
    seller = db.get(SellerEntity, po.seller_entity_id) if po and po.seller_entity_id else None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Tax Invoice {invoice.invoice_number}",
        author=(seller.name if seller else "Let's Try Foods"),
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=8, leading=10.5)
    small = ParagraphStyle("small", parent=body, fontSize=7, textColor=_GREY, leading=9)
    title = ParagraphStyle("title", parent=styles["Heading1"], fontSize=15,
                           alignment=TA_CENTER, spaceAfter=1)
    right = ParagraphStyle("right", parent=body, alignment=TA_RIGHT)

    story: list[Any] = [
        Paragraph("TAX INVOICE", title),
        Paragraph("Original for Recipient", ParagraphStyle(
            "sub", parent=small, alignment=TA_CENTER, spaceAfter=8)),
    ]

    story.append(_party_block(invoice, po, partner, seller, body, small))
    story.append(Spacer(1, 8))
    story.append(_meta_block(invoice, po, body, small))
    story.append(Spacer(1, 10))

    lines, has_igst = _line_rows(invoice)
    story.append(_line_table(lines, has_igst, body, right, small))
    story.append(Spacer(1, 8))

    # Totals and the declaration travel together — a signature block stranded alone
    # on page two of a forty-line Swiggy invoice looks like a different document.
    story.append(KeepTogether([
        _totals_table(invoice, has_igst, body, right),
        Spacer(1, 10),
        _footer(seller, small),
    ]))

    doc.build(story)
    return buf.getvalue()


# ── Blocks ────────────────────────────────────────────────────────────────────

def _party_block(invoice: Any, po: Any, partner: Any, seller: Any,
                 body: Any, small: Any) -> Table:
    """Seller on the left, buyer on the right — the layout AP desks expect."""
    seller_bits = [f"<b>{_esc(seller.name) if seller else 'Let&#39;s Try Foods Private Limited'}</b>"]
    if seller:
        for part in (seller.address_line1, seller.address_line2):
            if part:
                seller_bits.append(_esc(part))
        locality = ", ".join(x for x in (seller.city, seller.state, seller.pincode) if x)
        if locality:
            seller_bits.append(_esc(locality))
        if seller.gstin:
            seller_bits.append(f"GSTIN: <b>{_esc(seller.gstin)}</b>")

    buyer_name = (po.buyer_name if po else None) or (partner.name if partner else "—")
    buyer_bits = [f"<b>{_esc(buyer_name)}</b>"]
    if po:
        if po.ship_to_name:
            buyer_bits.append(f"Ship to: {_esc(po.ship_to_name)}")
        buyer_bits.extend(_address_lines(po.ship_to_address))
        if po.buyer_gstin:
            buyer_bits.append(f"GSTIN: <b>{_esc(po.buyer_gstin)}</b>")

    table = Table(
        [[
            Paragraph("<b>Seller</b>", small),
            Paragraph("<b>Buyer / Ship to</b>", small),
        ], [
            Paragraph("<br/>".join(seller_bits), body),
            Paragraph("<br/>".join(buyer_bits), body),
        ]],
        colWidths=[90 * mm, 90 * mm],
    )
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, _LINE),
        ("BACKGROUND", (0, 0), (-1, 0), _HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _meta_block(invoice: Any, po: Any, body: Any, small: Any) -> Table:
    """
    Invoice / PO / e-invoicing references.

    IRN and e-way bill are printed only when present. B1 fills the IRN in after the
    IRP responds, so an invoice downloaded minutes after posting legitimately has
    none — and an empty "IRN:" label reads like a failure rather than a pending step.
    """
    pairs: list[tuple[str, str]] = [
        ("Invoice No.", invoice.invoice_number),
        ("Invoice Date", invoice.invoice_date.strftime("%d %b %Y") if invoice.invoice_date else "—"),
        ("Buyer PO No.", (po.buyer_po_number if po else "—") or "—"),
        ("PO Date", po.buyer_po_date.strftime("%d %b %Y") if po and po.buyer_po_date else "—"),
    ]
    if invoice.irn:
        pairs.append(("IRN", invoice.irn))
    if invoice.eway_bill_number:
        pairs.append(("e-Way Bill", invoice.eway_bill_number))

    rows = [[
        Paragraph(f"<b>{_esc(label)}</b>", small),
        Paragraph(_esc(str(value)), body),
    ] for label, value in pairs]

    table = Table(rows, colWidths=[32 * mm, 148 * mm])
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, _LINE),
        ("BACKGROUND", (0, 0), (0, -1), _HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _line_rows(invoice: Any) -> tuple[list[Any], bool]:
    """Return the line items and whether this invoice is interstate (IGST-bearing)."""
    lines = list(invoice.line_items)
    has_igst = any(_d(li.igst_amount) > 0 for li in lines)
    return lines, has_igst


def _line_table(lines: list[Any], has_igst: bool, body: Any,
                right: Any, small: Any) -> Table:
    tax_headers = ["IGST"] if has_igst else ["CGST", "SGST"]
    header = ["#", "Description", "HSN", "Qty", "Rate", "Taxable", *tax_headers, "Total"]

    rows: list[list[Any]] = [[Paragraph(f"<b>{h}</b>", small) for h in header]]
    for idx, li in enumerate(lines, start=1):
        desc = li.description or li.b1_item_code or "—"
        tax_cells = (
            [Paragraph(_inr(li.igst_amount), right)]
            if has_igst else
            [Paragraph(_inr(li.cgst_amount), right), Paragraph(_inr(li.sgst_amount), right)]
        )
        rows.append([
            Paragraph(str(idx), small),
            Paragraph(_esc(desc), body),
            Paragraph(_esc(li.hsn_code or "—"), small),
            Paragraph(f"{_d(li.qty):g}", right),
            Paragraph(_inr(li.unit_price), right),
            Paragraph(_inr(li.taxable_amount), right),
            *tax_cells,
            Paragraph(_inr(li.line_total), right),
        ])

    widths = (
        [8 * mm, 62 * mm, 16 * mm, 14 * mm, 20 * mm, 22 * mm, 20 * mm, 22 * mm]
        if has_igst else
        [8 * mm, 52 * mm, 15 * mm, 13 * mm, 19 * mm, 22 * mm, 17 * mm, 17 * mm, 21 * mm]
    )

    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, _LINE),
        ("BACKGROUND", (0, 0), (-1, 0), _HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _totals_table(invoice: Any, has_igst: bool, body: Any, right: Any) -> Table:
    rows: list[tuple[str, Any]] = [("Taxable Value", invoice.subtotal_amount)]
    if has_igst:
        rows.append(("IGST", invoice.igst_amount))
    else:
        rows.append(("CGST", invoice.cgst_amount))
        rows.append(("SGST", invoice.sgst_amount))
    if _d(invoice.cess_amount) != 0:
        rows.append(("Cess", invoice.cess_amount))
    if _d(invoice.round_off) != 0:
        rows.append(("Round Off", invoice.round_off))

    data = [[Paragraph(label, body), Paragraph(_inr(value), right)] for label, value in rows]
    data.append([
        Paragraph("<b>Grand Total</b>", body),
        Paragraph(f"<b>{_inr(invoice.grand_total)}</b>", right),
    ])

    table = Table(data, colWidths=[40 * mm, 32 * mm], hAlign="RIGHT")
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, _LINE),
        ("BACKGROUND", (0, -1), (-1, -1), _HEAD_BG),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _footer(seller: Any, small: Any) -> Table:
    name = seller.name if seller else "Let's Try Foods Private Limited"
    declaration = (
        "Declaration: We certify that the particulars given above are true and correct, "
        "and that the amount indicated represents the price actually charged."
    )
    table = Table(
        [[
            Paragraph(declaration, small),
            Paragraph(
                f"For <b>{_esc(name)}</b><br/><br/><br/>Authorised Signatory",
                ParagraphStyle("sig", parent=small, alignment=TA_RIGHT),
            ),
        ]],
        colWidths=[110 * mm, 70 * mm],
    )
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _address_lines(address: Any) -> list[str]:
    """
    Turn `edi_purchase_orders.ship_to_address` into printable lines.

    That column is JSONB, and each parser fills a different subset of keys — Zepto sends
    one `line1` blob, other partners split city/state/pincode. Stringifying the dict puts
    a raw Python repr on the customer's tax invoice, so pick the known keys in postal
    order and quietly drop the rest (`name` and `gstin` are printed separately above).
    """
    if not address:
        return []
    if isinstance(address, str):
        return [_esc(address)]
    if not isinstance(address, dict):
        return [_esc(str(address))]

    lines: list[str] = []
    for key in ("line1", "line2", "street", "address_line", "block"):
        value = address.get(key)
        if value and str(value).strip():
            lines.append(_esc(str(value).strip()))

    locality = ", ".join(
        str(address[k]).strip()
        for k in ("city", "state", "zip_code", "postal_code", "pincode")
        if address.get(k) and str(address[k]).strip()
    )
    if locality:
        lines.append(_esc(locality))

    country = address.get("country")
    if country and str(country).strip():
        lines.append(_esc(str(country).strip()))
    return lines


def _esc(value: Any) -> str:
    """
    Escape for reportlab's mini-HTML.

    Product descriptions arrive from retailer feeds and routinely contain '&' —
    unescaped, reportlab treats it as an entity start and raises mid-render, so a
    single ampersand in an item name would otherwise fail the whole download.
    """
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
