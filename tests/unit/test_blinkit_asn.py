"""
Unit tests for the Blinkit ASN (856) payload builder and response reader.

Contract: "POVMS - ASN Sync API Contracts" (rev 100226-093807), archived at
`_archive/backend_old/assets/POVMS-ASN Sync API Contracts-*.txt`.

The response tests carry the most weight. Blinkit returns **2xx for rejections as well
as acceptances** and its own example pairs `"successful": true` with
`"asn_sync_status": "REJECTED"`, so reading the status line instead of the body would
mark a rejected ASN as delivered — and the first anyone would hear of it is a truck
turned away at the DC.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.adapters.outbound.blinkit_asn import (
    BlinkitCaseType,
    BlinkitDeliveryType,
    BlinkitPoStatus,
    build_blinkit_asn_payload,
    interpret_asn_response,
)

# ── Fakes ─────────────────────────────────────────────────────────────────────

class FakeSession:
    """
    Answers the three reads the builder makes: the material for an item code, and the
    ordered / invoiced totals behind po_status.
    """

    def __init__(self, materials: dict, ordered: Decimal, invoiced: Decimal) -> None:
        self._materials = materials
        self._ordered = ordered
        self._invoiced = invoiced
        self._scalar_calls = 0

    def execute(self, stmt):  # noqa: ANN001, ANN201
        entity = stmt.column_descriptions[0].get("entity")
        name = getattr(entity, "__name__", "")
        if name == "MaterialMaster":
            code = stmt.compile().params.get("item_code_1")
            return SimpleNamespace(scalar_one_or_none=lambda: self._materials.get(code))
        # The two aggregate reads arrive in a fixed order: ordered, then invoiced.
        self._scalar_calls += 1
        value = self._ordered if self._scalar_calls == 1 else self._invoiced
        return SimpleNamespace(scalar_one=lambda: value)


def _material(**kw) -> SimpleNamespace:
    base = dict(item_code="FG00310", item_name="Let's Try Pudina Makhana 57g",
                ean_code="8906161390443", case_size=36, mrp=Decimal("160"),
                hsn="21069099", grammage="57g", invntry_uom="PCS")
    base.update(kw)
    return SimpleNamespace(**base)


def _inv_line(**kw) -> SimpleNamespace:
    base = dict(
        b1_item_code="FG00310", description="Let's Try Pudina MaKhana 57g",
        hsn_code="21069099", qty=Decimal("360"), uom="PCS",
        unit_price=Decimal("67.05"), taxable_amount=Decimal("24138.00"),
        cgst_rate=Decimal("2.5"), cgst_amount=Decimal("603.45"),
        sgst_rate=Decimal("2.5"), sgst_amount=Decimal("603.45"),
        igst_rate=Decimal("0"), igst_amount=Decimal("0"),
        cess_rate=Decimal("0"), cess_amount=Decimal("0"),
        line_total=Decimal("25344.90"),
        po_line=SimpleNamespace(buyer_sku="10116317"),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _asn_line(**kw) -> SimpleNamespace:
    base = dict(b1_item_code="FG00310", batch_number="LTF26H12A",
                expiry_date=date(2027, 2, 12), shipped_qty=Decimal("360"))
    base.update(kw)
    return SimpleNamespace(**base)


def _build(inv_lines=None, asn_lines=None, materials=None,
           ordered="360", invoiced="360", **asn_kw):
    inv_lines = inv_lines if inv_lines is not None else [_inv_line()]
    asn_lines = asn_lines if asn_lines is not None else [_asn_line()]
    materials = materials if materials is not None else {"FG00310": _material()}

    po = SimpleNamespace(id="po-1", buyer_po_number="2264110009002",
                         buyer_gstin="27AAECG1234K1Z5")
    asn_base = dict(asn_number="ASN-INV-1", shipment_date=date(2026, 8, 21),
                    carrier="BlueDart", tracking_number="BD-MH-77410238",
                    line_items=asn_lines)
    asn_base.update(asn_kw)
    asn = SimpleNamespace(**asn_base)
    invoice = SimpleNamespace(
        invoice_number="LTF/26-27/001842", invoice_date=date(2026, 8, 20),
        subtotal_amount=Decimal("43508.40"), grand_total=Decimal("45684.00"),
        cess_amount=Decimal("0"), eway_bill_number="351002468971",
        line_items=inv_lines,
    )
    partner = SimpleNamespace(code="BLINKIT", name="Blink Commerce")
    seller = SimpleNamespace(name="Let's Try Foods Private Limited",
                             gstin="27AADCL9999Q1ZY",
                             address_line1="Unit 5, Andheri Industrial Estate",
                             address_line2="", city="Mumbai", state="Maharashtra",
                             pincode="400053", country="India")
    session = FakeSession(materials, Decimal(ordered), Decimal(invoiced))
    return build_blinkit_asn_payload(session, po, asn, invoice, partner, seller)


# ── Response reading ──────────────────────────────────────────────────────────

class TestInterpretResponse:
    def test_accepted(self) -> None:
        ack = interpret_asn_response({
            "successful": True, "asn_sync_status": "ACCEPTED",
            "asn_id": "Blinkit_98765", "success_count": 3,
        })
        assert ack.accepted is True
        assert ack.asn_id == "Blinkit_98765"

    def test_rejection_arrives_as_a_2xx_body(self) -> None:
        """The contract's own example: successful=true alongside REJECTED."""
        ack = interpret_asn_response({
            "successful": True, "success_count": 10, "error_count": 2,
            "asn_sync_status": "REJECTED",
            "data": {"errors": [
                {"code": "E108", "level": "asn", "message": "Invoice date too early"},
                {"code": "E112", "level": "item", "message": "Item IDs are incorrect"},
            ]},
        })
        assert ack.accepted is False
        assert [e["code"] for e in ack.asn_errors] == ["E108"]
        assert [e["code"] for e in ack.item_errors] == ["E112"]
        assert "E108" in ack.summary

    def test_single_asn_level_error_overrides_an_accepted_status(self) -> None:
        ack = interpret_asn_response({
            "asn_sync_status": "ACCEPTED",
            "data": {"errors": [{"code": "E109", "level": "asn",
                                 "message": "Supplier GSTIN mismatch"}]},
        })
        assert ack.accepted is False

    def test_item_errors_alone_do_not_reject(self) -> None:
        ack = interpret_asn_response({
            "asn_sync_status": "PARTIALLY_ACCEPTED",
            "data": {"errors": [{"code": "E112", "level": "item", "message": "bad id"}]},
        })
        assert ack.accepted is True
        assert len(ack.item_errors) == 1

    def test_missing_status_does_not_assume_success(self) -> None:
        """A shape we do not recognise must not be read as acceptance."""
        assert interpret_asn_response({"asn_id": "x", "success": True}).accepted is False
        assert interpret_asn_response({}).accepted is False
        assert interpret_asn_response("nonsense").accepted is False

    def test_missing_status_with_successful_and_no_errors_is_accepted(self) -> None:
        assert interpret_asn_response({"successful": True}).accepted is True


# ── Header ────────────────────────────────────────────────────────────────────

class TestHeader:
    def test_shape(self) -> None:
        payload, warnings = _build()
        assert payload["po_number"] == "2264110009002"
        assert payload["invoice_number"] == "LTF/26-27/001842"
        assert payload["invoice_date"] == "2026-08-20"
        assert payload["delivery_date"] == "2026-08-21"
        assert payload["item_count"] == "1"
        assert payload["quantity"] == "360.00"
        assert payload["basic_price"] == "43508.40"
        assert payload["landing_price"] == "45684.00"
        assert warnings == []

    def test_tax_distribution_groups_by_type_and_rate(self) -> None:
        """Two lines at the same rate produce one CGST row and one SGST row, summed."""
        payload, _ = _build(
            inv_lines=[
                _inv_line(),
                _inv_line(b1_item_code="FG00233", qty=Decimal("240"),
                          taxable_amount=Decimal("15086.40"),
                          cgst_amount=Decimal("377.16"), sgst_amount=Decimal("377.16"),
                          line_total=Decimal("15840.72"),
                          po_line=SimpleNamespace(buyer_sku="10116320")),
            ],
            asn_lines=[_asn_line(), _asn_line(b1_item_code="FG00233")],
            materials={"FG00310": _material(), "FG00233": _material(item_code="FG00233")},
            ordered="600", invoiced="600",
        )
        rows = {r["gst_type"]: r for r in payload["tax_distribution"]}
        assert set(rows) == {"CGST", "SGST"}
        assert rows["CGST"]["gst_total"] == 980.61          # 603.45 + 377.16
        assert rows["CGST"]["taxable_value"] == "39224.40"  # 24138.00 + 15086.40
        assert rows["CGST"]["gst_percentage"] == 2.5

    def test_zero_rate_taxes_are_omitted_from_the_summary(self) -> None:
        payload, _ = _build()
        assert {r["gst_type"] for r in payload["tax_distribution"]} == {"CGST", "SGST"}

    def test_courier_carries_partner_and_tracking(self) -> None:
        payload, warnings = _build()
        ship = payload["shipment_details"]
        assert ship["delivery_type"] == BlinkitDeliveryType.COURIER
        assert ship["delivery_partner"] == "BlueDart"
        assert ship["delivery_tracking_code"] == "BD-MH-77410238"
        assert ship["e_way_bill_number"] == "351002468971"
        assert warnings == []

    def test_no_carrier_means_self_delivery(self) -> None:
        payload, _ = _build(carrier=None, tracking_number=None)
        assert payload["shipment_details"]["delivery_type"] == BlinkitDeliveryType.SELF
        assert "delivery_partner" not in payload["shipment_details"]

    def test_courier_without_tracking_warns(self) -> None:
        """§11.4 makes delivery_tracking_code mandatory when delivery_type is COURIER."""
        _, warnings = _build(tracking_number=None)
        assert any("tracking" in w.lower() for w in warnings)

    def test_po_status_reflects_cumulative_invoicing(self) -> None:
        payload, _ = _build(ordered="360", invoiced="360")
        assert payload["po_status"] == BlinkitPoStatus.FULFILLED

        payload, _ = _build(ordered="360", invoiced="120")
        assert payload["po_status"] == BlinkitPoStatus.PARTIALLY_FULFILLED

    def test_supplier_address_uses_the_json_example_key_names(self) -> None:
        """
        The field table spells these addressLine1/addressLine2 while the contract's own
        JSON and XML examples use address_line_1/address_line_2. The examples are the
        wire format.
        """
        payload, _ = _build()
        addr = payload["supplier_details"]["supplier_address"]
        assert "address_line_1" in addr
        assert addr["address_line_1"] == "Unit 5, Andheri Industrial Estate"
        assert payload["buyer_details"]["gstin"] == "27AAECG1234K1Z5"


# ── Items ─────────────────────────────────────────────────────────────────────

class TestItems:
    def test_every_mandatory_field_is_present(self) -> None:
        payload, _ = _build()
        item = payload["items"][0]
        for key in ("batch_number", "sku_description", "upc", "case_config",
                    "quantity", "mrp", "tax_distribution", "unit_basic_price",
                    "unit_landing_price", "uom"):
            assert item[key] not in (None, ""), key

    def test_all_six_tax_percentages_are_sent_including_zeros(self) -> None:
        """§12.11 marks all six mandatory — omitting a key is not the same as zero."""
        payload, _ = _build()
        tax = payload["items"][0]["tax_distribution"]
        assert set(tax) == {
            "cgst_percentage", "sgst_percentage", "igst_percentage",
            "ugst_percentage", "cess_percentage", "additional_cess_value",
        }
        assert tax["cgst_percentage"] == 2.5
        assert tax["igst_percentage"] == 0

    def test_uom_is_an_object_derived_from_grammage(self) -> None:
        """§12.19 wants unit plus volume-per-unit, not a bare UoM code."""
        payload, _ = _build()
        assert payload["items"][0]["uom"] == {"unit": "g", "value": 57}

    def test_uom_falls_back_without_inventing_a_volume(self) -> None:
        payload, _ = _build(materials={"FG00310": _material(grammage=None)})
        assert payload["items"][0]["uom"] == {"unit": "PCS", "value": 1.0}

    def test_landing_price_is_derived_from_the_line_total(self) -> None:
        """25344.90 / 360 = 70.40 — always agrees with what was invoiced."""
        payload, _ = _build()
        assert payload["items"][0]["unit_landing_price"] == "70.40"

    def test_case_configuration_uses_the_contract_enum(self) -> None:
        payload, _ = _build()
        case = payload["items"][0]["case_configuration"][0]
        assert case["type"] == BlinkitCaseType.CRATE
        assert case["level"] == "outer_case"
        assert case["value"] == 36

    def test_missing_batch_warns(self) -> None:
        _, warnings = _build(asn_lines=[_asn_line(batch_number=None)])
        assert any("batch_number" in w for w in warnings)

    def test_missing_expiry_warns_and_omits_the_field(self) -> None:
        payload, warnings = _build(asn_lines=[_asn_line(expiry_date=None)])
        assert "expiry_date" not in payload["items"][0]
        assert any("expiry" in w.lower() for w in warnings)

    def test_missing_upc_warns(self) -> None:
        _, warnings = _build(materials={"FG00310": _material(ean_code=None)})
        assert any("UPC" in w for w in warnings)

    @pytest.mark.parametrize("field", ["mrp", "case_config"])
    def test_item_master_supplies_catalogue_data(self, field: str) -> None:
        payload, _ = _build()
        expected = {"mrp": 160.0, "case_config": 36}[field]
        assert payload["items"][0][field] == expected


class TestGoNumericEncoding:
    """
    Blinkit's API is Go. `json.Unmarshal` takes `360` into either an int or a
    float64 field but refuses `360.0` for an int one, which is how a valid ASN was
    rejected with "cannot unmarshal number 360.0 into Go struct field
    Item.items.quantity of type int". Integral values must go out as ints.
    """

    def test_integral_decimal_encodes_as_int(self) -> None:
        from app.adapters.outbound.blinkit_asn import _num

        for value in (Decimal("360"), Decimal("360.00"), 360, "360.0"):
            out = _num(value)
            assert isinstance(out, int), f"{value!r} -> {out!r} ({type(out).__name__})"
            assert out == 360

    def test_fractional_decimal_stays_float(self) -> None:
        from app.adapters.outbound.blinkit_asn import _num

        assert _num(Decimal("2.5")) == 2.5
        assert isinstance(_num(Decimal("2.5")), float)
        assert isinstance(_num(Decimal("1087.71")), float)

    def test_zero_encodes_as_int(self) -> None:
        from app.adapters.outbound.blinkit_asn import _num

        assert _num(Decimal("0.00")) == 0
        assert isinstance(_num(Decimal("0.00")), int)

    def test_whole_quantity_needs_no_warning(self) -> None:
        from app.adapters.outbound.blinkit_asn import _qty

        warnings: list[str] = []
        assert _qty(Decimal("360.00"), "Item FG00310", warnings) == 360
        assert warnings == []

    def test_fractional_quantity_rounds_and_warns(self) -> None:
        from app.adapters.outbound.blinkit_asn import _qty

        warnings: list[str] = []
        assert _qty(Decimal("12.4"), "Item FG00310", warnings) == 12
        assert len(warnings) == 1
        assert "not a whole number" in warnings[0]
        assert "FG00310" in warnings[0]

    def test_uom_value_is_integral_for_whole_grammage(self) -> None:
        from app.adapters.outbound.blinkit_asn import _uom

        material = SimpleNamespace(grammage="57g", invntry_uom="PCS")
        unit, value = _uom(material, SimpleNamespace(uom="PCS"))

        assert unit == "g"
        assert value == 57
        assert isinstance(value, int)

    def test_uom_fallback_value_is_int(self) -> None:
        from app.adapters.outbound.blinkit_asn import _uom

        material = SimpleNamespace(grammage=None, invntry_uom="PCS")
        _unit, value = _uom(material, SimpleNamespace(uom="PCS"))

        assert value == 1
        assert isinstance(value, int)


class TestContractTypeOverrides:
    """
    Blinkit's field table disagrees with its running API and with its own JSON
    examples. Where they conflict the API wins, so these encodings are pinned by
    test — a "tidy-up" back to the documented types would break dispatch.
    """

    def test_item_tax_percentages_are_numbers(self) -> None:
        """
        §12.11 types all six as string. The API rejects that:
          cannot unmarshal string into Go struct field
          ItemTaxDistribution.items.tax_distribution.cess_percentage of type float64
        """
        payload, _ = _build()
        taxes = payload["items"][0]["tax_distribution"]

        for key in (
            "cgst_percentage", "sgst_percentage", "igst_percentage",
            "ugst_percentage", "cess_percentage", "additional_cess_value",
        ):
            assert isinstance(taxes[key], (int, float)), f"{key} must be numeric, got {taxes[key]!r}"
            assert not isinstance(taxes[key], str)

    def test_unit_basic_price_is_a_number(self) -> None:
        """Unquoted in every contract JSON example, unlike its neighbours."""
        item = _build()[0]["items"][0]

        assert isinstance(item["unit_basic_price"], (int, float))
        assert not isinstance(item["unit_basic_price"], str)

    def test_neighbouring_price_fields_stay_strings(self) -> None:
        """Quoted in the same examples — changing these would break what works."""
        payload, _ = _build()

        assert isinstance(payload["items"][0]["unit_landing_price"], str)
        assert isinstance(payload["basic_price"], str)
        assert isinstance(payload["landing_price"], str)
        assert isinstance(payload["tax_distribution"][0]["taxable_value"], str)

    def test_header_counts_stay_strings(self) -> None:
        """§6/§7 say string and the API accepted them — decoding reached the items."""
        payload, _ = _build()

        assert isinstance(payload["quantity"], str)
        assert isinstance(payload["item_count"], str)
