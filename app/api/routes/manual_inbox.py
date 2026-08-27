"""
Manual Inbox routes — partners whose orders arrive by hand rather than by wire.

Covers two channels:
  MANUAL — no integration at all; orders arrive by phone / WhatsApp / paper and
           will be keyed in through the manual sales-order form (next phase).
  PORTAL — partners whose portal scraping (Phase 9) is not built yet. Until it
           is, their orders are effectively manual too, so they live here rather
           than being invisible in every inbox.

Message listing, detail, retry-parse and attachment download reuse the
/api/inbox/messages* routes, which are partner-scoped and channel-agnostic — a third
copy of that logic would drift.

GET  /api/manual-inbox/partners      — MANUAL + PORTAL partners with message counts
POST /api/manual-inbox/entries       — key in one purchase order by hand
GET  /api/manual-inbox/entries/{id}  — read one back for correction
GET  /api/manual-inbox/catalogue     — what this partner buys, for the line picker

A keyed-in order stays correctable because we authored it: a typo in a quantity is
ours to fix, unlike a partner's PO where our copy has to keep matching theirs. A
correction is filed as a **new revision** rather than an in-place edit — raw_messages
is the immutable record of what arrived, and the existing versioning supersedes the
previous one — so what was first typed survives alongside the fix.

Correcting stops the moment the order leaves the building: once it is in SAP, or has
an invoice or an ASN against it, a quiet edit here would leave our record disagreeing
with a document someone else is already acting on.

A keyed-in order is stored as a raw_message exactly like a webhook body, then parsed,
validated, mapped and pushed by the same pipeline. Nothing downstream can tell it
apart from a Blinkit webhook, which is the whole point: manual partners get SKU
mapping, the SAP push and the outbound 855/856 for free.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.models._enums import SourceChannel
from app.schemas.api import (
    InboxPartnerSummary,
    ManualCatalogueItem,
    ManualPoEntryDetail,
    ManualPoEntryRequest,
    ManualPoEntryResponse,
    PaginatedResponse,
    UserResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/manual-inbox", tags=["Manual Inbox"])

_MANUAL_CHANNELS = (SourceChannel.MANUAL, SourceChannel.PORTAL)


@router.get("/partners", response_model=PaginatedResponse[InboxPartnerSummary])
def list_manual_partners(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[InboxPartnerSummary]:
    """Return active MANUAL/PORTAL partners with per-partner message counts."""
    from sqlalchemy import func, select

    from app.models.master_data import TradingPartner
    from app.models.raw_messages import RawMessage

    base_q = select(TradingPartner).where(
        TradingPartner.is_active.is_(True),
        TradingPartner.deleted_at.is_(None),
        TradingPartner.source_channel.in_(_MANUAL_CHANNELS),
    )
    total = db.execute(select(func.count()).select_from(base_q.subquery())).scalar_one()
    partners = db.execute(
        base_q.order_by(TradingPartner.name).limit(limit).offset(offset)
    ).scalars().all()

    result: list[InboxPartnerSummary] = []
    for p in partners:
        counts = db.execute(
            select(
                func.count().label("total"),
                func.count().filter(RawMessage.parse_status == "PENDING").label("pending"),
                func.count().filter(RawMessage.parse_status == "FAILED").label("failed"),
                func.max(RawMessage.received_at).label("last_received_at"),
            ).where(RawMessage.trading_partner_id == p.id)
        ).one()

        result.append(InboxPartnerSummary(
            code=p.code,
            name=p.name,
            source_channel=str(p.source_channel),
            total=counts.total or 0,
            pending=counts.pending or 0,
            failed=counts.failed or 0,
            last_received_at=counts.last_received_at,
        ))

    return PaginatedResponse(items=result, total=total, limit=limit, offset=offset)


@router.post("/entries", response_model=ManualPoEntryResponse, status_code=201)
def create_manual_entry(
    entry: ManualPoEntryRequest,
    db: Session = Depends(get_sync_db),  # noqa: B008
    current_user: UserResponse = Depends(get_current_user),  # noqa: B008
) -> ManualPoEntryResponse:
    """
    Key in one purchase order for a partner with no integration.

    Stored verbatim as a raw_message, then handed to the same parse pipeline every
    other channel uses. The operator's keystrokes are never rewritten: raw_messages is
    the immutable record of what arrived, and the arithmetic — taxable amounts, the
    GST split, the totals — is derived in ManualEntryParser, so re-parsing the entry
    re-derives it rather than trusting a stored figure.
    """
    from sqlalchemy import func, select

    from app.models.master_data import SellerEntity, TradingPartner
    from app.models.raw_messages import RawMessage

    code = entry.partner_code.upper()
    partner = db.execute(
        select(TradingPartner).where(
            TradingPartner.code == code,
            TradingPartner.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if partner is None:
        raise HTTPException(status_code=404, detail=f"No trading partner {code!r}")
    if partner.source_channel not in _MANUAL_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{code} receives orders over {partner.source_channel}. Keying one in "
                f"by hand would create a second copy of an order that also arrives on "
                f"its own, so this is only allowed for manual and portal partners."
            ),
        )

    # The GST split needs both ends. Refusing here gives the operator the message on
    # the form they are looking at, rather than as a parse failure minutes later.
    if not (entry.ship_to.gstin or entry.ship_to.state or entry.buyer_gstin):
        raise HTTPException(
            status_code=422,
            detail=(
                "Enter the delivery state or a GSTIN — without one the CGST+SGST vs "
                "IGST split cannot be decided."
            ),
        )

    seller = db.execute(
        select(SellerEntity).where(SellerEntity.deleted_at.is_(None)).limit(1)
    ).scalar_one_or_none()
    if seller is None:
        raise HTTPException(
            status_code=409, detail="No seller entity configured — run seed_master_data.py"
        )

    # autoescape, because a PO number is free text and an underscore or percent in one
    # would otherwise match every other order.
    prior = db.execute(
        select(func.count())
        .select_from(RawMessage)
        .where(
            RawMessage.trading_partner_id == partner.id,
            RawMessage.external_id.startswith(
                _revision_prefix(entry.buyer_po_number), autoescape=True
            ),
        )
    ).scalar_one()
    if prior and not entry.replace_existing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"PO {entry.buyer_po_number} has already been entered for {code}. "
                f"Re-submit with 'replace_existing' to file this as a revision, which "
                f"supersedes the previous one."
            ),
        )

    if prior and entry.replace_existing:
        # A revision supersedes whatever is live under this number, so it must not be
        # filed once that order has left the building. Checked here as well as on the
        # read, because the form may have been open since before the SAP push.
        from app.models.edi_po import EdiPurchaseOrder

        live = db.execute(
            select(EdiPurchaseOrder)
            .where(
                EdiPurchaseOrder.trading_partner_id == partner.id,
                EdiPurchaseOrder.buyer_po_number == entry.buyer_po_number,
                EdiPurchaseOrder.deleted_at.is_(None),
            )
            .order_by(EdiPurchaseOrder.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        blocked = _locked_reason(db, live) if live is not None else None
        if blocked:
            raise HTTPException(status_code=409, detail=blocked)

    revision = prior + 1

    raw_id = uuid.uuid4()
    db.add(RawMessage(
        id=raw_id,
        trading_partner_id=partner.id,
        source_channel=partner.source_channel,
        external_id=_external_id(entry.buyer_po_number, revision),
        received_at=datetime.now(UTC),
        payload=_entry_payload(entry, code, seller, current_user, revision),
        processed=False,
        parse_status="PENDING",
    ))
    db.commit()

    queued = _enqueue_parse(raw_id)
    log.info(
        "manual.entry_created",
        partner=code,
        po_number=entry.buyer_po_number,
        revision=revision,
        lines=len(entry.line_items),
        raw_id=str(raw_id),
        by=current_user.email,
        queued=queued,
    )

    return ManualPoEntryResponse(
        raw_message_id=raw_id,
        partner_code=code,
        buyer_po_number=entry.buyer_po_number,
        revision=revision,
        queued=queued,
        message=(
            f"PO {entry.buyer_po_number} recorded"
            + (f" as revision {revision}" if revision > 1 else "")
            + (". Parsing now." if queued else
               ". The parse queue is unreachable — retry from the message when it is back.")
        ),
    )


def _revision_prefix(po_number: str) -> str:
    """Everything in the natural key that is the same across revisions of one order."""
    return f"manual:{po_number}:"


def _external_id(po_number: str, revision: int) -> str:
    """
    Natural key for a keyed-in order.

    raw_messages is unique on (partner, external_id), so the revision has to be part
    of it or a corrected order could never be entered. The `manual:` prefix keeps
    these from ever colliding with a partner's own message ids.
    """
    return f"{_revision_prefix(po_number)}{revision}"


def _entry_payload(
    entry: ManualPoEntryRequest,
    partner_code: str,
    seller: object,
    user: UserResponse,
    revision: int,
) -> dict:
    """
    The stored payload: what the operator typed, plus who typed it and the seller's
    place of supply.

    The seller's GSTIN and state are copied in rather than looked up at parse time
    on purpose — they decide the tax split, and reading them later would silently
    re-tax an old order if the seller entity were ever edited.
    """
    payload = entry.model_dump(mode="json", exclude={"replace_existing"})
    payload.update({
        "_entry_type": "MANUAL_PO",
        "partner_code": partner_code,
        "revision": revision,
        "seller_gstin": getattr(seller, "gstin", None),
        "seller_state": getattr(seller, "state", None),
        "entered_by": user.email,
        "entered_at": datetime.now(UTC).isoformat(),
    })
    return payload


def _enqueue_parse(raw_id: uuid.UUID) -> bool:
    """Hand the entry to the parse worker. False if the queue is unreachable."""
    try:
        from redis import Redis
        from rq import Queue

        from app.config import get_settings
        from app.workers.jobs import parse_raw_message_job

        queue = Queue("ingest", connection=Redis.from_url(get_settings().redis_url))
        queue.enqueue(parse_raw_message_job, str(raw_id), job_timeout=300)
        return True
    except Exception as exc:  # queue down must not lose the entry — it is already saved
        log.error("manual.enqueue_failed", raw_id=str(raw_id), error=str(exc))
        return False


def _locked_reason(db: Session, po: Any) -> str | None:
    """
    Why this keyed-in order can no longer be corrected, or None if it still can.

    The line is "has anyone outside this system acted on it yet". Up to the SAP push
    the order exists only here and a correction costs nothing. After it, a Sales Order,
    an invoice or an ASN is already someone else's record, and editing ours quietly
    would leave the two disagreeing with no trace of which is right.
    """
    from sqlalchemy import func, select

    from app.models.asn import EdiAdvanceShipNotice
    from app.models.invoice import EdiInvoice

    status = str(po.po_status)
    if status == "SUPERSEDED":
        return "This revision has been replaced by a newer one — edit that instead."
    if status == "CANCELLED":
        return "This order is cancelled."
    if po.b1_sales_order_doc_entry:
        return (
            f"Already in SAP as Sales Order {po.b1_sales_order_doc_num or po.b1_sales_order_doc_entry}. "
            "Correct it in SAP, or cancel there and key in a fresh order."
        )

    for model, what in ((EdiInvoice, "an invoice"), (EdiAdvanceShipNotice, "an ASN")):
        if db.execute(
            select(func.count()).select_from(model).where(model.po_id == po.id)
        ).scalar_one():
            return f"{what.capitalize()} has been raised against this order."

    return None


@router.get("/entries/{po_id}", response_model=ManualPoEntryDetail)
def read_manual_entry(
    po_id: uuid.UUID,
    db: Session = Depends(get_sync_db),  # noqa: B008
    _current_user: UserResponse = Depends(get_current_user),  # noqa: B008
) -> ManualPoEntryDetail:
    """
    Read a keyed-in order back in the shape it was entered, so it can be corrected.

    Returns the stored entry payload rather than the parsed PO. The entry is the thing
    the operator edits, and rebuilding a form from canonical line items would lose the
    GST rate they typed — the canonical document keeps the resulting split, not the
    rate that produced it.
    """
    from sqlalchemy import select

    from app.models.edi_po import EdiPurchaseOrder
    from app.models.master_data import TradingPartner
    from app.models.raw_messages import RawMessage
    from app.parsers.manual_parser import is_manual_entry

    po = db.get(EdiPurchaseOrder, po_id)
    if po is None or po.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    raw = db.get(RawMessage, po.raw_message_id) if po.raw_message_id else None
    if raw is None or not is_manual_entry(raw):
        raise HTTPException(
            status_code=400,
            detail=(
                "This order was not keyed in by hand, so there is no entry to edit. A "
                "partner's own PO has to keep matching the copy they hold."
            ),
        )

    partner = db.get(TradingPartner, po.trading_partner_id)
    payload = dict(raw.payload or {})
    revision = int(payload.get("revision") or 1)

    # Rebuild the request from the stored keystrokes, dropping the fields the route
    # added at entry time (seller GSTIN, who typed it) which are not the operator's to
    # edit and would be rejected by the schema's extra="forbid".
    entry = ManualPoEntryRequest.model_validate({
        "partner_code": payload.get("partner_code") or getattr(partner, "code", ""),
        "buyer_po_number": payload.get("buyer_po_number") or po.buyer_po_number,
        "line_items": payload.get("line_items") or [],
        "buyer_po_date": payload.get("buyer_po_date"),
        "requested_delivery_date": payload.get("requested_delivery_date"),
        "buyer_name": payload.get("buyer_name"),
        "buyer_gstin": payload.get("buyer_gstin"),
        "ship_to": payload.get("ship_to") or {},
        "currency": payload.get("currency") or "INR",
        "notes": payload.get("notes"),
    })

    # A superseded revision is not itself editable, but saying so is unhelpful when the
    # operator clicked Edit on the order: point at whichever revision is live.
    live = db.execute(
        select(EdiPurchaseOrder)
        .where(
            EdiPurchaseOrder.trading_partner_id == po.trading_partner_id,
            EdiPurchaseOrder.buyer_po_number == po.buyer_po_number,
            EdiPurchaseOrder.deleted_at.is_(None),
        )
        .order_by(EdiPurchaseOrder.version.desc())
        .limit(1)
    ).scalar_one_or_none() or po

    reason = _locked_reason(db, live)
    return ManualPoEntryDetail(
        po_id=live.id,
        partner_code=getattr(partner, "code", ""),
        partner_name=getattr(partner, "name", ""),
        revision=revision,
        po_status=str(live.po_status),
        editable=reason is None,
        locked_reason=reason,
        entry=entry,
    )


@router.get("/catalogue", response_model=PaginatedResponse[ManualCatalogueItem])
def manual_catalogue(
    partner_code: str = Query(..., description="Partner whose catalogue to search"),
    search: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_sync_db),  # noqa: B008
    _current_user: UserResponse = Depends(get_current_user),  # noqa: B008
) -> PaginatedResponse[ManualCatalogueItem]:
    """
    What this partner buys, for the line picker on the manual entry form.

    Their own SKU mappings come first and carry the things only the mapping knows —
    their buyer SKU, the contracted unit price, the UoM they order in — so choosing an
    item fills the line the way that partner actually buys it rather than with generic
    item data. Items with no mapping for this partner follow, because a hand-keyed
    order is often for something never sold to them before and refusing to show it
    would send the operator back to Master Data mid-entry.

    Ordering matters: a mapped row is the better answer whenever one exists, and
    putting the two groups in one response lets the picker stay a single list.
    """
    from sqlalchemy import func, or_, select

    from app.models.master_data import MaterialMaster, SkuMapping, TradingPartner

    code = partner_code.upper()
    partner = db.execute(
        select(TradingPartner).where(
            TradingPartner.code == code, TradingPartner.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if partner is None:
        raise HTTPException(status_code=404, detail=f"No trading partner {code!r}")

    pattern = f"%{search.strip()}%" if search and search.strip() else None

    mapped_q = (
        select(SkuMapping, MaterialMaster)
        .join(MaterialMaster, SkuMapping.material_id == MaterialMaster.id)
        .where(
            SkuMapping.trading_partner_id == partner.id,
            SkuMapping.deleted_at.is_(None),
            SkuMapping.is_active.is_(True),
            MaterialMaster.deleted_at.is_(None),
        )
    )
    if pattern:
        mapped_q = mapped_q.where(or_(
            SkuMapping.buyer_sku.ilike(pattern),
            SkuMapping.buyer_sku_description.ilike(pattern),
            MaterialMaster.item_code.ilike(pattern),
            MaterialMaster.item_name.ilike(pattern),
            MaterialMaster.ean_code.ilike(pattern),
        ))

    items: list[ManualCatalogueItem] = [
        _catalogue_row(material, mapping)
        for mapping, material in db.execute(
            mapped_q.order_by(MaterialMaster.item_name).limit(limit)
        ).all()
    ]
    seen = {row.b1_item_code for row in items}

    # Fill the rest of the page with unmapped items so one search covers both.
    remaining = limit - len(items)
    if remaining > 0:
        loose_q = select(MaterialMaster).where(
            MaterialMaster.deleted_at.is_(None),
            MaterialMaster.valid_for == 1,
            MaterialMaster.frozen_for.is_(False),
        )
        if pattern:
            loose_q = loose_q.where(or_(
                MaterialMaster.item_code.ilike(pattern),
                MaterialMaster.item_name.ilike(pattern),
                MaterialMaster.ean_code.ilike(pattern),
            ))
        if seen:
            loose_q = loose_q.where(MaterialMaster.item_code.notin_(seen))
        items += [
            _catalogue_row(m, None)
            for m in db.execute(
                loose_q.order_by(MaterialMaster.item_name).limit(remaining)
            ).scalars().all()
        ]

    total = db.execute(
        select(func.count()).select_from(mapped_q.subquery())
    ).scalar_one()
    return PaginatedResponse(items=items, total=max(total, len(items)), limit=limit, offset=0)


def _catalogue_row(material: Any, mapping: Any | None) -> ManualCatalogueItem:
    """
    Merge item data with this partner's mapping.

    The mapping wins where the two overlap, because it is the partner-specific fact:
    a contracted unit price for LOTS is not the price for anyone else, and the UoM
    they order in is theirs. Item data fills the rest — HSN, GST rate, MRP, EAN and
    case size are properties of the product whoever is buying it.
    """
    return ManualCatalogueItem(
        b1_item_code=material.item_code,
        item_name=material.item_name,
        mapped=mapping is not None,
        buyer_sku=getattr(mapping, "buyer_sku", None),
        buyer_uom=getattr(mapping, "buyer_uom", None) or material.sal_unit_msr or material.invntry_uom,
        unit_price=getattr(mapping, "unit_price", None),
        hsn_code=material.hsn,
        gst_rate=material.tax_rate,
        mrp=material.mrp,
        ean_code=material.ean_code,
        case_size=material.case_size,
    )
