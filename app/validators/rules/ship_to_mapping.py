"""
ShipToMappingRule — checks whether the PO's ship-to warehouse is mapped to a B1 WhsCode.

A missing ship-to mapping is a WARNING (not ERROR): we can default to a partner-level
warehouse, but ops should resolve it for accurate inventory allocation.

Issue code: W003_SHIP_TO_UNMAPPED
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.validators.engine import BaseRule, RuleViolation

if TYPE_CHECKING:
    from app.validators.engine import ValidationContext


class ShipToMappingRule(BaseRule):
    """Flags an unmapped ship-to code as a WARNING; sets b1_whs_code on lines when found."""

    def run(self, ctx: ValidationContext) -> list[RuleViolation]:
        ship_to = (ctx.po.ship_to_code or "").strip()
        if not ship_to:
            # No ship-to code on PO — nothing to validate
            return []

        from sqlalchemy import select

        from app.models.master_data import ShipToMapping

        mapping = ctx.session.execute(
            select(ShipToMapping).where(
                ShipToMapping.trading_partner_id == ctx.partner.id,
                ShipToMapping.buyer_warehouse_code == ship_to,
                ShipToMapping.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

        if mapping is None:
            return [RuleViolation(
                issue_code="W003_SHIP_TO_UNMAPPED",
                severity="WARNING",
                message=(
                    f"Ship-to warehouse '{ship_to}' has no B1 WhsCode mapping. "
                    "Add a mapping in Master Data → Ship-to Mapping."
                ),
                field_path="ship_to_code",
            )]

        if mapping.b1_whs_code:
            for line in ctx.lines:
                line.b1_whs_code = mapping.b1_whs_code

        return []
