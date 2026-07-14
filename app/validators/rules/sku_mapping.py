"""
SkuMappingRule — ensures every PO line has a resolved internal material code.

Auto-mapping pipeline (runs before flagging):
  1. Exact buyer_sku match in existing sku_mapping table          confidence=1.0
  2. Cross-partner EAN match (same buyer_sku used by another partner)  confidence=0.95
  3. Fuzzy description match via rapidfuzz token_sort_ratio ≥ 0.85   confidence=score

If none of the above resolve the SKU, an ERROR is written and the PO becomes
EXCEPTION (blocking B1 push — a Sales Order can't be created without ItemCode).

Auto-mapped lines get:
  - SkuMapping.mapping_status = AUTO_MAPPED + confidence_score
  - EdiPoLineItem.sku_mapping_id, sap_material_no, b1_whs_code updated in-place
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.validators.engine import BaseRule, RuleViolation

if TYPE_CHECKING:
    import uuid

    from app.models.edi_po import EdiPoLineItem
    from app.validators.engine import ValidationContext

_FUZZY_THRESHOLD = 0.85
_CROSS_PARTNER_CONFIDENCE = Decimal("0.95")
_FUZZY_CONFIDENCE_SCALE = Decimal("1") / Decimal("100")  # rapidfuzz returns 0-100


class SkuMappingRule(BaseRule):
    """
    Resolves buyer SKUs to internal material codes.
    Auto-maps via exact/EAN/fuzzy strategies before raising violations.
    """

    def run(self, ctx: ValidationContext) -> list[RuleViolation]:


        violations: list[RuleViolation] = []

        for line in ctx.lines:
            mapping = _load_exact_mapping(ctx, line)

            if mapping is None:
                mapping = _try_cross_partner_mapping(ctx, line)

            if mapping is None:
                mapping = _try_fuzzy_mapping(ctx, line)

            if mapping is not None:
                # Wire up the line to the resolved mapping
                _apply_mapping(ctx, line, mapping)
            else:
                violations.append(RuleViolation(
                    issue_code="E002_SKU_UNRESOLVED",
                    severity="ERROR",
                    message=(
                        f"Line {line.line_number}: buyer SKU '{line.buyer_sku}' has no mapping "
                        "to an internal material code. Resolve in Master Data before B1 push."
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
    """Return an existing MANUALLY_MAPPED or AUTO_MAPPED entry for this (partner, buyer_sku)."""
    from sqlalchemy import select

    from app.models.master_data import SkuMapping

    return ctx.session.execute(
        select(SkuMapping).where(
            SkuMapping.trading_partner_id == ctx.partner.id,
            SkuMapping.buyer_sku == line.buyer_sku,
            SkuMapping.deleted_at.is_(None),
            SkuMapping.material_id.isnot(None),
        )
    ).scalar_one_or_none()


def _try_cross_partner_mapping(
    ctx: ValidationContext,
    line: EdiPoLineItem,
) -> object | None:
    """
    Look for the same buyer_sku in a different partner's mapping.
    EANs are universal, so if Zepto mapped buyer_sku "Z-SKU-001" to material M-100,
    and Blinkit sends the same EAN, we can auto-map.
    """
    from sqlalchemy import select

    from app.models.master_data import SkuMapping

    row = ctx.session.execute(
        select(SkuMapping).where(
            SkuMapping.buyer_sku == line.buyer_sku,
            SkuMapping.deleted_at.is_(None),
            SkuMapping.material_id.isnot(None),
        )
    ).first()

    if not row:
        return None

    existing: object = row[0]
    # Create a new SkuMapping for this partner, inheriting from the found one
    return _create_auto_mapping(
        ctx,
        line,
        existing.material_id,  # type: ignore[attr-defined]
        _CROSS_PARTNER_CONFIDENCE,
        notes=f"Cross-partner EAN match from partner mapping id={existing.id}",  # type: ignore[attr-defined]
    )


def _try_fuzzy_mapping(
    ctx: ValidationContext,
    line: EdiPoLineItem,
) -> object | None:
    """
    Fuzzy-match line description against all active MaterialMaster descriptions.
    Uses rapidfuzz token_sort_ratio (order-insensitive, handles word shuffles).
    """
    if not line.buyer_sku_description:
        return None

    from rapidfuzz.fuzz import token_sort_ratio
    from sqlalchemy import select

    from app.models.master_data import MaterialMaster

    materials = ctx.session.execute(
        select(MaterialMaster).where(
            MaterialMaster.deleted_at.is_(None),
            MaterialMaster.is_active.is_(True),
        )
    ).scalars().all()

    best_score = 0.0
    best_material = None
    for mat in materials:
        score = token_sort_ratio(line.buyer_sku_description, mat.description)
        if score > best_score:
            best_score = score
            best_material = mat

    if best_material is None or best_score < _FUZZY_THRESHOLD * 100:
        return None

    confidence = Decimal(str(best_score)) * _FUZZY_CONFIDENCE_SCALE
    return _create_auto_mapping(
        ctx,
        line,
        best_material.id,
        confidence,
        notes=f"Fuzzy description match score={best_score:.1f}",
    )


def _create_auto_mapping(
    ctx: ValidationContext,
    line: EdiPoLineItem,
    material_id: uuid.UUID,
    confidence: Decimal,
    notes: str,
) -> object:
    """Upsert an AUTO_MAPPED SkuMapping row and return it."""
    # Re-use MappingStatus from models
    from app.models._enums import MappingStatus as MappingStatusEnum
    from app.models.master_data import SkuMapping

    existing = ctx.session.execute(
        __import__("sqlalchemy").select(SkuMapping).where(
            SkuMapping.trading_partner_id == ctx.partner.id,
            SkuMapping.buyer_sku == line.buyer_sku,
            SkuMapping.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if existing is not None:
        # Update in-place if previously UNMAPPED
        if existing.mapping_status == MappingStatusEnum.UNMAPPED:
            existing.material_id = material_id
            existing.mapping_status = MappingStatusEnum.AUTO_MAPPED
            existing.confidence_score = float(confidence)
            existing.notes = notes
            existing.buyer_sku_description = existing.buyer_sku_description or line.buyer_sku_description
        return existing

    mapping = SkuMapping(
        trading_partner_id=ctx.partner.id,
        buyer_sku=line.buyer_sku,
        buyer_sku_description=line.buyer_sku_description,
        buyer_uom=line.buyer_uom,
        material_id=material_id,
        mapping_status=MappingStatusEnum.AUTO_MAPPED,
        confidence_score=float(confidence),
        notes=notes,
    )
    ctx.session.add(mapping)
    ctx.session.flush()
    return mapping


def _apply_mapping(
    ctx: ValidationContext,
    line: EdiPoLineItem,
    mapping: object,
) -> None:
    """Write resolved ItemCode and WHS back onto the line item."""
    from app.models.master_data import MaterialMaster

    line.sku_mapping_id = mapping.id  # type: ignore[attr-defined]

    if mapping.material_id:  # type: ignore[attr-defined]
        mat = ctx.session.get(MaterialMaster, mapping.material_id)  # type: ignore[attr-defined]
        if mat:
            line.sap_material_no = mat.b1_item_code
