from __future__ import annotations

import enum


class SourceChannel(enum.StrEnum):
    EMAIL = "EMAIL"
    API = "API"
    WEBHOOK = "WEBHOOK"
    PORTAL = "PORTAL"
    MANUAL = "MANUAL"


class EdiDocType(enum.StrEnum):
    PO_850 = "PO_850"
    PO_ACK_855 = "PO_ACK_855"
    ASN_856 = "ASN_856"
    INVOICE_810 = "INVOICE_810"
    CREDIT_NOTE = "CREDIT_NOTE"
    RTV = "RTV"


class PoStatus(enum.StrEnum):
    RECEIVED = "RECEIVED"
    PARSED = "PARSED"
    VALIDATED = "VALIDATED"
    EXCEPTION = "EXCEPTION"
    SAP_PENDING = "SAP_PENDING"
    SAP_CONFIRMED = "SAP_CONFIRMED"
    SAP_REJECTED = "SAP_REJECTED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class ValidationStatus(enum.StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    SUPPRESSED = "SUPPRESSED"


class MappingStatus(enum.StrEnum):
    UNMAPPED = "UNMAPPED"
    AUTO_MAPPED = "AUTO_MAPPED"
    MANUALLY_MAPPED = "MANUALLY_MAPPED"
