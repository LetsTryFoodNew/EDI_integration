"""
LLM fallback parser — uses Anthropic Claude as a last resort to extract PO
fields from raw text (email body, extracted PDF text, or unstructured HTML).

Only invoked when:
  1. No structured parser exists for the partner, AND
  2. The partner's api_config has {"llm_fallback_enabled": true}

The LLM is given the raw text and asked to return a structured JSON object
matching our canonical schema. The response is validated with Pydantic.

Cost: ~$0.003–0.005 per PO at claude-sonnet-4-5 input rates.
Rate: respect Anthropic rate limits; failures do NOT retry via LLM (they
      go to the exception queue for manual ops review).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import structlog

from app.parsers.base import BaseParser, ParseResult

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """\
You are an EDI parser for an Indian FMCG company. Your job is to extract \
Purchase Order data from raw text and return it as a JSON object.

Return ONLY valid JSON. No markdown, no explanation, no code fences.

Required JSON shape:
{
  "po_number": "string — the buyer's PO/order reference number",
  "issue_date": "YYYY-MM-DD or null",
  "delivery_date": "YYYY-MM-DD or null",
  "buyer_name": "string or null",
  "buyer_gstin": "15-char GSTIN or null",
  "ship_to": {
    "name": "string or null",
    "line1": "string or null",
    "city": "string or null",
    "state": "string or null",
    "pincode": "string or null",
    "warehouse_code": "string or null"
  },
  "grand_total": number or null,
  "line_items": [
    {
      "line_number": integer,
      "buyer_sku": "partner SKU code (required)",
      "buyer_sku_description": "product name or null",
      "hsn_code": "6-8 digit HSN or null",
      "ordered_qty": number,
      "buyer_uom": "EA/CS/KG/etc or null",
      "unit_price": number or null,
      "cgst_rate": number or null,
      "sgst_rate": number or null,
      "igst_rate": number or null
    }
  ]
}

Rules:
- GSTIN is a 15-character alphanumeric string starting with 2 state code digits.
- HSN codes are 6-8 digits.
- If a field cannot be determined, use null.
- ordered_qty must always be a positive number.
- buyer_sku is mandatory for each line; skip lines where you cannot determine it.
- Return an empty line_items array if no line items can be extracted (parse will fail).
"""


class LlmFallbackParser(BaseParser):
    """
    Anthropic-backed parser for unrecognised formats.
    Not in the main registry — instantiated directly by parse_and_persist when
    the partner has llm_fallback_enabled=true and no structured parser succeeded.
    """

    @property
    def partner_code(self) -> str:
        return "__LLM_FALLBACK__"

    def can_parse(self, raw_message: Any) -> bool:
        text = _extract_text(raw_message)
        return bool(text and len(text.strip()) > 50)

    def parse(self, raw_message: Any) -> ParseResult:
        text = _extract_text(raw_message)
        if not text:
            return ParseResult(
                success=False,
                errors=["No text could be extracted for LLM fallback"],
                parser_name="LlmFallbackParser",
            )

        partner_code: str = getattr(
            getattr(raw_message, "trading_partner", None), "code", "UNKNOWN"
        )

        try:
            llm_json = self._call_llm(text, partner_code)
        except Exception as exc:
            log.error("llm_fallback.api_error", error=str(exc))
            return ParseResult(
                success=False,
                errors=[f"LLM API error: {exc}"],
                parser_name="LlmFallbackParser",
                extracted_text=text[:500],
            )

        return self._build_result(llm_json, raw_message, partner_code, text)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _call_llm(self, text: str, partner_code: str) -> dict[str, Any]:
        import anthropic  # lazy import — not required if LLM fallback is disabled

        from app.config import get_settings
        settings = get_settings()

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        message = client.messages.create(
            model=settings.llm_fallback_model,
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Partner: {partner_code}\n\n"
                        f"Raw PO text (first 8000 chars):\n\n{text[:8000]}"
                    ),
                }
            ],
        )
        raw_text = message.content[0].text.strip()
        log.info(
            "llm_fallback.response",
            partner=partner_code,
            tokens_in=message.usage.input_tokens,
            tokens_out=message.usage.output_tokens,
        )
        return json.loads(raw_text)

    def _build_result(
        self,
        data: dict[str, Any],
        raw_message: Any,
        partner_code: str,
        extracted_text: str,
    ) -> ParseResult:
        from decimal import Decimal

        from app.models._enums import PoStatus, SourceChannel
        from app.schemas.canonical import EDI850, EDI850Line, EDIAddress

        po_number = data.get("po_number")
        if not po_number:
            return ParseResult(
                success=False,
                errors=["LLM could not determine po_number"],
                parser_name="LlmFallbackParser",
                extracted_text=extracted_text[:500],
            )

        ship_raw = data.get("ship_to") or {}
        ship_to = EDIAddress(**{k: v for k, v in ship_raw.items() if k in EDIAddress.model_fields})

        lines: list[EDI850Line] = []
        line_errors: list[str] = []
        for item in data.get("line_items") or []:
            try:
                qty = Decimal(str(item.get("ordered_qty", 0)))
                if qty <= 0:
                    raise ValueError("ordered_qty must be positive")
                lines.append(EDI850Line(
                    line_number=item.get("line_number", len(lines) + 1),
                    buyer_sku=item["buyer_sku"],
                    buyer_sku_description=item.get("buyer_sku_description"),
                    hsn_code=item.get("hsn_code"),
                    ordered_qty=qty,
                    buyer_uom=item.get("buyer_uom"),
                    unit_price=Decimal(str(item["unit_price"])) if item.get("unit_price") else None,
                    cgst_rate=Decimal(str(item["cgst_rate"])) if item.get("cgst_rate") else None,
                    sgst_rate=Decimal(str(item["sgst_rate"])) if item.get("sgst_rate") else None,
                    igst_rate=Decimal(str(item["igst_rate"])) if item.get("igst_rate") else None,
                ))
            except Exception as exc:
                line_errors.append(f"LLM line {item.get('line_number', '?')}: {exc}")

        if not lines:
            return ParseResult(
                success=False,
                errors=["LLM returned no valid line items"] + line_errors,
                parser_name="LlmFallbackParser",
                extracted_text=extracted_text[:500],
            )

        source_channel = getattr(raw_message, "source_channel", SourceChannel.EMAIL)
        doc = EDI850(
            id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            trading_partner_code=partner_code,
            source_channel=source_channel,
            raw_message_id=getattr(raw_message, "id", None),
            buyer_po_number=po_number,
            ship_to=ship_to,
            buyer_gstin=data.get("buyer_gstin"),
            buyer_name=data.get("buyer_name"),
            grand_total=Decimal(str(data["grand_total"])) if data.get("grand_total") else None,
            line_items=lines,
            po_status=PoStatus.PARSED,
        )

        return ParseResult(
            success=True,
            doc=doc,
            warnings=line_errors + ["Parsed by LLM fallback — validate carefully"],
            parser_name="LlmFallbackParser",
            extracted_text=extracted_text[:500],
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_text(raw_message: Any) -> str | None:
    """Extract best available text from a RawMessage for LLM processing."""
    # Prefer plain text body
    if getattr(raw_message, "payload_raw", None):
        return raw_message.payload_raw

    # Fall back to payload JSON serialised as text
    payload = getattr(raw_message, "payload", None)
    if payload and isinstance(payload, dict):
        return json.dumps(payload, indent=2)

    return None
