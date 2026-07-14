"""
MoqRule — checks that ordered quantity meets the minimum order quantity (MOQ).

MOQ is read from TradingPartner.api_config["moq"][buyer_sku] (per-SKU override)
or SkuMapping.notes JSON {"moq": 6}.

If no MOQ is configured for a SKU, the rule is skipped for that line.

Issue code: W007_BELOW_MOQ
Severity: WARNING (we can still push to B1; ops should inform the buyer)
"""
from __future__ import annotations

import contextlib
import json
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from app.validators.engine import BaseRule, RuleViolation

if TYPE_CHECKING:
    from app.validators.engine import ValidationContext


class MoqRule(BaseRule):
    """Flags lines where ordered_qty < configured MOQ for that SKU."""

    def run(self, ctx: ValidationContext) -> list[RuleViolation]:
        partner_moqs: dict[str, Decimal] = _load_partner_moqs(ctx)
        violations: list[RuleViolation] = []

        for line in ctx.lines:
            moq = partner_moqs.get(line.buyer_sku) or _moq_from_mapping(line)
            if moq is None:
                continue

            try:
                qty = Decimal(str(line.ordered_qty))
            except InvalidOperation:
                continue

            if qty < moq:
                violations.append(RuleViolation(
                    issue_code="W007_BELOW_MOQ",
                    severity="WARNING",
                    message=(
                        f"Line {line.line_number}: ordered qty {qty} is below the MOQ of "
                        f"{moq} for SKU '{line.buyer_sku}'."
                    ),
                    line_id=line.id,
                    field_path="ordered_qty",
                ))

        return violations


def _load_partner_moqs(ctx: ValidationContext) -> dict[str, Decimal]:
    api_config = getattr(ctx.partner, "api_config", None) or {}
    raw = api_config.get("moq", {})
    result: dict[str, Decimal] = {}
    for sku, val in (raw or {}).items():
        with contextlib.suppress(InvalidOperation, TypeError):
            result[str(sku)] = Decimal(str(val))
    return result


def _moq_from_mapping(line: object) -> Decimal | None:
    mapping = getattr(line, "sku_mapping", None)
    if mapping is None:
        return None
    notes = getattr(mapping, "notes", None) or ""
    try:
        data = json.loads(notes)
        raw = data.get("moq")
        if raw is not None:
            return Decimal(str(raw))
    except (json.JSONDecodeError, InvalidOperation, TypeError):
        pass
    return None
