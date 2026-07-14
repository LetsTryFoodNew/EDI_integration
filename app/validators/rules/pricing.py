"""
PriceVarianceRule — checks actual unit_price against a contracted price.

Contracted price is stored in SkuMapping.notes as a JSON string when set, e.g.:
  {"contracted_price": 88.50, "tolerance_pct": 5}

If no contracted price is available for a SKU, the rule is skipped for that line.
The default tolerance is read from TradingPartner.api_config["price_variance_threshold_pct"]
(default 10%).

Issue code: W004_PRICE_VARIANCE
Severity: WARNING (price difference is informational; ops may want to review)
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from app.validators.engine import BaseRule, RuleViolation

if TYPE_CHECKING:
    from app.validators.engine import ValidationContext

_DEFAULT_TOLERANCE_PCT = Decimal("10")


class PriceVarianceRule(BaseRule):
    """Flags lines where unit_price deviates from the contracted price by > threshold."""

    def run(self, ctx: ValidationContext) -> list[RuleViolation]:
        tolerance_pct = _get_tolerance(ctx)
        violations: list[RuleViolation] = []

        for line in ctx.lines:
            if line.unit_price is None:
                continue

            contracted = _get_contracted_price(line)
            if contracted is None:
                continue

            try:
                actual = Decimal(str(line.unit_price))
            except InvalidOperation:
                continue

            if contracted == Decimal("0"):
                continue

            variance_pct = abs(actual - contracted) / contracted * Decimal("100")
            if variance_pct > tolerance_pct:
                violations.append(RuleViolation(
                    issue_code="W004_PRICE_VARIANCE",
                    severity="WARNING",
                    message=(
                        f"Line {line.line_number}: unit price ₹{actual:.2f} deviates "
                        f"{variance_pct:.1f}% from contracted price ₹{contracted:.2f} "
                        f"(threshold: {tolerance_pct}%)."
                    ),
                    line_id=line.id,
                    field_path="unit_price",
                ))

        return violations


def _get_tolerance(ctx: ValidationContext) -> Decimal:
    api_config = getattr(ctx.partner, "api_config", None) or {}
    raw = api_config.get("price_variance_threshold_pct")
    if raw is not None:
        try:
            return Decimal(str(raw))
        except InvalidOperation:
            pass
    return _DEFAULT_TOLERANCE_PCT


def _get_contracted_price(line: object) -> Decimal | None:
    """Extract contracted_price from SkuMapping.notes JSON, if present."""
    mapping = getattr(line, "sku_mapping", None)
    if mapping is None:
        return None

    notes = getattr(mapping, "notes", None) or ""
    try:
        data = json.loads(notes)
        raw = data.get("contracted_price")
        if raw is not None:
            return Decimal(str(raw))
    except (json.JSONDecodeError, InvalidOperation, TypeError):
        pass

    return None
