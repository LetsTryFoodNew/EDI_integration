"""
Parse-and-persist workflow — phase 2 of the processing pipeline:
  RawMessage → EDI850 (Pydantic) → edi_purchase_orders + edi_po_line_items (DB)

On success:
  raw_message.parse_status = "SUCCESS", raw_message.processed = True
  EdiPurchaseOrder.po_status = PARSED, or the parser's own terminal status when it
  reports the partner has already killed the order (Zepto EXPIRED → CANCELLED)

Skipped:
  A terminal-status document for a PO number we have never held is a notice about
  someone else's order, not an order. raw_message.parse_status = "SKIPPED", no PO row.

On failure:
  EdiValidationIssue with code E000_PARSE_FAILED written to DB
  raw_message.parse_status = "FAILED", raw_message.processed = True
  EdiPurchaseOrder.po_status = EXCEPTION  (minimal placeholder PO created)

LLM fallback is tried when:
  - no structured parser exists OR the structured parser fails
  - AND the partner's api_config has {"llm_fallback_enabled": true}
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from app.models._enums import PoStatus

log = structlog.get_logger(__name__)

@dataclass
class PersistResult:
    success: bool
    po_id: uuid.UUID | None = None
    partner_code: str = ""
    buyer_po_number: str = ""
    error: str | None = None


def parse_and_persist(raw_message_id: uuid.UUID) -> PersistResult:
    """
    Entry point for the parse RQ job.
    Loads RawMessage, selects a parser, calls it, and persists results.
    """
    from app.db import SyncSessionLocal
    from app.models.master_data import TradingPartner
    from app.models.raw_messages import RawMessage

    with SyncSessionLocal() as session:
        raw: RawMessage | None = session.get(RawMessage, raw_message_id)
        if not raw:
            log.error("parse.raw_message_not_found", raw_id=str(raw_message_id))
            return PersistResult(success=False, error="RawMessage not found")

        partner: TradingPartner | None = session.get(TradingPartner, raw.trading_partner_id)
        if not partner:
            log.error("parse.partner_not_found", raw_id=str(raw_message_id))
            return PersistResult(success=False, error="TradingPartner not found")

        # Remove placeholder POs from a previous failed attempt so we don't create duplicates
        _cleanup_placeholder_pos(session, raw_message_id)

        # Re-parse of a message that already produced a real PO → link, don't duplicate
        existing_own = _find_po_for_raw_message(session, raw_message_id)
        if existing_own is not None:
            raw.parse_status = "SUCCESS"
            raw.processed = True
            session.commit()
            log.info(
                "parse.already_parsed",
                raw_id=str(raw_message_id),
                po_id=str(existing_own.id),
                po_number=existing_own.buyer_po_number,
            )
            return PersistResult(
                success=True,
                po_id=existing_own.id,
                partner_code=partner.code,
                buyer_po_number=existing_own.buyer_po_number,
            )

        result = _run_parser(raw, partner)

        if result.success and result.doc and _is_orphan_terminal_notice(session, result.doc, partner):
            raw.parse_status = "SKIPPED"
            raw.processed = True
            session.commit()
            log.info(
                "parse.orphan_terminal_notice",
                partner=partner.code,
                po_number=result.doc.buyer_po_number,
                status=str(result.doc.po_status),
            )
            return PersistResult(
                success=True,
                partner_code=partner.code,
                buyer_po_number=result.doc.buyer_po_number,
            )

        if result.success and result.doc:
            try:
                po = _save_canonical_po(session, result.doc, raw, partner)
                raw.parse_status = "SUCCESS"
                raw.processed = True
                session.commit()
                log.info(
                    "parse.success",
                    partner=partner.code,
                    po_number=result.doc.buyer_po_number,
                    po_id=str(po.id),
                    parser=result.parser_name,
                )
                if po.po_status not in _TERMINAL_PO_STATUSES:
                    _enqueue_validate(po.id)
                return PersistResult(
                    success=True,
                    po_id=po.id,
                    partner_code=partner.code,
                    buyer_po_number=result.doc.buyer_po_number,
                )
            except Exception as exc:
                session.rollback()
                # Duplicate PO number for same partner — link to existing PO and move on
                if _is_duplicate_key_error(exc):
                    existing = _find_existing_po(session, partner.id, result.doc.buyer_po_number)
                    if existing:
                        # rollback above undid the placeholder cleanup — redo it
                        _cleanup_placeholder_pos(session, raw_message_id)
                        raw.parse_status = "SUCCESS"
                        raw.processed = True
                        session.commit()
                        log.info(
                            "parse.duplicate_po_linked",
                            partner=partner.code,
                            po_number=result.doc.buyer_po_number,
                            existing_po_id=str(existing.id),
                        )
                        return PersistResult(
                            success=True,
                            po_id=existing.id,
                            partner_code=partner.code,
                            buyer_po_number=result.doc.buyer_po_number,
                        )
                log.exception("parse.db_write_error", partner=partner.code, error=str(exc))
                result.success = False
                result.errors = [f"DB write failed: {exc}"]

        # Failure path — save a placeholder PO + validation issue
        try:
            po_number = (
                (result.doc.buyer_po_number if result.doc else None)
                or _best_effort_po_number(raw)
            )
            po = _save_failed_po(session, po_number, result.errors, raw, partner)
            raw.parse_status = "FAILED"
            raw.processed = True
            session.commit()
            log.warning(
                "parse.failed",
                partner=partner.code,
                po_number=po_number,
                errors=result.errors,
                parser=result.parser_name,
            )
            return PersistResult(
                success=False,
                po_id=po.id,
                partner_code=partner.code,
                buyer_po_number=po_number,
                error="; ".join(result.errors),
            )
        except Exception as exc:
            session.rollback()
            log.exception("parse.failure_write_error", partner=partner.code, error=str(exc))
            return PersistResult(success=False, error=str(exc), partner_code=partner.code)


# ── Cleanup helpers ───────────────────────────────────────────────────────────

def _cleanup_placeholder_pos(session: Any, raw_message_id: uuid.UUID) -> None:
    """Delete any PARSE_FAIL_ placeholder POs for this raw message before re-parsing."""
    from sqlalchemy import delete, select

    from app.models.edi_po import EdiPurchaseOrder, EdiValidationIssue

    stmt = select(EdiPurchaseOrder).where(
        EdiPurchaseOrder.raw_message_id == raw_message_id,
        EdiPurchaseOrder.buyer_po_number.like("PARSE_FAIL_%"),
    )
    placeholders = session.execute(stmt).scalars().all()
    for po in placeholders:
        session.execute(delete(EdiValidationIssue).where(EdiValidationIssue.po_id == po.id))
        session.delete(po)
    if placeholders:
        session.flush()
        log.info("parse.cleaned_placeholder_pos", count=len(placeholders), raw_id=str(raw_message_id))


def _is_duplicate_key_error(exc: Exception) -> bool:
    from psycopg2.errors import UniqueViolation
    from sqlalchemy.exc import IntegrityError
    if isinstance(exc, IntegrityError):
        orig = getattr(exc, "orig", None)
        return isinstance(orig, UniqueViolation)
    return False


def _find_existing_po(session: Any, trading_partner_id: uuid.UUID, buyer_po_number: str) -> Any:
    from sqlalchemy import select

    from app.models.edi_po import EdiPurchaseOrder
    return session.execute(
        select(EdiPurchaseOrder).where(
            EdiPurchaseOrder.trading_partner_id == trading_partner_id,
            EdiPurchaseOrder.buyer_po_number == buyer_po_number,
            EdiPurchaseOrder.deleted_at.is_(None),
        ).order_by(EdiPurchaseOrder.version.desc()).limit(1)
    ).scalar_one_or_none()


def _find_po_for_raw_message(session: Any, raw_message_id: uuid.UUID) -> Any:
    """Return the real (non-placeholder) PO already created from this raw message, if any."""
    from sqlalchemy import select

    from app.models.edi_po import EdiPurchaseOrder
    return session.execute(
        select(EdiPurchaseOrder).where(
            EdiPurchaseOrder.raw_message_id == raw_message_id,
            EdiPurchaseOrder.deleted_at.is_(None),
            ~EdiPurchaseOrder.buyer_po_number.like("PARSE_FAIL_%"),
        ).limit(1)
    ).scalar_one_or_none()


# ── Parser selection ──────────────────────────────────────────────────────────

def _run_parser(raw: Any, partner: Any) -> Any:
    from app.parsers.manual_parser import ManualEntryParser, is_manual_entry
    from app.parsers.registry import get_parser

    # A hand-keyed PO is routed on its payload, not its partner. Every manual partner
    # produces the same shape, so one parser covers all of them and onboarding a new
    # one needs no code — but a partner that also has a wire (a Blinkit order phoned
    # in during an outage, say) must not be handed to its webhook parser.
    if is_manual_entry(raw):
        log.debug("parse.using_parser", parser="ManualEntryParser", partner=partner.code)
        return ManualEntryParser().parse(raw)

    parser = get_parser(partner.code)
    if parser and parser.can_parse(raw):
        log.debug("parse.using_parser", parser=type(parser).__name__, partner=partner.code)
        result = parser.parse(raw)
        if result.success:
            return result
        log.warning(
            "parse.structured_failed",
            partner=partner.code,
            errors=result.errors,
        )

    # Try LLM fallback if enabled for this partner
    if _llm_fallback_enabled(partner):
        log.info("parse.trying_llm_fallback", partner=partner.code)
        from app.parsers.llm_fallback import LlmFallbackParser
        fallback = LlmFallbackParser()
        if fallback.can_parse(raw):
            return fallback.parse(raw)

    # No parser available or all parsers failed — return last result or generic error
    from app.parsers.base import ParseResult
    if parser:
        # Re-run to get the actual errors from the structured parser
        return parser.parse(raw)
    return ParseResult(
        success=False,
        errors=[f"No parser registered for partner '{partner.code}'"],
        parser_name="none",
    )


def _llm_fallback_enabled(partner: Any) -> bool:
    api_config = getattr(partner, "api_config", None) or {}
    return bool(api_config.get("llm_fallback_enabled"))


# ── DB persistence ────────────────────────────────────────────────────────────

# A PO in one of these is over: nothing further is validated, pushed or shipped.
_TERMINAL_PO_STATUSES: frozenset[PoStatus] = frozenset({PoStatus.CANCELLED, PoStatus.SUPERSEDED})


def _persisted_status(doc: Any) -> PoStatus:
    """
    The status a freshly-parsed document should be stored with.

    This used to be hardcoded to PARSED, which silently threw away the one thing a
    parser can tell us that nothing downstream can work out for itself: that the
    partner has already declared this order dead. ZeptoParser sets CANCELLED for an
    `EXPIRED` PO, validate_po refuses to touch a CANCELLED PO -- and in between, this
    function overwrote it with PARSED, so 533 expired Zepto POs were validated anyway
    and landed in the exceptions queue asking ops to map SKUs for orders nobody would
    ever ship.

    Only terminal statuses are honoured. Everything else becomes PARSED regardless of
    what the doc carries, because EDI850.po_status defaults to RECEIVED and most
    parsers never set it -- a doc's non-terminal status is silence, not an opinion.
    """
    status = getattr(doc, "po_status", None)
    return status if status in _TERMINAL_PO_STATUSES else PoStatus.PARSED


def _is_orphan_terminal_notice(session: Any, doc: Any, partner: Any) -> bool:
    """
    True when this document is a partner telling us an order we never received is over.

    Zepto emits an `UpdatePO` event with `status: EXPIRED` when a PO passes its
    expiryDate -- a nightly sweep, stamped 18:30:00Z (midnight IST). Those events sit
    in the same feed as real POs, and the poll window reaches back far enough (up to
    Zepto's 45-day cap) to pick up expiries for orders raised long before we started
    polling, or for a vendor's whole back-catalogue in one go: 210 of them arrived
    stamped 16 Aug alone.

    Each one used to become a *new purchase order* at version 1, because
    `_find_existing_po` found nothing to supersede. 303 of the 540 Zepto POs on the
    server exist for no other reason -- born already dead, from a notice about an
    order we had never held. That is what made a day with four real POs read as
    twenty-eight.

    An expiry notice against a PO we *do* hold is different and still processed
    normally: it supersedes the live version and closes it out, which is the whole
    point of receiving it.
    """
    if doc.po_status not in _TERMINAL_PO_STATUSES:
        return False
    return _find_existing_po(session, partner.id, doc.buyer_po_number) is None


_REVISION_WINDOW_DAYS = 25  # same PO number within this window = revision of the same PO


def _resolve_version(session: Any, doc: Any, raw: Any, partner: Any) -> int:
    """
    Handle re-issued (revised) POs.

    If a PO with the same (partner, buyer_po_number) already exists:
      - created within the last 25 days → this email is a REVISION:
        new PO gets version+1 and the previous version is marked SUPERSEDED.
      - older than 25 days → unrelated PO that reuses the number:
        version still bumps (unique constraint) but the old PO stays active.
    Returns the version number to use for the new PO.
    """
    from datetime import UTC, datetime, timedelta

    from app.models.edi_po import EdiPoStatusHistory

    existing = _find_existing_po(session, partner.id, doc.buyer_po_number)
    if existing is None:
        return 1

    new_version = existing.version + 1
    cutoff = datetime.now(UTC) - timedelta(days=_REVISION_WINDOW_DAYS)
    is_revision = existing.created_at >= cutoff

    if is_revision and existing.po_status not in _TERMINAL_PO_STATUSES:
        old_status = existing.po_status
        existing.po_status = PoStatus.SUPERSEDED
        session.add(EdiPoStatusHistory(
            po_id=existing.id,
            from_status=old_status,
            to_status=PoStatus.SUPERSEDED,
            changed_by="parser",
            notes=f"Superseded by revised PO version {new_version} (gmail {raw.external_id})",
        ))
        log.info(
            "parse.po_superseded",
            po_number=doc.buyer_po_number,
            old_version=existing.version,
            new_version=new_version,
            partner=partner.code,
        )
    return new_version


def _save_canonical_po(session: Any, doc: Any, raw: Any, partner: Any) -> Any:
    """Write a successfully-parsed EDI850 to edi_purchase_orders + lines."""
    from sqlalchemy import select

    from app.models.edi_po import EdiPoLineItem, EdiPurchaseOrder
    from app.models.master_data import SellerEntity

    seller = session.execute(
        select(SellerEntity).where(SellerEntity.deleted_at.is_(None)).limit(1)
    ).scalar_one_or_none()
    if not seller:
        raise RuntimeError("No SellerEntity in DB — run seed_master_data.py")

    version = _resolve_version(session, doc, raw, partner)

    po = EdiPurchaseOrder(
        id=doc.id,
        correlation_id=doc.correlation_id,
        trading_partner_id=partner.id,
        seller_entity_id=seller.id,
        raw_message_id=raw.id,
        buyer_po_number=doc.buyer_po_number,
        buyer_po_date=doc.buyer_po_date,
        version=version,
        po_status=_persisted_status(doc),
        ship_to_code=doc.ship_to.warehouse_code if doc.ship_to else None,
        ship_to_name=doc.ship_to.name if doc.ship_to else None,
        ship_to_address=doc.ship_to.model_dump(exclude_none=True) if doc.ship_to else None,
        requested_delivery_date=doc.requested_delivery_date,
        buyer_gstin=doc.buyer_gstin,
        buyer_name=doc.buyer_name,
        currency=doc.currency,
        subtotal_amount=float(doc.subtotal_amount) if doc.subtotal_amount else None,
        cgst_amount=float(doc.cgst_amount) if doc.cgst_amount else None,
        sgst_amount=float(doc.sgst_amount) if doc.sgst_amount else None,
        igst_amount=float(doc.igst_amount) if doc.igst_amount else None,
        grand_total=float(doc.grand_total) if doc.grand_total else None,
    )
    session.add(po)
    session.flush()  # get po.id before adding lines

    for line in doc.line_items:
        session.add(EdiPoLineItem(
            id=uuid.uuid4(),
            po_id=po.id,
            line_number=line.line_number,
            buyer_sku=line.buyer_sku,
            buyer_sku_description=line.buyer_sku_description,
            hsn_code=line.hsn_code,
            ordered_qty=float(line.ordered_qty),
            buyer_uom=line.buyer_uom,
            unit_price=float(line.unit_price) if line.unit_price else None,
            taxable_amount=float(line.taxable_amount) if line.taxable_amount else None,
            cgst_rate=float(line.cgst_rate) if line.cgst_rate else None,
            cgst_amount=float(line.cgst_amount) if line.cgst_amount else None,
            sgst_rate=float(line.sgst_rate) if line.sgst_rate else None,
            sgst_amount=float(line.sgst_amount) if line.sgst_amount else None,
            igst_rate=float(line.igst_rate) if line.igst_rate else None,
            igst_amount=float(line.igst_amount) if line.igst_amount else None,
            line_total=float(line.line_total) if line.line_total else None,
            # Only ever set by a hand-keyed order, where the operator picked the item
            # out of the material master. Dropping it here sent the line to validation
            # looking unmapped, so an operator's explicit choice was silently discarded
            # and the PO came back E002_SKU_UNRESOLVED anyway.
            sap_material_no=line.sap_material_no,
        ))

    return po


def _save_failed_po(
    session: Any,
    po_number: str,
    errors: list[str],
    raw: Any,
    partner: Any,
) -> Any:
    """
    Create a minimal placeholder PO (status=EXCEPTION) + EdiValidationIssue
    so the ops team can see the failure in the exception queue.
    """
    from sqlalchemy import select

    from app.models._enums import ValidationStatus
    from app.models.edi_po import EdiPurchaseOrder, EdiValidationIssue
    from app.models.master_data import SellerEntity

    seller = session.execute(
        select(SellerEntity).where(SellerEntity.deleted_at.is_(None)).limit(1)
    ).scalar_one_or_none()
    if not seller:
        raise RuntimeError("No SellerEntity in DB — run seed_master_data.py")

    po = EdiPurchaseOrder(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        trading_partner_id=partner.id,
        seller_entity_id=seller.id,
        raw_message_id=raw.id,
        buyer_po_number=po_number,
        po_status=PoStatus.EXCEPTION,
    )
    session.add(po)
    session.flush()

    session.add(EdiValidationIssue(
        id=uuid.uuid4(),
        po_id=po.id,
        issue_code="E000_PARSE_FAILED",
        severity="ERROR",
        message="; ".join(errors) or "Parse failed with no error message",
        validation_status=ValidationStatus.OPEN,
    ))

    return po


def _enqueue_validate(po_id: uuid.UUID) -> None:
    """Enqueue the validate_po_job after a successful parse."""
    try:
        from redis import Redis
        from rq import Queue

        from app.config import get_settings
        from app.workers.jobs import validate_po_job

        redis_conn = Redis.from_url(get_settings().redis_url)
        Queue("ingest", connection=redis_conn).enqueue(
            validate_po_job,
            str(po_id),
            job_timeout=300,
        )
        log.debug("parse.validate_enqueued", po_id=str(po_id))
    except Exception as exc:
        log.error("parse.validate_enqueue_error", po_id=str(po_id), error=str(exc))


def _best_effort_po_number(raw: Any) -> str:
    """Try to extract a PO number from the raw message payload for the failure record."""
    payload = getattr(raw, "payload", None) or {}
    for key in ("po_number", "purchaseOrderNumber", "order_id", "reference"):
        val = payload.get(key)
        if val:
            return str(val)
    return f"PARSE_FAIL_{str(getattr(raw, 'id', uuid.uuid4()))[:8].upper()}"
