"""
Unit tests for SQLAlchemy models — save/reload, FK constraints, soft-delete.

These tests require a real PostgreSQL database. Set DATABASE_SYNC_URL in .env
or export it before running:

    export DATABASE_SYNC_URL=postgresql+psycopg2://edi:edipass@localhost:5432/edi_middleware_test
    pytest tests/unit/test_models.py -v
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import (
    B1ApiLog,
    EdiPoLineItem,
    EdiPoStatusHistory,
    EdiPurchaseOrder,
    EdiValidationIssue,
    MaterialMaster,
    SellerEntity,
    ShipToMapping,
    SkuMapping,
    TradingPartner,
)
from app.models._enums import MappingStatus, PoStatus, SourceChannel, ValidationStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def engine():
    """Creates all tables in a test DB, drops them after the session."""
    from app.config import get_settings
    settings = get_settings()
    test_url = settings.database_sync_url.replace(
        "/edi_middleware", "/edi_middleware_test"
    )
    eng = create_engine(test_url, echo=False)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    """Provides a transactional session that rolls back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    Session_ = sessionmaker(bind=connection)
    sess = Session_()
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def seller(session: Session) -> SellerEntity:
    s = SellerEntity(
        id=uuid.uuid4(),
        name="Let's Try Foods Private Limited",
        gstin="27AADCL9999Q1ZY",
        b1_company_db="LETSTRY",
        country="India",
    )
    session.add(s)
    session.flush()
    return s


@pytest.fixture
def partner(session: Session) -> TradingPartner:
    p = TradingPartner(
        id=uuid.uuid4(),
        code="BLINKIT",
        name="Blinkit",
        source_channel=SourceChannel.WEBHOOK,
        ack_sla_hours=4,
        asn_sla_hours=12,
        is_active=True,
    )
    session.add(p)
    session.flush()
    return p


@pytest.fixture
def material(session: Session) -> MaterialMaster:
    m = MaterialMaster(
        id=uuid.uuid4(),
        b1_item_code="LTFM001",
        description="Peri Peri Makhana 30g",
        hsn_code="20089900",
        gst_rate=12.0,
        uom="PCS",
        is_active=True,
    )
    session.add(m)
    session.flush()
    return m


@pytest.fixture
def purchase_order(session: Session, partner: TradingPartner, seller: SellerEntity) -> EdiPurchaseOrder:
    po = EdiPurchaseOrder(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        trading_partner_id=partner.id,
        seller_entity_id=seller.id,
        buyer_po_number="BL-2026-00001",
        buyer_po_date=date(2026, 6, 29),
        po_status=PoStatus.RECEIVED,
        currency="INR",
        grand_total=1180.00,
    )
    session.add(po)
    session.flush()
    return po


# ── Tests: master data ────────────────────────────────────────────────────────

class TestSellerEntity:
    def test_save_and_reload(self, session: Session, seller: SellerEntity) -> None:
        reloaded = session.get(SellerEntity, seller.id)
        assert reloaded is not None
        assert reloaded.name == "Let's Try Foods Private Limited"
        assert reloaded.gstin == "27AADCL9999Q1ZY"
        assert reloaded.country == "India"

    def test_soft_delete_field_exists(self, session: Session, seller: SellerEntity) -> None:
        assert seller.deleted_at is None
        seller.deleted_at = datetime.now(timezone.utc)
        session.flush()
        reloaded = session.get(SellerEntity, seller.id)
        assert reloaded.deleted_at is not None


class TestTradingPartner:
    def test_save_and_reload(self, session: Session, partner: TradingPartner) -> None:
        reloaded = session.get(TradingPartner, partner.id)
        assert reloaded is not None
        assert reloaded.code == "BLINKIT"
        assert reloaded.source_channel == SourceChannel.WEBHOOK
        assert reloaded.is_active is True

    def test_unique_code_constraint(self, session: Session, partner: TradingPartner) -> None:
        duplicate = TradingPartner(
            id=uuid.uuid4(),
            code="BLINKIT",  # same code
            name="Duplicate",
            source_channel=SourceChannel.API,
            ack_sla_hours=4,
            asn_sla_hours=12,
            is_active=True,
        )
        session.add(duplicate)
        with pytest.raises(Exception):
            session.flush()


class TestSkuMapping:
    def test_save_and_reload(
        self, session: Session, partner: TradingPartner, material: MaterialMaster
    ) -> None:
        mapping = SkuMapping(
            id=uuid.uuid4(),
            trading_partner_id=partner.id,
            buyer_sku="8901234560001",
            material_id=material.id,
            qty_per_buyer_uom=1,
            buyer_uom="PCS",
            mapping_status=MappingStatus.MANUALLY_MAPPED,
        )
        session.add(mapping)
        session.flush()

        reloaded = session.get(SkuMapping, mapping.id)
        assert reloaded is not None
        assert reloaded.buyer_sku == "8901234560001"
        assert reloaded.mapping_status == MappingStatus.MANUALLY_MAPPED

    def test_unique_partner_sku_constraint(
        self, session: Session, partner: TradingPartner, material: MaterialMaster
    ) -> None:
        for _ in range(2):
            session.add(SkuMapping(
                id=uuid.uuid4(),
                trading_partner_id=partner.id,
                buyer_sku="DUPE-SKU",
                material_id=material.id,
                qty_per_buyer_uom=1,
                mapping_status=MappingStatus.MANUALLY_MAPPED,
            ))
        with pytest.raises(Exception):
            session.flush()

    def test_unmapped_sku_no_material(self, session: Session, partner: TradingPartner) -> None:
        mapping = SkuMapping(
            id=uuid.uuid4(),
            trading_partner_id=partner.id,
            buyer_sku="UNKNOWN-SKU",
            material_id=None,
            qty_per_buyer_uom=1,
            mapping_status=MappingStatus.UNMAPPED,
        )
        session.add(mapping)
        session.flush()
        reloaded = session.get(SkuMapping, mapping.id)
        assert reloaded.material_id is None
        assert reloaded.mapping_status == MappingStatus.UNMAPPED


class TestShipToMapping:
    def test_save_and_reload(self, session: Session, partner: TradingPartner) -> None:
        s = ShipToMapping(
            id=uuid.uuid4(),
            trading_partner_id=partner.id,
            buyer_warehouse_code="BL-MUM-001",
            buyer_warehouse_name="Blinkit Mumbai DC",
            b1_whs_code="WH01",
            mapping_status=MappingStatus.MANUALLY_MAPPED,
        )
        session.add(s)
        session.flush()
        reloaded = session.get(ShipToMapping, s.id)
        assert reloaded.b1_whs_code == "WH01"


# ── Tests: EDI purchase orders ────────────────────────────────────────────────

class TestEdiPurchaseOrder:
    def test_save_and_reload(self, session: Session, purchase_order: EdiPurchaseOrder) -> None:
        reloaded = session.get(EdiPurchaseOrder, purchase_order.id)
        assert reloaded is not None
        assert reloaded.buyer_po_number == "BL-2026-00001"
        assert reloaded.po_status == PoStatus.RECEIVED
        assert reloaded.grand_total == pytest.approx(1180.00)

    def test_fk_trading_partner(self, session: Session, purchase_order: EdiPurchaseOrder) -> None:
        reloaded = session.get(EdiPurchaseOrder, purchase_order.id)
        assert reloaded.trading_partner_id is not None

    def test_soft_delete(self, session: Session, purchase_order: EdiPurchaseOrder) -> None:
        assert purchase_order.deleted_at is None
        purchase_order.deleted_at = datetime.now(timezone.utc)
        session.flush()
        reloaded = session.get(EdiPurchaseOrder, purchase_order.id)
        assert reloaded.deleted_at is not None

    def test_unique_po_number_per_partner(
        self, session: Session, purchase_order: EdiPurchaseOrder,
        partner: TradingPartner, seller: SellerEntity
    ) -> None:
        duplicate = EdiPurchaseOrder(
            id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            trading_partner_id=partner.id,
            seller_entity_id=seller.id,
            buyer_po_number="BL-2026-00001",  # same PO number + same version
            po_status=PoStatus.RECEIVED,
            currency="INR",
        )
        session.add(duplicate)
        with pytest.raises(Exception):
            session.flush()


class TestEdiPoLineItem:
    def test_save_and_reload(
        self, session: Session, purchase_order: EdiPurchaseOrder
    ) -> None:
        line = EdiPoLineItem(
            id=uuid.uuid4(),
            po_id=purchase_order.id,
            line_number=1,
            buyer_sku="8901234560001",
            ordered_qty=100,
            buyer_uom="PCS",
            unit_price=10.00,
            taxable_amount=1000.00,
            cgst_rate=6.0,
            cgst_amount=60.0,
            sgst_rate=6.0,
            sgst_amount=60.0,
            line_total=1120.00,
        )
        session.add(line)
        session.flush()

        reloaded = session.get(EdiPoLineItem, line.id)
        assert reloaded.buyer_sku == "8901234560001"
        assert reloaded.ordered_qty == pytest.approx(100)

    def test_unique_line_number_per_po(
        self, session: Session, purchase_order: EdiPurchaseOrder
    ) -> None:
        for _ in range(2):
            session.add(EdiPoLineItem(
                id=uuid.uuid4(),
                po_id=purchase_order.id,
                line_number=1,  # duplicate line number
                buyer_sku="SKU-A",
                ordered_qty=1,
            ))
        with pytest.raises(Exception):
            session.flush()


class TestEdiValidationIssue:
    def test_save_open_issue(
        self, session: Session, purchase_order: EdiPurchaseOrder
    ) -> None:
        issue = EdiValidationIssue(
            id=uuid.uuid4(),
            po_id=purchase_order.id,
            issue_code="E001_UNMAPPED_SKU",
            severity="ERROR",
            message="SKU 8901234560099 has no mapping for BLINKIT",
            field_path="line_items[0].buyer_sku",
            validation_status=ValidationStatus.OPEN,
        )
        session.add(issue)
        session.flush()

        reloaded = session.get(EdiValidationIssue, issue.id)
        assert reloaded.validation_status == ValidationStatus.OPEN
        assert reloaded.severity == "ERROR"

    def test_resolve_issue(
        self, session: Session, purchase_order: EdiPurchaseOrder
    ) -> None:
        issue = EdiValidationIssue(
            id=uuid.uuid4(),
            po_id=purchase_order.id,
            issue_code="W001_PRICE_VARIANCE",
            severity="WARNING",
            message="Price variance 3.2% exceeds 3% threshold",
            validation_status=ValidationStatus.OPEN,
        )
        session.add(issue)
        session.flush()

        issue.validation_status = ValidationStatus.RESOLVED
        issue.resolved_by = "ops@letstryfoods.com"
        issue.resolved_at = datetime.now(timezone.utc)
        issue.resolution_notes = "Accepted after checking with commercial team"
        session.flush()

        reloaded = session.get(EdiValidationIssue, issue.id)
        assert reloaded.validation_status == ValidationStatus.RESOLVED
        assert reloaded.resolved_by == "ops@letstryfoods.com"


class TestB1ApiLog:
    def test_save_immutable_log(
        self, session: Session, purchase_order: EdiPurchaseOrder
    ) -> None:
        log = B1ApiLog(
            id=uuid.uuid4(),
            po_id=purchase_order.id,
            operation="CREATE_SO",
            http_method="POST",
            endpoint="/b1s/v1/Orders",
            request_body={"CardCode": "C00012"},
            response_status=201,
            response_body={"DocEntry": 42, "DocNum": 1001},
            duration_ms=342,
        )
        session.add(log)
        session.flush()

        reloaded = session.get(B1ApiLog, log.id)
        assert reloaded.operation == "CREATE_SO"
        assert reloaded.response_status == 201
        assert reloaded.response_body["DocEntry"] == 42
