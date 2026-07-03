"""
Canonical EDI schemas — the normalized representation of all inbound/outbound docs.
These are the Pydantic types parsers produce and mappers consume.
"""
from __future__ import annotations

import uuid
from datetime import date  # noqa: TC003
from decimal import Decimal  # noqa: TC003

from pydantic import BaseModel, Field, field_validator

from app.models._enums import EdiDocType, MappingStatus, PoStatus, SourceChannel


class EDIAddress(BaseModel):
    """Generic address block used for ship-to and bill-to."""

    name: str | None = None
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None
    country: str = "India"
    gstin: str | None = None
    warehouse_code: str | None = None


class EDI850Line(BaseModel):
    """One line item of a canonical EDI 850 Purchase Order."""

    line_number: int
    buyer_sku: str
    buyer_sku_description: str | None = None
    hsn_code: str | None = None

    # Quantities
    ordered_qty: Decimal
    buyer_uom: str | None = None

    # Pricing (all INR)
    unit_price: Decimal | None = None
    discount_pct: Decimal | None = None
    taxable_amount: Decimal | None = None

    # GST split — either CGST+SGST (intrastate) or IGST (interstate), never both
    cgst_rate: Decimal | None = None
    cgst_amount: Decimal | None = None
    sgst_rate: Decimal | None = None
    sgst_amount: Decimal | None = None
    igst_rate: Decimal | None = None
    igst_amount: Decimal | None = None
    cess_rate: Decimal | None = None
    cess_amount: Decimal | None = None
    line_total: Decimal | None = None

    # Populated after SKU mapping
    sap_material_no: str | None = None
    inventory_qty: Decimal | None = None
    b1_whs_code: str | None = None
    mapping_status: MappingStatus = MappingStatus.UNMAPPED

    @field_validator("ordered_qty")
    @classmethod
    def qty_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("ordered_qty must be positive")
        return v


class EDI850(BaseModel):
    """
    Canonical representation of one inbound Purchase Order (EDI 850).
    Produced by a parser and consumed by the validator + B1 mapper.
    """

    # Internal identifiers
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    correlation_id: uuid.UUID = Field(default_factory=uuid.uuid4)

    # Source info
    trading_partner_code: str
    source_channel: SourceChannel
    raw_message_id: uuid.UUID | None = None

    # PO identifiers
    buyer_po_number: str
    buyer_po_date: date | None = None
    version: int = 1
    doc_type: EdiDocType = EdiDocType.PO_850

    # Shipping
    ship_to: EDIAddress | None = None
    requested_delivery_date: date | None = None

    # Buyer info
    buyer_gstin: str | None = None
    buyer_name: str | None = None

    # Financials
    currency: str = "INR"
    subtotal_amount: Decimal | None = None
    total_discount: Decimal | None = None
    cgst_amount: Decimal | None = None
    sgst_amount: Decimal | None = None
    igst_amount: Decimal | None = None
    cess_amount: Decimal | None = None
    round_off: Decimal | None = None
    grand_total: Decimal | None = None

    line_items: list[EDI850Line] = Field(default_factory=list)

    # Status after processing
    po_status: PoStatus = PoStatus.RECEIVED

    class Config:
        use_enum_values = False


class ASNLine(BaseModel):
    """One line of an Advance Ship Notice."""

    po_line_id: uuid.UUID | None = None
    buyer_sku: str
    b1_item_code: str | None = None
    shipped_qty: Decimal
    batch_number: str | None = None
    expiry_date: date | None = None


class ASNDoc(BaseModel):
    """Canonical ASN (EDI 856) to be sent to a trading partner."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    po_id: uuid.UUID
    asn_number: str
    shipment_date: date | None = None
    carrier: str | None = None
    tracking_number: str | None = None
    line_items: list[ASNLine] = Field(default_factory=list)


class InvoiceLine(BaseModel):
    """One line of an EDI 810 invoice."""

    po_line_id: uuid.UUID | None = None
    b1_item_code: str | None = None
    description: str | None = None
    hsn_code: str | None = None
    qty: Decimal
    uom: str | None = None
    unit_price: Decimal | None = None
    taxable_amount: Decimal | None = None
    cgst_rate: Decimal | None = None
    cgst_amount: Decimal | None = None
    sgst_rate: Decimal | None = None
    sgst_amount: Decimal | None = None
    igst_rate: Decimal | None = None
    igst_amount: Decimal | None = None
    cess_rate: Decimal | None = None
    cess_amount: Decimal | None = None
    line_total: Decimal | None = None


class InvoiceDoc(BaseModel):
    """Canonical invoice (EDI 810) for outbound sending."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    po_id: uuid.UUID
    asn_id: uuid.UUID | None = None
    invoice_number: str
    invoice_date: date
    subtotal_amount: Decimal | None = None
    cgst_amount: Decimal | None = None
    sgst_amount: Decimal | None = None
    igst_amount: Decimal | None = None
    cess_amount: Decimal | None = None
    round_off: Decimal | None = None
    grand_total: Decimal | None = None
    # Populated after B1 A/R invoice creation
    irn: str | None = None
    eway_bill_number: str | None = None
    eway_bill_date: date | None = None
    line_items: list[InvoiceLine] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    """One validation rule failure or warning."""

    issue_code: str
    severity: str  # ERROR | WARNING | INFO
    message: str
    field_path: str | None = None
    line_number: int | None = None


class ValidationResult(BaseModel):
    """Aggregate result from the validation engine."""

    po_id: uuid.UUID
    issues: list[ValidationIssue] = Field(default_factory=list)
    is_valid: bool = True

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == "WARNING" for i in self.issues)
