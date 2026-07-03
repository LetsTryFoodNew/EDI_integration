# CLAUDE.md — EDI Middleware for SAP Business One

> **🛑 STOP. READ THIS FIRST.**
>
> This file is the **single source of truth** for the project. It supersedes any other notes, READMEs, partial code, or instructions you may find in this repository. If you see code or docs in this repo that conflict with this file, **this file wins**.
>
> Phases are defined in **Section 5** of this document (starting with "Phase 0 — Foundation Setup"). They are NOT defined anywhere else in the repo. Do not look for them in other files. Do not ask the user to re-define them. Read Section 5.
>
> If you cannot find Section 5 in this file, you are reading the wrong file. Stop and tell the user.

---

## 0. Project Starting State

### Current workspace layout

```
EDI INTEGRATION/                  ← workspace root (where you will build)
├── .vscode/
├── backend/                      ← OLD code (Blinkit + Zepto APIs working). REFERENCE ONLY.
├── frontend/                     ← OLD code. REFERENCE ONLY.
├── EDI_middleware/
│   └── CLAUDE.md                 ← this file (will move to workspace root)
└── PLAYBOOK.md                   ← business rules. READ THIS.
```

### What each folder means

**`backend/` and `frontend/` (OLD code) — READ-ONLY REFERENCE**
- These contain **working but messy** integrations with Blinkit and Zepto APIs.
- They exist ONLY so you can study HOW those APIs work — auth flow, endpoints, request/response shapes, quirks, error handling, retry logic, field names, edge cases discovered in production.
- **DO NOT modify, refactor, run, or import from these folders.** They are documentation in source-code form.
- **DO NOT copy-paste their code into the new project.** Read them, understand the API contract, then write fresh clean code in the new structure.
- When you need to know how a partner API behaves, grep these folders first before asking the user or guessing.

**`PLAYBOOK.md` — REQUIRED READING**
- Contains business rules specific to this company (SLAs, mapping conventions, exception handling policy, partner-specific quirks, accounting practices).
- Read it at the start of every session, after this `CLAUDE.md`.
- If `PLAYBOOK.md` and `CLAUDE.md` ever conflict on business rules, `PLAYBOOK.md` wins for business rules; `CLAUDE.md` wins for architecture/tech stack.

**`EDI_middleware/` — temporary home of this file**
- This subfolder will eventually be deleted. Move `CLAUDE.md` to the workspace root as part of Phase 0 deliverable 1.

### Where to build the new project

**At the workspace root.** The final layout will be:

```
EDI INTEGRATION/                  ← workspace root
├── CLAUDE.md                     ← moved here in Phase 0
├── PLAYBOOK.md                   ← stays
├── README.md
├── pyproject.toml
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── alembic/
├── app/                          ← NEW backend (replaces old backend/ eventually)
├── frontend/                     ← NEW frontend (REPLACES the old one after old is archived)
├── tests/
├── scripts/
├── docs/
└── _archive/                     ← old backend + frontend moved here for reference
    ├── backend_old/
    └── frontend_old/
```

### Phase 0 — Deliverable 1 specifics for THIS project

The very first thing Claude does in Phase 0:

1. Read `PLAYBOOK.md` and summarize the business rules to the user in 5–10 bullets to confirm understanding.
2. Open `backend/` and identify: which partner adapters exist, what auth method each uses, what endpoints are called, what the response shapes look like. Produce a short report in `docs/legacy-api-notes.md` covering at minimum:
   - Blinkit: base URL, auth type, key endpoints used, PO payload structure, known quirks.
   - Zepto: same.
   - Any other partner found.
3. Open `frontend/` and identify: which screens exist, what API calls they make, any reusable business logic worth re-implementing in the new React/Vite codebase. Note findings in `docs/legacy-frontend-notes.md`.
4. Create `_archive/` directory at workspace root.
5. Move `backend/` → `_archive/backend_old/` and `frontend/` → `_archive/frontend_old/`. **Use git mv** so history is preserved.
6. Move `EDI_middleware/CLAUDE.md` → workspace root `CLAUDE.md`. Delete the empty `EDI_middleware/` folder.
7. Confirm with the user before proceeding to deliverable 2 (which is the proper Phase 0 work — skeleton, Docker, etc., per Section 5).

### What "start phase 0" means

When the user says "start phase 0", respond exactly like this:

> *"I've read CLAUDE.md and PLAYBOOK.md.*
>
> *Phase 0 begins with reading the legacy code in `backend/` and `frontend/` to extract API knowledge (Blinkit, Zepto). I'll produce two reference docs: `docs/legacy-api-notes.md` and `docs/legacy-frontend-notes.md`. Then I'll archive the old folders into `_archive/` and move CLAUDE.md to the workspace root. After you approve, I'll proceed with the rest of Phase 0 (skeleton, Docker, DB, health check, Vite frontend bootstrap — 23 deliverables total) as defined in Section 5.*
>
> *Starting with reading PLAYBOOK.md and surveying the legacy code now."*

Then begin. Do NOT ask the user to redefine Phase 0 — it is fully defined in Section 5 of this file plus the steps above.

### Hard rules for the old code

- ❌ Never `import` from `backend/` or `frontend/` into the new code.
- ❌ Never run the old code or its tests.
- ❌ Never modify files inside `_archive/` after the archive step.
- ✅ Always read the old code when you need to understand a partner API.
- ✅ Cite the legacy file path in commit messages when you re-implement a feature based on its logic (e.g., *"Re-implemented Blinkit OAuth flow based on `_archive/backend_old/integrations/blinkit/auth.py`"*).

---

> **Read this entire file before writing any code.** This document is the single source of truth for the project. Every phase, every architectural decision, every tech choice is here. When in doubt, re-read the relevant section. If something is ambiguous, ask the user before proceeding.

---

## 1. Project Overview

We are building a **custom EDI (Electronic Data Interchange) middleware** that:

1. Receives Purchase Orders (POs), RTVs, and related documents from ~15 retail platforms (Blinkit, Zepto, Swiggy, BigBasket, Amazon, Flipkart, DMart, Reliance, etc.).
2. Some platforms provide **REST APIs** (Blinkit, Zepto, BigBasket, Amazon SP-API, Flipkart Seller).
3. Other platforms send POs via **Gmail (PDF/Excel attachments)** or require **portal scraping** (Swiggy, Reliance, DMart in some cases).
4. Normalizes everything into a **canonical EDI 850 schema** (Indian retail variant — GSTIN, HSN, CGST/SGST/IGST, e-invoicing aware).
5. Pushes Sales Orders into **SAP Business One** via its **Service Layer (REST/OData v4)** — NOT IDoc, NOT BAPI.
6. Manages outbound documents back to retailers: 855 (PO Ack), 856 (ASN), 810 (Invoice), Credit Notes for RTV.
7. Provides an **operations dashboard** for unmapped SKUs, parse failures, SAP push errors, and SLA tracking.

### Key constraints
- **ERP is SAP Business One** (not ECC, not S/4HANA). Use Service Layer only.
- **In Business One, an inbound retailer PO becomes an internal Sales Order** (object ORDR). Never call it a "Purchase Order" inside B1 — that's a different document there.
- India localization: GSTIN, HSN, CGST/SGST/IGST split, IRN for e-invoicing, e-way bill fields.
- Scale: ~50–300 POs/day, ~5,000 historical for backfill. **Do NOT over-engineer**.

---

## 2. Tech Stack (FINAL — Do Not Substitute Without Asking)

| Layer | Tech | Reason |
|---|---|---|
| Language | **Python 3.11+** | Best ecosystem for PDF, Excel, scraping |
| Web framework | **FastAPI** | Async, OpenAPI auto-docs |
| ORM | **SQLAlchemy 2.x** + **Alembic** | Standard, migration support |
| Database | **PostgreSQL 15** | JSONB, transactions, mature |
| Queue | **Redis 7 + RQ** | Right-sized for our volume |
| Scheduler | **APScheduler** | In-process cron, no extra infra |
| Frontend | **React 18 + Vite + TypeScript + Tailwind CSS** (separate SPA) | Modern, fast HMR, user's choice |
| Frontend routing | **React Router v6** | Standard |
| Frontend data fetching | **TanStack Query (React Query) v5** | Server state, caching, retries |
| Frontend forms | **React Hook Form + Zod** | Type-safe forms with validation |
| Frontend UI components | **shadcn/ui** (Radix + Tailwind) | Copy-paste components, no lock-in |
| Frontend icons | **lucide-react** | Consistent icon set |
| Frontend tables | **TanStack Table v8** | For PO lists, master data grids |
| Frontend HTTP client | **axios** with interceptors for auth | |
| PDF parsing | **pdfplumber**, **camelot-py** | Best Python PDF table extractors |
| Excel parsing | **openpyxl**, **pandas** | Standard |
| Email | **google-api-python-client** (Gmail API) | Not IMAP |
| Web scraping | **Playwright** (not Selenium) | More reliable |
| SAP B1 client | **requests** (Service Layer is plain REST) | No SAP SDK needed |
| LLM fallback parser | **Anthropic SDK** (model: `claude-sonnet-4-5`) | For unknown formats |
| Error tracking | **Sentry** | SaaS, free tier |
| Deployment | **Docker Compose** on single VPS | NOT Kubernetes |
| Tests | **pytest**, **pytest-asyncio**, **httpx** for API tests | |
| Linting | **ruff** + **black** + **mypy** (strict on new code) | |
| Secrets | **.env** file via **pydantic-settings** | |

### Forbidden (do NOT add unless user explicitly approves)
- Kafka, RabbitMQ, Airflow, Celery, Temporal
- Kubernetes, Helm
- Prometheus, Grafana, Jaeger, ELK/Loki
- MongoDB or any non-Postgres database
- React/Vue/Angular (unless user asks for a separate SPA) — ✅ **APPROVED: React + Vite is the chosen frontend**
- Next.js, Remix, or any SSR React framework (we're using plain Vite SPA)
- Vue, Angular, Svelte
- Redux, MobX, Zustand (use TanStack Query for server state; useState/useReducer for local state)
- CSS-in-JS libraries (styled-components, emotion) — Tailwind only
- Material UI, Ant Design, Chakra (we use shadcn/ui)
- Microservices — this is **one application** with multiple worker types

---

## 3. Repository Structure

Always follow this layout. Create directories as needed per phase.

```
edi-middleware/
├── CLAUDE.md                       ← this file
├── README.md                       ← human-facing setup guide
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── pyproject.toml
├── alembic.ini
├── alembic/
│   └── versions/                   ← all DB migrations
├── app/
│   ├── __init__.py
│   ├── main.py                     ← FastAPI entry
│   ├── config.py                   ← pydantic-settings
│   ├── db.py                       ← SQLAlchemy engine + session
│   ├── logging_config.py
│   ├── models/                     ← SQLAlchemy models, one file per domain
│   │   ├── __init__.py
│   │   ├── master_data.py
│   │   ├── raw_messages.py
│   │   ├── edi_po.py
│   │   ├── asn.py
│   │   ├── invoice.py
│   │   └── b1_log.py
│   ├── schemas/                    ← Pydantic schemas (canonical EDI types)
│   │   ├── __init__.py
│   │   ├── canonical.py            ← EDI850, EDI850Line, etc.
│   │   └── api.py                  ← request/response models
│   ├── adapters/                   ← one folder per source channel
│   │   ├── __init__.py
│   │   ├── base.py                 ← BaseAdapter interface
│   │   ├── email/
│   │   │   ├── gmail_client.py
│   │   │   ├── blinkit_email.py
│   │   │   ├── swiggy_email.py
│   │   │   └── ...
│   │   ├── api/
│   │   │   ├── blinkit_api.py
│   │   │   ├── zepto_api.py
│   │   │   └── ...
│   │   └── portal/
│   │       ├── playwright_base.py
│   │       ├── reliance_portal.py
│   │       └── ...
│   ├── parsers/                    ← canonical-document extractors
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── blinkit_parser.py
│   │   ├── zepto_parser.py
│   │   ├── llm_fallback.py
│   │   └── ...
│   ├── validators/                 ← business rule + schema validation
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   └── rules/
│   │       ├── gstin.py
│   │       ├── sku_mapping.py
│   │       ├── pricing.py
│   │       └── ...
│   ├── mappers/                    ← canonical ↔ B1 transforms
│   │   ├── __init__.py
│   │   ├── po_to_sales_order.py
│   │   ├── delivery.py
│   │   └── invoice.py
│   ├── sap_b1/
│   │   ├── __init__.py
│   │   ├── client.py               ← Service Layer client
│   │   ├── session_pool.py
│   │   └── errors.py
│   ├── workflows/                  ← end-to-end orchestration
│   │   ├── __init__.py
│   │   ├── ingest_to_canonical.py
│   │   ├── canonical_to_b1.py
│   │   ├── b1_to_outbound.py
│   │   └── rtv_flow.py
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── scheduler.py            ← APScheduler entry
│   │   └── jobs.py                 ← RQ job functions
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py
│   │   ├── routes/
│   │   │   ├── health.py
│   │   │   ├── pos.py
│   │   │   ├── master_data.py
│   │   │   ├── exceptions.py
│   │   │   ├── dashboard.py
│   │   │   └── auth.py
│   └── utils/
│       ├── ids.py
│       ├── money.py                ← Decimal-safe arithmetic
│       ├── gst.py                  ← interstate logic, rate splits
│       └── time.py
├── frontend/                       ← React + Vite SPA (separate package)
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── .eslintrc.cjs
│   ├── index.html
│   ├── public/
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── router.tsx
│       ├── lib/
│       │   ├── api-client.ts       ← axios instance + interceptors
│       │   ├── queryClient.ts      ← TanStack Query config
│       │   └── utils.ts            ← cn(), formatters
│       ├── hooks/                  ← reusable hooks (useAuth, usePOs, etc.)
│       ├── components/
│       │   ├── ui/                 ← shadcn/ui primitives
│       │   ├── layout/             ← Sidebar, Topbar, Shell
│       │   └── shared/             ← StatusBadge, GstInput, etc.
│       ├── features/               ← one folder per domain area
│       │   ├── auth/
│       │   ├── pos/                ← PO list, PO detail
│       │   ├── exceptions/
│       │   ├── master-data/        ← SKU/Ship-to/Partner CRUD
│       │   ├── b1-logs/
│       │   └── dashboard/
│       ├── types/                  ← TypeScript types matching Pydantic schemas
│       └── styles/
│           └── globals.css
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/                   ← sample PO PDFs, JSON responses
│   └── conftest.py
└── scripts/
    ├── backfill_historical.py
    ├── seed_master_data.py
    └── test_b1_connection.py
```

---

## 4. Coding Standards

### General
- **Python 3.11+ syntax**. Use type hints everywhere. Use `from __future__ import annotations` at the top of every module.
- **No `from X import *`**. Explicit imports only.
- **Decimal for money**, never float. Use `decimal.Decimal` and `quantize` with `ROUND_HALF_UP`.
- **Timezone-aware datetimes** only. Default to UTC in DB, convert to IST (`Asia/Kolkata`) only at the UI boundary.
- Use `pathlib.Path`, not `os.path`.
- Use `httpx` for outbound HTTP except where `requests` is already in use for B1 client (keep that one consistent).
- **Async** for FastAPI routes and external I/O where the library supports it; **sync** for worker code (RQ is sync, that's fine).

### Structure / patterns
- Each adapter, parser, and mapper has a **base class / Protocol** and concrete implementations.
- **Idempotency everywhere**: every external operation must be safe to retry. Use natural keys (`gmail_message_id`, `(partner_id, buyer_po_number, version)`).
- **Never hard-delete**. Use `deleted_at TIMESTAMPTZ` soft-delete.
- **Logs are structured**: use `structlog` or plain JSON via `logging.Formatter`. Every log line tied to a `correlation_id` (the canonical doc UUID).
- **No silent except**. Catch specific exceptions; always log; never `except: pass`.
- **Functions ≤ 50 lines**, classes ≤ 300 lines. If longer, split.

### Database
- **All schema changes go through Alembic.** Never write raw `ALTER TABLE` in code.
- **Use SQLAlchemy 2.x style** (`select()`, `session.execute()`), not legacy `Query`.
- Add indexes for every column used in a `WHERE`/`JOIN` of a hot query.
- Always include `created_at`, `updated_at` on mutable tables; add an `updated_at` trigger.

### Naming
- Tables: `snake_case`, plural (`edi_purchase_orders`).
- Columns: `snake_case`.
- Python classes: `PascalCase`. Functions/vars: `snake_case`.
- Constants: `UPPER_SNAKE`.
- Adapter classes: `<Partner>Adapter` (e.g. `BlinkitApiAdapter`, `SwiggyEmailAdapter`).
- Parser classes: `<Partner>Parser`.

### Testing
- Every parser ships with **at least 3 fixture files** and tests for: happy path, missing field, multi-page PDF.
- Mappers tested with golden-file JSON comparisons.
- B1 client tested against **mocked Service Layer** using `responses` or `respx`.
- Aim for ≥ 80% coverage on `app/parsers`, `app/mappers`, `app/validators`.

### Documentation
- Every adapter/parser file has a top-of-file docstring with: source format example, known quirks, sample document IDs.
- Update `README.md` whenever a new vendor is onboarded.

### Frontend (React + Vite)
- **TypeScript strict mode** (`"strict": true` in `tsconfig.json`). No `any` without a `// FIXME:` comment.
- **Functional components only**. No class components.
- **Server state via TanStack Query**; local state via `useState`/`useReducer`. Do NOT install Redux/Zustand/MobX.
- **Forms**: React Hook Form + Zod schema. Zod schemas live next to forms; reuse for inference (`z.infer<typeof schema>`).
- **Folder structure**: feature-first under `src/features/<domain>/`. Each feature has its own `components/`, `hooks/`, `api.ts`, `types.ts`.
- **API layer**: every endpoint wrapped in a typed function in `src/features/<domain>/api.ts`; never call `axios` directly from a component.
- **Types mirror Pydantic**: when a backend Pydantic schema changes, the TypeScript type in `src/types/` must be updated in the same PR. Optionally, generate types from the FastAPI OpenAPI schema using `openapi-typescript` (script: `npm run gen:types`).
- **Components**: ≤ 200 lines. Split into smaller components if longer.
- **Styling**: Tailwind utility classes only. No inline `style={{}}` except for dynamic values that can't be expressed in Tailwind.
- **UI primitives**: use `shadcn/ui` components installed via CLI into `src/components/ui/`. Don't install Material UI, Ant Design, etc.
- **Routing**: React Router v6 with file-organized routes in `src/router.tsx`.
- **Loading & error states are mandatory**. Every query-driven UI must handle `isLoading`, `isError`, and empty state explicitly. No silent blank screens.
- **Date/time display**: dates in IST (`Asia/Kolkata`) using `date-fns-tz`. Backend sends UTC ISO strings; frontend converts at the boundary.
- **Money display**: format with `Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' })`. Never use raw `toFixed(2)`.
- **Auth**: JWT stored in `httpOnly` cookie (set by backend) OR in `localStorage` with axios interceptor. Decision in Phase 8.
- **No client-side routing of sensitive data through URL params** (no GSTINs, tokens, etc. in query strings).
- **Accessibility**: every interactive element must be keyboard-reachable; use semantic HTML; alt text on images.
- **Tests**: **Vitest** + **React Testing Library**. Test feature-level user flows, not implementation details. Aim for coverage on critical features (PO list, SKU mapping form, exception resolution).
- **Linting**: ESLint with `@typescript-eslint`, `eslint-plugin-react-hooks`, `eslint-plugin-react-refresh`. Prettier for formatting.
- **Build**: Vite production build (`npm run build`) outputs to `frontend/dist/`. Backend serves it via FastAPI `StaticFiles` mount at `/` (production) OR runs separately on `localhost:5173` (dev) with CORS allowed.

---

## 5. Phase-Wise Build Plan

> **CRITICAL — How Claude should work through this:**
>
> 1. **Do one phase at a time.** Do NOT skip ahead. Do NOT mix phases.
> 2. Before starting a phase, **announce which phase you're starting** and **list the deliverables** for that phase.
> 3. Build the deliverables in the listed order.
> 4. After each phase, **stop and ask the user** to verify before moving to the next phase. Do not start the next phase autonomously.
> 5. If the user says "continue", proceed to the next phase. If they say "fix X first", address that before moving on.
> 6. If a phase reveals a missing decision (e.g. "what's the Gmail label name for X"), **stop and ask** — don't guess.

---

### **Phase 0 — Foundation Setup**

**Goal:** Working skeleton (backend + frontend) that can be deployed, has DB, has Docker, has CI/CD basics.

**Backend deliverables:**
1. Repository skeleton matching section 3 above (empty `__init__.py` files where needed).
2. `pyproject.toml` with all dependencies pinned to compatible versions.
3. `Dockerfile` (multi-stage, slim Python base).
4. `docker-compose.yml` with services: `postgres`, `redis`, `api`, `scheduler`, `worker-ingest`, `worker-parse`, `worker-sap`, `frontend` (Vite dev server in dev profile only).
5. `.env.example` listing every config variable required (DB URL, Redis URL, B1 URL/user/pass/DB, Gmail creds path, Sentry DSN, CORS origins).
6. `app/config.py` using `pydantic-settings`.
7. `app/db.py` with async + sync engines.
8. `app/main.py` with a `/health` endpoint returning `{ "status": "ok", "db": "ok", "redis": "ok" }` and CORS middleware configured for `http://localhost:5173`.
9. Alembic initialized; first migration is an empty no-op (to confirm tooling works).
10. `pre-commit` config with ruff + black + (for frontend) prettier + eslint.

**Frontend deliverables:**
11. `frontend/` initialized with `npm create vite@latest frontend -- --template react-ts`.
12. Install: `react-router-dom`, `@tanstack/react-query`, `axios`, `react-hook-form`, `zod`, `@hookform/resolvers`, `tailwindcss`, `postcss`, `autoprefixer`, `lucide-react`, `date-fns`, `date-fns-tz`, `clsx`, `tailwind-merge`.
13. Initialize Tailwind (`npx tailwindcss init -p`) with the shadcn/ui content paths configured.
14. Install shadcn/ui CLI and run `npx shadcn@latest init` — pick: TypeScript, default style, slate base color, CSS variables, `src/components/ui` path.
15. Pre-install a starter set of shadcn/ui components: `button`, `input`, `label`, `card`, `table`, `badge`, `dialog`, `dropdown-menu`, `select`, `toast`, `tabs`, `skeleton`, `alert`.
16. `frontend/src/lib/api-client.ts` — axios instance with `baseURL` from env, request/response interceptors (401 → redirect to login).
17. `frontend/src/lib/queryClient.ts` — TanStack Query client with sensible defaults (5min stale, 1 retry).
18. `frontend/src/App.tsx` — wraps `QueryClientProvider`, `RouterProvider`, theme.
19. `frontend/src/router.tsx` — basic routes: `/login`, `/`, `/pos`, `/exceptions`, `/master-data` (placeholder pages).
20. A single working page: `/` shows "EDI Middleware" with a card pinging `/health` via TanStack Query and showing the response — proves end-to-end wiring.
21. `.env.development` and `.env.production` in frontend with `VITE_API_BASE_URL`.
22. `frontend/Dockerfile` (multi-stage: build with node, serve with nginx).
23. `README.md` updated with: prerequisites, `cp .env.example .env`, `docker compose up`, how to run migrations, **how to run the frontend** (`cd frontend && npm install && npm run dev`).

**Exit criteria:** `docker compose up` runs cleanly; `GET /health` returns 200; `alembic upgrade head` succeeds; `cd frontend && npm run dev` boots Vite on port 5173; the home page successfully fetches and displays the `/health` response from the backend.

---

### **Phase 1 — Canonical EDI Schema (Database)**

**Goal:** Full canonical data model in Postgres, no business logic yet.

Deliverables:
1. Alembic migration creating ALL tables from the canonical EDI schema (see section 7 below for table list).
2. SQLAlchemy models under `app/models/` matching the schema. One file per domain area.
3. Pydantic schemas under `app/schemas/canonical.py` for `EDI850`, `EDI850Line`, `EDIAddress`, `ASN`, `Invoice`, etc.
4. Seed script `scripts/seed_master_data.py` that inserts: 1 seller entity, 15 trading partners (one row per Gmail label), and 5 sample items + sku mappings + ship-to mappings.
5. Triggers for `updated_at` auto-update and PO status history logging.
6. Views: `v_po_summary`, `v_exception_queue`.
7. Unit tests confirming models save & reload correctly, FKs work, soft-delete works.

**Exit criteria:** All tables created; seed script runs; tests pass; ER diagram exported as `docs/erd.png` (use `eralchemy` or similar).

---

### **Phase 2 — Email Ingestion (Gmail-based partners)**

**Goal:** Pull PO emails from Gmail labels, store raw, save attachments. NO parsing yet.

Deliverables:
1. `app/adapters/email/gmail_client.py` — OAuth2 flow, label listing, message fetch, attachment download.
2. `app/adapters/email/base.py` — `BaseEmailAdapter` interface.
3. `app/adapters/email/blinkit_email.py` — concrete first adapter (label: `BLINKIT_PO`).
4. `app/workflows/ingest_to_canonical.py` — ingestion workflow (raw save only, parse step is a stub).
5. RQ job `ingest_label_job(label_name, partner_code)` in `app/workers/jobs.py`.
6. APScheduler in `app/workers/scheduler.py` triggering ingestion every 2 minutes for all email-based partners.
7. Idempotency: same `gmail_message_id` cannot be inserted twice (DB unique constraint + pre-check).
8. Attachments saved to `./data/attachments/{partner_code}/{yyyy-mm-dd}/{message_id}/{filename}` (local disk for now; S3 abstraction interface ready for later).
9. `scripts/auth_gmail.py` — one-time CLI script to authorize Gmail and produce `token.json`.
10. Tests with mocked Gmail responses.

**Exit criteria:** Running the worker for 5 minutes against a real Gmail account populates `raw_messages` with at least one record per active label; attachments saved on disk; no duplicates on re-run.

---

### **Phase 3 — Parser Layer for Email-Based Partners**

**Goal:** Convert raw emails/PDFs into canonical `EDI850` documents in DB.

Deliverables:
1. `app/parsers/base.py` — `BaseParser` abstract class with `can_parse()` and `parse()` methods.
2. `app/parsers/registry.py` — dict mapping partner_code → parser class.
3. **One parser per email-based partner** — start with Blinkit, then Swiggy, then add others one PR per parser. The user will confirm priority order.
4. Each parser produces a valid `EDI850` Pydantic schema instance.
5. RQ job `parse_raw_message_job(raw_message_id)`.
6. Worker pipeline: after raw save (Phase 2), enqueue parse job.
7. On parse success → write to `edi_purchase_orders` + `edi_po_line_items`.
8. On parse failure → write to `parse_failures` (the `edi_validation_issues` table with code `E000_PARSE_FAILED`); mark raw message as `processed=true, parse_status='FAILED'`.
9. `app/parsers/llm_fallback.py` — LLM-based parser using Anthropic SDK as last resort when regex/table extraction fails. Only invoked if explicitly enabled per-partner in config.
10. Tests with at least 3 real PDF fixtures per parser.

**Exit criteria:** ≥ 90% of historical Blinkit/Swiggy POs parse successfully into the canonical schema; failures appear in the exception view.

---

### **Phase 4 — API-Based Partner Adapters**

**Goal:** Pull POs directly from REST APIs of partners that provide them.

> **Important:** Blinkit and Zepto adapters already exist in `_archive/backend_old/`. Before writing the new adapters, read those files carefully and document findings in `docs/legacy-api-notes.md` (this should already exist from Phase 0). The new adapters re-implement that logic cleanly in the new structure — they do NOT copy-paste from the old code.

Deliverables:
1. `app/adapters/api/base.py` — `BaseApiAdapter` interface with `fetch_new_pos(since)` and `fetch_po_detail(po_id)`.
2. Concrete adapters for partners with APIs — order: **Blinkit API → Zepto** (both have legacy reference) **→ BigBasket → Amazon SP-API → Flipkart Seller**. One PR per partner.
3. Auth handling: OAuth2 (refresh tokens stored encrypted in DB), API keys, HMAC signatures — as required per partner. Reference legacy code for the exact auth flows that work in production.
4. Same downstream pipeline as Phase 3: API response → raw_messages → canonical EDI doc.
5. Watermark/cursor tracking per partner (`last_fetched_at` in partner config) to avoid refetching.
6. Webhook receiver endpoint `/api/webhooks/{partner_code}` for partners that support push (Blinkit does — confirm by checking legacy code).
7. Rate-limit-aware fetching (respect `Retry-After`, add backoff).
8. Tests with `respx` mocking partner APIs. Use real response payloads captured from `_archive/` as fixtures (sanitize secrets first).
9. Commit messages cite the legacy file(s) used as reference.

**Exit criteria:** API partners' POs flow end-to-end into canonical schema with the same shape as email-based ones. Both Blinkit and Zepto adapters in the new code produce identical results to the legacy code when given the same inputs.

---

### **Phase 5 — Validation & Master-Data Mapping**

**Goal:** Every canonical PO is validated and SKUs/warehouses are mapped to B1 codes before SAP push.

Deliverables:
1. `app/validators/engine.py` — runs ordered list of rule classes, produces `ValidationResult`.
2. Rule classes in `app/validators/rules/`:
   - `GstinFormatRule` — validates buyer/seller GSTIN structure.
   - `SkuMappingRule` — flags unmapped SKUs.
   - `ShipToMappingRule` — flags unmapped warehouses.
   - `PriceVarianceRule` — actual vs contracted price diff > X%.
   - `TaxConsistencyRule` — CGST+SGST OR IGST, never both; rates match HSN.
   - `TotalReconciliationRule` — sum of line totals == header grand_total ± rounding.
   - `MoqRule` — minimum order quantity check.
3. Results written to `edi_validation_issues`. PO status set to `VALIDATED`, `EXCEPTION`, or kept at `PARSED` depending on severity.
4. **Auto-mapping helper** for SKUs: EAN exact match → fuzzy description match (threshold 0.85 via `rapidfuzz`) → flag for manual review. Auto-mapped records get `mapping_status='AUTO_MAPPED'`, `confidence_score`.
5. Manual review UI endpoint (basic, JSON-only — full UI in Phase 8): `GET /api/exceptions`, `POST /api/sku-mapping`.
6. Tests for each rule covering pass/fail/edge cases.

**Exit criteria:** Running validation on Phase 3/4 output produces correct mapping_status and validation_issues; ~80%+ of SKUs auto-mapped after a single ops review session.

---

### **Phase 6 — SAP Business One Service Layer Integration**

**Goal:** Push validated POs into SAP B1 as Sales Orders.

Deliverables:
1. `app/sap_b1/client.py` — full Service Layer client with:
   - Login, logout, auto re-login on 401.
   - Session pooling (max N concurrent sessions, configurable; default 2).
   - Methods: `create_sales_order`, `create_delivery`, `create_invoice`, `create_return`, `create_credit_note`, `get_item`, `get_business_partner`, generic `query`.
   - Returns parsed responses; raises `B1ApiError` with `code` and `message` on failure.
2. `app/sap_b1/session_pool.py` — thread-safe session manager (B1 has limited concurrent sessions).
3. `app/mappers/po_to_sales_order.py` — canonical EDI850 → B1 Sales Order JSON payload.
4. RQ job `push_po_to_b1_job(po_id)`:
   - Pre-flight checks (status, mapping completeness).
   - Build payload via mapper.
   - Call client.
   - On success: update PO with `b1_sales_order_doc_entry`, `b1_sales_order_doc_num`, status → `SAP_CONFIRMED`.
   - On failure: status → `SAP_REJECTED`, log error.
   - Always write to `b1_api_log`.
5. Scheduler triggers push for all `VALIDATED` POs every 1 minute.
6. **Single dedicated worker** for SAP push (concurrency limit because of B1 session limits).
7. `scripts/test_b1_connection.py` — standalone script to verify B1 connectivity and credentials.
8. UDF setup documentation in `docs/b1_setup.md` covering required UDFs (`U_EDI_SOURCE`, `U_EDI_DOC_UUID`, `U_EDI_RECEIVED_AT`, `U_BUYER_GSTIN`, `U_EDI_PO_NUMBER`, `U_EDI_LINE_NO`, `U_BUYER_SKU`).
9. Tests using mocked Service Layer.

**Exit criteria:** A validated PO appears as a Sales Order in B1 with all UDFs populated; failures are visible in `b1_api_log` with full request/response.

---

### **Phase 7 — Outbound Documents (Ack, ASN, Invoice, Credit Note)**

**Goal:** Send confirmations, dispatch notices, invoices, and credit notes back to retailers.

Deliverables:
1. **855 PO Acknowledgement**: after SAP_CONFIRMED, send ack back to partner. Channel per partner: API call for API partners, formatted email reply for email partners.
2. **856 ASN**: triggered when a B1 Delivery is created against the Sales Order. We poll B1 for new Deliveries linked to our Sales Orders, OR (better) provide a small B1 extension/UDF event hook. **Start with polling.**
3. **810 Invoice**: triggered when a B1 A/R Invoice is created. Same polling approach.
4. **Credit Note / Return (for RTV — the 1,420 RTV emails)**: dedicated workflow in `app/workflows/rtv_flow.py`. Inbound RTV email → parse → match to original PO/Invoice → create B1 Return or Credit Memo → optionally notify partner.
5. `edi_outbound_messages` table tracks all outbound docs with retry, status, ack_received_at.
6. Per-partner outbound formatters (e.g., Blinkit ASN JSON vs Swiggy ASN email PDF).
7. Retry policy: 5 attempts with exponential backoff (1m, 5m, 30m, 2h, 6h).
8. SLA monitoring: ack must be sent within `trading_partners.ack_sla_hours`; alert if breached.

**Exit criteria:** Full document cycle works end-to-end for at least one API partner and one email partner.

---

### **Phase 8 — Operations Dashboard (React Frontend)**

**Goal:** Full-featured React SPA for the ops team to monitor, fix exceptions, and manage master data.

**Backend deliverables:**
1. Complete the API routes under `app/api/routes/`:
   - `auth.py` — `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`. JWT-based.
   - `pos.py` — `GET /pos` (paginated, filterable), `GET /pos/{id}`, `POST /pos/{id}/retry-sap`, `POST /pos/{id}/cancel`.
   - `exceptions.py` — `GET /exceptions`, `POST /exceptions/{id}/resolve`.
   - `master_data.py` — full CRUD for partners, sku_mapping, ship_to_mapping, material_master.
   - `dashboard.py` — `GET /dashboard/today`, `GET /dashboard/sla-breaches`, `GET /dashboard/unmapped-skus`.
   - `b1_logs.py` — `GET /b1-logs` (paginated, filter by status/po_id).
2. Pagination via `limit`/`offset` or cursor — pick one and be consistent.
3. All endpoints return Pydantic response models with proper typing.
4. OpenAPI schema auto-generated at `/openapi.json`; frontend types regenerated via `npm run gen:types`.
5. Audit logging middleware: capture every mutation (who, when, before/after) into a new `audit_log` table.

**Frontend deliverables (`frontend/src/`):**

6. **Layout** (`components/layout/`):
   - `Shell.tsx` — sidebar + topbar + content slot.
   - `Sidebar.tsx` — nav links with active-state highlighting.
   - `Topbar.tsx` — user menu, environment badge (DEV/STAGING/PROD).

7. **Auth feature** (`features/auth/`):
   - Login page with email + password (React Hook Form + Zod).
   - `useAuth` hook (current user, login, logout).
   - Protected route wrapper that redirects to `/login` if unauthenticated.

8. **Dashboard / Home** (`features/dashboard/`):
   - Page route `/`.
   - Cards: today's PO count per partner, SLA breach count, unmapped SKU count, B1 push failures.
   - Recent activity feed (last 20 events).
   - Auto-refresh every 30s via TanStack Query `refetchInterval`.

9. **PO list** (`features/pos/`):
   - Page route `/pos`.
   - TanStack Table v8 with: filters (partner, status, date range), pagination, sorting.
   - Status badge component (color-coded by `po_status_t` enum).
   - Click row → navigate to `/pos/:id`.
   - URL-synced filters (filters live in query string so the page is shareable/bookmarkable).

10. **PO detail** (`features/pos/`):
    - Page route `/pos/:id`.
    - Tabs: **Overview**, **Line Items**, **Validation Issues**, **B1 Push History**, **Outbound Messages**, **Raw Source**.
    - "Retry SAP Push" button (only if status = `SAP_REJECTED`).
    - "Cancel PO" button (only if status allows).
    - Raw source tab: download original PDF inline (`<embed>` or `<iframe>` for PDFs).

11. **Exceptions queue** (`features/exceptions/`):
    - Page route `/exceptions`.
    - Grouped by severity (errors first, then warnings).
    - Inline "Resolve" action with note field.
    - For unmapped SKUs: inline mapping form (dropdown search of material master + UoM conversion field).

12. **Master data** (`features/master-data/`):
    - Tabs: **Partners**, **SKU Mapping**, **Ship-to Mapping**, **Material Master**.
    - Each tab: table with search + filters, "Add new" dialog, inline edit.
    - SKU Mapping: search by buyer SKU or EAN; auto-suggest material master matches.
    - Bulk import via CSV upload with preview-before-commit.

13. **B1 Logs** (`features/b1-logs/`):
    - Filterable by HTTP status (errors only by default), date range, PO ID.
    - Detail view shows full request JSON + response JSON, syntax-highlighted (`react-syntax-highlighter`).

14. **Shared components** (`components/shared/`):
    - `StatusBadge` — maps PO status to color and label.
    - `MoneyDisplay` — formats amounts in INR with proper rounding.
    - `DateDisplay` — UTC → IST formatting.
    - `EmptyState` — consistent empty/no-results UI.
    - `LoadingSkeleton` — matches each table layout.

15. **Toast notifications**: use shadcn `toast` for success/error feedback on every mutation.

16. **Production build & serving**:
    - `npm run build` → `frontend/dist/`.
    - Backend mounts at root: `app.mount("/", StaticFiles(directory="frontend/dist", html=True))` (only in production).
    - API routes prefixed `/api` to avoid clash with SPA routing.
    - SPA fallback: any non-`/api` route serves `index.html`.

17. **Tests** (Vitest + RTL):
    - PO list filtering renders correctly.
    - SKU mapping form validates and submits.
    - Exception resolution updates the list.

**Exit criteria:** Ops user can log in, see today's POs, click into one, fix an unmapped SKU inline, re-trigger a SAP push, view a parse failure with the raw PDF inline; all without ever editing the database directly. Production build serves from a single backend container.

---

### **Phase 9 — Portal Scraping (Last-Resort Adapter)**

**Goal:** Cover partners with no API and no useful email — Reliance/JioMart, sometimes Flipkart, etc.

Deliverables:
1. `app/adapters/portal/playwright_base.py` — base class managing browser lifecycle, login, cookie persistence.
2. Concrete scrapers per portal — one per PR.
3. Headless mode in production, headed in dev for debugging.
4. Same downstream pipeline (raw_message → canonical).
5. Robust selectors with retry; screenshot on failure for debugging.
6. **MFA/CAPTCHA strategy** documented per portal (some require manual intervention — design a "park" flow where the scraper pauses and emails ops).
7. Tests use Playwright's record/replay where possible.

**Exit criteria:** At least one portal-only partner (Reliance suggested) flows end-to-end.

---

### **Phase 10 — Hardening, Backfill, Production Readiness**

**Goal:** Make it production-grade.

Deliverables:
1. `scripts/backfill_historical.py` — process all ~5,000 historical emails into canonical schema. Idempotent; resumable.
2. Sentry integration with release tagging.
3. DB backup script + restore-test script.
4. Health check enhancements: `/health/detailed` showing per-queue depth, last-successful-fetch per partner, B1 connectivity.
5. Alerting via Slack webhook on: parse failure rate > 5%, SAP push failure rate > 2%, any partner with no PO in 24h (might indicate broken integration).
6. `docs/runbook.md` — common incidents and remediation.
7. Load test with simulated burst of 500 POs in 5 minutes; tune worker counts.
8. Security review: secrets in env only, DB user with least privilege, HTTPS via nginx, rate-limited public endpoints.
9. Documentation pass on all `README.md`, `docs/*`, and inline docstrings.

**Exit criteria:** System runs for 7 consecutive days with zero manual intervention; backfill complete; runbook tested.

---

## 6. Canonical EDI Schema (Reference)

> Full DDL is in `alembic/versions/0001_canonical_edi_schema.py` (created in Phase 1). The tables are:

**Master data**
- `trading_partners` — Blinkit, Zepto, etc., with B1 `CardCode` linkage.
- `seller_entities` — your company, with `b1_company_db`.
- `material_master` — your SKUs with `b1_item_code`.
- `sku_mapping` — buyer SKU → your material master + UoM conversion.
- `ship_to_mapping` — buyer warehouse → B1 `WhsCode`.

**Inbound**
- `raw_messages` — immutable original payloads (email, API, scrape), with S3/disk pointer to attachments.

**Canonical EDI documents**
- `edi_purchase_orders` — header, with B1 Sales Order linkage (`b1_sales_order_doc_entry`, `b1_sales_order_doc_num`).
- `edi_po_line_items` — line-level, with `sap_material_no` (= `b1_item_code` once mapped), GST split, shipped/invoiced/accepted qty.
- `edi_po_status_history` — full lifecycle audit.
- `edi_validation_issues` — every rule failure or warning, resolvable by ops.

**Outbound**
- `edi_outbound_messages` — 855/856/810/credit-note delivery state.
- `edi_advance_ship_notices` + `edi_asn_line_items` — ASN.
- `edi_invoices` + `edi_invoice_line_items` — A/R Invoice with IRN, e-way bill fields.

**Integration logs**
- `b1_api_log` — every Service Layer request/response.

Enums: `source_channel_t`, `edi_doc_type_t`, `po_status_t`, `validation_status_t`, `mapping_status_t`.

(See user's prior conversation with Claude for the full DDL — Phase 1 implements it exactly as designed there.)

---

## 7. SAP Business One Integration Specifics

- **ERP version**: SAP Business One (NOT ECC, NOT S/4HANA).
- **Integration method**: Service Layer (REST/OData v4) only. Default port `50000` (HTTP, dev) or `50001` (HTTPS, prod).
- **Inbound retailer PO → B1 Sales Order** (object code `17`, table `ORDR`/`RDR1`). Never use B1's Purchase Order object.
- **Sessions**: B1 sessions expire in 30 mins; re-login on 401; pool with concurrency cap (default 2).
- **License**: each session consumes a Service Layer license slot — do NOT spawn unlimited workers for SAP push.
- **Decimal precision**: B1 prices use 6 decimal places — always serialize via `str(Decimal)` or `float` carefully.
- **India localization**: B1 must have India localization enabled. CGST+SGST vs IGST is auto-derived by B1 from `BPLId` (branch place) + customer state — but we ALSO compute and store the breakdown in our canonical schema for outbound docs and audit.
- **UoM groups**: items often have UoM groups (e.g., 1 Case = 24 PCS). Conversion happens in our middleware (`sku_mapping.qty_per_buyer_uom`) — we send quantities in **inventory UoM** to B1.
- **Posting periods**: if a PO targets a closed posting period, B1 rejects with a specific error. Surface this clearly.
- **Self-signed certs**: dev B1 often uses self-signed SSL. Configurable `verify_ssl` flag; **must be True in production**.

### Required B1 setup (do BEFORE Phase 6)
- API user (e.g., `EDI_BOT`) with permissions: add Sales Order/Delivery/Invoice/Return/Credit Memo; read-only on Items, BPs, Warehouses.
- UDFs on `ORDR` (header) and `RDR1` (lines) — listed in Phase 6 deliverables.
- Business Partners (Customers) created for each retailer with `CardCode`, GSTIN, payment terms, default warehouse.
- Item master populated with `ItemCode`, HSN, tax codes (`GST5`, `GST12`, `GST18`, `GST28`).
- Warehouses created with `WhsCode`.

---

## 8. India-Specific Rules

- **GSTIN format**: 15 chars, validate via regex `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[0-9A-Z]{1}Z[0-9A-Z]{1}$`.
- **CGST + SGST vs IGST**: if seller state == buyer ship-to state → CGST+SGST (each = half of GST rate); else IGST = full rate. Helper in `app/utils/gst.py`.
- **HSN code**: required on every line item; varies by product.
- **e-Invoicing (IRN)**: required for taxpayers above threshold. We capture IRN once SAP B1 (with IRP integration) returns it. We do NOT call IRP directly — that's B1's job.
- **e-Way Bill**: required above ₹50,000 inter-state movement. Capture from B1 if available.
- **Rounding**: ₹ rounded to 2 decimals; tax amounts use `ROUND_HALF_UP`; final round-off goes to a `round_off` column.

---

## 9. Operational Rules for Claude

When working on this codebase, you (Claude) MUST:

1. **Confirm the phase** at the start of every work session: "We are in Phase X. Current deliverable: Y."
2. **Re-read this file and PLAYBOOK.md** at the start of every session if more than an hour has passed.
3. **Consult `_archive/` for partner API behavior** before writing any new adapter or parser. The legacy code is the most accurate source for how Blinkit/Zepto APIs actually behave in production.
4. **Never invent partners, labels, or codes** — if you don't see the partner in the seed data or `_archive/`, ask.
5. **Never modify files in `_archive/`**. They are frozen reference material.
6. **Never `import` from `_archive/`** into the new code. Re-implement cleanly in the new structure.
7. **Never write to `main` directly** in version control terms — always create a branch named `phase-X/<short-task>`.
8. **Run tests before declaring a task done**: `pytest` must pass, `ruff check` must pass, `mypy app` must pass (strict on new modules). Frontend: `tsc --noEmit` + `npm run lint` + `npm test`.
9. **Update the CHANGELOG.md** for each completed task with the phase number and a one-line summary.
10. **If a deliverable in this file is ambiguous, ASK** — do not silently make a design choice that locks the project in.
11. **Avoid scope creep**: if a great-sounding feature comes to mind that isn't in the current phase, write it to `docs/backlog.md` instead of implementing it.
12. **Match existing patterns**: read 2-3 sibling files before adding a new one in the same folder.
13. **Do not add new top-level dependencies** without justifying in the PR description and updating section 2 of this file.
14. **Cite legacy references in commits** when re-implementing logic studied from `_archive/`. Format: *"Re-implemented X based on `_archive/backend_old/path/to/file.py:42`"*.

---

## 10. How to Start a Session

When the user opens VS Code and says "continue":

1. Read `CLAUDE.md` (this file) fully.
2. Read `PLAYBOOK.md` fully.
3. Read `CHANGELOG.md` to see what's been done.
4. Check `git status` and `git log -10` for context.
5. Announce: *"We are in Phase X. Last completed: <last changelog entry>. Next deliverable: <next item in phase>. Proceeding unless you'd like a different task."*
6. Wait 5 seconds — if no objection, start working.

When the user says "start phase 0":
- Follow the script in Section 0 exactly. Read PLAYBOOK.md, survey legacy code, archive it, move CLAUDE.md to root, then ask user to confirm before continuing.

When the user says "start phase N" (N > 0):
- Confirm Phase 0 is complete (`_archive/` exists, skeleton is in place).
- Jump to phase N, list its deliverables, confirm order, begin with deliverable 1.

When the user says "review":
- Run tests, lint, mypy. Summarize results. Do NOT make changes unless asked.

When the user says "demo":
- Show how to exercise the most recently completed deliverable end-to-end (curl command, script invocation, or UI screenshot).

When the user references partner API behavior:
- ALWAYS check `_archive/backend_old/` first for the real implementation before guessing or asking.

---

## 11. Definition of "Done" for Any Task

A task is done only when ALL of these are true:

**Backend tasks:**
- [ ] Code matches coding standards (section 4).
- [ ] Tests written and passing (`pytest`).
- [ ] `ruff check app/` passes.
- [ ] `mypy app/<changed_module>` passes (strict on new files).
- [ ] Migration applied cleanly (`alembic upgrade head` then `alembic downgrade -1` then `upgrade head` again).
- [ ] If a new env var: added to `.env.example` and `app/config.py`.
- [ ] If a new dependency: pinned in `pyproject.toml`, justified in commit message.
- [ ] Docstring on new public functions/classes.

**Frontend tasks:**
- [ ] TypeScript compiles without errors (`npm run typecheck` or `tsc --noEmit`).
- [ ] ESLint passes (`npm run lint`).
- [ ] Prettier formatting applied (`npm run format`).
- [ ] Vitest tests pass (`npm test`).
- [ ] Loading, error, and empty states implemented for every data-driven view.
- [ ] Mobile responsive at 768px breakpoint (use Tailwind responsive utilities).
- [ ] No console errors or warnings in browser dev tools.
- [ ] If a new shadcn/ui component: installed via `npx shadcn@latest add <name>`, not copied manually.
- [ ] If a new npm dependency: justified in commit message and added to `frontend/package.json`.
- [ ] Backend Pydantic schema changes → matching TS type updated in same PR.

**All tasks:**
- [ ] `CHANGELOG.md` updated.
- [ ] Manual smoke test passed (described in commit body).

---

## 12. Glossary

- **EDI** — Electronic Data Interchange
- **PO** — Purchase Order (from buyer's perspective). For us = inbound from retailer → becomes Sales Order in B1.
- **ASN** — Advance Ship Notice (EDI 856).
- **RTV** — Return To Vendor. In B1: A/R Credit Memo or Return.
- **IRN** — Invoice Reference Number (India e-invoicing).
- **B1** — SAP Business One.
- **Service Layer** — B1's REST API.
- **CardCode** — B1's Business Partner code (e.g., `C00012`).
- **DocEntry** — B1's internal document ID (integer).
- **DocNum** — B1's user-visible document number.
- **WhsCode** — B1 warehouse code.
- **ItemCode** — B1 item/material code.
- **UDF** — User-Defined Field in B1.
- **HSN** — Harmonized System of Nomenclature (Indian product tax code).
- **GSTIN** — GST Identification Number (15-char).
- **SPA** — Single Page Application (our React frontend).
- **TanStack Query** — server-state library (formerly React Query). Handles caching, refetching, retries.
- **shadcn/ui** — copy-paste component library built on Radix UI primitives + Tailwind. NOT an npm package.
- **Vite** — frontend build tool. Dev server on port 5173, production build to `frontend/dist/`.
- **HMR** — Hot Module Replacement (Vite dev feature).

---

*End of CLAUDE.md. Keep this file authoritative. When in doubt, this file wins.*
