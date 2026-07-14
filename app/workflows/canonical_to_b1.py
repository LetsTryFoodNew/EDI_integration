"""
Canonical-to-B1 workflow — Phase 4 of the processing pipeline.

  EdiPurchaseOrder (status=VALIDATED) → B1 Sales Order (ORDR)
  → status SAP_CONFIRMED (on success) or SAP_REJECTED (on failure)

Always writes a B1ApiLog entry regardless of outcome.

Called by push_po_to_b1_job (RQ) which is triggered by the scheduler
for all VALIDATED POs every minute.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

log = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from uuid import UUID


@dataclass
class PushResult:
    success: bool
    po_id: UUID
    b1_doc_entry: int | None = None
    b1_doc_num: int | None = None
    error: str | None = None
    skipped: bool = False
    skip_reason: str = ""


def push_po_to_b1(po_id: UUID) -> PushResult:
    """
    Push one VALIDATED PO to SAP B1 as a Sales Order.
    Idempotent: if b1_sales_order_doc_entry is already set, skips.
    """
    from sqlalchemy import select

    from app.db import SyncSessionLocal
    from app.mappers.po_to_sales_order import build_sales_order_payload
    from app.models._enums import PoStatus
    from app.models.edi_po import EdiPoLineItem, EdiPurchaseOrder
    from app.models.master_data import SellerEntity, TradingPartner
    from app.sap_b1.client import get_b1_client
    from app.sap_b1.errors import B1ApiError

    with SyncSessionLocal() as session:
        po = session.get(EdiPurchaseOrder, po_id)
        if not po:
            return PushResult(success=False, po_id=po_id, error="PO not found")

        # Idempotency guard
        if po.b1_sales_order_doc_entry is not None:
            return PushResult(
                success=True,
                po_id=po_id,
                b1_doc_entry=po.b1_sales_order_doc_entry,
                b1_doc_num=po.b1_sales_order_doc_num,
                skipped=True,
                skip_reason="already pushed",
            )

        # Pre-flight status check
        if po.po_status not in (PoStatus.VALIDATED, PoStatus.SAP_REJECTED):
            return PushResult(
                success=False,
                po_id=po_id,
                skipped=True,
                skip_reason=f"PO status is {po.po_status!r}, expected VALIDATED",
            )

        partner = session.get(TradingPartner, po.trading_partner_id)
        seller = session.execute(
            select(SellerEntity).where(SellerEntity.deleted_at.is_(None)).limit(1)
        ).scalar_one_or_none()

        if not partner or not seller:
            return PushResult(success=False, po_id=po_id, error="Partner or Seller not found")

        lines = session.execute(
            select(EdiPoLineItem).where(EdiPoLineItem.po_id == po_id)
        ).scalars().all()

        # Pre-flight: all lines must have an ItemCode
        unmapped = [li for li in lines if not li.sap_material_no]
        if unmapped:
            skus = ", ".join(li.buyer_sku for li in unmapped)
            err = f"Lines with unmapped SKUs: {skus} — run validation first"
            _update_po_status(session, po, PoStatus.SAP_REJECTED, err)
            session.commit()
            return PushResult(success=False, po_id=po_id, error=err)

        # Load SKU mappings for UoM conversion
        sku_mappings = _load_sku_mappings(session, partner.id, lines)

        # Mark as pending before the external call
        _update_po_status(session, po, PoStatus.SAP_PENDING, "Pushing to SAP B1")
        session.commit()

        # Build payload
        try:
            payload = build_sales_order_payload(
                po=po, lines=list(lines), partner=partner,
                seller=seller, sku_mappings=sku_mappings,
            )
        except ValueError as exc:
            err = f"Payload build failed: {exc}"
            with SyncSessionLocal() as s2:
                po2 = s2.get(EdiPurchaseOrder, po_id)
                if po2:
                    _update_po_status(s2, po2, PoStatus.SAP_REJECTED, err)
                    s2.commit()
            return PushResult(success=False, po_id=po_id, error=err)

        # Call B1
        client = get_b1_client()
        t_start = time.monotonic()
        response: dict[str, Any] | None = None
        error_msg: str | None = None
        http_status = 0

        try:
            response = client.create_sales_order(payload)
            http_status = 201
        except B1ApiError as exc:
            error_msg = str(exc)
            http_status = exc.http_status
            log.error(
                "b1.push_failed",
                po_id=str(po_id),
                partner=partner.code,
                b1_code=exc.b1_code,
                error=error_msg,
            )
        except Exception as exc:
            error_msg = f"Unexpected error: {exc}"
            log.exception("b1.push_unexpected_error", po_id=str(po_id))

        duration_ms = int((time.monotonic() - t_start) * 1000)

        # Persist outcome
        with SyncSessionLocal() as s3:
            _write_b1_log(
                session=s3,
                po_id=po_id,
                operation="create_sales_order",
                payload=payload,
                response=response,
                error=error_msg,
                http_status=http_status,
                duration_ms=duration_ms,
            )

            po3 = s3.get(EdiPurchaseOrder, po_id)
            if po3 is None:
                s3.commit()
                return PushResult(success=False, po_id=po_id, error="PO disappeared")

            if response is not None:
                doc_entry = response.get("DocEntry")
                doc_num = response.get("DocNum")
                po3.b1_sales_order_doc_entry = int(doc_entry) if doc_entry is not None else None
                po3.b1_sales_order_doc_num = int(doc_num) if doc_num is not None else None
                po3.b1_pushed_at = datetime.now(UTC)
                po3.b1_error_message = None
                _update_po_status(s3, po3, PoStatus.SAP_CONFIRMED, "Sales Order created in B1")
                s3.commit()

                log.info(
                    "b1.push_ok",
                    po_id=str(po_id),
                    partner=partner.code,
                    doc_entry=doc_entry,
                    doc_num=doc_num,
                )
                return PushResult(
                    success=True,
                    po_id=po_id,
                    b1_doc_entry=int(doc_entry) if doc_entry is not None else None,
                    b1_doc_num=int(doc_num) if doc_num is not None else None,
                )
            else:
                po3.b1_error_message = error_msg
                _update_po_status(s3, po3, PoStatus.SAP_REJECTED, error_msg or "Unknown B1 error")
                s3.commit()
                return PushResult(success=False, po_id=po_id, error=error_msg)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _update_po_status(
    session: Any,
    po: Any,
    new_status: Any,
    notes: str,
) -> None:
    from app.models.edi_po import EdiPoStatusHistory
    old = po.po_status
    po.po_status = new_status
    if old != new_status:
        session.add(EdiPoStatusHistory(
            po_id=po.id,
            from_status=old,
            to_status=new_status,
            changed_by="sap_worker",
            notes=notes,
        ))


def _load_sku_mappings(session: Any, partner_id: UUID, lines: Any) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.master_data import SkuMapping

    buyer_skus = [li.buyer_sku for li in lines]
    rows = session.execute(
        select(SkuMapping).where(
            SkuMapping.trading_partner_id == partner_id,
            SkuMapping.buyer_sku.in_(buyer_skus),
            SkuMapping.deleted_at.is_(None),
        )
    ).scalars().all()
    return {m.buyer_sku: m for m in rows}


def _write_b1_log(
    session: Any,
    po_id: UUID,
    operation: str,
    payload: dict[str, Any],
    response: dict[str, Any] | None,
    error: str | None,
    http_status: int,
    duration_ms: int,
) -> None:
    from app.models.b1_log import B1ApiLog

    session.add(B1ApiLog(
        po_id=po_id,
        operation=operation,
        http_method="POST",
        endpoint="/b1s/v1/Orders",
        request_body=payload,
        response_status=http_status,
        response_body=response,
        duration_ms=duration_ms,
        error_message=error,
    ))
