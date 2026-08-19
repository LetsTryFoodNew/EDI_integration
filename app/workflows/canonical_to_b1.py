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


def push_po_to_b1(
    po_id: UUID,
    *,
    bpl_id: int | None = None,
    whs_code: str | None = None,
    ship_to_code: str | None = None,
    pay_to_code: str | None = None,
    allowed_statuses: tuple[Any, ...] | None = None,
) -> PushResult:
    """
    Push one VALIDATED PO to SAP B1 as a Sales Order.

    Idempotent: if b1_sales_order_doc_entry is already set, skips rather than creating
    a second document for the same retailer PO.

    The branch/warehouse arguments come from the operator's choice on the push dialog.
    Omitted, they fall back to whatever was saved on the PO by an earlier attempt; there
    is no "pick a sensible default" path, because the branch decides the tax treatment.
    """
    from sqlalchemy import select

    from app.db import SyncSessionLocal
    from app.mappers.po_to_sales_order import build_sales_order_payload
    from app.models._enums import PoStatus
    from app.models.edi_po import EdiPoLineItem, EdiPurchaseOrder
    from app.models.master_data import TradingPartner
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
        permitted = allowed_statuses or (PoStatus.VALIDATED, PoStatus.SAP_REJECTED)
        if po.po_status not in permitted:
            reason = (
                f"PO status is {str(po.po_status)!r}; this push accepts "
                f"{', '.join(str(s) for s in permitted)}."
            )
            return PushResult(
                success=False, po_id=po_id, skipped=True, skip_reason=reason, error=reason,
            )

        partner = session.get(TradingPartner, po.trading_partner_id)
        if not partner:
            return PushResult(success=False, po_id=po_id, error="Trading partner not found")

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

        # Which branch books it and which warehouse ships it. Resolved before the
        # status moves to SAP_PENDING so a bad selection never leaves a PO stuck
        # mid-push with nothing sent.
        try:
            dispatch = resolve_dispatch(
                session, po, bpl_id=bpl_id, whs_code=whs_code,
                ship_to_code=ship_to_code, pay_to_code=pay_to_code,
            )
        except DispatchError as exc:
            err = str(exc)
            _update_po_status(session, po, PoStatus.SAP_REJECTED, err)
            po.b1_error_message = err
            session.commit()
            return PushResult(success=False, po_id=po_id, error=err)

        # Remember the choice so a retry repeats it and the UI can show what was used.
        po.b1_bpl_id = dispatch.branch.bpl_id
        po.b1_whs_code = dispatch.warehouse_code
        po.b1_ship_to_code = dispatch.ship_to_code
        po.b1_pay_to_code = dispatch.pay_to_code

        # Mark as pending before the external call
        _update_po_status(session, po, PoStatus.SAP_PENDING, "Pushing to SAP B1")
        session.commit()

        # Build payload
        try:
            payload, build_warnings = build_sales_order_payload(
                po=po, lines=list(lines), partner=partner,
                branch=dispatch.branch, sku_mappings=sku_mappings,
                warehouse_code=dispatch.warehouse_code,
                ship_to_code=dispatch.ship_to_code,
                pay_to_code=dispatch.pay_to_code,
            )
            for w in build_warnings:
                log.warning("b1.payload_warning", po_id=str(po_id), warning=w)
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
                endpoint=f"{client.api_base}/Orders",
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


# ── Dispatch resolution (branch + warehouse + addresses) ─────────────────────


@dataclass
class Dispatch:
    """Where a PO is booked from, and where it goes. Resolved before every push."""

    branch: Any
    warehouse_code: str
    ship_to_code: str | None = None
    pay_to_code: str | None = None


class DispatchError(ValueError):
    """The branch/warehouse pairing cannot be resolved or is not valid in B1."""


def resolve_dispatch(
    session: Any,
    po: Any,
    *,
    bpl_id: int | None = None,
    whs_code: str | None = None,
    ship_to_code: str | None = None,
    pay_to_code: str | None = None,
) -> Dispatch:
    """
    Work out which branch books the order and which warehouse ships it.

    Explicit arguments win (the operator just chose them on screen), otherwise what was
    saved on the PO from a previous attempt is reused. There is deliberately **no
    fallback to "the first active branch"**: the branch is the from-state for place of
    supply, so guessing it picks a tax treatment, and a wrong guess misfiles GST rather
    than failing loudly.

    The warehouse is checked to belong to the branch, because B1 rejects a document
    whose warehouse and branch disagree — catching it here turns a Service Layer error
    into a sentence the operator can act on.
    """
    from sqlalchemy import select

    from app.models.master_data import BranchMaster, WarehouseMaster

    bpl_id = bpl_id if bpl_id is not None else po.b1_bpl_id
    whs_code = (whs_code or po.b1_whs_code or "").strip().upper() or None

    if bpl_id is None:
        raise DispatchError(
            "No branch selected. The branch decides CGST+SGST vs IGST, so it has to be "
            "chosen rather than assumed — pick one before pushing."
        )
    if not whs_code:
        raise DispatchError("No warehouse selected — pick one before pushing.")

    branch = session.execute(
        select(BranchMaster).where(
            BranchMaster.bpl_id == bpl_id,
            BranchMaster.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if branch is None:
        raise DispatchError(
            f"Branch BPLId {bpl_id} is not in Branch Master. Re-run the B1 org sync."
        )
    if branch.disabled:
        raise DispatchError(
            f"Branch {bpl_id} ({branch.bpl_name}) is disabled in SAP — B1 will not "
            f"accept a document booked against it."
        )
    if not branch.is_active:
        raise DispatchError(
            f"Branch {bpl_id} ({branch.bpl_name}) is parked locally: "
            f"{branch.notes or 'no reason recorded'}."
        )

    warehouse = session.execute(
        select(WarehouseMaster).where(
            WarehouseMaster.whs_code == whs_code,
            WarehouseMaster.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if warehouse is None:
        raise DispatchError(
            f"Warehouse {whs_code!r} is not in Warehouse Master. Re-run the B1 org sync."
        )
    if warehouse.branch_id != branch.id:
        raise DispatchError(
            f"Warehouse {whs_code} does not belong to branch {bpl_id} "
            f"({branch.bpl_name}). B1 rejects a document whose warehouse and branch "
            f"disagree — pick a warehouse under that branch."
        )
    if warehouse.inactive:
        raise DispatchError(f"Warehouse {whs_code} is inactive in SAP.")
    if not warehouse.is_active:
        raise DispatchError(
            f"Warehouse {whs_code} is parked locally: "
            f"{warehouse.notes or 'no reason recorded'}."
        )

    return Dispatch(
        branch=branch,
        warehouse_code=whs_code,
        ship_to_code=ship_to_code or po.b1_ship_to_code,
        pay_to_code=pay_to_code or po.b1_pay_to_code,
    )


def build_payload_preview(
    po_id: UUID,
    *,
    bpl_id: int | None = None,
    whs_code: str | None = None,
    ship_to_code: str | None = None,
    pay_to_code: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """
    Build the exact JSON that would be POSTed, without sending it.

    Backs the "Preview" step on the push dialog. Worth having as its own path: the
    payload is where a wrong branch shows up as a wrong VatGroup, and that is much
    cheaper to notice before the document exists than after.
    """
    from sqlalchemy import select

    from app.db import SyncSessionLocal
    from app.mappers.po_to_sales_order import build_sales_order_payload
    from app.models.edi_po import EdiPoLineItem, EdiPurchaseOrder
    from app.models.master_data import TradingPartner

    with SyncSessionLocal() as session:
        po = session.get(EdiPurchaseOrder, po_id)
        if not po:
            raise DispatchError("PO not found")
        partner = session.get(TradingPartner, po.trading_partner_id)
        if not partner:
            raise DispatchError("Trading partner not found")

        lines = list(session.execute(
            select(EdiPoLineItem)
            .where(EdiPoLineItem.po_id == po_id)
            .order_by(EdiPoLineItem.line_number)
        ).scalars().all())

        dispatch = resolve_dispatch(
            session, po, bpl_id=bpl_id, whs_code=whs_code,
            ship_to_code=ship_to_code, pay_to_code=pay_to_code,
        )
        return build_sales_order_payload(
            po=po, lines=lines, partner=partner, branch=dispatch.branch,
            sku_mappings=_load_sku_mappings(session, partner.id, lines),
            warehouse_code=dispatch.warehouse_code,
            ship_to_code=dispatch.ship_to_code,
            pay_to_code=dispatch.pay_to_code,
        )


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
    endpoint: str,
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
        endpoint=endpoint,
        request_body=payload,
        response_status=http_status,
        response_body=response,
        duration_ms=duration_ms,
        error_message=error,
    ))
