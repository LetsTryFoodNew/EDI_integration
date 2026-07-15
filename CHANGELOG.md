# Changelog

## Phase 8.1 — Inbox Search/Date Filters + PO Received-At (2026-07-15)
- `GET /api/inbox/messages` — added `search` (matches PO number or email subject via JSONB `headers.subject`), `date_from`, and `date_to` query params; dates compared in IST timezone
- `InboxPage.tsx` — search box (350ms debounce → URL param), date-range pickers, "Clear" button; filter state URL-synced so page is bookmarkable; pagination preserved across filter changes; empty state distinguishes filtered vs unfiltered
- `InboxDetailPage.tsx` + `/inbox/:messageId` route — individual email detail view with attachment download, parse retry, link to canonical PO
- `GET /api/pos` — `received_at` column added (coalesces `RawMessage.received_at` → `EdiPurchaseOrder.created_at`); PO list now sorted by received time; date filters use received_at; `version` field added for PO revision display
- `POListItem` schema + `POListItem` TypeScript type updated with `received_at: datetime`
- PO list "Received" sortable column (replaces created_at); version chip (`v2`) shown on revised POs
- `SUPERSEDED` and `RECEIVED` statuses added to PO list filter dropdown and StatusBadge config
- Fixed `E741` ruff warning in `swiggy_parser.py` (ambiguous variable `l` → `part`)

## Phase 3.5 — PO Revision Flow (2026-07-14)
- Migration `0004` — added `SUPERSEDED` to `po_status_t` enum
- When a partner re-sends a PO with the same PO number (revised qty/SKU/expiry after e.g. a case-size rejection), the new email now creates **version N+1** of the PO and the previous version is marked **SUPERSEDED** (read-only, hidden from exceptions queue, blocked from edit/SAP push)
- Revision matching window: **25 days** (same PO number older than that = unrelated PO reusing the number; version still bumps for the unique constraint but old PO stays active)
- Re-parsing an email that already produced a PO now links to it instead of creating a fake revision
- `version` exposed in PO list + detail APIs; UI shows `v2` chip next to the PO number, "Superseded" badge, and hides all action buttons on superseded versions
- Verified live: duplicate GWAPO36356 email created v2 and superseded v1 with a status-history note naming the new version and source email
- Fixed "Failed to load PDF document" — Cloudinary blocks public delivery of PDFs (401). Added `GET /api/inbox/messages/{id}/attachments/{index}` which streams the file through the backend using a signed private-download URL (auth-protected by our login); inbox attachment "Open" button now fetches via this proxy; PO Raw Source tab's dead `/api/raw-messages` link now points to the source email page

## Phase 5 — Real Master Data + Case Size & SKU Validation (2026-07-14)
- Migration `0003` — added `case_size`, `ean`, `mrp` columns to `material_master`
- Created `scripts/import_master_data.py` — imports real master data from `docs/Mapping.xlsx` (692 platform SKU mappings, 7 chains) and `docs/sku master.xlsx` (181 SKUs with case size); replaces dummy seed data; idempotent upsert
- Created `scripts/build_combined_mapping.py` — generates `docs/master-data-combined.xlsx`, one file joining platform mappings with SKU master (case size, EAN, HSN); rows missing a SAP code highlighted red (85 rows, 55 of them SWIGGY)
- New validator `CaseSizeRule` (`E008_CASE_SIZE_MISMATCH`) — ordered qty must be a whole multiple of the SKU's case size; message names the SKU, nearest valid quantities, and tells ops to request the platform to reissue the PO
- `SkuMappingRule` (`E002_SKU_UNRESOLVED`) now runs against real data — SKUs in a PO but not in master data are highlighted per platform
- Dockerfile now copies `scripts/` into the image
- Fixed duplicate-email path in `parse_and_persist.py` — rollback was restoring the PARSE_FAIL placeholder PO; cleanup now re-runs after rollback
- Re-validated all SWIGGY POs: 52 case-size violations across 28 POs; 138 unmapped-SKU flags across 22 POs
- Fixed `GET /api/master-data/sku-mappings` 500 — `SkuMappingResponse.notes` was typed `dict` but the DB column is `Text`; corrected to `str` (backend schema + frontend type); endpoint now also filters soft-deleted mappings
- SKU Mappings tab: platform dropdown (all 15 partners) + status dropdown + pagination (50/page) — previously only the first 100 rows (AMAZON + part of BIGBASKET) were visible with no way to page
- Fixed `PATCH /api/master-data/sku-mappings/{id}` — was broken 3 ways: schema required `material_id` UUID while frontend sends `b1_item_code`; `MappingStatus.MANUAL` doesn't exist (→ `MANUALLY_MAPPED`); notes merged as dict onto a Text column. Endpoint now resolves material by `b1_item_code` (case-insensitive)
- Fixed `PATCH /api/pos/{id}` 500 — audit log call passed `changes=` but the `AuditLog` column is `payload`; Edit Purchase Order dialog now saves correctly
- Added `POST /api/pos/{id}/revalidate` + "Re-validate" button on PO detail page — re-runs the validation engine synchronously after ops fixes data (open issues recomputed, resolved ones kept, status → VALIDATED or EXCEPTION); blocked once PO is sent to SAP
- Fixed `GET /api/pos` list 500 — same `issue_date` vs `buyer_po_date` attribute mismatch as the detail endpoint; `POListItem.issue_date` type corrected to `date`
- Fixed PO list pagination — `setParam` deleted the `page` param it had just set, so next/prev buttons never changed page; now only resets page on filter changes
- Fixed PO list status filter — "All statuses" sentinel `__all__` leaked into the API query causing 400; now mapped back to empty before the request
- Fixed Cancel PO — it soft-deleted the row (`deleted_at`), making the PO 404 everywhere; now sets `po_status=CANCELLED` + status history entry, so cancelled POs stay visible (read-only). Restored the one PO cancelled under the old behavior (LKPPO14412)
- Fixed profile menu crash — Base UI requires `DropdownMenuLabel` (a group label) inside `DropdownMenuGroup`; Topbar used it bare, crashing the whole app when the user menu opened
- Material Master tab: added pagination (50/page, ‹ › controls, total count) — previously only the first 50 of 181 items were reachable
- PO Validation tab: resolved issues now render muted with a green "Resolved" badge and strikethrough in a separate section; tab badge counts only OPEN issues (resolved ones previously looked identical to live errors)
- Push to SAP now blocked until all ERROR-severity validation issues are resolved — enforced in the endpoint (400 with count) and in the UI (button disabled with error-count badge + tooltip); WARNING-severity issues do not block. Reset GWAPO38795 stuck in SAP_PENDING from a pre-guard push back to EXCEPTION

## Phase 3 — Swiggy PO Parser + Manual Edit + Push to SAP (2026-07-14)
- Created `app/parsers/swiggy_parser.py` — parses Scootsy/Swiggy SpreadsheetML `.xls` attachments; extracts PO number, dates, ship-to address, 18-column line items (IGST/CGST/SGST/CESS), grand total from footer
- Updated `app/parsers/registry.py` — registered `SwiggyParser` for partner code `SWIGGY`
- Updated `app/workflows/parse_and_persist.py` — added `_cleanup_placeholder_pos()` to delete `PARSE_FAIL_` placeholder POs before re-parsing, preventing duplicates on retry
- Added `POST /api/inbox/messages/{id}/retry-parse` — reset a failed message to PENDING and re-enqueue its parse job
- Added `POST /api/inbox/retry-all-failed?partner_code=SWIGGY` — bulk re-queue all failed parse jobs for a partner
- Added `PATCH /api/pos/{id}` — manual edit of PO header fields (buyer_po_number, dates, GSTIN, ship-to, grand_total)
- Added `POST /api/pos/{id}/push-to-sap` — manual SAP push trigger; moves PO to SAP_PENDING and enqueues sap_push job
- Added `POUpdateRequest` Pydantic schema
- Frontend: Retry Parse button in InboxDetailPage; Retry All Failed button in InboxPage header
- Frontend: Edit PO dialog in PODetailPage (React Hook Form, all header fields editable)
- Frontend: Push to SAP button in PODetailPage (enabled for PARSED/VALIDATED/EXCEPTION statuses)
- Re-queued 44 failed SWIGGY parse jobs; all 44 parsed successfully
- Updated `parse_and_persist.py` — duplicate PO emails (same partner + PO number) now link to the existing PO instead of failing with a unique-constraint error
- Fixed `GET /api/pos/{id}` 500 error — endpoint referenced non-existent model attributes (`source_channel`, `issue_date`, `delivery_date`, `seller_gstin` now mapped from RawMessage/`buyer_po_date`/`requested_delivery_date`/SellerEntity); line items and validation issues built explicitly instead of `model_validate` (field name mismatches: `description`/`uom`/`field_name`/`resolution_note`)

## Phase 2 — Gmail Ingestion + Cloudinary Storage (2026-07-14)
- Added `cloudinary==1.41.0` dependency for PDF/Excel attachment storage
- Added Cloudinary settings to `app/config.py` (`CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`)
- Created `app/adapters/storage.py` — uploads attachments to Cloudinary as `raw` resource type; local disk fallback when credentials absent
- Created `app/adapters/email/swiggy_email.py` — `SwiggyEmailAdapter` targeting `SWIGGY_PO` Gmail label; filters to POs only (rejects GRN, invoice, delivery-note emails by subject keyword)
- Removed `app/adapters/email/blinkit_email.py` — Blinkit uses API/websocket adapter, not email
- Updated `app/workflows/ingest_to_canonical.py`: `_save_attachments()` now uploads to Cloudinary; registry uses `SwiggyEmailAdapter`
- Ran seed script — all 15 trading partners now in DB including SWIGGY (label=`SWIGGY_PO`)
- Scheduler auto-picks up SWIGGY from DB (`gmail_label` is set → polled every 2 min)

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

## Phase 8 — Operations Dashboard (2026-07-06)
- `app/models/users.py` — `User` model (email, password_hash, full_name, is_active)
- `app/models/audit_log.py` — `AuditLog` model (user_email, action, entity_type, entity_id, payload JSONB)
- `alembic/versions/0002_users_and_audit_log.py` — creates `users` + `audit_log` tables with indexes + `trg_users_updated_at` trigger
- `app/schemas/api.py` — `PaginatedResponse[T]` + all API request/response Pydantic schemas (auth, POs, dashboard, exceptions, master data, B1 logs)
- `app/api/routes/auth.py` — `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`; JWT in httpOnly cookie (`edi_token`); HS256; 8h expiry; `get_current_user` FastAPI dependency
- `app/api/routes/pos.py` — `GET /api/pos` (paginated + filtered), `GET /api/pos/{id}`, `POST /api/pos/{id}/retry-sap`, `POST /api/pos/{id}/cancel`
- `app/api/routes/dashboard.py` — `GET /api/dashboard/today`, `/sla-breaches`, `/unmapped-skus`, `/activity`
- `app/api/routes/exceptions.py` — `GET /api/exceptions`, `POST /api/exceptions/{id}/resolve`
- `app/api/routes/master_data.py` — CRUD for partners, materials, SKU mappings, ship-to mappings
- `app/api/routes/b1_logs.py` — `GET /api/b1-logs` (filtered), `GET /api/b1-logs/{id}` (full payload)
- `app/api/middleware.py` — `AuditMiddleware`: logs all POST/PATCH/PUT/DELETE to `/api/` with user email from JWT
- `app/main.py` — wired all Phase 8 routers + AuditMiddleware + SPA static files mount
- `frontend/src/types/index.ts` — full TypeScript interfaces mirroring all Pydantic schemas
- `frontend/src/components/shared/` — `StatusBadge`, `MoneyDisplay`, `DateDisplay`, `EmptyState`, `LoadingSkeleton`
- `frontend/src/components/layout/` — `Shell`, `Sidebar`, `Topbar`
- `frontend/src/features/auth/` — `useAuth` hook (TanStack Query), `ProtectedRoute`
- `frontend/src/pages/LoginPage.tsx` — React Hook Form + Zod login form with error handling
- `frontend/src/features/dashboard/DashboardPage.tsx` — 4 metric cards, per-partner stats, SLA breaches, unmapped SKUs, activity feed; auto-refresh 30s
- `frontend/src/features/pos/POListPage.tsx` — TanStack Table v8; URL-synced filters (search, partner, status, page); pagination
- `frontend/src/features/pos/PODetailPage.tsx` — 6 tabs: Overview, Line Items, Validation Issues, B1 Push History, Outbound Messages, Raw Source; Retry SAP + Cancel PO actions
- `frontend/src/features/exceptions/ExceptionsPage.tsx` — grouped by severity; inline resolve dialog with note field
- `frontend/src/features/master-data/MasterDataPage.tsx` — 4 tabs: Partners, Material Master, SKU Mappings (inline edit), Ship-to (inline edit)
- `frontend/src/features/b1-logs/B1LogsPage.tsx` — filterable table with errors-only toggle; JSON detail dialog
- `frontend/src/router.tsx` — all routes under `ProtectedRoute` + `Shell`; `/login` unprotected
- `frontend/src/hooks/use-toast.ts` — sonner-backed `useToast()` hook
- `pyproject.toml` — added `B008`, `TC002`, `TC003` to ruff ignore list (FastAPI idioms)

## Phase 7 — Outbound Documents (Ack, ASN, Invoice, Credit Note) (2026-07-06)
- `app/adapters/outbound/base.py` — `OutboundResult` dataclass; `BaseOutboundAdapter` ABC with `send()` + `channel` property
- `app/adapters/outbound/blinkit_outbound.py` — `BlinkitOutboundAdapter`: PO_ACK_855 via `acknowledge_po()`, ASN_856 via `send_asn()`; channel = WEBHOOK
- `app/adapters/outbound/zepto_outbound.py` — `ZeptoOutboundAdapter`: ASN_856 via `send_asn()`; channel = API
- `app/adapters/outbound/email_outbound.py` — `EmailOutboundAdapter`: sends MIME multipart emails via Gmail API; requires `gmail.send` scope; supports reply threading via `reply_to_message_id`
- `app/adapters/outbound/registry.py` — `get_outbound_adapter()`: partner-code lookup → channel fallback → `UnsupportedOutboundPartnerError`
- `app/workflows/send_outbound.py` — `send_outbound_message(outbound_msg_id)`: idempotency guard (SENT/FAILED skip); SLA breach log for ACK_855; dispatch via registry; 5-attempt retry schedule [60s, 300s, 1800s, 7200s, 21600s]; sets SENT/PENDING(retry)/FAILED
- `app/workflows/b1_to_outbound.py` — `trigger_acks_for_confirmed_pos()`: creates PO_ACK_855 for SAP_CONFIRMED POs with no existing ACK; `poll_b1_deliveries()`: queries B1 DeliveryNotes linked to Sales Orders, creates EdiASN + ASN_856; `poll_b1_invoices()`: queries B1 Invoices linked to Deliveries, creates EdiInvoice + INVOICE_810; `enqueue_due_retries()`: re-enqueues PENDING messages past next_retry_at; partner-specific payload builders for Blinkit/Zepto/Email
- `app/workflows/rtv_flow.py` — `process_rtv(raw_message_id)`: extracts PO number from email text (4-pattern regex), matches EdiPurchaseOrder, builds B1 Return payload, calls Service Layer `POST /Returns`, creates CREDIT_NOTE EdiOutboundMessage for partner notification
- `app/workers/jobs.py` — `send_outbound_job`, `poll_b1_outbound_job`, `retry_pending_outbound_job`, `process_rtv_job` RQ jobs
- `app/workers/scheduler.py` — added: B1 outbound poll every 5 min, retry pending every 2 min; outbound queue `"outbound"` declared
- `tests/unit/test_outbound.py` — 31 tests: send_outbound_message (8), SLA check (3), registry (5), trigger_acks (3), retry enqueue (2), RTV extraction (5), RTV payload building (3)

## Phase 6 — SAP Business One Service Layer Integration (2026-07-06)
- `app/sap_b1/errors.py` — `B1ApiError` + `B1SessionError` (401) + `B1ClosedPeriodError` (-5002/closed period); parses standard B1 error envelope
- `app/sap_b1/session_pool.py` — thread-safe `SessionPool`; max N concurrent sessions; 29-min TTL; `Condition`-based blocking acquire (30s timeout); auto-purge expired sessions
- `app/sap_b1/client.py` — `ServiceLayerClient`: Login/Logout, `create_sales_order/delivery/invoice/return/credit_note`, `get_item`, `get_business_partner`, `query`; 401 auto-retry with fresh session; module-level singleton via `get_b1_client()`
- `app/mappers/po_to_sales_order.py` — `build_sales_order_payload()`: maps EDI850 → B1 Sales Order JSON; header UDFs (U_EDI_SOURCE/UUID/RECEIVED_AT/BUYER_GSTIN/PO_NUMBER); line UoM conversion via sku_mapping.qty_per_buyer_uom; HSNOrSACCode; line UDFs (U_EDI_LINE_NO/BUYER_SKU)
- `app/workflows/canonical_to_b1.py` — `push_po_to_b1(po_id)`: idempotency guard (b1_sales_order_doc_entry already set → skip); status pre-flight (VALIDATED/SAP_REJECTED only); unmapped-SKU check; SAP_PENDING → call B1 → SAP_CONFIRMED or SAP_REJECTED; always writes B1ApiLog
- `app/workers/jobs.py` — `push_po_to_b1_job(po_id)` RQ job on dedicated `sap_push` queue
- `app/workers/scheduler.py` — added SAP push job: queries VALIDATED POs every 60s, enqueues `push_po_to_b1_job` for each
- `scripts/test_b1_connection.py` — standalone 5-step connectivity verifier (Login, company info, Items read, BusinessPartners read, Logout)
- `docs/b1_setup.md` — full B1 setup guide: Service Layer access, API user permissions, Business Partners, Item master, Warehouses, UDF creation steps (7 UDFs across ORDR/RDR1), tax codes, India localisation checklist
- `tests/unit/test_sap_b1.py` — 34 tests: B1ApiError parsing (6), SessionPool (8), ServiceLayerClient via `responses` mock (8), po_to_sales_order mapper (8), push_po_to_b1 workflow (4)

## Phase 5 — Validation & Master-Data Mapping (2026-07-06)
- `app/validators/engine.py` — `ValidationEngine`, `ValidationContext`, `BaseRule`, `RuleViolation`, `EngineResult` (has_errors / has_warnings)
- `app/validators/rules/gstin.py` — `GstinFormatRule`: missing/malformed buyer GSTIN → ERROR
- `app/validators/rules/sku_mapping.py` — `SkuMappingRule`: auto-maps via exact/cross-partner/fuzzy (rapidfuzz ≥ 0.85); unmapped SKU → ERROR; auto-mapped lines updated with sap_material_no
- `app/validators/rules/ship_to_mapping.py` — `ShipToMappingRule`: unmapped ship-to warehouse → WARNING; propagates b1_whs_code to line items
- `app/validators/rules/tax_consistency.py` — `TaxConsistencyRule`: CGST+IGST both non-zero → ERROR; CGST≠SGST → WARNING
- `app/validators/rules/total_reconciliation.py` — `TotalReconciliationRule`: line sum vs grand_total diff > ₹1 → WARNING
- `app/validators/rules/pricing.py` — `PriceVarianceRule`: unit_price vs contracted_price (from SkuMapping.notes JSON) > threshold% → WARNING
- `app/validators/rules/moq.py` — `MoqRule`: ordered_qty < MOQ (from partner api_config or SkuMapping.notes) → WARNING
- `app/workflows/validate_po.py` — `validate_po(po_id)`: runs engine, persists EdiValidationIssue rows, sets PO status VALIDATED (no errors) or EXCEPTION (any ERROR), writes EdiPoStatusHistory; idempotent re-run
- `app/workers/jobs.py` — `validate_po_job(po_id)` RQ job
- `app/workflows/parse_and_persist.py` — enqueues validate_po_job after successful parse
- `app/api/routes/exceptions.py` — `GET /api/exceptions`, `POST /api/exceptions/{id}/resolve`, `POST /api/sku-mapping`, `GET /api/sku-mapping`
- `app/main.py` — registered exceptions_router
- `tests/unit/test_validators.py` — 38 tests: GstinRule (6), TaxRule (6), TotalRule (6), MoqRule (5), PriceRule (5), ShipToRule (3), SkuRule (3), Engine (4)

## Phase 4 — API-Based Partner Adapters (2026-07-06)
- `app/adapters/api/base.py` — `FetchedPO`, `FetchResult` dataclasses; `BaseApiAdapter` ABC with `fetch_new_pos()` + optional `fetch_po_detail()`
- `app/adapters/api/blinkit_api.py` — outbound-only adapter (Blinkit is webhook-push); `acknowledge_po()` + `send_asn()` with 3-attempt retry, `Retry-After` respect, no retry on 4xx; re-implemented from `_archive/backend_old/app/services/blinkit.py`
- `app/adapters/api/zepto_api.py` — `ZeptoApiAdapter(BaseApiAdapter)` polling Silk Route API; `fetch_new_pos()` with pagination + dedup by `eventId`; `_since_to_days()` watermark → days param (cap 45); `send_asn()`; re-implemented from `_archive/backend_old/app/services/zepto.py`
- `app/api/routes/webhooks.py` — `POST /api/webhooks/{partner_code}` generic dispatcher; Blinkit auth via `api-key` header vs `webhook_secret`; idempotent `_save_raw_message()`; parse enqueue via FastAPI `BackgroundTasks`
- `app/api/deps.py` — `get_sync_db()` FastAPI dependency for sync DB sessions
- `app/workflows/fetch_api_pos.py` — `fetch_and_store_api_pos(partner_code)`: loads partner, reads watermark, calls adapter, saves `RawMessage` rows, enqueues parse jobs, advances watermark on clean run
- `app/workers/jobs.py` — `fetch_api_partner_job(partner_code)` RQ job calling `fetch_and_store_api_pos`
- `app/workers/scheduler.py` — added API polling every 5 min for `source_channel=API` partners alongside existing email ingest
- `app/main.py` — registered `webhooks_router`
- `tests/fixtures/zepto_po_events_response.json` — Zepto paginated events response fixture (2 POs)
- `tests/unit/test_api_adapters.py` — 23 tests: ZeptoApiAdapter (11), BlinkitApiAdapter (7), webhook route (5)

## Phase 3 — Parser Layer (2026-07-06)
- `app/parsers/base.py` — `ParseResult` dataclass; `BaseParser` ABC with `can_parse()` + `parse()`
- `app/parsers/registry.py` — lazy partner-code → parser-class registry; `get_parser()`, `registered_codes()`
- `app/parsers/blinkit_parser.py` — JSON webhook parser; CGST/SGST + IGST branches; header total fallback; re-implemented from `_archive/backend_old/app/routes.py`
- `app/parsers/zepto_parser.py` — JSON API parser (Silk Route v12); nested `productIdentifier` path; re-implemented from `_archive/backend_old/app/services/zepto.py`
- `app/parsers/llm_fallback.py` — Anthropic `claude-sonnet-4-5` fallback; lazy import; only active when `api_config.llm_fallback_enabled=true`
- `app/workflows/parse_and_persist.py` — `parse_and_persist(raw_message_id)`: parser dispatch + LLM fallback + DB write (success → `edi_purchase_orders`+lines; failure → placeholder PO + `E000_PARSE_FAILED` validation issue)
- `app/workers/jobs.py` — `parse_raw_message_job` stub replaced with real implementation calling `parse_and_persist`
- `app/workflows/ingest_to_canonical.py` — `_enqueue_parse_stub` replaced with `_enqueue_parse_job` (RQ enqueue); migrated `session.query()` to SQLAlchemy 2.x `session.execute(select(...))`
- `tests/fixtures/blinkit_po_webhook.json` — 2-line PO with CGST/SGST
- `tests/fixtures/blinkit_po_webhook_igst.json` — 1-line interstate PO with IGST
- `tests/fixtures/zepto_po_event.json` — 2-line Zepto Silk Route API PO
- `tests/unit/test_parsers.py` — 29 tests: BlinkitParser (13), ZeptoParser (12), ParseResult contract (4)

## Phase 2 — Email Ingestion (2026-07-03)
- `app/adapters/email/base.py` — `AttachmentMeta`, `InboundEmail` dataclasses; `BaseEmailAdapter` ABC
- `app/adapters/email/gmail_client.py` — full Gmail API v1 client: OAuth2 token management, label resolution, message listing, recursive MIME part traversal, base64url decoding, attachment download
- `app/adapters/email/blinkit_email.py` — `BlinkitEmailAdapter` (label: `BLINKIT_PO`); accepts @blinkit.com / @grofers.com domains, PO subject keywords, or PDF attachments
- `app/workflows/ingest_to_canonical.py` — `ingest_label()` workflow: Gmail → raw_messages + disk attachments; dual idempotency (pre-check + DB unique constraint); adapter-level `is_po_email` filter; stub parse enqueue for Phase 3
- `app/workers/jobs.py` — `ingest_label_job(partner_code, label_name)` RQ job; `parse_raw_message_job` stub
- `app/workers/scheduler.py` — APScheduler with `BlockingScheduler`; auto-discovers email partners from DB at startup; enqueues ingest jobs via RQ every 2 minutes
- `scripts/auth_gmail.py` — one-time OAuth2 authorization CLI; writes `token.json` to `GMAIL_TOKEN_PATH`
- `tests/unit/conftest.py` — mocks asyncpg/psycopg2 for unit tests (drivers unavailable without Docker)
- `tests/fixtures/gmail_message_po.json` — fixture: multipart/mixed email with PDF attachment
- `tests/fixtures/gmail_message_nonpo.json` — fixture: newsletter email (no attachments)
- `tests/unit/test_gmail_ingestion.py` — 19 tests: MIME parsing (7), BlinkitAdapter filter (7), workflow save/duplicate/filter/error/disk (5)
