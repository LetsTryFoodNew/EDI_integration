# EDI Integration Playbook — Let's Try Foods
> Last updated: 2026-06-29
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
   https://po-integration-backend.onrender.com/api/webhook/inbound/blinkit/po
   ```
2. Give them your Vendor ID: `18309`
3. Set these in your Render environment variables:
   ```
   BLINKIT_API_KEY=<key from Blinkit>
   BLINKIT_BASE_URL=https://dev.partnersbiz.com        ← testing
   BLINKIT_PATH_ASN=webhook/public/v1/asn
   BLINKIT_PATH_PO_ACK=webhook/public/v1/po/acknowledgement
   ```

### Real API Endpoints (confirmed from Blinkit contract docs)
| Action | Method | URL |
|---|---|---|
| Receive PO (inbound) | POST to us | `/api/webhook/inbound/blinkit/po` |
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

### Webhooks & Inbound
| Method | URL | Description |
|--------|-----|-------------|
| POST | `/api/webhook/inbound/blinkit/po` | Blinkit pushes POs here |
| POST | `/api/webhook/inbound/po` | Generic inbound PO webhook |
| POST | `/api/webhook/simulate/{partner}` | Simulate a test PO |
| GET | `/api/webhook/logs` | View all webhook events |

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

## 12. Project History / Session Log

| Date | What Was Done |
|---|---|
| 2026-06-29 | Phase 0 prep: read CLAUDE.md + PLAYBOOK.md; surveyed legacy `backend/` and `frontend/`; created `docs/legacy-api-notes.md` and `docs/legacy-frontend-notes.md`; moved `CLAUDE.md` to workspace root; archived `backend/` → `_archive/backend_old/` and `frontend/` → `_archive/frontend_old/`. |
| 2026-07-02 | Phase 1 complete: 16-table canonical EDI schema (SQLAlchemy models, Alembic migration 0001, Pydantic schemas, seed script, unit tests, ER diagram). Enum types: source_channel_t, edi_doc_type_t, po_status_t, validation_status_t, mapping_status_t. Triggers: updated_at (11 tables), po_status_history auto-log. Views: v_po_summary, v_exception_queue. |
| 2026-06-29 | Phase 0 complete (all 23 deliverables): repo skeleton, pyproject.toml, Dockerfile (multi-stage), docker-compose.yml (7 services), .env.example, app/config.py, app/db.py, app/logging_config.py, app/main.py + /health endpoint, Alembic init + no-op migration 0000, .pre-commit-config.yaml; frontend bootstrapped with Vite react-ts + Tailwind v4 + shadcn/ui (12 components), TanStack Query, react-router-dom, api-client.ts, queryClient.ts, App.tsx, router.tsx, HomePage with /health card, .env.development/.env.production, frontend/Dockerfile (3-stage: dev/builder/production + nginx), README.md. Exit criteria pending: needs `docker compose up` smoke test once credentials are set. |

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
