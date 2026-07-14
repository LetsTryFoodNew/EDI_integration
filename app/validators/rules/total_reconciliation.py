"""
TotalReconciliationRule — verifies that sum(line.line_total) ≈ PO.grand_total.

Tolerance: ₹1.00 (covers rounding differences from the partner's calculation).
If the PO has no grand_total set, this rule is skipped.

Issue code: W006_TOTAL_MISMATCH
Severity: WARNING (doesn't block SAP push; B1 recomputes totals from lines anyway)
"""
from __future__ import annotations

import contextlib
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from app.validators.engine import BaseRule, RuleViolation

if TYPE_CHECKING:
    from app.validators.engine import ValidationContext

_TOLERANCE = Decimal("1.00")


class TotalReconciliationRule(BaseRule):
    """Flags a significant discrepancy between line totals and the PO header grand_total."""

    def run(self, ctx: ValidationContext) -> list[RuleViolation]:
        if ctx.po.grand_total is None:
            return []

        try:
            header_total = Decimal(str(ctx.po.grand_total))
        except InvalidOperation:
            return []

        line_sum = Decimal("0")
        for line in ctx.lines:
            if line.line_total is not None:
                with contextlib.suppress(InvalidOperation):
                    line_sum += Decimal(str(line.line_total))

        diff = abs(header_total - line_sum)
        if diff > _TOLERANCE:
            return [RuleViolation(
                issue_code="W006_TOTAL_MISMATCH",
                severity="WARNING",
                message=(
                    f"Sum of line totals (₹{line_sum:.2f}) differs from PO grand_total "
                    f"(₹{header_total:.2f}) by ₹{diff:.2f}, which exceeds the ₹1 tolerance."
                ),
                field_path="grand_total",
            )]

        return []
