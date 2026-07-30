# Master Data API — SAP B1 Integration Guide

**Audience:** SAP Business One integration team
**Purpose:** push Customer, Item Master, SKU Mapping and Ship-to master data from SAP B1 into the EDI middleware, and read back what landed.
**Version:** 2.0 — 2026-07-18

Every request and response example in this document was executed against a running instance. The outputs shown are actual responses, not illustrations.

---

## Contents

1. [How the integration works](#1-how-the-integration-works)
2. [Base URL](#2-base-url)
3. [Authentication](#3-authentication)
4. [Endpoint summary](#4-endpoint-summary)
5. [Rules that apply everywhere](#5-rules-that-apply-everywhere)
6. [Errors](#6-errors)
7. [Customer](#7-customer)
8. [Item Master](#8-item-master)
9. [SKU Mapping](#9-sku-mapping)
10. [Ship-to](#10-ship-to)
11. [Integration sequence](#11-integration-sequence)
12. [Smoke test](#12-smoke-test)
13. [Open question for SAP](#13-open-question-for-sap)
14. [Support](#14-support)

---

## 1. How the integration works

The middleware does **not** call SAP's Service Layer to read master data. Service Layer sessions are licensed and capped, and a lookup per purchase order would exhaust them. SAP pushes master data in whenever it changes; the middleware stores it locally and serves every read from that copy.

```
  SAP B1  ──POST /sync──►  EDI middleware  ──GET──►  ops dashboard
                                  │
                                  └──►  PO validation → B1 Sales Order
```

Push frequency is your choice — a nightly full push or on-change deltas both work. Endpoints are **idempotent**: re-sending the same rows updates them in place and never duplicates.

### The four tables

| Table | What it holds | Natural key |
|---|---|---|
| **Customer** | Retail partners you trade with | `code` |
| **Item Master** | Your products (mirrors B1 `OITM`) | `item_code` |
| **SKU Mapping** | Retailer's SKU code → your item code | (`partner_code`, `buyer_sku`) |
| **Ship-to** | Retailer delivery locations | (`partner_code`, `buyer_whs_code`) |

---

## 2. Base URL

| Environment | Base URL |
|---|---|
| Local / dev | `http://localhost:8000` |
| Staging | `https://<staging-host>` |
| Production | `https://<prod-host>` |

> ⚠️ Replace the staging and production hosts before circulating this document. All paths below are relative to the base URL.

---

## 3. Authentication

All endpoints require a JWT bearer token.

### 3.1 Obtain a token

```http
POST /auth/login
Content-Type: application/json

{ "email": "<service-account-email>", "password": "<password>" }
```

**200 OK**
```json
{
  "user": { "id": "5aaba039-…", "email": "svc@example.com",
            "full_name": "SAP Service", "is_active": true },
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
  "token_type": "bearer",
  "expires_in": 28800
}
```

### 3.2 Use it

```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…
```

**Tokens expire after 8 hours** (`expires_in` = 28800 seconds). Re-authenticate on `401`.

> The same response also sets an httpOnly `edi_token` cookie for the browser dashboard. Server-to-server callers should ignore it and use the Bearer header.

### 3.3 Verify

```bash
BASE="https://<host>"
TOKEN=$(curl -s -X POST "$BASE/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"…","password":"…"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s "$BASE/auth/me" -H "Authorization: Bearer $TOKEN"
```

---

## 4. Endpoint summary

**Endpoints SAP uses:**

| Table | Push | Read |
|---|---|---|
| Customer | `POST /api/master-data/partners` (create, once)<br>`POST /api/master-data/partners/sync` (update, ongoing) | `GET /api/master-data/partners`<br>`GET /api/master-data/partners/{id}` |
| Item Master | `POST /api/master-data/materials/sync` | `GET /api/master-data/materials` |
| SKU Mapping | `POST /api/master-data/sku-mappings/sync` | `GET /api/master-data/sku-mappings` |
| Ship-to | `POST /api/master-data/ship-to/sync` | `GET /api/master-data/ship-to` |

**Exist, but for our operations team — not part of your integration:**

| Endpoint | Purpose |
|---|---|
| `PUT /api/master-data/partners/{id}` | Edit one customer |
| `POST /api/master-data/materials` | Add one item manually |
| `PUT /api/master-data/materials/{id}` | Edit one item |
| `PUT /api/master-data/ship-to/{id}` | Assign a DC to a B1 warehouse |

There is deliberately **no** `PUT` for SKU mappings — see [§9.3](#93-why-there-is-no-put).

---

## 5. Rules that apply everywhere

### 5.1 `Content-Type: application/json` is required

Without it the body arrives as plain text and you get `415`. **In Postman this is the most common setup mistake** — the Body tab's `raw` mode defaults to **Text**; change the dropdown to **JSON**.

### 5.2 Sync endpoints take a batch, not a single record

```json
{ "mappings": [ { …row… }, { …row… } ] }
```

| Endpoint | Wrapper |
|---|---|
| `/partners/sync` | `partners` |
| `/materials/sync` | `items` |
| `/sku-mappings/sync` | `mappings` |
| `/ship-to/sync` | `mappings` |

Maximum **2000 rows** per request. Split larger loads into pages — the endpoints are idempotent, so page boundaries are safe.

### 5.3 A `200` does not mean every row was accepted

Every sync returns:

```json
{ "created": 13, "updated": 5, "skipped": 1,
  "errors": ["DOCDEMO/X: item 'NOPE' not in Item_master"] }
```

| Field | Meaning |
|---|---|
| `created` | New rows inserted |
| `updated` | Existing rows updated in place |
| `skipped` | Rows **rejected and not stored** |
| `errors` | One message per skipped row |

**Always check `skipped` and `errors`.** Rejected rows are not stored at all — correct and re-send them.

### 5.4 Field names are `snake_case`, and unknown fields are rejected

`item_code`, not `itemCode`. An unrecognised field returns `422` naming it, rather than being silently ignored — silent ignoring would let an integration appear to succeed while writing nothing.

### 5.5 Sync is not a mirror

Rows absent from your payload are **left untouched, not deleted**. To deactivate a record, send it with `status: false` (or `valid_for: false` for items).

### 5.6 You can post back what you read

Every sync endpoint accepts the extra fields its `GET` returns (`id`, timestamps, and so on). They are ignored, never written. So `GET` a record, change a value, and post it straight back — no need to strip fields first.

### 5.7 Conventions

- **Timestamps** — UTC, ISO-8601 (`2026-07-29T16:21:24.171933Z`)
- **Decimals** — returned as JSON **strings** (`"34.000000"`) to avoid float rounding; may be sent as numbers or strings
- **Leading zeros** — `ean_code`, `zip_code`, `phone_numbers` must be sent as **strings**
- **Booleans** — JSON `true`/`false`, never `"Y"`/`"N"`. Convert B1's flags before sending
- **Pagination** — `GET` lists accept `limit` (default 100, max 500) and `offset`, and return `{items, total, limit, offset}`

---

## 6. Errors

Every failure returns the same envelope:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "1 field failed validation.",
    "details": [
      { "field": "mappings[1].b1_item_code", "in": "body",
        "problem": "required field is missing",
        "received": { "partner_code": "BLINKIT", "buyer_sku": "B" } }
    ],
    "hint": "Compare the payload against docs/sap-master-data-api.md.",
    "request_id": "99a8c2abaad4"
  }
}
```

`field` includes the row index (`mappings[1].b1_item_code`), so in a 2000-row batch you can identify the exact failing row.

**Quote `request_id` when reporting a problem** — every request is logged against it server-side.

| HTTP | `error.code` | Meaning |
|---|---|---|
| `200` | — | Processed. For sync, still check `skipped` / `errors` |
| `201` | — | Created |
| `401` | `UNAUTHENTICATED` | Token missing or expired |
| `404` | `NOT_FOUND` | Record does not exist |
| `409` | `CONFLICT` / `DUPLICATE` | Record already exists |
| `409` | `IMMUTABLE_FIELD` | Attempt to change a field that cannot change |
| `409` | `REFERENCE_NOT_FOUND` | Points at something absent |
| `415` | `UNSUPPORTED_MEDIA_TYPE` | Body not sent as JSON |
| `422` | `VALIDATION_ERROR` | Field missing, wrong type, or unknown |
| `500` | `INTERNAL_ERROR` | Our bug — send us the `request_id` |

---

## 7. Customer

Retail partners. Stored in `trading_partners`.

### 7.1 Create — `POST /api/master-data/partners`

A customer must exist before sync can update it. This creates it. **Once per customer.**

```json
{
  "code": "DOCDEMO",
  "name": "Doc Demo Retail Pvt Ltd",
  "b1_card_code": "C09999",
  "gstin": "27AAECG1234K1Z5",
  "business_type": "E-Commerce",
  "group_name": "Marketplace",
  "phone_numbers": ["+912240001200"],
  "email_address": "vendors@docdemo.in"
}
```
→ **201**

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `code` | string(50) | **yes** | | Natural key. Uppercased |
| `name` | string(255) | **yes** | | |
| `source_channel` | string | no | `MANUAL` | `EMAIL` / `API` / `WEBHOOK` / `PORTAL` / `MANUAL` |
| `b1_card_code` | string(50) | no | | SAP `CardCode` |
| `gstin` | string(15) | no | | |
| `business_type` | string(100) | no | | |
| `group_name` | string(100) | no | | |
| `phone_numbers` | string[] | no | | Strings — leading zeros / `+91` |
| `email_address` | string(255) | no | | |
| `gmail_label` | string(200) | no | | Only for `EMAIL` channel |
| `webhook_secret` | string(500) | no | | Only for `WEBHOOK` channel |
| `ack_sla_hours` | integer | no | `24` | |
| `asn_sla_hours` | integer | no | `48` | |
| `is_active` | boolean | no | `true` | |

The response includes a **`warnings`** array:

```json
"warnings": [
  "No parser is registered for 'DOCDEMO', so incoming documents cannot be read yet…",
  "source_channel is MANUAL, so nothing is polled or received automatically…"
]
```

> **Creating the customer does not make purchase orders flow.** Master-data sync works immediately, but PO ingestion also needs an adapter (how to fetch from that retailer) and a parser (how to read their PO format), both of which live in our code. The warnings tell you what is still missing. Contact us when a new retailer needs live PO ingestion.

> **`source_channel` defaults to `MANUAL` deliberately.** Our scheduler polls only `API` customers and `EMAIL` customers that have a `gmail_label`, so a `MANUAL` customer accepts master data and stays inert — no failed polls — until its integration is built.

| Situation | Response |
|---|---|
| `code` already exists | `409 CONFLICT` |
| Invalid `source_channel` | `422` listing allowed values |

### 7.2 Update — `POST /api/master-data/partners/sync`

```json
{ "partners": [
  { "code": "DOCDEMO",
    "name": "Doc Demo Retail Private Limited",
    "b1_card_code": "C09999",
    "gstin": "27AAECG1234K1Z5",
    "business_type": "E-Commerce",
    "group_name": "Marketplace",
    "phone_numbers": ["+912240001200", "+919812345601"],
    "email_address": "vendors@docdemo.in",
    "status": true }
]}
```
→ `{"created": 0, "updated": 1, "skipped": 0, "errors": []}`

Required: `code`, `name`. `status` may also be sent as `is_active`.

> ### ⚠️ Customer sync is **update-only**
>
> A `code` that does not already exist is **skipped**, not created:
>
> ```json
> {"created":0,"updated":0,"skipped":1,
>  "errors":["NEVERSEEN: no existing partner — onboard manually first"]}
> ```
>
> This is intentional. A customer row here also carries integration configuration — which channel its POs arrive on, which mailbox label, which credentials — none of which exists in a SAP Business Partner record. A row created from SAP data alone would receive nothing.
>
> **If you push your full customer list, expect a large `skipped` count.** Only retailers we have EDI integrations with will match; the rest are reported and ignored. That is normal and harmless.
>
> Sync never modifies integration configuration. It writes only the fields in the table above.

### 7.3 Read

`GET /api/master-data/partners` — optional `is_active`, `limit`, `offset`.

`GET /api/master-data/partners/{id}` — one customer **plus its full SKU-mapping and ship-to arrays** in a single call. The quickest way to confirm a push landed:

```json
{ "code": "DOCDEMO", "name": "…", "b1_card_code": "C09999",
  "sku_mappings":     [ { "buyer_sku": "DOC-SKU-1", "b1_item_code": "DOCITEM1", … } ],
  "ship_to_mappings": [ { "dc_code": "DOC-DC-1", "city": "Mumbai", … } ] }
```

---

## 8. Item Master

Your products. Stored in `material_master`, a 1:1 mirror of B1 `OITM`.

### 8.1 Push — `POST /api/master-data/materials/sync`

```json
{ "items": [
  { "item_code": "DOCITEM1",
    "item_name": "Doc Demo Makhana 30g",
    "frgn_name": "Doc Demo Foxnut 30g",
    "hsn": "20089900",
    "tax_rate": 12,
    "itms_grp_cod": 103,
    "items_group_name": "Makhana",
    "invntry_uom": "PCS",
    "sal_unit_msr": "CASE",
    "vat_group_pu": "GST12",
    "vat_group_sa": "GST12",
    "case_size": 24,
    "lot_size": 24,
    "grammage": "30g",
    "ean_code": "8901234599001",
    "mrp": 50.00,
    "frozen_for": false,
    "valid_for": true }
]}
```
→ `{"created": 1, "updated": 0, "skipped": 0, "errors": []}`

| Field | Type | Required | OITM source |
|---|---|---|---|
| `item_code` | string(50) | **yes** | `ItemCode` — natural key |
| `item_name` | string(500) | **yes** | `ItemName` |
| `frgn_name` | string(500) | no | `FrgnName` |
| `hsn` | string(10) | no | HSN / SAC |
| `tax_rate` | decimal(5,2) | no | e.g. `18.00` |
| `itms_grp_cod` | integer | no | `ItmsGrpCod` |
| `items_group_name` | string(100) | no | Readable group name |
| `invntry_uom` | string(20) | no (default `"PCS"`) | Inventory UoM |
| `sal_unit_msr` | string(20) | no | `SalUnitMsr` |
| `vat_group_pu` | string(20) | no | `VatGroupPu` |
| `vat_group_sa` | string(20) | no | `VatGroupSa` |
| `case_size` | integer | no | Units per case — see below |
| `lot_size` | integer | no | |
| `grammage` | string(50) | no | e.g. `"30g"` |
| `ean_code` | string(14) | no | Barcode — send as **string** |
| `mrp` | decimal | no | |
| `frozen_for` | boolean | no (default `false`) | `Frozen` Y/N → `true`/`false` |
| `valid_for` | boolean | no (default `true`) | `Valid` Y/N → `true`/`false` |

**Rules**

- Creates **and** updates — a new `item_code` is inserted, an existing one updated.
- **`frozen_for` and `valid_for` are always overwritten** from your payload. They are SAP's authoritative item-status flags: if SAP unfreezes an item we must stop blocking it immediately, and vice versa. **Send them on every push.**
- Convert B1's `Y`/`N` to JSON `true`/`false`.
- `case_size` is not an OITM field but we use it — an ordered quantity that is not a whole multiple of the case size is flagged as a PO exception. Send it if you have it.
- An item soft-deleted on our side is skipped with a message; tell us and we will restore it.

### 8.2 Read — `GET /api/master-data/materials`

Optional: `search` (matches item code, name or EAN), `valid_for`, `limit`, `offset`.

---

## 9. SKU Mapping

Maps a retailer's own SKU code to your item code. Stored in `sku_mapping`.

### 9.1 Push — `POST /api/master-data/sku-mappings/sync`

```json
{ "mappings": [
  { "partner_code": "DOCDEMO",
    "buyer_sku": "DOC-SKU-1",
    "b1_item_code": "DOCITEM1",
    "item_name": "Doc Demo Makhana 30g",
    "unit_price": 32.50,
    "margin": 35.0,
    "qty_per_buyer_uom": 1,
    "status": true }
]}
```
→ `{"created": 1, "updated": 0, "skipped": 0, "errors": []}`

| Field | Type | Required | Notes |
|---|---|---|---|
| `partner_code` | string(50) | **yes** | Customer **code**, not a numeric id |
| `buyer_sku` | string(100) | **yes** | The retailer's SKU code |
| `b1_item_code` | string(50) | **yes** | Must exist in Item Master |
| `item_name` | string | no | The retailer's description |
| `unit_price` | decimal(18,6) | no | Negotiated price for this customer |
| `margin` | decimal(9,4) | no | Negotiated margin % |
| `qty_per_buyer_uom` | decimal | no (default `1`) | Buyer UoM → inventory UoM conversion |
| `status` | boolean | no (default `true`) | May also be sent as `is_active` |

Natural key: **(`partner_code`, `buyer_sku`)**.

### 9.2 Two rejection rules

Both return the row in `errors` and store nothing:

```json
{"created":0,"updated":0,"skipped":1,
 "errors":["NOPE/X: unknown partner code"]}

{"created":0,"updated":0,"skipped":1,
 "errors":["DOCDEMO/X: item 'NOPE' not in Item_master"]}
```

> **Push Item Master before SKU Mapping.** A mapping pointing at an item we do not have is rejected outright rather than stored half-resolved — otherwise it would fail much later, during Sales Order creation, with a far less obvious error.

### 9.3 Why there is no PUT

SAP is the **sole author** of these mappings. We deliberately provide no way for our operations team to create or edit one locally, so a later sync can never overwrite a local edit and the two systems cannot disagree.

Our middleware also does **not** guess mappings. It previously attempted fuzzy description matching; that was removed because a near-match between, say, *"Salted Almonds 100g"* and *"Salted Cashews 100g"* would create a Sales Order for the wrong product and ship the wrong goods.

> **Operational consequence:** if a retailer sends a PO for a SKU you have not mapped, that PO stops with `E002_SKU_UNRESOLVED` and cannot reach SAP until the mapping arrives. When we report an unresolved SKU, the fix is: **add the mapping in SAP → re-run this sync → we retry the PO.**

### 9.4 Read — `GET /api/master-data/sku-mappings`

Optional: `partner_code`, `is_active`, `search`, `limit`, `offset`. Each row also returns `mrp` joined from Item Master, plus `created_at` / `updated_at`:

```json
{ "id": "0e64f007-…", "partner_code": "BLINKIT",
  "buyer_sku": "8901234560001", "item_name": "Peri Peri Makhana 30g",
  "b1_item_code": "LTFM001", "unit_price": "32.500000",
  "margin": "35.0000", "mrp": "50.00", "qty_per_buyer_uom": "1.0000",
  "is_active": true, "created_at": "…", "updated_at": "…" }
```

---

## 10. Ship-to

Retailer delivery locations. Stored in `ship_to_mapping`.

### 10.1 Push — `POST /api/master-data/ship-to/sync`

```json
{ "mappings": [
  { "partner_code": "DOCDEMO",
    "buyer_whs_code": "DOC-DC-1",
    "buyer_warehouse_name": "Doc Demo Mumbai DC",
    "address_line": "Plot 1, MIDC, Mumbai 400093",
    "address_type": ["SHIP_TO", "BILL_TO"],
    "street": "Plot 1, MIDC Industrial Area",
    "block": "Andheri East",
    "city": "Mumbai",
    "zip_code": "400093",
    "state": "Maharashtra",
    "country": "India",
    "gst_registration_no": "27AAECG1234K1Z5",
    "gst_type": ["Regular"] }
]}
```
→ `{"created": 1, "updated": 0, "skipped": 0, "errors": []}`

| Field | Type | Required | Notes |
|---|---|---|---|
| `partner_code` | string(50) | **yes** | Customer **code** |
| `buyer_whs_code` | string(100) | **yes** | The retailer's DC code |
| `buyer_warehouse_name` | string(500) | no | |
| `address_line` | string(500) | no | Full address as one line |
| `address_type` | string[] | no | e.g. `["SHIP_TO","BILL_TO"]` |
| `street` | string(255) | no | |
| `block` | string(100) | no | |
| `city` | string(100) | no | |
| `zip_code` | string(10) | no | **String** — leading zeros matter |
| `state` | string(100) | no | **See the GST note below** |
| `country` | string(50) | no | |
| `gst_registration_no` | string(15) | no | GSTIN **of this delivery location** |
| `gst_type` | string[] | no | e.g. `["Regular"]` |

Natural key: **(`partner_code`, `buyer_whs_code`)**.

> ### ⚠️ `state` drives the GST split — always send it
>
> Indian GST requires **CGST + SGST** for an intra-state movement and **IGST** for inter-state. We determine which by comparing our seller state against the **ship-to state**. If `state` is missing we cannot compute the split correctly.
>
> Likewise `gst_registration_no` must be the GSTIN **of that specific delivery location** — for a multi-state retailer it differs per DC.

**Rules**

- Creates and updates.
- Sync writes address and GST fields **only**. It never touches `b1_whs_code`, the B1 warehouse assignment, which is an operations decision on our side — so re-syncing an address can never undo it.
- New locations arrive with no B1 warehouse assigned and are queued for our team.
- Unknown `partner_code` → row skipped and reported.

### 10.2 Read — `GET /api/master-data/ship-to`

Optional: `partner_code`, `limit`, `offset`.

---

## 11. Integration sequence

Order matters, because of the references between tables:

```
1.  POST /auth/login                          → capture access_token

2.  POST /api/master-data/partners            → create each new customer (once)
3.  POST /api/master-data/partners/sync       → update customers (ongoing)

4.  POST /api/master-data/materials/sync      → items    ── must precede step 5
5.  POST /api/master-data/sku-mappings/sync   → mappings ── needs items from step 4
6.  POST /api/master-data/ship-to/sync        → addresses

7.  GET  /api/master-data/partners/{id}       → verify one customer end-to-end
```

**Check `skipped` and `errors` after every step before continuing.**

For a recurring nightly push, steps 3–6 are all you need; step 2 only when onboarding a new retailer.

---

## 12. Smoke test

```bash
BASE="https://<host>"

TOKEN=$(curl -s -X POST "$BASE/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"<email>","password":"<password>"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

AUTH=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

# 1. Create a customer
curl -s -X POST "$BASE/api/master-data/partners" "${AUTH[@]}" \
  -d '{"code":"DOCDEMO","name":"Doc Demo Retail Pvt Ltd","b1_card_code":"C09999"}'
# expect: 201, with a "warnings" array

# 2. Update it by code
curl -s -X POST "$BASE/api/master-data/partners/sync" "${AUTH[@]}" \
  -d '{"partners":[{"code":"DOCDEMO","name":"Doc Demo Retail Private Limited"}]}'
# expect: {"created":0,"updated":1,"skipped":0,"errors":[]}

# 3. Item first
curl -s -X POST "$BASE/api/master-data/materials/sync" "${AUTH[@]}" \
  -d '{"items":[{"item_code":"DOCITEM1","item_name":"Doc Demo Makhana 30g",
       "hsn":"20089900","tax_rate":12,"invntry_uom":"PCS","mrp":50.00,"valid_for":true}]}'
# expect: {"created":1,…}

# 4. Then a mapping that references it
curl -s -X POST "$BASE/api/master-data/sku-mappings/sync" "${AUTH[@]}" \
  -d '{"mappings":[{"partner_code":"DOCDEMO","buyer_sku":"DOC-SKU-1",
       "b1_item_code":"DOCITEM1","unit_price":32.50,"margin":35.0}]}'
# expect: {"created":1,…}

# 5. Ship-to
curl -s -X POST "$BASE/api/master-data/ship-to/sync" "${AUTH[@]}" \
  -d '{"mappings":[{"partner_code":"DOCDEMO","buyer_whs_code":"DOC-DC-1",
       "city":"Mumbai","state":"Maharashtra","zip_code":"400093",
       "gst_registration_no":"27AAECG1234K1Z5"}]}'
# expect: {"created":1,…}

# 6. NEGATIVE — unknown item must be rejected
curl -s -X POST "$BASE/api/master-data/sku-mappings/sync" "${AUTH[@]}" \
  -d '{"mappings":[{"partner_code":"DOCDEMO","buyer_sku":"DOC-SKU-2",
       "b1_item_code":"DOES_NOT_EXIST"}]}'
# expect: {"created":0,"updated":0,"skipped":1,
#          "errors":["DOCDEMO/DOC-SKU-2: item 'DOES_NOT_EXIST' not in Item_master"]}

# 7. NEGATIVE — unknown customer must be rejected
curl -s -X POST "$BASE/api/master-data/partners/sync" "${AUTH[@]}" \
  -d '{"partners":[{"code":"NEVERSEEN","name":"x"}]}'
# expect: {"created":0,"updated":0,"skipped":1,
#          "errors":["NEVERSEEN: no existing partner — onboard manually first"]}

# 8. Verify the whole customer
curl -s "$BASE/api/master-data/partners" "${AUTH[@]}" | grep DOCDEMO
```

**If steps 6 or 7 return `created: 1` instead of a rejection, stop and contact us** — the safety checks are not working.

---

## 13. Open question for SAP

**Does a retailer operating in several states have one Business Partner, or one per state GSTIN?**

Our customer table currently holds a single `b1_card_code` per retailer, which assumes **one CardCode per retailer**. If SAP instead maintains a separate Business Partner per state — for example Blinkit Maharashtra, Blinkit Karnataka and Blinkit Delhi, each with its own GSTIN — then one field cannot represent that, and the CardCode needs to move onto the ship-to location and be selected per PO from the delivery state.

This is a live issue: retailers already in our system span four states with a distinct GSTIN each.

**Please confirm which model SAP uses before the initial CardCode load** — the answer changes where the value belongs, and correcting it later means re-mapping every stored document.

---

## 14. Support

Report issues with:

- the `request_id` from the error envelope,
- the full request (headers and body),
- the response received.

Every request is logged server-side against its `request_id` and can be traced.
