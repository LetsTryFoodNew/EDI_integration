"""
Validate-PO workflow — Phase 3 of the processing pipeline.

  EdiPurchaseOrder (status=PARSED) → ValidationEngine → EdiValidationIssue rows
  → status VALIDATED (no errors) or EXCEPTION (any ERROR-severity violation)

Called by validate_po_job (RQ) which is enqueued after a successful parse.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import uuid

log = structlog.get_logger(__name__)


@dataclass
class ValidateResult:
    success: bool
    po_id: uuid.UUID
    status: str = ""
    error_count: int = 0
    warning_count: int = 0
    errors: list[str] = field(default_factory=list)


def validate_po(po_id: uuid.UUID) -> ValidateResult:
    """
    Load the PO, run the full validation engine, persist issues, update status.
    Safe to retry — existing OPEN issues for the same PO are deleted and rewritten.
    """
    from sqlalchemy import select

    from app.db import SyncSessionLocal
    from app.models._enums import PoStatus, ValidationStatus
    from app.models.edi_po import (
        EdiPoLineItem,
        EdiPoStatusHistory,
        EdiPurchaseOrder,
        EdiValidationIssue,
    )
    from app.models.master_data import TradingPartner
    from app.validators.engine import ValidationContext, ValidationEngine

    with SyncSessionLocal() as session:
        po = session.get(EdiPurchaseOrder, po_id)
        if not po:
            log.error("validate.po_not_found", po_id=str(po_id))
            return ValidateResult(success=False, po_id=po_id, errors=["PO not found"])

        partner = session.get(TradingPartner, po.trading_partner_id)
        if not partner:
            log.error("validate.partner_not_found", po_id=str(po_id))
            return ValidateResult(success=False, po_id=po_id, errors=["TradingPartner not found"])

        lines = session.execute(
            select(EdiPoLineItem).where(EdiPoLineItem.po_id == po_id)
        ).scalars().all()

        # Delete any pre-existing OPEN validation issues (idempotent re-run)
        existing = session.execute(
            select(EdiValidationIssue).where(
                EdiValidationIssue.po_id == po_id,
                EdiValidationIssue.validation_status == ValidationStatus.OPEN,
            )
        ).scalars().all()
        for issue in existing:
            session.delete(issue)
        session.flush()

        # Run engine
        ctx = ValidationContext(po=po, lines=list(lines), partner=partner, session=session)
        engine_result = ValidationEngine().run(ctx)

        # Persist violations
        for viol in engine_result.violations:
            session.add(EdiValidationIssue(
                po_id=po_id,
                line_id=viol.line_id,
                issue_code=viol.issue_code,
                severity=viol.severity,
                message=viol.message,
                field_path=viol.field_path,
                validation_status=ValidationStatus.OPEN,
            ))

        # Determine new PO status
        old_status = po.po_status
        new_status = PoStatus.EXCEPTION if engine_result.has_errors else PoStatus.VALIDATED
        po.po_status = new_status

        if old_status != new_status:
            session.add(EdiPoStatusHistory(
                po_id=po_id,
                from_status=old_status,
                to_status=new_status,
                changed_by="validator",
                notes=_status_note(engine_result),
            ))

        session.commit()

        error_count = sum(1 for v in engine_result.violations if v.severity == "ERROR")
        warning_count = sum(1 for v in engine_result.violations if v.severity == "WARNING")

        log.info(
            "validate.done",
            po_id=str(po_id),
            partner=partner.code,
            status=new_status,
            errors=error_count,
            warnings=warning_count,
        )

        return ValidateResult(
            success=True,
            po_id=po_id,
            status=new_status,
            error_count=error_count,
            warning_count=warning_count,
        )


def _status_note(result: object) -> str:
    err = sum(1 for v in result.violations if v.severity == "ERROR")  # type: ignore[attr-defined]
    warn = sum(1 for v in result.violations if v.severity == "WARNING")  # type: ignore[attr-defined]
    if err:
        return f"Validation failed: {err} error(s), {warn} warning(s)"
    if warn:
        return f"Validated with {warn} warning(s)"
    return "Validated — no issues found"
