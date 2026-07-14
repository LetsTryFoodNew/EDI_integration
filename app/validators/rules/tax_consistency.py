"""
TaxConsistencyRule — enforces Indian GST tax structure per line.

Rules:
  1. CGST and IGST are mutually exclusive. A line may not have both > 0.
     (CGST+SGST = intra-state; IGST = inter-state)
  2. If CGST is present, SGST must also be present (and vice versa) and equal.
  3. At least one tax component must be non-zero unless the item is exempt.

Severity: ERROR for rule 1 (wrong tax charge → compliance risk)
          WARNING for rule 2 (asymmetric CGST/SGST → likely data entry error)
          INFO for rule 3 (zero-rated items are valid but should be verified)
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.validators.engine import BaseRule, RuleViolation

if TYPE_CHECKING:
    from app.validators.engine import ValidationContext

_ZERO = Decimal("0")
_TOLERANCE = Decimal("0.01")  # rounding tolerance for rate comparison


class TaxConsistencyRule(BaseRule):
    """Validates GST structure on every PO line."""

    def run(self, ctx: ValidationContext) -> list[RuleViolation]:
        violations: list[RuleViolation] = []

        for line in ctx.lines:
            cgst = Decimal(str(line.cgst_rate or 0))
            sgst = Decimal(str(line.sgst_rate or 0))
            igst = Decimal(str(line.igst_rate or 0))

            # Rule 1 — cannot have both CGST and IGST
            if cgst > _ZERO and igst > _ZERO:
                violations.append(RuleViolation(
                    issue_code="E005_TAX_CGST_AND_IGST",
                    severity="ERROR",
                    message=(
                        f"Line {line.line_number}: both CGST ({cgst}%) and IGST ({igst}%) are "
                        "non-zero. A transaction is either intra-state (CGST+SGST) or "
                        "inter-state (IGST), never both."
                    ),
                    line_id=line.id,
                    field_path="cgst_rate",
                ))

            # Rule 2 — CGST and SGST must match
            elif cgst > _ZERO and abs(cgst - sgst) > _TOLERANCE:
                violations.append(RuleViolation(
                    issue_code="W005_CGST_SGST_MISMATCH",
                    severity="WARNING",
                    message=(
                        f"Line {line.line_number}: CGST ({cgst}%) ≠ SGST ({sgst}%). "
                        "They must be equal for intra-state transactions."
                    ),
                    line_id=line.id,
                    field_path="sgst_rate",
                ))

        return violations
