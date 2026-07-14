# EDI Integration Playbook — Let's Try Foods
> Last updated: 2026-07-07
> This document is the single source of truth for the system. Update it every time something is built or changed.

---

## Table of Contents
1. [What This System Does](#1-what-this-system-does)
2. [Architecture Overview](#2-architecture-overview)
3. [How POs Come In — Two Channels](#3-how-pos-come-in--two-channels)
4. [Blinkit Integration](#4-blinkit-integration)
5. [Zepto Integration](#5-zepto-integration)
6. [Running the System Locally](#6-running-the-system-locally)
7. [Deploying to Render (Production)](#7-deploying-to-render-production)
8. [Environment Variables — Complete List](#8-environment-variables--complete-list)
9. [Database Tables](#9-database-tables)
10. [API Endpoints — Quick Reference](#10-api-endpoints--quick-reference)
11. [What Still Needs To Be Done](#11-what-still-needs-to-be-done)

---

## 1. What This System Does

This is a custom EDI (Electronic Data Interchange) system for **Let's Try Foods**, an FMCG snack brand.

**The problem it solves:** Retail partners like Blinkit, Zepto, Swiggy, and BigBasket send Purchase Orders (POs) in different ways — some via API webhooks, some via email, some via their own portals. Without this system, someone has to manually read every PO and enter it into SAP or a spreadsheet.

**What this system does automatically:**
- Receives POs from Blinkit (via webhook) and Zepto (via API pull)
- Maps each partner's SKU codes to your internal SAP product codes
- Checks stock levels and marks POs as STOCK_AVAILABLE / PARTIAL / OUT_OF_STOCK
- Creates SAP Sales Orders
- Sends back Advance Shipment Notifications (ASN) when you ship

---

## 2. Architecture Overview

```
Partner (Blinkit / Zepto / Email)
        │
        ▼
  [Render Server]  ← Static IP (whitelisted by partners)
  backend on Render.com
        │
        ├── POST /api/webhook/inbound/blinkit/po   ← Blinkit pushes POs here
        ├── GET  /api/zepto/po-events               ← We pull POs from Zepto
        │
        ▼
  [PostgreSQL DB]
  - purchase_orders
  - po_items
  - webhook_logs
  - product_mappings
  - asn_records
  - sap_sales_orders
        │
        ▼
  [React Frontend]  ← You manage everything from here
  frontend on Render.com
```

**Local development:** Your Mac cannot receive webhooks from the internet. So for local dev, Blinkit and Zepto calls are proxied through the Render server (which has a static IP).

---

## 3. How POs Come In — Two Channels

| Channel | How It Works | Where You See It |
|---------|-------------|-----------------|
| **Blinkit** | Blinkit pushes PO events to our webhook URL | Sidebar → Blinkit → PO Events |
| **Zepto** | We poll Zepto's Silk Route API every time you open the page | Sidebar → Zepto → PO Events |

---

## 4. Blinkit Integration

### How It Works
Blinkit **pushes** POs to us — we do not pull. There is no Blinkit "List POs" API.

**Flow:**
1. Blinkit's tech team configures our webhook URL in their system
2. When they create a PO for us, they POST the event to our server
3. We store it in `webhook_logs` and display it on the Blinkit POs page
4. You create an ASN (invoice) against each PO from the frontend

### Setup Steps
1. Share this webhook URL with Blinkit's tech team:
   ```
   https://<your-domain>/api/webhooks/BLINKIT
   ```
2. Give them your Vendor ID: `18309`
3. Set these in `.env`:
   ```
   BLINKIT_API_KEY=<key from Blinkit>
   BLINKIT_VENDOR_ID=18309
   BLINKIT_BASE_URL=https://dev.partnersbiz.com        ← testing; prod: https://api.partnersbiz.com
   ```
   The `TradingPartner.webhook_secret` column stores the `api-key` header value Blinkit sends us — set it via seed script or DB directly.

### Real API Endpoints (confirmed from Blinkit contract docs)
| Action | Method | URL |
|---|---|---|
| Receive PO (inbound) | POST to us | `POST /api/webhooks/BLINKIT` |
| Submit ASN (outbound) | POST | `https://dev.partnersbiz.com/webhook/public/v1/asn` |
| PO Acknowledgement (outbound) | POST | `https://dev.partnersbiz.com/webhook/public/v1/po/acknowledgement` |

### Known Bugs Fixed
- **2026-05-12**: Webhook returned HTTP 500 because `WebhookStatus.PROCESSED` wasn't in the Postgres ENUM type. Fixed to use `WebhookStatus.PENDING`. Blinkit test PO `50033210003038` was the first PO pushed.
- **2026-05-12**: `RENDER_URL` not set on Render caused outbound URLs to be built as relative paths. Fixed `_url()` to skip proxy routing when `RENDER_URL` is empty.

### Where to Manage
- **Sidebar → Blinkit → PO Events** — see all POs received via webhook, create ASNs
- **Sidebar → Blinkit → ASN Manager** — track submitted ASNs

### ASN Local Tracking (2026-05-14)
Because Blinkit has no List-ASNs API, every ASN submission is tracked locally in `blinkit_asn_allocations`:
- `POST /blinkit/asn` stores per-item invoiced qty after a successful Blinkit response
- `GET /blinkit/asn?po_number=X` returns locally-tracked ASNs grouped by `asn_id`
- `GET /blinkit/po/{po_number}/sku-allocations` returns `{ item_id → invoiced_qty }` map

The frontend uses these to:
- Show fill rate % badge on every PO row (green=0%, yellow=partial, red=100%)
- Pre-fill remaining qty in the ASN form (requested − already invoiced)
- Disable the ASN button when fill rate reaches 100%
- Show existing ASNs panel inside the Create ASN modal

### Known Bugs Fixed
- **2026-05-12**: Webhook returned HTTP 500 because `WebhookStatus.PROCESSED` wasn't in the Postgres ENUM type. Fixed to use `WebhookStatus.PENDING`. Blinkit test PO `50033210003038` was the first PO pushed.
- **2026-05-12**: `RENDER_URL` not set on Render caused outbound URLs to be built as relative paths. Fixed `_url()` to skip proxy routing when `RENDER_URL` is empty.
- **2026-05-14**: Multiple ASN type errors fixed (item_id string, unit_basic_price float64, tax_distribution float64, supplier_address required). Type contract from Go struct takes precedence over PDF spec table when they conflict.

---

## 5. Zepto Integration

### How It Works
Zepto uses the **Silk Route API**. We **pull** PO events from Zepto — they don't push to us.

**Flow:**
1. We call Zepto's API: `GET /api/v1/external/po/events?days=7`
2. Zepto returns PO events (CreatePO, UpdatePO, CancelPO)
3. You see them on the Zepto POs page
4. You create an ASN against each PO

### Setup Steps
1. Zepto gives you a `client_id` and `client_secret` during onboarding
2. Set in `backend/.env`:
   ```
   ZEPTO_CLIENT_ID=your-client-id
   ZEPTO_CLIENT_SECRET=your-client-secret
   ```
3. Zepto requires your server's outbound IP to be whitelisted. Share these Render IPs with Zepto:
   ```
   74.220.48.0/24
   74.220.56.0/24
   ```
4. QA environment: `https://silkroute.zeptonow.dev` (used when ENVIRONMENT=local)
5. Production environment: `https://silkroute.zepto.co.in` (used when ENVIRONMENT=production)

### Where to Manage
- **Sidebar → Zepto → PO Events** — list POs, create ASNs, download PDFs
- **Sidebar → Zepto → ASN Manager** — cancel ASNs, see per-SKU allocations

### Per-SKU Allocation Tracking
Zepto's List-ASNs API never returns item-level breakdowns (only total qty). So we track it ourselves in the `zepto_asn_allocations` table. When you submit an ASN, we record how many units of each SKU were invoiced. The frontend uses this to show remaining qty per SKU.

---

## 6. Running the System Locally

### Backend
```bash
# From project root
.venv/bin/uvicorn main:app --reload --port 8000 --app-dir backend
```

### Frontend
```bash
cd frontend
npm install
npm run dev
# Opens at http://localhost:5173
```

### Local API docs
Once backend is running: http://localhost:8000/docs

### Note on local webhooks
Blinkit cannot push POs to your laptop. When running locally:
- All Blinkit API calls go through the Render proxy (your Mac → Render → Blinkit)
- Zepto calls also proxy through Render
- This requires `RENDER_URL` and `ENVIRONMENT=local` set in `.env`

---

## 7. Deploying to Render (Production)

### Backend (Web Service)
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT --app-dir backend`
- Root directory: `.` (project root)

### Frontend (Static Site)
- Build command: `cd frontend && npm install && npm run build`
- Publish directory: `frontend/dist`

### Environment Variables to Set in Render
See Section 9 below.

---

## 8. Environment Variables — Complete List

Set these in `backend/.env` (local) and in Render Dashboard → Environment (production).

```bash
# Database
DATABASE_URL=postgresql+psycopg2://user:pass@host/dbname

# App
SECRET_KEY=your-secret-key
ENVIRONMENT=local        # or "production"
RENDER_URL=https://po-integration-backend.onrender.com

# Blinkit
BLINKIT_API_KEY=your-blinkit-api-key
BLINKIT_VENDOR_ID=18309
BLINKIT_BASE_URL=https://api.partnersbiz.com   # dev: https://dev.partnersbiz.com

# Zepto
ZEPTO_CLIENT_ID=your-zepto-client-id
ZEPTO_CLIENT_SECRET=your-zepto-client-secret
ZEPTO_BASE_URL=                                # Leave blank to auto-select by ENVIRONMENT

# Swiggy (future)
SWIGGY_API_KEY=your-swiggy-api-key
```

---

## 9. Database Tables

| Table | Purpose |
|-------|---------|
| `companies` | Partner companies (Blinkit, Zepto, Swiggy, etc.) |
| `products` | Your internal product catalogue with SAP codes |
| `purchase_orders` | All POs — from any source (MANUAL / WEBHOOK / EMAIL) |
| `po_items` | Line items for each PO |
| `product_mappings` | Maps partner SKU codes → your internal products |
| `webhook_logs` | Every inbound webhook event (Blinkit, generic) |
| `asn_records` | Advance Shipment Notifications sent to partners |
| `sap_sales_orders` | SAP Sales Orders created from POs |
| `zepto_asn_allocations` | Per-SKU qty tracker for Zepto ASNs |
| `blinkit_asn_allocations` | Per-item_id qty tracker for Blinkit ASNs (Blinkit has no List-ASNs API) |
| `unmapped_sku_alerts` | Unknown partner SKUs flagged for human review |

---

## 10. API Endpoints — Quick Reference

### Core
| Method | URL | Description |
|--------|-----|-------------|
| GET | `/api/dashboard` | Dashboard stats |
| GET | `/api/companies` | List companies |
| GET | `/api/products` | List products |
| GET | `/api/purchase-orders` | List all POs |
| GET | `/api/purchase-orders/{id}` | Single PO detail |
| PATCH | `/api/purchase-orders/{id}/status` | Update PO status |

### Webhooks & Inbound (Phase 4)
| Method | URL | Description |
|--------|-----|-------------|
| POST | `/api/webhooks/BLINKIT` | Blinkit pushes POs here (generic dispatcher) |
| POST | `/api/webhooks/{partner_code}` | Generic partner webhook (any future push partner) |

### Blinkit
| Method | URL | Description |
|--------|-----|-------------|
| GET | `/api/blinkit/pos` | List POs from webhook store |
| GET | `/api/blinkit/health` | Test Blinkit connectivity |
| POST | `/api/blinkit/asn` | Submit ASN to Blinkit + persist allocation locally |
| GET | `/api/blinkit/asn` | List locally-tracked Blinkit ASNs |
| GET | `/api/blinkit/po/{po_number}/sku-allocations` | Per-item invoiced qty map |
| POST | `/api/blinkit/po-ack` | Send PO acknowledgement |

### Zepto
| Method | URL | Description |
|--------|-----|-------------|
| GET | `/api/zepto/po-events` | Fetch PO events from Zepto |
| POST | `/api/zepto/asn` | Submit ASN to Zepto |
| DELETE | `/api/zepto/asn/{asn_number}` | Cancel an ASN |
| GET | `/api/zepto/po/{po}/sku-allocations` | Per-SKU invoiced qty |

### Product Mappings & SAP
| Method | URL | Description |
|--------|-----|-------------|
| GET | `/api/product-mappings` | List all SKU mappings |
| POST | `/api/product-mappings` | Add a new mapping |
| GET | `/api/product-mappings/resolve` | Test: resolve a partner SKU |
| GET | `/api/sap-orders` | List SAP Sales Orders |
| GET | `/api/unmapped-skus` | SKUs that couldn't be mapped |

---

## Phase 6 — SAP B1 Service Layer Integration (completed 2026-07-06)

### B1 session management
The `SessionPool` (max 2 sessions by default, configurable via `B1_SESSION_POOL_SIZE`) ensures we never exceed the licensed concurrent session limit. Sessions expire after 29 minutes (1 min before B1's 30-min timeout). All SAP push jobs run on the dedicated `sap_push` RQ queue — set worker concurrency ≤ `B1_SESSION_POOL_SIZE` to avoid pool starvation.

### Push flow
```
Scheduler (every 60s)
  → queries VALIDATED POs
  → enqueues push_po_to_b1_job for each
  
push_po_to_b1_job
  → push_po_to_b1(po_id)
  → idempotency check (b1_sales_order_doc_entry already set → skip)
  → status check (must be VALIDATED or SAP_REJECTED)
  → unmapped-SKU preflight
  → status → SAP_PENDING
  → build_sales_order_payload()
  → client.create_sales_order(payload)
  → success: status → SAP_CONFIRMED, store DocEntry/DocNum
  → failure: status → SAP_REJECTED, store error
  → always: write B1ApiLog row
```

### UDFs required in B1 (set up before first push)
| Object | Field Name | Type | Size |
|---|---|---|---|
| ORDR (header) | U_EDI_SOURCE | Alpha-Numeric | 20 |
| ORDR (header) | U_EDI_DOC_UUID | Alpha-Numeric | 36 |
| ORDR (header) | U_EDI_RECEIVED_AT | Alpha-Numeric | 30 |
| ORDR (header) | U_BUYER_GSTIN | Alpha-Numeric | 15 |
| ORDR (header) | U_EDI_PO_NUMBER | Alpha-Numeric | 50 |
| RDR1 (lines) | U_EDI_LINE_NO | Alpha-Numeric | 10 |
| RDR1 (lines) | U_BUYER_SKU | Alpha-Numeric | 50 |

Full setup instructions: `docs/b1_setup.md`

### Environment variables added in Phase 6
```
B1_SERVICE_LAYER_URL=https://<b1-server>:50001
B1_COMPANY_DB=SBO_LETSTRY
B1_USERNAME=EDI_BOT
B1_PASSWORD=<password>
B1_SESSION_POOL_SIZE=2
B1_VERIFY_SSL=true    ← MUST be true in production
```

### Error types
| Exception | When | Effect |
|---|---|---|
| `B1SessionError` | 401 from B1 | Pool invalidates session, retries once with fresh session |
| `B1ClosedPeriodError` | code -5002 | PO → SAP_REJECTED; ops must change doc date |
| `B1ApiError` | any other 4xx/5xx | PO → SAP_REJECTED; full error in B1ApiLog |

### Verify connectivity
```bash
python scripts/test_b1_connection.py
```
This runs 5 checks (Login, company info, Items read, BusinessPartners read, Logout) without writing any data.

---

## 12. Project History / Session Log

| Date | What Was Done |
|---|---|
| 2026-07-06 | Phase 6 complete: `B1ApiError` hierarchy, `SessionPool` (thread-safe, TTL 29 min), `ServiceLayerClient` (all document operations + master data reads), `po_to_sales_order` mapper (7 UDFs), `push_po_to_b1` workflow (idempotent), scheduler SAP push job (every 60s), `scripts/test_b1_connection.py`, `docs/b1_setup.md`, 34 passing tests. |
| 2026-07-06 | Phase 5 complete: `ValidationEngine` + 6 rule classes (GSTIN, SKU mapping with 3-stage auto-map via rapidfuzz, ship-to, tax consistency, total reconciliation, price variance, MOQ), `validate_po` workflow, `GET /api/exceptions` + `POST /api/sku-mapping`, 38 passing tests. |
| 2026-07-06 | Phase 4 complete: `BlinkitApiAdapter` (outbound ACK + ASN; webhook-push only), `ZeptoApiAdapter` (polling, watermark, rate-limit), `POST /api/webhooks/{partner_code}` generic webhook dispatcher, `fetch_api_pos.py` workflow, scheduler updated for 5-min API polling, 23 passing tests. |
| 2026-07-06 | Phase 3 complete: `BlinkitParser`, `ZeptoParser`, `LlmFallbackParser`, `parse_and_persist` workflow, `parse_raw_message_job`, 29 passing tests. |
| 2026-07-03 | Phase 2 complete: `GmailClient`, `BlinkitEmailAdapter`, `ingest_to_canonical` workflow, APScheduler (every 2 min), `auth_gmail.py`, 19 passing tests. |
| 2026-07-02 | Phase 1 complete: 16-table canonical EDI schema (SQLAlchemy models, Alembic migration 0001, Pydantic schemas, seed script, unit tests, ER diagram). Enum types: source_channel_t, edi_doc_type_t, po_status_t, validation_status_t, mapping_status_t. Triggers: updated_at (11 tables), po_status_history auto-log. Views: v_po_summary, v_exception_queue. |
| 2026-06-29 | Phase 0 complete (all 23 deliverables): repo skeleton, pyproject.toml, Dockerfile (multi-stage), docker-compose.yml (7 services), .env.example, app/config.py, app/db.py, app/logging_config.py, app/main.py + /health endpoint, Alembic init + no-op migration 0000, .pre-commit-config.yaml; frontend bootstrapped with Vite react-ts + Tailwind v4 + shadcn/ui (12 components), TanStack Query, react-router-dom, api-client.ts, queryClient.ts, App.tsx, router.tsx, HomePage with /health card, .env.development/.env.production, frontend/Dockerfile (3-stage: dev/builder/production + nginx), README.md. |

---

## 11. What Still Needs To Be Done

Track outstanding work here. Check off items as they are completed.

### Blinkit
- [x] Share webhook URL with Blinkit tech team: `/api/webhook/inbound/blinkit/po` — URL sent to Aman
- [x] Set `BLINKIT_API_KEY` in Render env vars
- [x] Fix HTTP 500 on webhook — `WebhookStatus.PROCESSED` ENUM bug fixed (now uses PENDING)
- [x] First real PO received: `50033210003038` — stored and visible in frontend
- [x] Frontend updated: removed irrelevant filters (days/status), added "Received At" column, ASN payload uses real Blinkit format (snake_case: `po_number`, `supplier_details`, `shipment_details`, `items[].item_id` etc.)
- [ ] Switch `BLINKIT_BASE_URL` from dev (`https://dev.partnersbiz.com`) to production once live
- [ ] Test end-to-end ASN submission once real PO comes in with line items

### Zepto
- [ ] Get production `ZEPTO_CLIENT_ID` and `ZEPTO_CLIENT_SECRET` from Zepto onboarding
- [ ] Share Render outbound IPs with Zepto for whitelisting (`74.220.48.0/24`)
- [ ] Switch `ENVIRONMENT=production` on Render when going live
- [x] Per-SKU ASN tracking (`zepto_asn_allocations` table) — exact remaining qty per SKU
- [x] PO search bar — find any PO by code, including dummy POs created by Zepto team
- [x] Error banner — Zepto API errors now surfaced in UI instead of silently ignored
- [x] Clickable PO numbers — opens full PO detail modal (from main table and ASN modal)
- [x] Clickable SKU codes — opens full SKU detail modal (from expanded rows and ASN modal)

### General
- [ ] Set up real PostgreSQL on Render (not local)
- [ ] Add Swiggy integration (API details needed from Swiggy)

---

## Phase 2 — Email Ingestion (completed 2026-07-03)

### Gmail setup (one-time, per deployment)
1. Create OAuth credentials in Google Cloud Console → APIs & Services → OAuth client ID (Desktop app type)
2. Save JSON file to `GMAIL_CREDENTIALS_PATH` (default: `./credentials/gmail_credentials.json`)
3. Run `python scripts/auth_gmail.py` — a browser will open; authorize with tech@letstryfoods.com
4. `token.json` is written to `GMAIL_TOKEN_PATH` (default: `./credentials/gmail_token.json`)
5. Token auto-refreshes; re-run auth script only if access is revoked

### Gmail labels — one per email-based partner
| Label name | Partner code | SLA ack / ASN |
|---|---|---|
| SWIGGY_PO | SWIGGY | 6h / 24h |
| BIGBASKET_PO | BIGBASKET | 12h / 48h |
| DMART_PO | DMART | 24h / 48h |
| (and others in seed data) | | |

Blinkit uses a WEBHOOK channel — email ingestion is for legacy/manual forwards only.

### Attachment storage
Path pattern: `{ATTACHMENT_BASE_PATH}/{partner_code}/{yyyy-mm-dd}/{gmail_message_id}/{filename}`  
Default base: `./data/attachments/`

### Idempotency
Raw messages are stored with a UNIQUE constraint on `(trading_partner_id, external_id)` where `external_id = gmail_message_id`. The workflow also pre-checks before fetching the full message body to avoid unnecessary API calls on re-runs.

### Parse pipeline
After saving a raw message, the workflow enqueues a `parse_raw_message_job` on the `ingest` RQ queue. The parse worker calls `parse_and_persist(raw_message_id)` which dispatches to the correct parser.

---

## Phase 3 — Parser Layer (completed 2026-07-06)

### How parsing works

Every raw message goes through this pipeline:
1. `parse_raw_message_job` (RQ) → `parse_and_persist(raw_message_id)`
2. `get_parser(partner_code)` from registry → concrete parser (e.g. `BlinkitParser`)
3. `parser.can_parse(raw_message)` — quick structural check before attempting
4. `parser.parse(raw_message)` → `ParseResult(success, doc: EDI850, errors, warnings)`
5. On success: write `edi_purchase_orders` + `edi_po_line_items`, set `parse_status = SUCCESS`
6. On failure: write placeholder PO (status=EXCEPTION) + `EdiValidationIssue(code=E000_PARSE_FAILED)`, set `parse_status = FAILED`

### LLM fallback
If the structured parser fails AND the partner has `api_config.llm_fallback_enabled = true`, `LlmFallbackParser` (Anthropic `claude-sonnet-4-5`) is tried. Cost: ~$0.003–0.005 per PO. Enable per-partner only for unstructured formats.

### Parsers implemented
| Partner | Parser class | Source format | Field notes |
|---|---|---|---|
| BLINKIT | `BlinkitParser` | JSON webhook (`po_number`, `details`) | `sku_code` = buyer SKU; `basic_price` = unit price; `igst_percentage` may be null (intrastate) |
| ZEPTO | `ZeptoParser` | JSON API (`purchaseOrderNumber`, `lineItems`) | `productIdentifier.buyerProductIdentifier.skuCode`; qty in `orderedQuantity.amount`; `unit` defaults to PC |

### Adding a new parser
1. Create `app/parsers/<partner>_parser.py` with a class extending `BaseParser`
2. Implement `partner_code`, `can_parse()`, `parse()`
3. Register in `app/parsers/registry.py` `_build_registry()`
4. Add at least 3 fixtures in `tests/fixtures/` and tests in `tests/unit/test_parsers.py`

### Tax calculation rules (applied in every parser)
- **Intrastate** (seller state == buyer state): `cgst_amount = taxable * cgst_rate / 100`, same for sgst. `igst = None`.
- **Interstate**: `igst_amount = taxable * igst_rate / 100`. `cgst = sgst = None`.
- Blinkit signals intrastate by sending `igst_percentage: null`. Zepto sends `igstRate: 0.0` for intrastate.
- `line_total = taxable_amount + cgst_amount + sgst_amount + igst_amount` (null amounts treated as 0).
- Header `total_amount` is used as `grand_total` when present; otherwise computed from line totals.

### Exception queue
Parse failures appear in `edi_validation_issues` with `issue_code = E000_PARSE_FAILED`. The ops team can view these via `GET /api/exceptions` (Phase 8 adds the UI). The placeholder PO row links to the raw_message so the original file is always accessible.

---

## Phase 7 — Outbound Documents (completed 2026-07-06)

### Outbound document types

| Enum value | EDI code | Trigger | Partner delivery |
|---|---|---|---|
| `PO_ACK_855` | 855 | PO reaches `SAP_CONFIRMED` | API (Blinkit/Zepto) or Email (others) |
| `ASN_856` | 856 | B1 Delivery Note linked to SO | API (Blinkit/Zepto) or Email |
| `INVOICE_810` | 810 | B1 A/R Invoice linked to Delivery | Email notification only (for now) |
| `CREDIT_NOTE` | — | Inbound RTV processed → B1 Return | Email |

### Outbound pipeline

```
trigger event → b1_to_outbound.py creates EdiOutboundMessage(status=PENDING)
                → enqueues send_outbound_job(outbound_msg_id)
                → send_outbound_message() → get_outbound_adapter(partner, channel)
                → adapter.send() → SENT or schedule retry
```

### Retry policy

5 attempts total. Delays before each retry: 60s → 300s → 1800s → 7200s → 21600s.  
After attempt 5, `status = FAILED`. Message stays in DB for ops review.

### SLA monitoring

`trading_partners.ack_sla_hours` (default 24h). If a PO_ACK_855 is sent after the deadline, a WARNING log is emitted: `"outbound.sla_breached"`. Phase 10 will add Slack alerting.

### B1 polling

`poll_b1_deliveries()` queries `GET /b1s/v1/DeliveryNotes?$filter=BaseEntry eq {so_doc_entry} and BaseType eq 17` for all SAP_CONFIRMED POs.  
`poll_b1_invoices()` queries `GET /b1s/v1/Invoices?$filter=BaseEntry eq {delivery_doc_entry} and BaseType eq 15`.  
Both run every 5 minutes via APScheduler.

### RTV flow

RTV emails arrive in Gmail and are saved as `RawMessage(doc_type=RTV)`.  
`process_rtv(raw_message_id)` extracts the PO number using 4 regex patterns (explicit PO prefix, RTV+for keyword, letter-hyphen-digit pattern, bare 12-20 digit numeric).  
Matches to `EdiPurchaseOrder`, creates `POST /b1s/v1/Returns` in B1, enqueues a CREDIT_NOTE outbound message.  
If PO number cannot be extracted or not found → logged as `rtv.unmatched` for ops review.

### Outbound adapter registry

Lookup order: exact partner_code → source_channel fallback → `UnsupportedOutboundPartnerError`.  
Partners with no explicit adapter AND non-EMAIL channel (e.g. portal scrapers) will be SKIPPED.  
Email adapter requires `gmail.send` scope — re-run `scripts/auth_gmail.py` after adding the scope.

### Adding a new outbound partner

1. Create `app/adapters/outbound/<partner>_outbound.py` extending `BaseOutboundAdapter`
2. Add to `_PARTNER_MAP` in `app/adapters/outbound/registry.py`
3. Update payload builders in `app/workflows/b1_to_outbound.py` for the new partner's schema
4. Add tests in `tests/unit/test_outbound.py`

---

## Phase 8 — Operations Dashboard (completed 2026-07-06)

### Authentication

JWT stored in an httpOnly cookie named `edi_token` (8h expiry, HS256).  
`SECRET_KEY` in `.env` is required — generate with `python -c "import secrets; print(secrets.token_hex(32))"`.  
`get_current_user` is a FastAPI dependency injected into all protected routes.  
All POST/PATCH/PUT/DELETE to `/api/` are logged to `audit_log` by `AuditMiddleware`.

### API endpoints added in Phase 8

| Method | URL | Description |
|--------|-----|-------------|
| POST | `/auth/login` | Set httpOnly JWT cookie |
| POST | `/auth/logout` | Clear cookie |
| GET | `/auth/me` | Current user info |
| GET | `/api/pos` | PO list (paginated, filters: partner_code, po_status, date_from, date_to, search) |
| GET | `/api/pos/{id}` | PO detail with lines, issues, b1_push_history, outbound_messages |
| POST | `/api/pos/{id}/retry-sap` | Re-queue SAP_REJECTED PO |
| POST | `/api/pos/{id}/cancel` | Cancel a PO |
| GET | `/api/dashboard/today` | Today's PO counts + per-partner stats |
| GET | `/api/dashboard/sla-breaches` | SAP_CONFIRMED POs past ack_sla_hours with no SENT ACK |
| GET | `/api/dashboard/unmapped-skus` | Unmapped SKUs grouped by (buyer_sku, partner) |
| GET | `/api/dashboard/activity` | Recent status history entries |
| GET | `/api/exceptions` | Open validation issues |
| POST | `/api/exceptions/{id}/resolve` | Resolve with optional note |
| GET | `/api/master-data/partners` | List trading partners |
| PATCH | `/api/master-data/partners/{id}` | Update partner |
| GET | `/api/master-data/materials` | List material master |
| POST | `/api/master-data/materials` | Create material |
| GET | `/api/master-data/sku-mappings` | List SKU mappings |
| PATCH | `/api/master-data/sku-mappings/{id}` | Map a SKU |
| GET | `/api/master-data/ship-to-mappings` | List ship-to mappings |
| PATCH | `/api/master-data/ship-to-mappings/{id}` | Map a warehouse |
| GET | `/api/b1-logs` | B1 API call log (filterable) |
| GET | `/api/b1-logs/{id}` | Full request/response JSON |

### Frontend SPA

- Dev: `cd frontend && npm run dev` → `http://localhost:5173`
- Production: `npm run build` → `frontend/dist/` served by FastAPI `StaticFiles` mount at `/`
- API prefix `/api/` avoids clashing with SPA client-side routes
- All routes except `/login` require authentication (`ProtectedRoute` wrapper)

### Environment variables added in Phase 8

```
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
```

### Creating the first admin user

```python
# One-time: run in Python shell or add to scripts/seed_master_data.py
from app.api.routes.auth import hash_password
from app.models.users import User
from app.db import SyncSessionLocal

with SyncSessionLocal() as db:
    user = User(email="tech@letstryfoods.com", password_hash=hash_password("changeme"), full_name="Admin")
    db.add(user)
    db.commit()
```
