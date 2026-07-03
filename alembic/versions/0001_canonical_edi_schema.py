"""canonical EDI schema — all tables, enums, triggers, views

Revision ID: 0001
Revises: 0000
Create Date: 2026-06-29 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = "0000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""

-- ── 1. Enum types ──────────────────────────────────────────────────────────
CREATE TYPE source_channel_t   AS ENUM ('EMAIL', 'API', 'WEBHOOK', 'PORTAL', 'MANUAL');
CREATE TYPE edi_doc_type_t     AS ENUM ('PO_850', 'PO_ACK_855', 'ASN_856', 'INVOICE_810', 'CREDIT_NOTE', 'RTV');
CREATE TYPE po_status_t        AS ENUM ('RECEIVED', 'PARSED', 'VALIDATED', 'EXCEPTION', 'SAP_PENDING', 'SAP_CONFIRMED', 'SAP_REJECTED', 'CANCELLED');
CREATE TYPE validation_status_t AS ENUM ('OPEN', 'RESOLVED', 'SUPPRESSED');
CREATE TYPE mapping_status_t   AS ENUM ('UNMAPPED', 'AUTO_MAPPED', 'MANUALLY_MAPPED');

-- ── 2. updated_at trigger function ─────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── 3. seller_entities ─────────────────────────────────────────────────────
CREATE TABLE seller_entities (
    id              UUID PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    gstin           VARCHAR(15),
    b1_company_db   VARCHAR(100),
    b1_server_url   VARCHAR(500),
    address_line1   VARCHAR(500),
    address_line2   VARCHAR(500),
    city            VARCHAR(100),
    state           VARCHAR(100),
    pincode         VARCHAR(10),
    country         VARCHAR(50)  NOT NULL DEFAULT 'India',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);
CREATE TRIGGER trg_seller_entities_updated_at
    BEFORE UPDATE ON seller_entities
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 4. trading_partners ────────────────────────────────────────────────────
CREATE TABLE trading_partners (
    id              UUID PRIMARY KEY,
    code            VARCHAR(50)       NOT NULL UNIQUE,
    name            VARCHAR(255)      NOT NULL,
    b1_card_code    VARCHAR(50),
    gstin           VARCHAR(15),
    source_channel  source_channel_t  NOT NULL,
    gmail_label     VARCHAR(200),
    webhook_secret  VARCHAR(500),
    api_config      JSONB,
    ack_sla_hours   INTEGER           NOT NULL DEFAULT 24,
    asn_sla_hours   INTEGER           NOT NULL DEFAULT 48,
    is_active       BOOLEAN           NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);
CREATE TRIGGER trg_trading_partners_updated_at
    BEFORE UPDATE ON trading_partners
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 5. material_master ─────────────────────────────────────────────────────
CREATE TABLE material_master (
    id              UUID PRIMARY KEY,
    b1_item_code    VARCHAR(50)  NOT NULL UNIQUE,
    description     VARCHAR(500) NOT NULL,
    hsn_code        VARCHAR(10),
    gst_rate        NUMERIC(5,2),
    uom             VARCHAR(20)  NOT NULL,
    uom_group       VARCHAR(50),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);
CREATE TRIGGER trg_material_master_updated_at
    BEFORE UPDATE ON material_master
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 6. raw_messages ────────────────────────────────────────────────────────
CREATE TABLE raw_messages (
    id                  UUID PRIMARY KEY,
    trading_partner_id  UUID             NOT NULL REFERENCES trading_partners(id),
    source_channel      source_channel_t NOT NULL,
    external_id         VARCHAR(500)     NOT NULL,
    received_at         TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    headers             JSONB,
    payload             JSONB,
    payload_raw         TEXT,
    attachment_paths    JSONB,
    processed           BOOLEAN          NOT NULL DEFAULT FALSE,
    parse_status        VARCHAR(20)      NOT NULL DEFAULT 'PENDING',
    created_at          TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_raw_message_partner_ext UNIQUE (trading_partner_id, external_id)
);
CREATE INDEX ix_raw_messages_partner     ON raw_messages (trading_partner_id);
CREATE INDEX ix_raw_messages_received_at ON raw_messages (received_at);
CREATE INDEX ix_raw_messages_parse_status ON raw_messages (parse_status);

-- ── 7. sku_mapping ─────────────────────────────────────────────────────────
CREATE TABLE sku_mapping (
    id                    UUID PRIMARY KEY,
    trading_partner_id    UUID              NOT NULL REFERENCES trading_partners(id),
    buyer_sku             VARCHAR(100)      NOT NULL,
    buyer_sku_description VARCHAR(500),
    material_id           UUID              REFERENCES material_master(id),
    qty_per_buyer_uom     NUMERIC(12,4)     NOT NULL DEFAULT 1,
    buyer_uom             VARCHAR(20),
    mapping_status        mapping_status_t  NOT NULL DEFAULT 'UNMAPPED',
    confidence_score      NUMERIC(5,4),
    notes                 TEXT,
    created_at            TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    deleted_at            TIMESTAMPTZ,
    CONSTRAINT uq_sku_mapping_partner_sku UNIQUE (trading_partner_id, buyer_sku)
);
CREATE INDEX ix_sku_mapping_buyer_sku ON sku_mapping (buyer_sku);
CREATE INDEX ix_sku_mapping_partner   ON sku_mapping (trading_partner_id);
CREATE TRIGGER trg_sku_mapping_updated_at
    BEFORE UPDATE ON sku_mapping
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 8. ship_to_mapping ─────────────────────────────────────────────────────
CREATE TABLE ship_to_mapping (
    id                    UUID PRIMARY KEY,
    trading_partner_id    UUID             NOT NULL REFERENCES trading_partners(id),
    buyer_warehouse_code  VARCHAR(100)     NOT NULL,
    buyer_warehouse_name  VARCHAR(500),
    b1_whs_code           VARCHAR(20),
    mapping_status        mapping_status_t NOT NULL DEFAULT 'UNMAPPED',
    notes                 TEXT,
    created_at            TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    deleted_at            TIMESTAMPTZ,
    CONSTRAINT uq_ship_to_partner_whs UNIQUE (trading_partner_id, buyer_warehouse_code)
);
CREATE INDEX ix_ship_to_mapping_partner ON ship_to_mapping (trading_partner_id);
CREATE TRIGGER trg_ship_to_mapping_updated_at
    BEFORE UPDATE ON ship_to_mapping
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 9. edi_purchase_orders ─────────────────────────────────────────────────
CREATE TABLE edi_purchase_orders (
    id                       UUID PRIMARY KEY,
    correlation_id           UUID             NOT NULL UNIQUE,
    trading_partner_id       UUID             NOT NULL REFERENCES trading_partners(id),
    seller_entity_id         UUID             NOT NULL REFERENCES seller_entities(id),
    raw_message_id           UUID             REFERENCES raw_messages(id),
    buyer_po_number          VARCHAR(200)     NOT NULL,
    buyer_po_date            DATE,
    version                  INTEGER          NOT NULL DEFAULT 1,
    doc_type                 edi_doc_type_t   NOT NULL DEFAULT 'PO_850',
    po_status                po_status_t      NOT NULL DEFAULT 'RECEIVED',
    ship_to_code             VARCHAR(100),
    ship_to_name             VARCHAR(500),
    ship_to_address          JSONB,
    requested_delivery_date  DATE,
    currency                 VARCHAR(3)       NOT NULL DEFAULT 'INR',
    subtotal_amount          NUMERIC(15,2),
    total_discount           NUMERIC(15,2),
    cgst_amount              NUMERIC(15,2),
    sgst_amount              NUMERIC(15,2),
    igst_amount              NUMERIC(15,2),
    cess_amount              NUMERIC(15,2),
    round_off                NUMERIC(5,2),
    grand_total              NUMERIC(15,2),
    buyer_gstin              VARCHAR(15),
    buyer_name               VARCHAR(255),
    b1_sales_order_doc_entry INTEGER,
    b1_sales_order_doc_num   INTEGER,
    b1_pushed_at             TIMESTAMPTZ,
    b1_error_message         TEXT,
    created_at               TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    deleted_at               TIMESTAMPTZ,
    CONSTRAINT uq_po_partner_number_ver UNIQUE (trading_partner_id, buyer_po_number, version)
);
CREATE INDEX ix_edi_po_partner        ON edi_purchase_orders (trading_partner_id);
CREATE INDEX ix_edi_po_status         ON edi_purchase_orders (po_status);
CREATE INDEX ix_edi_po_buyer_po_number ON edi_purchase_orders (buyer_po_number);
CREATE INDEX ix_edi_po_created_at     ON edi_purchase_orders (created_at);
CREATE TRIGGER trg_edi_purchase_orders_updated_at
    BEFORE UPDATE ON edi_purchase_orders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 10. edi_po_line_items ──────────────────────────────────────────────────
CREATE TABLE edi_po_line_items (
    id                   UUID PRIMARY KEY,
    po_id                UUID          NOT NULL REFERENCES edi_purchase_orders(id),
    sku_mapping_id       UUID          REFERENCES sku_mapping(id),
    line_number          INTEGER       NOT NULL,
    buyer_sku            VARCHAR(100)  NOT NULL,
    buyer_sku_description VARCHAR(500),
    hsn_code             VARCHAR(10),
    ordered_qty          NUMERIC(12,4) NOT NULL,
    accepted_qty         NUMERIC(12,4),
    shipped_qty          NUMERIC(12,4),
    invoiced_qty         NUMERIC(12,4),
    buyer_uom            VARCHAR(20),
    inventory_qty        NUMERIC(12,4),
    unit_price           NUMERIC(15,6),
    discount_pct         NUMERIC(5,2),
    taxable_amount       NUMERIC(15,2),
    cgst_rate            NUMERIC(5,2),
    cgst_amount          NUMERIC(15,2),
    sgst_rate            NUMERIC(5,2),
    sgst_amount          NUMERIC(15,2),
    igst_rate            NUMERIC(5,2),
    igst_amount          NUMERIC(15,2),
    cess_rate            NUMERIC(5,2),
    cess_amount          NUMERIC(15,2),
    line_total           NUMERIC(15,2),
    sap_material_no      VARCHAR(50),
    b1_whs_code          VARCHAR(20),
    created_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_po_line_number UNIQUE (po_id, line_number)
);
CREATE INDEX ix_edi_po_line_po_id    ON edi_po_line_items (po_id);
CREATE INDEX ix_edi_po_line_buyer_sku ON edi_po_line_items (buyer_sku);
CREATE TRIGGER trg_edi_po_line_items_updated_at
    BEFORE UPDATE ON edi_po_line_items
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 11. edi_po_status_history ──────────────────────────────────────────────
CREATE TABLE edi_po_status_history (
    id          UUID PRIMARY KEY,
    po_id       UUID        NOT NULL REFERENCES edi_purchase_orders(id),
    from_status po_status_t,
    to_status   po_status_t NOT NULL,
    changed_by  VARCHAR(100) NOT NULL DEFAULT 'system',
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_po_status_history_po_id ON edi_po_status_history (po_id);

-- ── 12. edi_validation_issues ──────────────────────────────────────────────
CREATE TABLE edi_validation_issues (
    id                UUID PRIMARY KEY,
    po_id             UUID                NOT NULL REFERENCES edi_purchase_orders(id),
    line_id           UUID                REFERENCES edi_po_line_items(id),
    issue_code        VARCHAR(50)         NOT NULL,
    severity          VARCHAR(10)         NOT NULL,
    message           TEXT                NOT NULL,
    field_path        VARCHAR(200),
    validation_status validation_status_t NOT NULL DEFAULT 'OPEN',
    resolved_by       VARCHAR(100),
    resolved_at       TIMESTAMPTZ,
    resolution_notes  TEXT,
    created_at        TIMESTAMPTZ         NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ         NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_validation_issues_po_id  ON edi_validation_issues (po_id);
CREATE INDEX ix_validation_issues_status ON edi_validation_issues (validation_status);
CREATE INDEX ix_validation_issues_code   ON edi_validation_issues (issue_code);
CREATE TRIGGER trg_edi_validation_issues_updated_at
    BEFORE UPDATE ON edi_validation_issues
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 13. edi_outbound_messages ──────────────────────────────────────────────
CREATE TABLE edi_outbound_messages (
    id                  UUID PRIMARY KEY,
    po_id               UUID           NOT NULL REFERENCES edi_purchase_orders(id),
    trading_partner_id  UUID           NOT NULL REFERENCES trading_partners(id),
    doc_type            edi_doc_type_t NOT NULL,
    external_reference  VARCHAR(200),
    payload             JSONB,
    channel             VARCHAR(20)    NOT NULL DEFAULT 'API',
    status              VARCHAR(20)    NOT NULL DEFAULT 'PENDING',
    attempt_count       INTEGER        NOT NULL DEFAULT 0,
    last_attempt_at     TIMESTAMPTZ,
    next_retry_at       TIMESTAMPTZ,
    ack_received_at     TIMESTAMPTZ,
    error_message       TEXT,
    created_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_outbound_po_id      ON edi_outbound_messages (po_id);
CREATE INDEX ix_outbound_status     ON edi_outbound_messages (status);
CREATE INDEX ix_outbound_next_retry ON edi_outbound_messages (next_retry_at);
CREATE TRIGGER trg_edi_outbound_messages_updated_at
    BEFORE UPDATE ON edi_outbound_messages
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 14. edi_advance_ship_notices ───────────────────────────────────────────
CREATE TABLE edi_advance_ship_notices (
    id                    UUID PRIMARY KEY,
    po_id                 UUID        NOT NULL REFERENCES edi_purchase_orders(id),
    trading_partner_id    UUID        NOT NULL REFERENCES trading_partners(id),
    asn_number            VARCHAR(100) NOT NULL UNIQUE,
    shipment_date         DATE,
    carrier               VARCHAR(100),
    tracking_number       VARCHAR(200),
    b1_delivery_doc_entry INTEGER,
    b1_delivery_doc_num   INTEGER,
    status                VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_asn_po_id   ON edi_advance_ship_notices (po_id);
CREATE INDEX ix_asn_number  ON edi_advance_ship_notices (asn_number);
CREATE TRIGGER trg_edi_advance_ship_notices_updated_at
    BEFORE UPDATE ON edi_advance_ship_notices
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 15. edi_asn_line_items ─────────────────────────────────────────────────
CREATE TABLE edi_asn_line_items (
    id            UUID PRIMARY KEY,
    asn_id        UUID          NOT NULL REFERENCES edi_advance_ship_notices(id),
    po_line_id    UUID          REFERENCES edi_po_line_items(id),
    shipped_qty   NUMERIC(12,4) NOT NULL,
    buyer_sku     VARCHAR(100),
    b1_item_code  VARCHAR(50),
    batch_number  VARCHAR(100),
    expiry_date   DATE,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_asn_line_asn_id ON edi_asn_line_items (asn_id);

-- ── 16. edi_invoices ───────────────────────────────────────────────────────
CREATE TABLE edi_invoices (
    id                   UUID PRIMARY KEY,
    po_id                UUID        NOT NULL REFERENCES edi_purchase_orders(id),
    asn_id               UUID        REFERENCES edi_advance_ship_notices(id),
    trading_partner_id   UUID        NOT NULL REFERENCES trading_partners(id),
    invoice_number       VARCHAR(100) NOT NULL UNIQUE,
    invoice_date         DATE        NOT NULL,
    b1_invoice_doc_entry INTEGER,
    b1_invoice_doc_num   INTEGER,
    irn                  VARCHAR(200),
    eway_bill_number     VARCHAR(50),
    eway_bill_date       DATE,
    subtotal_amount      NUMERIC(15,2),
    cgst_amount          NUMERIC(15,2),
    sgst_amount          NUMERIC(15,2),
    igst_amount          NUMERIC(15,2),
    cess_amount          NUMERIC(15,2),
    round_off            NUMERIC(5,2),
    grand_total          NUMERIC(15,2),
    status               VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_invoice_po_id  ON edi_invoices (po_id);
CREATE INDEX ix_invoice_number ON edi_invoices (invoice_number);
CREATE TRIGGER trg_edi_invoices_updated_at
    BEFORE UPDATE ON edi_invoices
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 17. edi_invoice_line_items ─────────────────────────────────────────────
CREATE TABLE edi_invoice_line_items (
    id              UUID PRIMARY KEY,
    invoice_id      UUID          NOT NULL REFERENCES edi_invoices(id),
    po_line_id      UUID          REFERENCES edi_po_line_items(id),
    b1_item_code    VARCHAR(50),
    description     VARCHAR(500),
    hsn_code        VARCHAR(10),
    qty             NUMERIC(12,4) NOT NULL,
    uom             VARCHAR(20),
    unit_price      NUMERIC(15,6),
    taxable_amount  NUMERIC(15,2),
    cgst_rate       NUMERIC(5,2),
    cgst_amount     NUMERIC(15,2),
    sgst_rate       NUMERIC(5,2),
    sgst_amount     NUMERIC(15,2),
    igst_rate       NUMERIC(5,2),
    igst_amount     NUMERIC(15,2),
    cess_rate       NUMERIC(5,2),
    cess_amount     NUMERIC(15,2),
    line_total      NUMERIC(15,2),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_invoice_line_invoice_id ON edi_invoice_line_items (invoice_id);

-- ── 18. b1_api_log ─────────────────────────────────────────────────────────
CREATE TABLE b1_api_log (
    id              UUID PRIMARY KEY,
    po_id           UUID         REFERENCES edi_purchase_orders(id),
    operation       VARCHAR(100) NOT NULL,
    http_method     VARCHAR(10)  NOT NULL,
    endpoint        VARCHAR(500) NOT NULL,
    request_body    JSONB,
    response_status INTEGER,
    response_body   JSONB,
    duration_ms     INTEGER,
    b1_session_id   VARCHAR(200),
    error_code      VARCHAR(50),
    error_message   TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_b1_log_po_id          ON b1_api_log (po_id);
CREATE INDEX ix_b1_log_created_at     ON b1_api_log (created_at);
CREATE INDEX ix_b1_log_response_status ON b1_api_log (response_status);
CREATE INDEX ix_b1_log_operation      ON b1_api_log (operation);

-- ── 19. PO status history auto-log trigger ─────────────────────────────────
CREATE OR REPLACE FUNCTION log_po_status_change()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.po_status IS DISTINCT FROM NEW.po_status THEN
        INSERT INTO edi_po_status_history (id, po_id, from_status, to_status, changed_by, created_at)
        VALUES (gen_random_uuid(), NEW.id, OLD.po_status, NEW.po_status, 'system', NOW());
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_po_status_history
    AFTER UPDATE ON edi_purchase_orders
    FOR EACH ROW EXECUTE FUNCTION log_po_status_change();

-- ── 20. Views ──────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_po_summary AS
SELECT
    p.id,
    p.correlation_id,
    p.buyer_po_number,
    p.buyer_po_date,
    p.po_status,
    p.grand_total,
    p.currency,
    p.buyer_gstin,
    p.created_at,
    p.updated_at,
    tp.code             AS partner_code,
    tp.name             AS partner_name,
    COUNT(l.id)         AS line_count,
    SUM(l.ordered_qty)  AS total_ordered_qty,
    COUNT(vi.id) FILTER (WHERE vi.validation_status = 'OPEN' AND vi.severity = 'ERROR')
                        AS open_errors,
    COUNT(vi.id) FILTER (WHERE vi.validation_status = 'OPEN' AND vi.severity = 'WARNING')
                        AS open_warnings
FROM edi_purchase_orders p
JOIN trading_partners tp ON tp.id = p.trading_partner_id
LEFT JOIN edi_po_line_items l ON l.po_id = p.id
LEFT JOIN edi_validation_issues vi ON vi.po_id = p.id
WHERE p.deleted_at IS NULL
GROUP BY p.id, tp.code, tp.name;

CREATE OR REPLACE VIEW v_exception_queue AS
SELECT
    vi.id              AS issue_id,
    vi.issue_code,
    vi.severity,
    vi.message,
    vi.field_path,
    vi.validation_status,
    vi.created_at      AS issue_created_at,
    p.id               AS po_id,
    p.buyer_po_number,
    p.po_status,
    tp.code            AS partner_code,
    tp.name            AS partner_name,
    l.line_number,
    l.buyer_sku
FROM edi_validation_issues vi
JOIN edi_purchase_orders p ON p.id = vi.po_id
JOIN trading_partners tp ON tp.id = p.trading_partner_id
LEFT JOIN edi_po_line_items l ON l.id = vi.line_id
WHERE vi.validation_status = 'OPEN'
  AND p.deleted_at IS NULL
ORDER BY vi.severity DESC, vi.created_at ASC;

    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_exception_queue")
    op.execute("DROP VIEW IF EXISTS v_po_summary")
    op.execute("DROP TRIGGER IF EXISTS trg_po_status_history ON edi_purchase_orders")
    op.execute("DROP FUNCTION IF EXISTS log_po_status_change()")

    for tbl in [
        "edi_invoices", "edi_advance_ship_notices", "edi_outbound_messages",
        "edi_validation_issues", "edi_po_line_items", "edi_purchase_orders",
        "ship_to_mapping", "sku_mapping", "material_master",
        "trading_partners", "seller_entities",
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tbl}_updated_at ON {tbl}")

    op.execute("DROP TABLE IF EXISTS b1_api_log")
    op.execute("DROP TABLE IF EXISTS edi_invoice_line_items")
    op.execute("DROP TABLE IF EXISTS edi_invoices")
    op.execute("DROP TABLE IF EXISTS edi_asn_line_items")
    op.execute("DROP TABLE IF EXISTS edi_advance_ship_notices")
    op.execute("DROP TABLE IF EXISTS edi_outbound_messages")
    op.execute("DROP TABLE IF EXISTS edi_validation_issues")
    op.execute("DROP TABLE IF EXISTS edi_po_status_history")
    op.execute("DROP TABLE IF EXISTS edi_po_line_items")
    op.execute("DROP TABLE IF EXISTS edi_purchase_orders")
    op.execute("DROP TABLE IF EXISTS ship_to_mapping")
    op.execute("DROP TABLE IF EXISTS sku_mapping")
    op.execute("DROP TABLE IF EXISTS raw_messages")
    op.execute("DROP TABLE IF EXISTS material_master")
    op.execute("DROP TABLE IF EXISTS trading_partners")
    op.execute("DROP TABLE IF EXISTS seller_entities")

    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    op.execute("DROP TYPE IF EXISTS mapping_status_t")
    op.execute("DROP TYPE IF EXISTS validation_status_t")
    op.execute("DROP TYPE IF EXISTS po_status_t")
    op.execute("DROP TYPE IF EXISTS edi_doc_type_t")
    op.execute("DROP TYPE IF EXISTS source_channel_t")
