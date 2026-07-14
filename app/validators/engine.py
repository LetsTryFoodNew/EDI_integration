"""
Validation engine — runs an ordered list of rule classes against one PO.

Usage (inside a DB session):
    from app.validators.engine import ValidationEngine, ValidationContext
    ctx = ValidationContext(po=po, lines=lines, partner=partner, session=session)
    violations = ValidationEngine().run(ctx)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from app.models.edi_po import EdiPoLineItem, EdiPurchaseOrder
    from app.models.master_data import TradingPartner


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class RuleViolation:
    """One issue found by a rule."""
    issue_code: str
    severity: str           # "ERROR" | "WARNING" | "INFO"
    message: str
    line_id: uuid.UUID | None = None
    field_path: str | None = None


@dataclass
class ValidationContext:
    """Everything a rule needs to assess one PO."""
    po: EdiPurchaseOrder
    lines: list[EdiPoLineItem]
    partner: TradingPartner
    session: Session


class BaseRule(ABC):
    """Interface every validation rule must implement."""

    @abstractmethod
    def run(self, ctx: ValidationContext) -> list[RuleViolation]:
        """Return violations found; empty list means the rule passed."""


# ── Engine ────────────────────────────────────────────────────────────────────

@dataclass
class EngineResult:
    violations: list[RuleViolation] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(v.severity == "ERROR" for v in self.violations)

    @property
    def has_warnings(self) -> bool:
        return any(v.severity == "WARNING" for v in self.violations)


def _default_rules() -> list[BaseRule]:
    from app.validators.rules.case_size import CaseSizeRule
    from app.validators.rules.gstin import GstinFormatRule
    from app.validators.rules.moq import MoqRule
    from app.validators.rules.pricing import PriceVarianceRule
    from app.validators.rules.ship_to_mapping import ShipToMappingRule
    from app.validators.rules.sku_mapping import SkuMappingRule
    from app.validators.rules.tax_consistency import TaxConsistencyRule
    from app.validators.rules.total_reconciliation import TotalReconciliationRule

    return [
        # Run SKU mapping first — it may auto-resolve items before other rules fire
        SkuMappingRule(),
        CaseSizeRule(),  # needs resolved materials, so directly after SkuMappingRule
        ShipToMappingRule(),
        GstinFormatRule(),
        TaxConsistencyRule(),
        TotalReconciliationRule(),
        PriceVarianceRule(),
        MoqRule(),
    ]


class ValidationEngine:
    """Runs all registered rules in order, collecting violations."""

    def __init__(self, rules: list[BaseRule] | None = None) -> None:
        self._rules = rules if rules is not None else _default_rules()

    def run(self, ctx: ValidationContext) -> EngineResult:
        result = EngineResult()
        for rule in self._rules:
            try:
                violations = rule.run(ctx)
                result.violations.extend(violations)
            except Exception as exc:
                import structlog
                structlog.get_logger(__name__).exception(
                    "validator.rule_error",
                    rule=type(rule).__name__,
                    po_id=str(ctx.po.id),
                    error=str(exc),
                )
                result.violations.append(RuleViolation(
                    issue_code="E999_RULE_INTERNAL_ERROR",
                    severity="ERROR",
                    message=f"Rule {type(rule).__name__} raised an unexpected error: {exc}",
                ))
        return result
