"""
Unit tests for the SAP invoice-push workflow.

Focus is the validation gate, because that is what decides whether an ASN reaches a
retailer unattended. The persistence paths are exercised by the integration suite;
here we pin the arithmetic and the PO-line matching that the gate depends on.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.schemas.api import InvoiceLineItemPush, InvoicePush


def _po_line(line_number: int, buyer_sku: str, ordered_qty: str, item_code: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        line_number=line_number,
        buyer_sku=buyer_sku,
        ordered_qty=Decimal(ordered_qty),
        sap_material_no=item_code or None,
    )


def _invoice(lines: list[InvoiceLineItemPush], **kw) -> InvoicePush:
    payload = {
        "invoice_number": "INV-001",
        "invoice_date": date(2026, 8, 10),
        "line_items": lines,
        "b1_sales_order_doc_entry": 4321,
    }
    payload.update(kw)
    return InvoicePush(**payload)


def _line(**kw) -> InvoiceLineItemPush:
    base = {"b1_item_code": "ITM-1", "qty": Decimal("10")}
    base.update(kw)
    return InvoiceLineItemPush(**base)


def _session_with_prior_lines(lines: list) -> MagicMock:
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = lines
    return session


class TestLineTotals:
    def test_sums_only_populated_line_totals(self) -> None:
        from app.workflows.invoice_from_sap import _sum_line_totals

        inv = _invoice([
            _line(line_total=Decimal("100.50")),
            _line(line_total=Decimal("49.50")),
            _line(line_total=None),
        ])
        assert _sum_line_totals(inv) == Decimal("150.00")

    def test_returns_zero_when_no_totals_sent(self) -> None:
        from app.workflows.invoice_from_sap import _sum_line_totals

        assert _sum_line_totals(_invoice([_line()])) == Decimal("0")


class TestPoLineMatching:
    def test_explicit_line_number_wins_over_sku(self) -> None:
        from app.workflows.invoice_from_sap import _match_po_line, _po_line_index

        first = _po_line(1, "SKU-A", "10")
        second = _po_line(2, "SKU-B", "10")
        po = SimpleNamespace(line_items=[first, second])
        index = _po_line_index(po)

        # Contradictory hints: line number says 2, SKU says A (line 1). Number wins.
        matched = _match_po_line(index, _line(po_line_number=2, buyer_sku="SKU-A"))
        assert matched == second.id

    def test_falls_back_to_buyer_sku(self) -> None:
        from app.workflows.invoice_from_sap import _match_po_line, _po_line_index

        line = _po_line(1, "SKU-A", "10")
        index = _po_line_index(SimpleNamespace(line_items=[line]))
        assert _match_po_line(index, _line(buyer_sku="SKU-A")) == line.id

    def test_falls_back_to_b1_item_code(self) -> None:
        from app.workflows.invoice_from_sap import _match_po_line, _po_line_index

        line = _po_line(1, "SKU-A", "10", item_code="ITM-99")
        index = _po_line_index(SimpleNamespace(line_items=[line]))
        assert _match_po_line(index, _line(b1_item_code="ITM-99")) == line.id

    def test_unmatchable_line_returns_none(self) -> None:
        """An unmatched line is still storable — it just cannot be quantity-checked."""
        from app.workflows.invoice_from_sap import _match_po_line, _po_line_index

        index = _po_line_index(SimpleNamespace(line_items=[_po_line(1, "SKU-A", "10")]))
        assert _match_po_line(index, _line(b1_item_code="NOPE", buyer_sku="NOPE")) is None


class TestOverShipment:
    def test_within_ordered_quantity_passes(self) -> None:
        from app.workflows.invoice_from_sap import _check_over_shipment

        line = _po_line(1, "SKU-A", "100")
        po = SimpleNamespace(id=uuid.uuid4(), line_items=[line])
        inv = _invoice([_line(buyer_sku="SKU-A", qty=Decimal("40"))])

        problems = _check_over_shipment(
            _session_with_prior_lines([]), po, SimpleNamespace(id=uuid.uuid4()), inv
        )
        assert problems == []

    def test_exceeding_ordered_quantity_is_flagged(self) -> None:
        from app.workflows.invoice_from_sap import _check_over_shipment

        line = _po_line(1, "SKU-A", "100")
        po = SimpleNamespace(id=uuid.uuid4(), line_items=[line])
        inv = _invoice([_line(buyer_sku="SKU-A", qty=Decimal("150"))])

        problems = _check_over_shipment(
            _session_with_prior_lines([]), po, SimpleNamespace(id=uuid.uuid4()), inv
        )
        assert len(problems) == 1
        assert "150" in problems[0] and "100" in problems[0]

    def test_earlier_invoices_count_toward_the_total(self) -> None:
        """
        Partial dispatch is normal; the check is cumulative. 60 already invoiced plus
        50 now exceeds the 100 ordered, even though neither invoice does alone.
        """
        from app.workflows.invoice_from_sap import _check_over_shipment

        line = _po_line(1, "SKU-A", "100")
        po = SimpleNamespace(id=uuid.uuid4(), line_items=[line])
        prior = SimpleNamespace(po_line_id=line.id, qty=Decimal("60"))
        inv = _invoice([_line(buyer_sku="SKU-A", qty=Decimal("50"))])

        problems = _check_over_shipment(
            _session_with_prior_lines([prior]), po, SimpleNamespace(id=uuid.uuid4()), inv
        )
        assert len(problems) == 1
        assert "already invoiced" in problems[0]

    def test_second_partial_within_remaining_passes(self) -> None:
        from app.workflows.invoice_from_sap import _check_over_shipment

        line = _po_line(1, "SKU-A", "100")
        po = SimpleNamespace(id=uuid.uuid4(), line_items=[line])
        prior = SimpleNamespace(po_line_id=line.id, qty=Decimal("60"))
        inv = _invoice([_line(buyer_sku="SKU-A", qty=Decimal("40"))])

        problems = _check_over_shipment(
            _session_with_prior_lines([prior]), po, SimpleNamespace(id=uuid.uuid4()), inv
        )
        assert problems == []


class TestTotalReconciliation:
    def test_matching_total_passes(self) -> None:
        from app.workflows.invoice_from_sap import _validate

        line = _po_line(1, "SKU-A", "100")
        po = SimpleNamespace(id=uuid.uuid4(), line_items=[line])
        inv = _invoice(
            [_line(buyer_sku="SKU-A", qty=Decimal("10"), line_total=Decimal("1000.00"))],
            grand_total=Decimal("1000.00"),
        )
        assert _validate(_session_with_prior_lines([]), po, SimpleNamespace(id=uuid.uuid4()), inv) == []

    def test_rounding_residue_within_tolerance_passes(self) -> None:
        """B1 rounds centrally, so a sub-rupee gap is expected, not an error."""
        from app.workflows.invoice_from_sap import _validate

        line = _po_line(1, "SKU-A", "100")
        po = SimpleNamespace(id=uuid.uuid4(), line_items=[line])
        inv = _invoice(
            [_line(buyer_sku="SKU-A", qty=Decimal("10"), line_total=Decimal("1000.00"))],
            grand_total=Decimal("1000.40"),
        )
        assert _validate(_session_with_prior_lines([]), po, SimpleNamespace(id=uuid.uuid4()), inv) == []

    def test_header_disagreeing_with_lines_is_flagged(self) -> None:
        from app.workflows.invoice_from_sap import _validate

        line = _po_line(1, "SKU-A", "100")
        po = SimpleNamespace(id=uuid.uuid4(), line_items=[line])
        inv = _invoice(
            [_line(buyer_sku="SKU-A", qty=Decimal("10"), line_total=Decimal("1000.00"))],
            grand_total=Decimal("9999.00"),
        )
        problems = _validate(_session_with_prior_lines([]), po, SimpleNamespace(id=uuid.uuid4()), inv)
        assert len(problems) == 1
        assert "does not reconcile" in problems[0]


class TestAsnPayload:
    def test_payload_carries_batch_and_expiry(self) -> None:
        from app.workflows.invoice_from_sap import _asn_payload

        po = SimpleNamespace(buyer_po_number="P368477")
        asn = SimpleNamespace(
            asn_number="ASN-INV-001",
            shipment_date=date(2026, 8, 11),
            carrier="Delhivery",
            tracking_number="TRK-9",
            line_items=[SimpleNamespace(
                buyer_sku="SKU-A", b1_item_code="ITM-1", shipped_qty=Decimal("10"),
                batch_number="B-77", expiry_date=date(2027, 1, 31),
            )],
        )
        invoice = SimpleNamespace(
            invoice_number="INV-001", invoice_date=date(2026, 8, 10),
            irn="IRN-123", eway_bill_number="EWB-9", grand_total=Decimal("1000.00"),
        )

        body = _asn_payload(po, asn, invoice)
        assert body["po_number"] == "P368477"
        assert body["invoice_number"] == "INV-001"
        assert body["irn"] == "IRN-123"
        assert body["line_items"][0]["batch_number"] == "B-77"
        assert body["line_items"][0]["expiry_date"] == "2027-01-31"
        # Decimals must serialise as strings — this payload lands in a JSONB column.
        assert body["grand_total"] == "1000.00"
        assert body["line_items"][0]["shipped_qty"] == "10"
