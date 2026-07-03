# Changelog

## Phase 0 — Foundation Setup (2026-06-29)
- Documented legacy Blinkit/Zepto APIs in `docs/legacy-api-notes.md`
- Documented legacy frontend screens in `docs/legacy-frontend-notes.md`
- Archived `backend/` → `_archive/backend_old/`, `frontend/` → `_archive/frontend_old/`
- Moved `CLAUDE.md` to workspace root
- Repo skeleton with all `__init__.py` stubs
- `pyproject.toml` with all dependencies pinned
- `Dockerfile` (multi-stage Python 3.11-slim)
- `docker-compose.yml` (7 services: postgres, redis, api, scheduler, worker-ingest, worker-parse, worker-sap)
- `.env.example` with all required config vars
- `app/config.py` (pydantic-settings)
- `app/db.py` (async asyncpg + sync psycopg2 engines)
- `app/logging_config.py` (structlog JSON)
- `app/main.py` + `app/api/routes/health.py` (`GET /health`)
- Alembic initialized + migration `0000` (no-op tooling check)
- `.pre-commit-config.yaml` (ruff, mypy, prettier, eslint)
- Frontend: Vite react-ts + Tailwind v4 + shadcn/ui (13 components) + TanStack Query + React Router v6
- Frontend: `api-client.ts`, `queryClient.ts`, `App.tsx`, `router.tsx`, `HomePage` (pings `/health`)
- Frontend: `Dockerfile` (3-stage: dev/builder/production+nginx)
- `README.md` with full setup instructions

## Phase 1 — Canonical EDI Schema (2026-07-02)
- `app/models/_enums.py` — 5 PostgreSQL enums as Python `StrEnum`
- `app/models/master_data.py` — SellerEntity, TradingPartner, MaterialMaster, SkuMapping, ShipToMapping
- `app/models/raw_messages.py` — RawMessage (immutable inbound store)
- `app/models/edi_po.py` — EdiPurchaseOrder, EdiPoLineItem, EdiPoStatusHistory, EdiValidationIssue
- `app/models/asn.py` — EdiAdvanceShipNotice, EdiAsnLineItem
- `app/models/invoice.py` — EdiInvoice, EdiInvoiceLineItem (with IRN/e-way bill fields)
- `app/models/outbound.py` — EdiOutboundMessage
- `app/models/b1_log.py` — B1ApiLog
- `app/schemas/canonical.py` — EDI850, EDI850Line, EDIAddress, ASNDoc, InvoiceDoc, ValidationResult Pydantic schemas
- `alembic/versions/0001_canonical_edi_schema.py` — creates 16 tables, 5 enum types, `updated_at` trigger (applied to 11 tables), `po_status_history` auto-log trigger, views `v_po_summary` + `v_exception_queue`
- `scripts/seed_master_data.py` — seeds 1 seller, 15 partners, 5 materials, 8 SKU mappings, 5 ship-to mappings (idempotent)
- `tests/unit/test_models.py` — 16 unit tests covering save/reload, FKs, unique constraints, soft-delete, enum values
- `docs/erd.png` — ER diagram auto-generated from SQLAlchemy metadata
