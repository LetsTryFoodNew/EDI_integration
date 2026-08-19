"""
SAP push routes that need a branch / warehouse decision — Phase 6.

A B1 Sales Order carries two values our canonical PO does not: the branch it is booked
against (`BPL_IDAssignedToInvoice`) and the warehouse it ships from (`WarehouseCode`).
Neither can be derived from the retailer's document, because both describe our side of
the trade.

The branch matters more than it looks. Under the India localization it is the **from**
state for place of supply, so it decides whether the order is taxed CGST+SGST or IGST.
Booking a Maharashtra order against the Haryana branch produces a valid-looking document
with the wrong tax code, the wrong ledger and the wrong GST return — a mistake that
surfaces at filing time, not at push time. So the operator chooses, the UI shows the tax
consequence of each branch up front, and nothing is defaulted.

  GET  /api/pos/{id}/dispatch-options  — branches, their warehouses, B1 addresses
  POST /api/pos/{id}/preview-sap       — build the exact payload, send nothing
  POST /api/pos/{id}/push-to-sap-with  — push using an explicit selection

Split from pos.py only to keep that module a readable size; same prefix and tag.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_sync_db
from app.api.routes.auth import get_current_user
from app.schemas.api import (
    B1AddressOption,
    BranchOption,
    DispatchOptionsResponse,
    POActionResponse,
    SapPreviewResponse,
    SapPushRequest,
    UserResponse,
    WarehouseOption,
)
from app.utils.gst import is_interstate, resolve_state

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/pos", tags=["POs"])

_PUSHABLE = {"PARSED", "VALIDATED", "EXCEPTION", "SAP_REJECTED"}


def _load_po(db: Session, po_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    from app.models.edi_po import EdiPurchaseOrder

    po = db.execute(
        select(EdiPurchaseOrder).where(
            EdiPurchaseOrder.id == po_id,
            EdiPurchaseOrder.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    return po


def _po_ship_to(po: Any) -> tuple[str | None, str | None]:
    """(state, pincode) as the retailer stated them on the PO."""
    addr = po.ship_to_address if isinstance(po.ship_to_address, dict) else {}
    state = next((addr[k] for k in ("state", "State", "state_name") if addr.get(k)), None)
    pin = next(
        (addr[k] for k in ("postal_code", "pincode", "zip_code", "zipCode", "PostalCode")
         if addr.get(k)),
        None,
    )
    return (str(state) if state else None), (str(pin) if pin else None)


def _fetch_b1_addresses(card_code: str, pin: str | None, state: str | None) -> tuple[list[B1AddressOption], str | None]:
    """
    Candidate ShipTo/PayTo addresses, read live from B1.

    This is the one place we read B1 on an operator action rather than from our local
    mirror. BP addresses are not synced (a single customer here has 142 of them, they
    change often, and they are only ever needed at this moment), so a live read costs
    one session when a human opens the dialog — not per PO, and never on a schedule.
    A failure is returned as a message rather than raised: the operator can still pick
    a branch and warehouse, and type an address name if they know it.
    """
    from app.sap_b1.client import get_b1_client

    try:
        bp = get_b1_client().get_business_partner(card_code)
    except Exception as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
        log.warning("b1.address_lookup_failed", card_code=card_code, error=str(exc))
        return [], f"Could not read addresses from SAP: {exc}"

    if not bp:
        return [], f"Customer {card_code} was not found in SAP."

    target_state = resolve_state(state=state)
    options: list[B1AddressOption] = []
    for a in bp.get("BPAddresses", []):
        name = a.get("AddressName")
        if not name:
            continue
        a_state = a.get("State")
        a_zip = a.get("ZipCode")
        matches = bool(
            (pin and a_zip and str(a_zip).strip() == str(pin).strip())
            or (target_state and a_state and resolve_state(state=str(a_state)) == target_state)
        )
        options.append(B1AddressOption(
            address_name=str(name),
            address_type=str(a.get("AddressType") or ""),
            city=a.get("City"), state=a_state, zip_code=a_zip,
            gstin=a.get("GSTIN"), matches_po=matches,
        ))
    # Addresses matching the PO first — a customer can have well over a hundred.
    options.sort(key=lambda o: (not o.matches_po, o.address_type, o.address_name))
    return options, None


@router.get("/{po_id}/dispatch-options", response_model=DispatchOptionsResponse)
def dispatch_options(
    po_id: uuid.UUID,
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> DispatchOptionsResponse:
    """Everything the push dialog needs, including the tax effect of each branch."""
    from sqlalchemy import select

    from app.models.master_data import BranchMaster, TradingPartner, WarehouseMaster

    po = _load_po(db, po_id)
    partner = db.get(TradingPartner, po.trading_partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="Trading partner not found")

    ship_state, ship_pin = _po_ship_to(po)
    resolved_ship_state = resolve_state(gstin=po.buyer_gstin, state=ship_state)

    rows = db.execute(
        select(BranchMaster, WarehouseMaster)
        .outerjoin(
            WarehouseMaster,
            (WarehouseMaster.branch_id == BranchMaster.id)
            & WarehouseMaster.deleted_at.is_(None)
            & WarehouseMaster.is_active.is_(True)
            & WarehouseMaster.inactive.is_(False),
        )
        .where(
            BranchMaster.deleted_at.is_(None),
            BranchMaster.is_active.is_(True),
            BranchMaster.disabled.is_(False),
        )
        .order_by(BranchMaster.bpl_id, WarehouseMaster.whs_code)
    ).all()

    branches: dict[int, BranchOption] = {}
    tax_by_branch: dict[str, str] = {}
    for branch, warehouse in rows:
        opt = branches.get(branch.bpl_id)
        if opt is None:
            opt = BranchOption(
                bpl_id=branch.bpl_id, bpl_name=branch.bpl_name,
                state=branch.state, gstin=branch.gstin, warehouses=[],
            )
            branches[branch.bpl_id] = opt
            inter = is_interstate(
                resolve_state(gstin=branch.gstin, state=branch.state), resolved_ship_state
            )
            tax_by_branch[str(branch.bpl_id)] = (
                "UNKNOWN" if inter is None else ("IGST" if inter else "CSGST")
            )
        if warehouse is not None:
            opt.warehouses.append(WarehouseOption(
                whs_code=warehouse.whs_code, whs_name=warehouse.whs_name,
                bpl_id=branch.bpl_id,
            ))

    addresses: list[B1AddressOption] = []
    lookup_error: str | None = None
    if partner.b1_card_code:
        addresses, lookup_error = _fetch_b1_addresses(
            partner.b1_card_code, ship_pin, ship_state
        )
    else:
        lookup_error = (
            f"Partner '{partner.code}' has no b1_card_code, so its SAP addresses "
            f"cannot be listed — and the push will fail without one."
        )

    return DispatchOptionsResponse(
        po_id=po_id,
        buyer_po_number=po.buyer_po_number,
        partner_code=partner.code,
        b1_card_code=partner.b1_card_code,
        ship_to_state=resolved_ship_state or ship_state,
        ship_to_pincode=ship_pin,
        branches=list(branches.values()),
        addresses=addresses,
        address_lookup_error=lookup_error,
        selected_bpl_id=po.b1_bpl_id,
        selected_whs_code=po.b1_whs_code,
        selected_ship_to_code=po.b1_ship_to_code,
        selected_pay_to_code=po.b1_pay_to_code,
        tax_by_branch=tax_by_branch,
    )


@router.post("/{po_id}/preview-sap", response_model=SapPreviewResponse)
def preview_sap_payload(
    po_id: uuid.UUID,
    body: SapPushRequest,
    db: Session = Depends(get_sync_db),
    _current_user: UserResponse = Depends(get_current_user),
) -> SapPreviewResponse:
    """
    Build the exact document that would be posted, and send nothing.

    Worth its own endpoint: a wrong branch shows up as a wrong VatGroup on every line,
    and that is far cheaper to spot here than on a posted document that then needs
    cancelling in B1.
    """
    from app.mappers.po_to_sales_order import MappingError
    from app.sap_b1.client import get_b1_client
    from app.workflows.canonical_to_b1 import DispatchError, build_payload_preview

    _load_po(db, po_id)   # 404 before doing any work

    try:
        payload, warnings = build_payload_preview(
            po_id, bpl_id=body.bpl_id, whs_code=body.whs_code,
            ship_to_code=body.ship_to_code, pay_to_code=body.pay_to_code,
        )
    except (DispatchError, MappingError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return SapPreviewResponse(
        po_id=po_id,
        endpoint=f"{get_b1_client().api_base}/Orders",
        payload=payload,
        warnings=warnings,
    )


@router.post("/{po_id}/push-to-sap-with", response_model=POActionResponse)
def push_to_sap_with_selection(
    po_id: uuid.UUID,
    body: SapPushRequest,
    request: Request,
    db: Session = Depends(get_sync_db),
    current_user: UserResponse = Depends(get_current_user),
) -> POActionResponse:
    """
    Push this PO to B1 as a Sales Order using an explicit branch/warehouse selection.

    Runs synchronously rather than through the queue: the operator is watching, and a
    B1 rejection is far more useful shown immediately than discovered later in a log.
    The idempotency guard in push_po_to_b1 still applies, so a double-click cannot
    create two Sales Orders for one retailer PO.
    """
    from sqlalchemy import func, select

    from app.models._enums import ValidationStatus
    from app.models.audit_log import AuditLog
    from app.models.edi_po import EdiValidationIssue

    po = _load_po(db, po_id)
    current_status = str(po.po_status)

    if po.b1_sales_order_doc_entry is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Already in SAP as Sales Order {po.b1_sales_order_doc_num} "
                   f"(DocEntry {po.b1_sales_order_doc_entry}).",
        )
    if current_status not in _PUSHABLE:
        raise HTTPException(
            status_code=400, detail=f"Cannot push a PO with status '{current_status}'."
        )

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
            detail=f"{open_errors} unresolved validation error(s) — resolve them first.",
        )

    db.add(AuditLog(
        user_email=current_user.email,
        action="push_to_sap",
        entity_type="EdiPurchaseOrder",
        entity_id=str(po_id),
        payload=body.model_dump(mode="json", exclude_none=True),
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()

    from app.models._enums import PoStatus
    from app.workflows.canonical_to_b1 import push_po_to_b1

    result = push_po_to_b1(
        po_id, bpl_id=body.bpl_id, whs_code=body.whs_code,
        ship_to_code=body.ship_to_code, pay_to_code=body.pay_to_code,
        # The route has already checked the real gate (no open ERROR issues), so a PO
        # parked at EXCEPTION or PARSED is pushable here even though the scheduler
        # would leave it alone.
        allowed_statuses=(
            PoStatus.VALIDATED, PoStatus.SAP_REJECTED,
            PoStatus.EXCEPTION, PoStatus.PARSED,
        ),
    )

    if not result.success:
        # skip_reason and error are distinct: one means "did not try", the other means
        # "tried and B1 said no". Reporting a blank message for the first hides the cause.
        detail = result.error or result.skip_reason or "SAP rejected the order."
        log.warning("pos.push_failed", po_id=str(po_id), detail=detail, skipped=result.skipped)
        raise HTTPException(status_code=422, detail=detail)

    return POActionResponse(
        success=True,
        message=(
            f"Sales Order {result.b1_doc_num} created in SAP "
            f"(DocEntry {result.b1_doc_entry})."
        ),
        po_id=po_id,
    )
