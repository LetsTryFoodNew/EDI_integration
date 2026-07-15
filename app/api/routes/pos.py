"""
PO routes — Phase 8.

GET  /api/pos                     — list POs (paginated, filterable)
GET  /api/pos/{po_id}             — PO detail with lines, issues, B1 history, outbound msgs
POST /api/pos/{po_id}/retry-sap   — re-enqueue SAP push (SAP_REJECTED only)
POST /api/pos/{po_id}/cancel      — cancel a PO (PARSED/VALIDATED/EXCEPTION/SAP_REJECTED)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.schemas.api import (
    B1PushHistoryItem,
    OutboundMessageResponse,
    PaginatedResponse,
    POActionResponse,
    PODetail,
    POLineItemResponse,
    POListItem,
    POUpdateRequest,
    UserResponse,
    ValidationIssueResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/pos", tags=["POs"])

_CANCELLABLE_STATUSES = {"PARSED", "VALIDATED", "EXCEPTION", "SAP_REJECTED"}


@router.get("", response_model=PaginatedResponse[POListItem])
def list_pos(
    request: Request,
    partner_code: str | None = Query(None),
    po_status: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PaginatedResponse[POListItem]:
    from sqlalchemy import func, select

    from app.models.edi_po import EdiPoLineItem, EdiPurchaseOrder
    from app.models.master_data import TradingPartner
    from app.models.raw_messages import RawMessage

    # "Received" = when the PO email/webhook arrived, not when the parser
    # created the canonical record (which can be days later on re-parse).
    received_at = func.coalesce(RawMessage.received_at, EdiPurchaseOrder.created_at).label("received_at")

    q = (
        select(
            EdiPurchaseOrder,
            TradingPartner.code.label("partner_code"),
            TradingPartner.name.label("partner_name"),
            func.count(EdiPoLineItem.id).label("line_count"),
            received_at,
        )
        .join(TradingPartner, EdiPurchaseOrder.trading_partner_id == TradingPartner.id)
        .outerjoin(EdiPoLineItem, EdiPoLineItem.po_id == EdiPurchaseOrder.id)
        .outerjoin(RawMessage, RawMessage.id == EdiPurchaseOrder.raw_message_id)
        .where(EdiPurchaseOrder.deleted_at.is_(None))
        .group_by(EdiPurchaseOrder.id, TradingPartner.code, TradingPartner.name, RawMessage.received_at)
        .order_by(received_at.desc())
    )

    if partner_code:
        q = q.where(TradingPartner.code == partner_code)
    if po_status:
        from app.models._enums import PoStatus
        valid = {s.value for s in PoStatus}
        if po_status not in valid:
            raise HTTPException(status_code=400, detail=f"Invalid po_status '{po_status}'. Valid: {sorted(valid)}")
        q = q.where(EdiPurchaseOrder.po_status == po_status)
    if date_from:
        q = q.where(func.coalesce(RawMessage.received_at, EdiPurchaseOrder.created_at) >= date_from)
    if date_to:
        q = q.where(func.coalesce(RawMessage.received_at, EdiPurchaseOrder.created_at) <= date_to)
    if search:
        q = q.where(EdiPurchaseOrder.buyer_po_number.ilike(f"%{search}%"))

    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.limit(limit).offset(offset)).all()

    items = [
        POListItem(
            id=row.EdiPurchaseOrder.id,
            partner_code=row.partner_code,
            partner_name=row.partner_name,
            buyer_po_number=row.EdiPurchaseOrder.buyer_po_number,
            version=row.EdiPurchaseOrder.version,
            po_status=str(row.EdiPurchaseOrder.po_status),
            issue_date=row.EdiPurchaseOrder.buyer_po_date,
            grand_total=row.EdiPurchaseOrder.grand_total,
            currency=row.EdiPurchaseOrder.currency or "INR",
            line_count=row.line_count,
            b1_sales_order_doc_num=row.EdiPurchaseOrder.b1_sales_order_doc_num,
            received_at=row.received_at,
            created_at=row.EdiPurchaseOrder.created_at,
            updated_at=row.EdiPurchaseOrder.updated_at,
        )
        for row in rows
    ]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{po_id}", response_model=PODetail)
def get_po(
    po_id: uuid.UUID,
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> PODetail:
    from sqlalchemy import select

    from app.models.b1_log import B1ApiLog
    from app.models.edi_po import EdiPoLineItem, EdiPurchaseOrder, EdiValidationIssue
    from app.models.master_data import SellerEntity, TradingPartner
    from app.models.outbound import EdiOutboundMessage
    from app.models.raw_messages import RawMessage

    po = db.execute(
        select(EdiPurchaseOrder).where(
            EdiPurchaseOrder.id == po_id,
            EdiPurchaseOrder.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PO not found")

    partner = db.get(TradingPartner, po.trading_partner_id)
    seller = db.get(SellerEntity, po.seller_entity_id) if po.seller_entity_id else None
    raw_msg = db.get(RawMessage, po.raw_message_id) if po.raw_message_id else None
    lines = db.execute(
        select(EdiPoLineItem).where(EdiPoLineItem.po_id == po.id).order_by(EdiPoLineItem.line_number)
    ).scalars().all()
    issues = db.execute(
        select(EdiValidationIssue).where(EdiValidationIssue.po_id == po.id).order_by(EdiValidationIssue.created_at)
    ).scalars().all()
    b1_logs = db.execute(
        select(B1ApiLog).where(B1ApiLog.po_id == po.id).order_by(B1ApiLog.created_at.desc()).limit(50)
    ).scalars().all()
    outbound_msgs = db.execute(
        select(EdiOutboundMessage).where(EdiOutboundMessage.po_id == po.id).order_by(EdiOutboundMessage.created_at)
    ).scalars().all()

    return PODetail(
        id=po.id,
        partner_code=partner.code if partner else "",
        partner_name=partner.name if partner else "",
        buyer_po_number=po.buyer_po_number,
        version=po.version,
        po_status=str(po.po_status),
        source_channel=str(raw_msg.source_channel) if raw_msg else "",
        issue_date=po.buyer_po_date,
        delivery_date=po.requested_delivery_date,
        ship_to_code=po.ship_to_code,
        ship_to_name=po.ship_to_name,
        buyer_gstin=po.buyer_gstin,
        seller_gstin=seller.gstin if seller else None,
        grand_total=po.grand_total,
        currency=po.currency or "INR",
        b1_sales_order_doc_entry=po.b1_sales_order_doc_entry,
        b1_sales_order_doc_num=po.b1_sales_order_doc_num,
        raw_message_id=po.raw_message_id,
        created_at=po.created_at,
        updated_at=po.updated_at,
        lines=[
            POLineItemResponse(
                id=line.id,
                line_number=line.line_number,
                buyer_sku=line.buyer_sku,
                description=line.buyer_sku_description,
                ordered_qty=line.ordered_qty,
                uom=line.buyer_uom,
                unit_price=line.unit_price,
                line_total=line.line_total,
                taxable_amount=line.taxable_amount,
                cgst_amount=line.cgst_amount,
                sgst_amount=line.sgst_amount,
                igst_amount=line.igst_amount,
                hsn_code=line.hsn_code,
                sap_material_no=line.sap_material_no,
                mapping_status=(
                    str(line.sku_mapping.mapping_status) if line.sku_mapping else None
                ),
            )
            for line in lines
        ],
        validation_issues=[
            ValidationIssueResponse(
                id=i.id,
                issue_code=i.issue_code,
                severity=i.severity,
                field_name=i.field_path,
                message=i.message,
                resolution_note=i.resolution_notes,
                resolved_at=i.resolved_at,
                created_at=i.created_at,
            )
            for i in issues
        ],
        b1_push_history=[
            B1PushHistoryItem(
                id=log_entry.id,
                http_method=log_entry.http_method,
                endpoint=log_entry.endpoint,
                http_status=log_entry.http_status,
                success=log_entry.success,
                error_code=log_entry.error_code,
                error_message=log_entry.error_message,
                duration_ms=log_entry.duration_ms,
                created_at=log_entry.created_at,
            )
            for log_entry in b1_logs
        ],
        outbound_messages=[OutboundMessageResponse.model_validate(m) for m in outbound_msgs],
    )


@router.patch("/{po_id}", response_model=POActionResponse)
def update_po(
    po_id: uuid.UUID,
    body: POUpdateRequest,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> POActionResponse:
    """Manually correct PO header fields before pushing to SAP."""
    from datetime import date

    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.edi_po import EdiPurchaseOrder

    po = db.execute(
        select(EdiPurchaseOrder).where(EdiPurchaseOrder.id == po_id, EdiPurchaseOrder.deleted_at.is_(None))
    ).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if str(po.po_status) in ("SUPERSEDED", "CANCELLED", "SAP_CONFIRMED"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot edit PO with status '{po.po_status}' (read-only)",
        )

    changed: list[str] = []

    if body.buyer_po_number is not None:
        po.buyer_po_number = body.buyer_po_number
        changed.append("buyer_po_number")
    if body.buyer_po_date is not None:
        try:
            po.buyer_po_date = date.fromisoformat(body.buyer_po_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="buyer_po_date must be YYYY-MM-DD") from None
        changed.append("buyer_po_date")
    if body.buyer_name is not None:
        po.buyer_name = body.buyer_name
        changed.append("buyer_name")
    if body.buyer_gstin is not None:
        po.buyer_gstin = body.buyer_gstin
        changed.append("buyer_gstin")
    if body.ship_to_name is not None:
        po.ship_to_name = body.ship_to_name
        changed.append("ship_to_name")
    if body.ship_to_code is not None:
        po.ship_to_code = body.ship_to_code
        changed.append("ship_to_code")
    if body.requested_delivery_date is not None:
        try:
            po.requested_delivery_date = date.fromisoformat(body.requested_delivery_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="requested_delivery_date must be YYYY-MM-DD") from None
        changed.append("requested_delivery_date")
    if body.grand_total is not None:
        po.grand_total = float(body.grand_total)
        changed.append("grand_total")
    if body.currency is not None:
        po.currency = body.currency
        changed.append("currency")

    if not changed:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    db.add(AuditLog(
        user_email=current_user.email,
        action="update_po",
        entity_type="EdiPurchaseOrder",
        entity_id=str(po_id),
        payload={"fields": changed},
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    log.info("pos.updated", po_id=str(po_id), fields=changed, user=current_user.email)
    return POActionResponse(success=True, message=f"Updated: {', '.join(changed)}", po_id=po_id)


@router.post("/{po_id}/revalidate", response_model=POActionResponse)
def revalidate_po(
    po_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> POActionResponse:
    """
    Re-run the validation engine on this PO after ops has fixed data
    (edited PO fields, mapped a SKU, etc.). Runs synchronously — open issues
    are deleted and recomputed; status moves to VALIDATED or EXCEPTION.
    Not allowed once the PO has been sent to SAP.
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.edi_po import EdiPurchaseOrder
    from app.workflows.validate_po import validate_po

    _REVALIDATABLE = {"PARSED", "VALIDATED", "EXCEPTION"}

    po = db.execute(
        select(EdiPurchaseOrder).where(EdiPurchaseOrder.id == po_id, EdiPurchaseOrder.deleted_at.is_(None))
    ).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if str(po.po_status) not in _REVALIDATABLE:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot re-validate PO with status '{po.po_status}' (already sent to SAP)",
        )

    db.add(AuditLog(
        user_email=current_user.email,
        action="revalidate_po",
        entity_type="EdiPurchaseOrder",
        entity_id=str(po_id),
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()

    result = validate_po(po_id)  # opens its own session
    if not result.success:
        raise HTTPException(status_code=500, detail="; ".join(result.errors) or "Validation failed")

    log.info("pos.revalidated", po_id=str(po_id), status=result.status, user=current_user.email)
    return POActionResponse(
        success=True,
        message=(
            f"Re-validated: status {result.status} — "
            f"{result.error_count} error(s), {result.warning_count} warning(s)"
        ),
        po_id=po_id,
    )


@router.post("/{po_id}/push-to-sap", response_model=POActionResponse)
def push_to_sap(
    po_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> POActionResponse:
    """
    Manually trigger a SAP B1 Sales Order push for this PO.
    The PO must be in PARSED, VALIDATED, or EXCEPTION status.
    SAP push is handled by the sap_push worker queue.
    """
    from sqlalchemy import func, select

    from app.models._enums import PoStatus, ValidationStatus
    from app.models.audit_log import AuditLog
    from app.models.edi_po import EdiPurchaseOrder, EdiValidationIssue

    _PUSHABLE = {"PARSED", "VALIDATED", "EXCEPTION", "SAP_REJECTED"}

    po = db.execute(
        select(EdiPurchaseOrder).where(EdiPurchaseOrder.id == po_id, EdiPurchaseOrder.deleted_at.is_(None))
    ).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")

    current_status = str(po.po_status)
    if current_status == "SAP_CONFIRMED":
        raise HTTPException(status_code=400, detail="PO already confirmed in SAP")
    if current_status not in _PUSHABLE:
        raise HTTPException(status_code=400, detail=f"Cannot push PO with status '{current_status}'")

    # Pre-flight: every ERROR-severity issue must be resolved before B1 push
    open_errors = db.execute(
        select(func.count()).select_from(EdiValidationIssue).where(
            EdiValidationIssue.po_id == po_id,
            EdiValidationIssue.validation_status == ValidationStatus.OPEN,
            EdiValidationIssue.severity == "ERROR",
        )
    ).scalar_one()
    if open_errors:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot push to SAP: {open_errors} unresolved validation error(s). Resolve them first.",
        )

    # Move to SAP_PENDING so the worker picks it up
    po.po_status = PoStatus.SAP_PENDING
    db.add(AuditLog(
        user_email=current_user.email,
        action="push_to_sap",
        entity_type="EdiPurchaseOrder",
        entity_id=str(po_id),
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()

    try:
        import redis as redis_lib
        from rq import Queue

        from app.config import get_settings
        from app.workers.jobs import push_po_to_b1_job

        redis_conn = redis_lib.from_url(get_settings().redis_url)
        Queue("sap_push", connection=redis_conn).enqueue(push_po_to_b1_job, str(po_id), job_timeout=300)
        log.info("pos.push_to_sap_queued", po_id=str(po_id), user=current_user.email)
    except Exception as exc:
        log.error("pos.push_to_sap_enqueue_failed", po_id=str(po_id), error=str(exc))

    return POActionResponse(success=True, message="SAP push queued", po_id=po_id)


@router.post("/{po_id}/retry-sap", response_model=POActionResponse)
def retry_sap_push(
    po_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> POActionResponse:
    from sqlalchemy import select

    from app.models._enums import PoStatus
    from app.models.audit_log import AuditLog
    from app.models.edi_po import EdiPurchaseOrder

    po = db.execute(
        select(EdiPurchaseOrder).where(EdiPurchaseOrder.id == po_id, EdiPurchaseOrder.deleted_at.is_(None))
    ).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if str(po.po_status) != "SAP_REJECTED":
        raise HTTPException(status_code=400, detail=f"Can only retry SAP_REJECTED POs (current: {po.po_status})")

    po.po_status = PoStatus.VALIDATED
    db.add(AuditLog(
        user_email=current_user.email,
        action="retry_sap_push",
        entity_type="EdiPurchaseOrder",
        entity_id=str(po_id),
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()

    # Enqueue immediately
    import redis as redis_lib
    from rq import Queue

    from app.config import get_settings
    from app.workers.jobs import push_po_to_b1_job
    redis_conn = redis_lib.from_url(get_settings().redis_url)
    Queue("sap_push", connection=redis_conn).enqueue(push_po_to_b1_job, str(po_id), job_timeout=300)
    log.info("pos.retry_sap", po_id=str(po_id), user=current_user.email)
    return POActionResponse(success=True, message="SAP push re-queued", po_id=po_id)


@router.post("/{po_id}/cancel", response_model=POActionResponse)
def cancel_po(
    po_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> POActionResponse:
    from sqlalchemy import select

    from app.models._enums import PoStatus
    from app.models.audit_log import AuditLog
    from app.models.edi_po import EdiPoStatusHistory, EdiPurchaseOrder

    po = db.execute(
        select(EdiPurchaseOrder).where(EdiPurchaseOrder.id == po_id, EdiPurchaseOrder.deleted_at.is_(None))
    ).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if str(po.po_status) not in _CANCELLABLE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Cannot cancel PO with status {po.po_status}")

    # Status change, NOT soft-delete — a cancelled PO must stay visible in the dashboard
    old_status = po.po_status
    po.po_status = PoStatus.CANCELLED
    db.add(EdiPoStatusHistory(
        po_id=po_id,
        from_status=old_status,
        to_status=PoStatus.CANCELLED,
        changed_by=current_user.email,
        notes="Cancelled via dashboard",
    ))
    db.add(AuditLog(
        user_email=current_user.email,
        action="cancel_po",
        entity_type="EdiPurchaseOrder",
        entity_id=str(po_id),
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    log.info("pos.cancelled", po_id=str(po_id), user=current_user.email)
    return POActionResponse(success=True, message="PO cancelled", po_id=po_id)
