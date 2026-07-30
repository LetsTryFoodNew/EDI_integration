"""
SkuMappingRule — ensures every PO line resolves to an internal material code.

Lookup only. SAP is the sole author of SKU_Mapping (b1ItemCode is not-null, so every
row is already a confirmed mapping), so this rule never creates or guesses a mapping:

  - exact (partner, buyer_sku) hit  -> wire ItemCode onto the line
  - no hit                          -> E002_SKU_UNRESOLVED (ERROR), PO becomes EXCEPTION

The middleware used to auto-map via cross-partner EAN reuse and rapidfuzz description
matching (>=0.85). Both were removed deliberately: a fuzzy match at 0.86 between
"Salted Almonds 100g" and "Salted Cashews 100g" would post a Sales Order for the wrong
product and ship the wrong goods. An unresolved SKU is fixed by adding the mapping in
SAP and re-syncing master data, then retrying the PO.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.validators.engine import BaseRule, RuleViolation

if TYPE_CHECKING:
    from app.models.edi_po import EdiPoLineItem
    from app.validators.engine import ValidationContext


class SkuMappingRule(BaseRule):
    """Resolves buyer SKUs to internal material codes via exact lookup only."""

    def run(self, ctx: ValidationContext) -> list[RuleViolation]:
        violations: list[RuleViolation] = []

        for line in ctx.lines:
            mapping = _load_exact_mapping(ctx, line)

            if mapping is not None:
                _apply_mapping(ctx, line, mapping)
            else:
                violations.append(RuleViolation(
                    issue_code="E002_SKU_UNRESOLVED",
                    severity="ERROR",
                    message=(
                        f"Line {line.line_number}: buyer SKU '{line.buyer_sku}' has no mapping "
                        "to an internal material code. Add it in SAP and re-sync master data, "
                        "then retry this PO."
                    ),
                    line_id=line.id,
                    field_path="buyer_sku",
                ))

        return violations


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_exact_mapping(
    ctx: ValidationContext,
    line: EdiPoLineItem,
) -> object | None:
    """Return the active mapping for this (partner, buyer_sku), or None."""
    from sqlalchemy import select

    from app.models.master_data import SkuMapping

    return ctx.session.execute(
        select(SkuMapping).where(
            SkuMapping.trading_partner_id == ctx.partner.id,
            SkuMapping.buyer_sku == line.buyer_sku,
            SkuMapping.deleted_at.is_(None),
            SkuMapping.is_active.is_(True),
        )
    ).scalar_one_or_none()


def _apply_mapping(
    ctx: ValidationContext,
    line: EdiPoLineItem,
    mapping: object,
) -> None:
    """Write the resolved ItemCode back onto the line item."""
    from app.models.master_data import MaterialMaster

    line.sku_mapping_id = mapping.id  # type: ignore[attr-defined]

    mat = ctx.session.get(MaterialMaster, mapping.material_id)  # type: ignore[attr-defined]
    if mat:
        line.sap_material_no = mat.item_code
