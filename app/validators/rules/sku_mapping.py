"""
SkuMappingRule — ensures every PO line resolves to an internal material code.

Lookup only. SAP is the sole author of SKU_Mapping (b1ItemCode is not-null, so every
row is already a confirmed mapping), so this rule never creates or guesses a mapping:

  - line already names an ItemCode  -> confirm it exists and is sellable
  - exact (partner, buyer_sku) hit  -> wire ItemCode onto the line
  - no hit                          -> E002_SKU_UNRESOLVED (ERROR), PO becomes EXCEPTION

The middleware used to auto-map via cross-partner EAN reuse and rapidfuzz description
matching (>=0.85). Both were removed deliberately: a fuzzy match at 0.86 between
"Salted Almonds 100g" and "Salted Cashews 100g" would post a Sales Order for the wrong
product and ship the wrong goods. An unresolved SKU is fixed by adding the mapping in
SAP and re-syncing master data, then retrying the PO.

**The first branch is not a hole in that.** What the rule refuses to do is *guess*, and
a line arriving with an ItemCode on it has not been guessed: no partner parser sets
`sap_material_no`, so the only way one is present before validation is that an operator
picked the item out of the material master while keying the order in. That case has no
alternative — a manual partner has no catalogue for a buyer SKU to be mapped from, so
demanding a sku_mapping row first would make hand-keyed orders impossible to process.
The code is still checked against material_master here, so a typed or stale one is
rejected rather than posted.
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
            preassigned = _preassigned(ctx, line)
            if preassigned is not None:
                violations.extend(preassigned)
                continue

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

def _preassigned(
    ctx: ValidationContext,
    line: EdiPoLineItem,
) -> list[RuleViolation] | None:
    """
    Handle a line that already names its SAP item.

    Returns None when the line names nothing, so the caller falls through to the
    normal lookup. Otherwise returns the violations for that item — empty when it
    checks out.

    Only a hand-keyed order reaches here with a code set: an operator chose it from
    the material master, which is a decision rather than a match. It is still verified
    against master data, because a code typed from memory or left behind by an item
    that has since been retired would otherwise post a Sales Order B1 rejects with
    ODBC -2028 — which is exactly how Blinkit PO 2873410040494 failed, on an FG00460
    that does not exist.
    """
    from sqlalchemy import select

    from app.models.master_data import MaterialMaster

    code = (getattr(line, "sap_material_no", None) or "").strip()
    if not code:
        return None

    material = ctx.session.execute(
        select(MaterialMaster).where(
            MaterialMaster.item_code == code,
            MaterialMaster.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if material is None:
        return [RuleViolation(
            issue_code="E002_SKU_UNRESOLVED",
            severity="ERROR",
            message=(
                f"Line {line.line_number}: item code '{code}' is not in the material "
                "master. Pick an item from the list, or sync master data if it is new "
                "in SAP."
            ),
            line_id=line.id,
            field_path="sap_material_no",
        )]

    if getattr(material, "valid_for", 1) == 0 or getattr(material, "frozen_for", False):
        return [RuleViolation(
            issue_code="E002_SKU_UNRESOLVED",
            severity="ERROR",
            message=(
                f"Line {line.line_number}: item '{code}' ({material.item_name}) is "
                "inactive or frozen in SAP and cannot be sold."
            ),
            line_id=line.id,
            field_path="sap_material_no",
        )]

    return []



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
