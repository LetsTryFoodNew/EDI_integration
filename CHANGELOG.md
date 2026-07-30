# Changelog

## Phase 8 — SAP master-data API document rebuilt (v2.0) (2026-07-18)

`docs/sap-master-data-api.md` rewritten from the live contract rather than patched further. The v1 document had accumulated eight rounds of changes and no longer matched the API: it predated `POST /partners`, `PUT /materials/{id}`, the round-trip tolerance, the error envelope, and the batch-wrapper rules.

Rebuilt against the running instance's OpenAPI schema, covering all 14 master-data endpoints:

- **§5 Rules that apply everywhere** — Content-Type, batch wrappers per endpoint, "a 200 does not mean every row was accepted", snake_case + unknown-field rejection, sync-is-not-a-mirror, GET→POST round-tripping, and the type conventions (decimals as strings, leading zeros as strings, booleans not Y/N).
- **§6 Errors** — the single envelope, indexed field paths (`mappings[1].b1_item_code`) for locating a failing row inside a 2000-row batch, and the full status/code table.
- **§7–10** — one section per table: create/update/read, full field tables with types and requiredness, and the rules that bite (customer sync being update-only, Item Master before SKU Mapping, `frozen_for`/`valid_for` always overwritten, `state` driving the CGST/SGST-vs-IGST split, sync never touching `b1_whs_code`).
- **§11 sequence** and **§12 smoke test**, including two negative tests whose failure would indicate the safety checks are broken.

Verified before publishing: every field in `TradingPartnerCreate`, `TradingPartnerSyncItem`, `MaterialMasterSyncItem`, `SkuMappingSyncItem` and `ShipToMappingSyncItem` cross-checked against the live OpenAPI schema (all present, all required fields documented), and the full create→sync→items→mappings→ship-to→verify sequence executed end to end on a throwaway `DOCDEMO` partner, with both rejection paths confirmed. Test data cleaned up.

Still to fill before sending: the staging/production hosts in §2 and the service-account credentials in §3.

## Phase 8 — source_channel is editable on PUT /partners/{id} (2026-07-18)

`source_channel` was locked as immutable on the grounds that changing it "would re-point every PO, raw message and mapping attached to this partner." **That justification was wrong.** `raw_messages` carries its own `source_channel`, stamped at ingestion — verified against live data (182 DMART, 372 SWIGGY, 4 ZEPTO rows, each storing its own value independently of the partner). Changing a partner's channel rewrites no history; it governs future routing only.

The lock also contradicted the onboarding flow added one step earlier: `POST /partners` deliberately defaults to `MANUAL` so a new partner sits inert, and the documented next step is switching it to a live channel — which the PUT then refused.

- `source_channel` now editable on `PUT /partners/{id}`, validated against the enum (`422` listing allowed values on a bad one).
- `code` and `id` remain immutable, with a corrected reason: `code` is embedded in the webhook URL, is SAP's sync match key, and files every stored document.
- PUT now returns the same `warnings` array as create (missing parser, `EMAIL` without `gmail_label`, `WEBHOOK` without `webhook_secret`, inert `MANUAL`), via a shared `_partner_write_response()` so the two endpoints cannot drift. `TradingPartnerCreateResponse` renamed `TradingPartnerWriteResponse`.
- `webhook_secret` and `asn_sla_hours` added to the updatable set — both were previously unreachable, so a WEBHOOK partner could never be given its secret through the API.

Verified: the reported payload returns 200 with `source_channel: "API"`; switching to `EMAIL` without a label warns; `FTP` returns 422; a `code` change still 409s; and raw_message channel counts are unchanged after two channel switches. Doc §10.0 added.

## Phase 8 — POST /partners: partner onboarding endpoint (2026-07-18)

Partners could only be created by editing `scripts/seed_master_data.py` or inserting a row by hand — there was no API path at all, so `POST /partners/sync` (update-only) reported `skipped` for anything new and there was nowhere to go from there.

- **`POST /api/master-data/partners`** — creates a partner; `code` + `name` required, everything else optional. Returns **201** with the row and a `warnings` array. `409` if the code exists (pointing at the PUT), `422` on an invalid `source_channel` listing the allowed values.
- **Sync stays update-only.** Kept deliberately: SAP's customer list is mostly not EDI trading partners, so a bulk push must never mass-create rows here. Creating one is an explicit, per-partner act — which is the carve-out CLAUDE.md's "never invent partners" rule was really aimed at.
- **`source_channel` defaults to `MANUAL`**, the only inert state: the scheduler polls `API` partners and `EMAIL` partners *with* a `gmail_label`, so a `MANUAL` partner takes master data without generating failed polls. Verified `_get_api_partners()` / `_get_email_partners()` both exclude a freshly created partner.
- **`warnings` closes the gap between "row exists" and "POs flow"** — flags a missing parser for that code, `EMAIL` without a `gmail_label`, `WEBHOOK` without a `webhook_secret`, and the inert `MANUAL` state. Without this, a created partner looks ready when nothing can actually read its documents.

Verified end to end: create `BIGBAZARJIO` → 201 with both warnings; `POST /partners/sync` on the same code → `updated: 1` (was `skipped: 1`); SAP's name, CardCode and email all landed. Doc §6.0 added.

## Phase 8 — Sync endpoints accept round-trip payloads (2026-07-18)

Posting a record straight back from its `GET` response failed on every sync endpoint: the GET shapes are wider than the sync schemas, and `extra="forbid"` rejected the difference. Reported against `/partners/sync`, where a payload copied from `GET /partners` produced five "unknown field" errors for `source_channel`, `gmail_label`, `ack_sla_hours`, `created_at` and `is_active`.

- All four sync item schemas now accept the keys their GET counterpart returns (`id`, `trading_partner_id`, timestamps, plus per-table extras). They are **dropped before any write** via `_SYNC_READ_ONLY` / `_SHIP_TO_READ_ONLY`, so sync still cannot touch integration config or the ops-owned `b1_whs_code` / `mapping_status`. Verified: posting `b1_whs_code: "HACKED"` through `/ship-to/sync` leaves the stored value at `WH02`.
- `is_active` accepted as an alias of `status` (`status` wins when both are sent). `status` became nullable so an omitted value is distinguishable from an explicit `false`.
- Batch-wrapper hint added to the validation handler: posting a bare record to a `/sync` endpoint previously reported only *"field 'mappings' is missing"*, which never said the body needs wrapping. Now returns the exact wrapper for that endpoint. Fires only when the body is genuinely an unwrapped record — a real missing field inside a correct batch still reports `mappings[0].b1_item_code` with the normal hint.

**Bug caught during this change:** widening the schemas meant `item.model_dump()` in the materials and ship-to sync loops would have `setattr`'d the new keys — letting a sync overwrite `id`, `b1_whs_code` and `mapping_status`, the exact thing those endpoints are designed to protect. Excluded explicitly before the write.

## Phase 8 — PUT endpoints accept round-trip payloads (2026-07-18)

`PUT /materials/{id}` rejected `item_code` outright, which broke the ordinary GET → edit → PUT flow: a client sending back the object it just read got *"unknown field — check the spelling"* for a field that is neither unknown nor misspelled. The immutability rule was right; enforcing it by rejecting the field's mere presence was not.

- **Identity and read-only fields are now accepted on all three PUTs and ignored when unchanged**, so round-tripping works. Changing one returns `409 IMMUTABLE_FIELD` with `sent` vs `current` and the reason — never a silent discard, which would look like a successful edit that did nothing.
- Applied consistently, because the three PUTs previously disagreed: materials errored on extra fields while partners and ship-to **silently ignored** them (no `extra="forbid"`). All three now: accept identity fields, reject genuine typos, reject real changes to immutable fields.
- Ship-to additionally guards the sync-owned address/GST block — those come from `POST /ship-to/sync`, so changing `city` here returns `409` pointing at SAP rather than dropping the edit. `b1_whs_code` became optional so a partial update is possible.
- **Found while testing:** the same data has different field names depending on the endpoint — `GET /ship-to` returns `address_line`/`gst_registration_no`/`buyer_warehouse_name`, while the nested array in `GET /partners/{id}` calls them `address`/`gst_regn_no`/`warehouse_name`. I had built the update schema from the nested names, so the round-trip 422'd. Aligned to the `GET /ship-to` names. The underlying inconsistency remains and is worth unifying.
- Error handler renders structured `HTTPException(detail={...})` into `details` instead of `str()`-ing a Python dict into the message.

## Phase 8 — PUT /materials/{id} (2026-07-18)

- **`PUT /api/master-data/materials/{material_id}`** — partial edit of one item; only the fields sent are written. Accepts every field from the sync schema **except `item_code`**, which is deliberately immutable: it is the natural key SAP syncs on and the target of every SKU mapping's `b1ItemCode`, so editing it would orphan those rows. Retire with `valid_for: false` and create a replacement instead. Audit-logged with `mode="json"` so Decimal fields cannot break the JSONB write.
- Errors: unknown id → `404 NOT_FOUND`; `{}` → `422` "No fields to update"; any unknown key (including `item_code`) → `422` naming it. Added `400` and `422` to the error handler's code map so a route-raised 422 reports `VALIDATION_ERROR` rather than the generic `HTTP_ERROR`.
- **SKU mappings still have no PUT** — unchanged from the earlier decision that SAP is their sole author.
- Frontend: removed `updateSkuMapping()`, which was dead code still pointing at the removed `PUT /sku-mappings/{id}` (a 404 waiting to happen); added `updateItem()`.

Master-data write surface is now: `POST .../sync` (bulk upsert, all four tables) plus single-record `POST`/`PUT` on materials and `PUT` on partners and ship-to. Doc §10 rewritten accordingly.

## Phase 8 — Global error handlers + three POST /materials bugs (2026-07-18)

Triggered by a Postman 422 (`"Input should be a valid dictionary or object to extract fields from"`) that never mentioned its own cause. Reproducing it uncovered that the endpoint was broken three ways underneath.

- **`app/api/error_handlers.py`** (new) — one envelope for every failure: `{error: {code, message, details[], hint, request_id}}`.
  - `RequestValidationError` — detects the body-arrived-as-text case and returns **415** naming `Content-Type` explicitly, with the Postman fix in `hint`, instead of a pydantic message that never mentions headers. Field errors render as `mappings[1].b1_item_code` so a failing row is identifiable inside a 2000-row batch, with a plain-English `problem` per pydantic error type and the offending input echoed back (truncated at 200 chars).
  - `IntegrityError` → **409**, distinguishing unique violations from FK violations and pointing at the sync ordering.
  - `StarletteHTTPException` → same envelope; 401 explains the Bearer header and 8-hour expiry.
  - Catch-all `Exception` → **500** with a `request_id`; the traceback is logged server-side and never returned, so internal paths and SQL cannot leak.
- **Unknown fields now rejected** (`extra="forbid"`) on all SAP-facing request models. `itemCode` instead of `item_code` was previously accepted and silently ignored — an integration could appear to succeed while writing nothing.

**Three pre-existing bugs in `POST /api/master-data/materials`, all masked by the Content-Type error:**

1. `create_material` still referenced `body.b1_item_code` after the rename to `item_code` — **the endpoint returned 500 for every request**. Same class as the two `master_data.py` misses fixed earlier; this one was in the request-schema path rather than the ORM path.
2. `MaterialMasterCreate` exposed only 4 of 18 Item_master fields, so `tax_rate`, `mrp`, `ean_code`, `case_size` and the rest were **silently dropped** — a caller sending the documented payload would have created a half-empty item and seen 201. Now mirrors the full field set.
3. Widening it then exposed a latent bug: `audit_log.payload` is JSONB and `model_dump()` emits `Decimal`, which is not JSON-serializable — the insert failed. Both audit payloads now use `model_dump(mode="json")`, so adding Decimal fields to a schema can never break the audit write again.

Verified against the reported payload: wrong Content-Type → actionable 415; correct Content-Type → 201 with all 18 fields persisted; duplicate → 409; missing field → 422 naming it; camelCase typo → 422 listing each unknown key; batch error → indexed path. Doc §5.3–5.5 updated with the real responses.

## Phase 8 — SAP master-data API doc + Bearer token auth (2026-07-18)

- **`docs/sap-master-data-api.md`** — integration guide for the SAP B1 team covering all four master-data tables: auth, endpoint reference, per-field tables mapped to the schema's own names (`customerCode`, `itemCode`, `buyerSKUCode`, `dcCode`…), example payloads, sync-result semantics, error codes, ordering constraints, and a copy-paste smoke test. Every request example in it was executed against the running API and the documented responses are actual output, not illustrative.
- **Bearer token auth added** (`app/api/routes/auth.py`). Auth was cookie-only: `POST /auth/login` set an httpOnly `edi_token` cookie and `get_current_user_email` read `request.cookies` exclusively. Workable for the browser SPA, but it forced any server-to-server caller to scrape `Set-Cookie` and replay it, re-authenticating every 8 hours. Login now also returns `access_token` in the body and the auth dependency accepts `Authorization: Bearer <token>`, header taking precedence. The cookie path is untouched, so the SPA is unaffected — `postLogin` just unwraps `.user` from the new `LoginResponse`.
- Verified with the cookie jar removed entirely: all four master-data GETs plus `/auth/me` return 200 on Bearer alone, no-auth returns 401, and a real `POST /materials/sync` succeeds over Bearer.

**Documented as an open question for SAP, not silently assumed:** whether a multi-state retailer is one Business Partner or one per state GSTIN. `trading_partners.b1_card_code` is a single column and assumes the former; if SAP uses the latter the CardCode has to move onto `ship_to_mapping` and be chosen per PO from the delivery state. Raised in §14 of the doc.

## Phase 5/8 — SKU_Mapping is SAP-owned; auto-mapping removed (2026-07-18)

`b1ItemCode [not null]` in the master-data schema means every SKU_Mapping row is a confirmed mapping by construction. Reshaped the system around that.

- Migration `0009` — dropped `sku_mapping.mapping_status` and `confidence_score`; `material_id` is now `NOT NULL`. Rows with a null material_id (created by the old sync) were deleted, with referencing PO lines detached first so the FK could not cascade into transactional data.
- **Auto-mapping deleted** from `SkuMappingRule`: the cross-partner EAN reuse and the `rapidfuzz` fuzzy-description match (≥0.85) are gone. A 0.86 match between "Salted Almonds 100g" and "Salted Cashews 100g" would have posted a Sales Order for the wrong product. The rule is now exact lookup → `E002_SKU_UNRESOLVED` if absent. CLAUDE.md Phase 5 §4–5 rewritten to match.
- **No local writes**: removed `PUT /api/master-data/sku-mappings/{id}` and `POST|GET /api/sku-mapping` (exceptions). An unresolved SKU is fixed in SAP and re-synced, so a later sync cannot overwrite an ops edit. Read-only listing lives at `GET /api/master-data/sku-mappings`.
- `POST /sku-mappings/sync` now **requires `b1_item_code`** and rejects any row whose item code is absent from Item_master, rather than storing it half-resolved — the schema marks that ref *"no cascade — fail loud on unmapped item"*. Verified: both reject paths fire (`unknown partner code`, `item 'NO_SUCH_ITEM' not in Item_master`).
- SKU views now carry `created_at`, `updated_at` and **`mrp` joined from Item_master** (item data, not customer-specific). Inner join, since `material_id` is non-null.

**Three bugs found by loading real test data — all pre-existing, none caught by lint or typecheck:**

1. `master_data.py:440` — `row.b1_item_code` on a SELECT aliasing `item_code`: **`GET /sku-mappings` returned 500**, SKU list entirely broken.
2. `master_data.py:491` — `material.b1_item_code` ORM attribute: `PUT /sku-mappings/{id}` would have crashed on every save. Only reachable after fixing #1.
3. `dashboard.py` — `EdiPoLineItem.mapping_status` *and* `.description`, neither of which exists: **`GET /api/dashboard/unmapped-skus` had never worked** (the Dashboard's "Unmapped SKUs" card). Now queries `sap_material_no IS NULL` against the real column names and returns 52 unresolved SKUs.

**`npm run typecheck` was a no-op.** `tsconfig.json` uses `"files": []` with project references, so `tsc --noEmit` checked **zero files** — every "typecheck clean" reported during this work was vacuous. Switched the script to `tsc -b --force` (what `npm run build` already used); it immediately caught a stale test fixture. CLAUDE.md's "Definition of Done" cites `npm run typecheck`, which now actually checks.

## Phase 8 — Master Data rebuilt against the Customer/Item_master schema (2026-07-18)

**Data reset.** Catalogue tables cleared and reloaded: `material_master` (186 rows), `sku_mapping` (700), `ship_to_mapping` (5). `trading_partners` (15) and all transactional data were **kept** — `edi_purchase_orders` (549), `edi_po_line_items` (8,001) and `raw_messages` (549) all FK into master data, so a blanket delete would have destroyed them. 7,535 line items had `sku_mapping_id` nulled first so they survive as unmapped and re-resolve against the new catalogue. Pre-wipe dump saved to `_backups/master_data_before_wipe_*.sql`.

**Repo↔DB consistency repair.** The DB was stamped at revision `0005` while the `0005` migration file was missing from the repo (lost in a revert), leaving the ORM on `buyer_warehouse_code` and Postgres on `buyer_whs_code` — the ship-to code paths were broken and `alembic downgrade` could not run. Migration `0005` recreated to match the applied schema exactly.

**Schema now mirrors the master-data diagram.**
- `0005` — `trading_partners` + `business_type`/`group_name`/`phone_numbers[]`/`email_address`; `material_master` + the OITM columns; `sku_mapping` + `unit_price`/`margin`; `ship_to_mapping` renamed `buyer_warehouse_code`→`buyer_whs_code`, + `is_active` and the structured address/GSTIN block (`state` drives CGST/SGST vs IGST, CLAUDE.md §8, and previously had nowhere to live).
- `0006` — `sku_mapping.is_active` (the schema's `status`), kept distinct from `deleted_at` and `mapping_status` since the three mean different things.
- `0007` — `material_master` renamed to mirror `Item_master` 1:1: `b1_item_code`→`item_code`, `description`→`item_name`, `uom`→`invntry_uom`, `sales_uom`→`sal_unit_msr`, `vat_group_purchase`→`vat_group_pu`, `vat_group_sales`→`vat_group_sa`, `gst_rate`→`tax_rate`, `hsn_code`→`hsn`, `ean`→`ean_code`, `is_active`→`valid_for`. All call sites updated (validators, exceptions route, mappers, seed + import scripts, tests). `uom_group` and `case_size` deliberately retained — not in the diagram but they back the UoM conversion and `CaseSizeRule` respectively.
- `0008` — index on `sku_mapping.material_id`, the equivalent of the schema's `SKU_Mapping.b1ItemCode` index (we model that reference as a UUID FK, not a varchar code). Backs the customer drill-down join, which runs on every row expand.
- Deliberately **not** added, per review: `SKU_Mapping.partnerName` (derivable from the customer join) and a second `Item_master.status` alongside `validFor` (collapsed into `valid_for`).
- **FKs deliberately left `NO ACTION`** rather than the `ON DELETE CASCADE` the diagram specifies. Every one of these tables uses `deleted_at` soft-delete (CLAUDE.md §4 "never hard-delete"), so a hard `DELETE` never fires and the cascade would be dead code — while making an accidental hard delete destroy `sku_mapping` rows that `edi_po_line_items` still references.

**Master Data UI rebuilt — two tabs instead of four.**
- **Customers / Partners** — one row per customer showing customerCode/Name/BusinessType/Group/Email/Channel/Status. Expanding a row calls the new `GET /api/master-data/partners/{id}` and shows that customer's **SKU Mappings** and **Ship-to Addresses** arrays in sub-tabs (one round trip, not two screens).
- **Item Master** — full OITM grid using the schema's field names.
- Old 4-tab layout and its `SkuMappingsTab.test.tsx` removed; replaced with `MasterDataPage.test.tsx` covering both tabs and the drill-down.
- Fixed: frontend was calling `/api/master-data/ship-to-mappings` while the route is `/ship-to` — that tab was silently 404ing. Update verbs switched PATCH→PUT.

**SAP-push sync endpoints** (`POST .../sync` for partners/materials/sku-mappings/ship-to) so SAP pushes master data in rather than the middleware querying Service Layer on every read (sessions are licensed and capped, CLAUDE.md §7). Sync and manual PUT write disjoint field sets, so a sync never clobbers an ops mapping decision (`material_id`, `mapping_status`, `b1_whs_code`). Partner sync is update-only — unknown codes are skipped and reported, since onboarding needs a `source_channel` decision SAP cannot express.

Verified: `0005`→`0007` upgrade/downgrade/upgrade clean; seed reloaded; `GET /materials`, `GET /partners`, `GET /partners/{id}` (5 SKU mappings + 2 ship-to addresses) all returning the schema's field names; `ruff` clean on every changed file; `tsc --noEmit` and `oxlint` clean.

Frontend tests pass: **3 files / 12 tests in 3.5s**, including the new `MasterDataPage.test.tsx` (two-tab layout, customer drill-down loading SKU + ship-to arrays, Item_master field names). Note for anyone hitting this later: vitest's *first* run in a fresh checkout has to build its transform cache and can take ~30 minutes, during which short-bounded runs die at vitest's own 60s worker-startup timeout and print "Failed to start forks worker" — that is a cold-cache artifact, not a broken suite. Subsequent runs are seconds.

## Phase 0 — CI/CD Deploy Fixes (2026-07-27)
- `pyproject.toml` — removed `types-bcrypt==4.0.0.20240106` dev dependency; the package doesn't exist on PyPI and was breaking `pip install -e ".[dev]"` in CI. `bcrypt` ships its own inline types, so no stub package is needed.
- `docker-compose.yml` — removed public port mappings on `postgres` (`5433:5432`) and `redis` (`6379:6379`); both are only reached by other containers over the internal Docker network and had no reason to be exposed on the VPS host.
- Untracked all `__pycache__/`/`*.pyc` files (already covered by `.gitignore` but committed before the rule existed).

## Phase 4 — Zepto + Blinkit Live Integration (2026-07-17)
- `GET /api/api-inbox/status` — per-partner connection health: last_fetched_at (watermark), last_message_at, 24h message/failure counts, `is_configured` flag (checks env credentials), webhook URL for BLINKIT
- `POST /api/api-inbox/trigger-fetch?partner_code=ZEPTO` — manually enqueue an immediate Zepto poll without waiting for the 5-minute scheduler cycle; returns RQ job_id; returns 400 for WEBHOOK-only partners
- `ApiPartnerStatus` schema added to `app/schemas/api.py`
- `frontend/src/features/api-inbox/api.ts` — added `fetchApiPartnerStatus()` and `triggerFetch()` calls
- `ApiInboxPage.tsx` — added `ConnectionStatusPanel` at top showing per-partner: online/offline icon (green Wifi vs red WifiOff), push vs poll badge, last fetch time, 24h event count, "Fetch Now" button (disabled if credentials missing or already queued); panel auto-refreshes every 60s via TanStack Query

## Phase 4 — Zepto API Inbox (2026-07-17)
- `app/api/routes/api_inbox.py` — new router at `/api/api-inbox/*` for API/webhook-based partners:
  - `GET /api/api-inbox/partners` — active partners with source_channel in ('API', 'WEBHOOK') plus per-partner message counts
  - `GET /api/api-inbox/messages` — paginated raw messages; search by PO number or external_id; date filters in IST
  - `GET /api/api-inbox/messages/{id}` — detail with full `payload` JSON field (Zepto's raw PO event dict)
  - `POST /api/api-inbox/messages/{id}/retry-parse` — re-queue failed parse jobs
- `app/schemas/api.py` — added `ApiPartnerSummary` and `ApiMessageDetail` schemas
- `app/main.py` — registered `api_inbox_router`
- `frontend/src/features/api-inbox/api.ts` — typed API client for `/api/api-inbox/*` endpoints
- `frontend/src/features/api-inbox/ApiInboxPage.tsx` — two-panel layout (platform list + event list with eventId, PO number, parse status, date filters, pagination); mirrors InboxPage pattern
- `frontend/src/features/api-inbox/ApiInboxDetailPage.tsx` — detail view: metadata card, linked PO card, raw JSON payload viewer (formatted `<pre>`)
- `frontend/src/router.tsx` — added `/api-inbox` and `/api-inbox/:messageId` routes
- `frontend/src/components/layout/Sidebar.tsx` — added "API Inbox" nav item with Zap icon
- Re-implemented Zepto adapter based on `_archive/backend_old/app/services/zepto.py`

## Phase 8 — Vitest Tests (2026-07-17)
- Installed `vitest`, `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`, `jsdom` as dev dependencies
- Added `"test": "vitest run"` and `"typecheck": "tsc --noEmit"` scripts to `frontend/package.json`
- Configured Vitest in `vite.config.ts` (jsdom environment, globals, setup file, thread pool)
- Created `src/test/setup.ts` (jest-dom matchers) and `src/test/utils.tsx` (test wrapper with `QueryClientProvider` + `MemoryRouter`)
- 11 tests across 3 files, all passing:
  - `POListPage.test.tsx` — renders rows, loading state, URL-synced `po_status` filter, empty state
  - `SkuMappingsTab.test.tsx` — renders unmapped SKU, shows inline edit inputs, calls `updateSkuMapping` with correct args
  - `ExceptionsPage.test.tsx` — renders grouped exceptions, opens resolve dialog, calls `resolveException` + re-fetches list, empty state

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
