"""
GstinFormatRule — validates the buyer GSTIN on the PO header.

GSTIN format (India): 15 chars
  ^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[0-9A-Z]{1}Z[0-9A-Z]{1}$

Severity: ERROR — an invalid GSTIN blocks e-invoicing in B1.
Issue code: E001_INVALID_BUYER_GSTIN
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.validators.engine import BaseRule, RuleViolation

if TYPE_CHECKING:
    from app.validators.engine import ValidationContext

_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


class GstinFormatRule(BaseRule):
    """Flags a missing or malformed buyer GSTIN as an ERROR."""

    def run(self, ctx: ValidationContext) -> list[RuleViolation]:
        gstin = (ctx.po.buyer_gstin or "").strip()

        if not gstin:
            return [RuleViolation(
                issue_code="E001_MISSING_BUYER_GSTIN",
                severity="ERROR",
                message="Buyer GSTIN is missing; required for e-invoicing.",
                field_path="buyer_gstin",
            )]

        if not _GSTIN_RE.match(gstin):
            return [RuleViolation(
                issue_code="E001_INVALID_BUYER_GSTIN",
                severity="ERROR",
                message=f"Buyer GSTIN '{gstin}' does not match the required 15-character format.",
                field_path="buyer_gstin",
            )]

        return []
