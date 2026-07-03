"""
Import all models here so that:
- SQLAlchemy mapper registry is fully populated before the first query.
- Alembic autogenerate can discover all tables.
"""
from app.models._enums import EdiDocType, MappingStatus, PoStatus, SourceChannel, ValidationStatus
from app.models.asn import EdiAdvanceShipNotice, EdiAsnLineItem
from app.models.b1_log import B1ApiLog
from app.models.edi_po import (
    EdiPoLineItem,
    EdiPoStatusHistory,
    EdiPurchaseOrder,
    EdiValidationIssue,
)
from app.models.invoice import EdiInvoice, EdiInvoiceLineItem
from app.models.master_data import (
    MaterialMaster,
    SellerEntity,
    ShipToMapping,
    SkuMapping,
    TradingPartner,
)
from app.models.outbound import EdiOutboundMessage
from app.models.raw_messages import RawMessage

__all__ = [
    "EdiDocType",
    "MappingStatus",
    "PoStatus",
    "SourceChannel",
    "ValidationStatus",
    "EdiAdvanceShipNotice",
    "EdiAsnLineItem",
    "B1ApiLog",
    "EdiPoLineItem",
    "EdiPoStatusHistory",
    "EdiPurchaseOrder",
    "EdiValidationIssue",
    "EdiInvoice",
    "EdiInvoiceLineItem",
    "MaterialMaster",
    "SellerEntity",
    "ShipToMapping",
    "SkuMapping",
    "TradingPartner",
    "EdiOutboundMessage",
    "RawMessage",
]
