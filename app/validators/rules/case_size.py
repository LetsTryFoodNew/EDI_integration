"""
CaseSizeRule — ordered quantity must be a whole multiple of the item's case size.

Products ship in fixed cases (e.g. 36 units/case). A PO asking for 76 units of
a 36/case item cannot be fulfilled exactly (2 cases = 72, 3 cases = 108), so the
platform must reissue the PO with a valid quantity. We raise an ERROR naming
the SKU and the nearest valid quantities so ops can quote them to the platform.

Runs after SkuMappingRule (which resolves line → material); lines without a
resolved material are skipped — they already carry E002_SKU_UNRESOLVED.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.validators.engine import BaseRule, RuleViolation

if TYPE_CHECKING:
    from app.validators.engine import ValidationContext


class CaseSizeRule(BaseRule):
    """Flags lines whose ordered qty is not a multiple of the material's case size."""

    def run(self, ctx: ValidationContext) -> list[RuleViolation]:
        from app.models.master_data import MaterialMaster, SkuMapping

        violations: list[RuleViolation] = []

        for line in ctx.lines:
            if line.sku_mapping_id is None:
                continue
            mapping = ctx.session.get(SkuMapping, line.sku_mapping_id)
            if mapping is None or mapping.material_id is None:
                continue
            material = ctx.session.get(MaterialMaster, mapping.material_id)
            if material is None or not material.case_size or material.case_size <= 1:
                continue

            qty = int(line.ordered_qty)
            case_size = material.case_size
            if qty % case_size == 0:
                continue

            lower = (qty // case_size) * case_size
            upper = lower + case_size
            nearest = f"{lower} or {upper}" if lower > 0 else str(upper)
            violations.append(RuleViolation(
                issue_code="E008_CASE_SIZE_MISMATCH",
                severity="ERROR",
                message=(
                    f"Line {line.line_number}: SKU '{line.buyer_sku}' "
                    f"({material.b1_item_code}) ordered qty {qty} is not a multiple of "
                    f"case size {case_size}. Nearest valid qty: {nearest}. "
                    f"Request {ctx.partner.name} to reissue the PO with a valid quantity."
                ),
                line_id=line.id,
                field_path="ordered_qty",
            ))

        return violations
