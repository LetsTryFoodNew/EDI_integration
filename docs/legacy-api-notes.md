# Legacy API Notes — Blinkit & Zepto

> Derived from `_archive/backend_old/` (formerly `backend/`). Read-only reference.
> Source files: `app/services/blinkit.py`, `app/services/zepto.py`, `app/routes.py`, `app/models.py`.

---

## Blinkit (Partner API — partnersbiz.com)

### Base URLs
| Environment | URL |
|---|---|
| Testing / Pre-prod | `https://dev.partnersbiz.com` |
| Production | `https://api.partnersbiz.com` |

### Auth
- **Auth type:** API key in header `api-key: <key>` + header `x-vendor-id: 18309`
- **No Bearer token** — plain API key.
- **IP whitelisting required.** Only whitelisted server IPs can reach `partnersbiz.com`. Local dev uses a Render.com proxy (static IP) as a passthrough.
- Vendor ID for Let's Try Foods: `18309` (env: `BLINKIT_VENDOR_ID`)

### PO Flow (INBOUND — Blinkit pushes to us)
Blinkit POSTs PO creation/update events to our webhook. **There is no "List POs" pull API.**

- **Our inbound webhook:** `POST /api/webhook/inbound/blinkit/po`
- **Production webhook URL:** `https://po-integration-backend.onrender.com/api/webhook/inbound/blinkit/po`
- Blinkit sends a `type` field (`PO_CREATION`, `PO_CANCEL`, etc.) and the full PO in `body.details`.

#### Inbound PO payload structure (from `routes.py` parsing logic)
```json
{
  "po_number": "50033210003038",
  "type": "PO_CREATION",
  "details": {
    "total_qty": 120,
    "total_amount": 5000.00,
    "delivery_date": "2026-06-30",
    "expiry_date": "2026-07-01",
    "issue_date": "2026-06-25",
    "outlet_id": "12345",
    "item_data": [
      {
        "item_id": "100001",
        "sku_code": "BLK-NK-001",
        "upc": "8901234567890",
        "name": "Let's Try Namkeen 200g",
        "units_ordered": 60,
        "basic_price": 80.00,
        "mrp": 100.00,
        "hsn_code": "21069099",
        "tax_details": {
          "igst_percentage": null,
          "cgst_percentage": 2.5,
          "sgst_percentage": 2.5
        }
      }
    ],
    "buyer_details": {
      "name": "Blinkit Warehouse Mumbai",
      "gstin": "27AABCB1234C1ZX",
      "destination_address": {
        "line1": "Plot 5, Andheri",
        "line2": "",
        "city": "Mumbai",
        "state": "Maharashtra",
        "postal_code": "400058"
      }
    }
  }
}
```

#### Webhook ACK response format (what we return immediately)
```json
{
  "success": true,
  "message": "PO received",
  "timestamp": "2026-06-25T12:00:00Z",
  "data": {
    "po_status": "processing",
    "po_number": "50033210003038",
    "errors": [],
    "warnings": []
  }
}
```
Then a separate async call sends a "final" ACK to Blinkit's outbound endpoint.

### Key Outbound Endpoints (we call Blinkit)

| Action | Method | Path | Notes |
|---|---|---|---|
| Send PO Acknowledgement | POST | `webhook/public/v1/po/acknowledgement` | After receiving inbound PO |
| Submit ASN | POST | `webhook/public/v1/asn` | After dispatch |
| Request PO Amendment | POST | `webhook/public/v1/po/amendment` | Fix MRP/UPC/UOM errors |

#### PO Acknowledgement payload
```json
{
  "success": true,
  "message": "PO 50033210003038 acknowledged — accepted",
  "timestamp": "2026-06-25T12:00:00Z",
  "data": {
    "po_status": "ACCEPTED",
    "po_number": "50033210003038",
    "errors": [],
    "warnings": []
  }
}
```
- `po_status` values: `PROCESSING`, `ACCEPTED`, `PARTIALLY_ACCEPTED`, `REJECTED`
- Send `processing` immediately, then a final status.

#### ASN payload (POST webhook/public/v1/asn)
```json
{
  "po_number": "50033210003038",
  "invoice_number": "INV-2026-001",
  "invoice_date": "2026-06-25",
  "delivery_date": "2026-06-30",
  "supplier_details": {
    "name": "Let's Try Foods Pvt Ltd",
    "gstin": "07AABCL1234C1ZX",
    "supplier_address": "Unit 4, Sector 5, Noida, UP 201301"
  },
  "buyer_details": {
    "gstin": "27AABCB1234C1ZX"
  },
  "shipment_details": {
    "delivery_type": "ROAD",
    "delivery_partner": "Delhivery",
    "tracking_code": "DEL1234567890",
    "e_way_bill": "1234567890123",
    "license_number": "DL-01-AA-1234",
    "driver_phone": "9876543210"
  },
  "items": [
    {
      "item_id": "100001",
      "sku_code": "BLK-NK-001",
      "batch_number": "BATCH-001",
      "sku_description": "Let's Try Namkeen 200g",
      "upc": "8901234567890",
      "quantity": 60,
      "mrp": 100.00,
      "unit_basic_price": 80.00,
      "unit_landing_price": 83.00,
      "expiry_date": "2027-01-01",
      "uom": "UNIT",
      "tax_distribution": {
        "cgst_percentage": 2.5,
        "sgst_percentage": 2.5,
        "igst_percentage": 0.0
      }
    }
  ]
}
```
- **Type contract:** `item_id` is a **string** (not int); `unit_basic_price` is **float64**; `tax_distribution` is **float64** per field; `supplier_address` is **required** (bug discovered 2026-05-14).
- Response contains `asn_id` — store it.
- **Blinkit has no List-ASNs API and no Cancel-ASN API.** Track allocations locally.

#### PO Amendment payload
```json
{
  "request_data": [
    {
      "item_id": "100001",
      "variants": [
        {
          "upc": "8901234567890",
          "mrp": 99.99,
          "uom": { "type": "STANDARD", "value": "200", "unit": "g" },
          "po_numbers": ["50033210003038"]
        }
      ]
    }
  ]
}
```
- Corrects MRP, UPC, or UOM retroactively. Not enabled for all vendors in dev environment.

### Known Quirks & Bug History
- **HTTP 500 on webhook (2026-05-12):** `WebhookStatus.PROCESSED` was not in the Postgres ENUM. Fixed to use `PENDING`.
- **Relative outbound URLs (2026-05-12):** `RENDER_URL` not set on Render caused `_url()` to build relative paths. Now skips proxy when `RENDER_URL` is empty.
- **ASN type errors (2026-05-14):** `item_id` must be a string, not int; `unit_basic_price` and `tax_distribution` fields must be float64, not int. Go struct type contract overrides PDF spec.
- **403 from local Mac:** Blinkit blocks non-whitelisted IPs. HTTP 403 = reachable but not whitelisted. Route local calls through Render proxy.
- **Amendment API not enabled in dev:** POST to `/webhook/public/v1/po/amendment` returns 404 in test env for Vendor 18309. Contact Blinkit to activate.

### Local Dev Proxy Pattern
- `ENVIRONMENT=local` + `RENDER_URL` set → `BlinkitService._url()` builds `{RENDER_URL}/api/proxy/blinkit/{path}`.
- The Render server at `/api/proxy/blinkit/{path:path}` forwards to `dev.partnersbiz.com`, injecting `api-key` and `x-vendor-id`.
- Credentials fallback: proxy reads from env vars first, then from forwarded request headers.

---

## Zepto (Silk Route API)

### Base URLs
| Environment | URL |
|---|---|
| QA / Local | `https://silkroute.zeptonow.dev` |
| Production | `https://silkroute.zepto.co.in` |

### Auth
- **Auth type:** `X-Client-Id` + `X-Client-Secret` headers (not Bearer token, not API key).
- **IP whitelisting required.** Zepto whitelists specific server IPs. Render IPs to whitelist: `74.220.48.0/24` and `74.220.56.0/24`.
- All **write operations** require `X-Idempotency-Key` header (use UUID per request).

### API Contract Rules (v12)
- Rate limit: 60 RPM per `clientId` per API.
- Quantities must be in **pieces (PC)**, not case sizes.
- **No ASN update API** — cancel + recreate with a new `invoiceNumber`.
- Use `eventId` as idempotency key when polling PO events.
- PO PDF links (`expiringUrlForPoPDF`) expire in ~7 days — download promptly. Pre-signed S3 URLs embed STS tokens that may expire even sooner.
- All timestamps are UTC.

### PO Flow (PULL — we poll Zepto)

#### List PO Events
`GET /api/v1/external/po/events`

| Param | Type | Notes |
|---|---|---|
| `days` | int | Max 45 |
| `pageSize` | int | Max 20 |
| `pageNumber` | int | 1-based |
| `includeAllPoEvents` | bool | false = latest snapshot only |
| `includeLineItemDetails` | bool | false = header only |
| `vendorCodes` | comma string | Max 10 |
| `poCodes` | comma string | Max 10 |

#### PO event object key fields
```json
{
  "eventId": "EVT-12345",
  "eventType": "CreatePO",
  "timestamp": "2026-06-25T10:00:00Z",
  "code": "P364929",
  "status": "RELEASED",
  "vendorCode": "V18309",
  "vendorName": "Let's Try Foods",
  "orderDate": "2026-06-25",
  "deliveryDate": "2026-06-30",
  "expiryDate": "2026-07-01",
  "expiringUrlForPoPDF": "https://s3.amazonaws.com/...?X-Amz-Expires=604800&...",
  "expiringPoPdfLink": "...",
  "totalQty": 100,
  "toStoreCode": "ZEP-BOM-001",
  "toStoreName": "Zepto Mumbai Dark Store",
  "isInterstate": false,
  "address": {
    "storeAddress": "...",
    "vendorAddress": "...",
    "storeShippingAddress": "...",
    "storeBillingAddress": "..."
  },
  "financialDetails": {
    "vendorGSTIN": "07AABCL1234C1ZX",
    "entityGSTIN": "27ZZZZE9999E1ZX"
  },
  "poLineItems": [
    {
      "skuCode": "ZPT-NK-9821",
      "materialCode": "MAT-10045",
      "matetrialCode": "MAT-10045",
      "productName": "Let's Try Namkeen",
      "quantity": 50,
      "ean": "8901234567890",
      "mrp": 100.00,
      "costPrice": 80.00,
      "hsnCode": "21069099",
      "igstPercentage": 5.0,
      "cgstPercentage": null,
      "sgstPercentage": null
    }
  ]
}
```
- **Typo in API:** field is `matetrialCode` (not `materialCode`) in some responses. Both may be present.
- `eventType` values: `CreatePO`, `UpdatePO`, `CancelPO`.
- `status` values: `RELEASED`, `EXPIRED`, `CANCELLED`, `CLOSED`, `OPEN`.

### ASN Operations

#### Create ASN
`POST /api/v1/external/asn` (requires `X-Idempotency-Key`)

```json
{
  "purchaseOrderDetails": {
    "purchaseOrderNumber": "P364929"
  },
  "itemDetails": [
    {
      "productIdentifier": {
        "buyerProductIdentifier": {
          "skuCode": "ZPT-NK-9821"
        }
      },
      "quantity": {
        "invoicedQuantity": {
          "amount": 50,
          "unit": "PC"
        }
      },
      "batchDetails": {
        "batchNumber": "BATCH-001",
        "expiryDate": "2027-01-01"
      }
    }
  ]
}
```
- Response: `{ "data": { "asnNumber": "ASN-XYZ-001" } }` — store `asnNumber` for cancellation.
- `invoiceNumber` must be unique per ASN.

#### Cancel ASN
`DELETE /api/v1/external/asn?asnNumber=ASN-XYZ-001` (requires `X-Idempotency-Key`)

#### List ASNs for a PO
`GET /api/v1/external/asn?poCode=P364929&pageSize=10&pageNumber=1`
- **Returns only total ASN qty — no per-SKU breakdown.** Track per-SKU locally.

#### PO Amendment
`POST /api/v1/external/po/{po_number}/amendment`
- Supported `attributeNames`: `MRP`, `BASE_PRICE`, `EAN`, `CASE_SIZE`, `EXPIRY_DATE`.

### Per-SKU Allocation Tracking (our local DB)
Zepto's List-ASNs API never returns `itemDetails`. We track per-SKU allocations in `zepto_asn_allocations`:
- Row inserted on successful `POST /zepto/asn`.
- `cancelled=True` when `DELETE /zepto/asn/{asn_number}` succeeds.
- `GET /zepto/po/{po_code}/sku-allocations` sums non-cancelled rows per SKU.
- Response: `{ "po_code": "P364929", "allocations": { "ZPT-NK-9821": 50 } }`

### Known Quirks & Bug History
- **Proxy wrapping (old behavior):** Old Render proxy returned HTTP 200 even for Zepto 4xx errors, wrapping them in `{proxied: True, status_code: <real>, data: <body>}`. The `ZeptoService._proxy_error()` method detects and unwraps this. Current proxy was refactored to pass Zepto's raw HTTP response transparently.
- **Zepto error shapes seen in production:**
  - `{"errors": [{"code": 400, "error": "Invalid record. ..."}], "data": null}`
  - `{"message": "...", "statusCode": 400}`
- **PDF URLs expire quickly:** STS tokens embedded in pre-signed S3 URLs may expire in hours even if `X-Amz-Expires` says 7 days. Always call `/zepto/po/{po_number}/pdf` to get a fresh redirect — do not cache the URL.

### Local Dev Proxy Pattern
- `ENVIRONMENT=local` → `ZeptoService._url()` builds `{RENDER_URL}/api/proxy/zepto/{path}`.
- Render `/api/proxy/zepto/{path:path}` forwards to `silkroute.zeptonow.dev`, injecting `X-Client-Id`, `X-Client-Secret`, and `X-Idempotency-Key`.
- The proxy returns Zepto's raw HTTP response (body + status code) transparently.
