# SAP → EDI Middleware: Invoice Push API

**Version 1.0 — 2026-08-11**
Companion to `docs/sap-master-data-api.md`. Same base URL, same authentication.

---

## 1. What this is for

When we push a retailer PO into Business One it becomes a **Sales Order** (`ORDR`). Your
side then raises one or more **A/R Invoices** against that Sales Order as stock actually
dispatches.

This API is how you tell us about those invoices. For each one we:

1. Store it against the originating PO.
2. Build the **ASN (EDI 856)** for that shipment.
3. Send the ASN to the retailer — over their API (Zepto, Blinkit) or by email (Swiggy and
   other mail-based partners). You do not need to know or care which; we resolve the
   channel from the partner.

**One PO can carry many invoices.** Partial dispatch is normal and expected — send each
invoice as it is raised.

---

## 2. Endpoint

```
POST {baseUrl}/api/invoices
Content-Type: application/json
Authorization: Bearer <access_token>
```

Obtain the token from `POST {baseUrl}/auth/login` exactly as for master data. Tokens last
8 hours.

Up to **500 invoices per request**.

---

## 3. Request

```json
{
  "invoices": [
    {
      "invoice_number": "INV/2026/00871",
      "invoice_date": "2026-08-11",

      "b1_sales_order_doc_entry": 4321,

      "b1_invoice_doc_entry": 9912,
      "b1_invoice_doc_num": 100455,

      "irn": "35b2f1c8d9e04a7b8c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b",
      "eway_bill_number": "381005498765",
      "eway_bill_date": "2026-08-11",

      "subtotal_amount": 10450.00,
      "cgst_amount": 940.50,
      "sgst_amount": 940.50,
      "igst_amount": 0.00,
      "cess_amount": 0.00,
      "round_off": -0.50,
      "grand_total": 12330.50,

      "shipment_date": "2026-08-11",
      "carrier": "Delhivery",
      "tracking_number": "DL8827361925",

      "line_items": [
        {
          "b1_item_code": "FG-ALM-200",
          "buyer_sku": "2223",
          "po_line_number": 1,
          "description": "Roasted Almonds 200g",
          "hsn_code": "20081910",
          "qty": 50,
          "uom": "PCS",
          "unit_price": 209.00,
          "taxable_amount": 10450.00,
          "cgst_rate": 9.00,
          "cgst_amount": 940.50,
          "sgst_rate": 9.00,
          "sgst_amount": 940.50,
          "igst_rate": 0.00,
          "igst_amount": 0.00,
          "line_total": 12331.00,
          "batch_number": "LTF-2026-08-A",
          "expiry_date": "2027-08-10"
        }
      ]
    }
  ]
}
```

### 3.1 Identifying the Sales Order

Send **either** form. `b1_sales_order_doc_entry` is strongly preferred — it is B1's own
key and cannot drift.

| Form | Fields | Notes |
|---|---|---|
| **Preferred** | `b1_sales_order_doc_entry` | The `DocEntry` of the Sales Order we created |
| Fallback | `partner_code` + `po_number` | e.g. `"ZEPTO"` + `"P368477"` |

An invoice carrying neither is rejected with a clear message. If both are sent, DocEntry
wins.

### 3.2 Required fields

| Field | Required | Notes |
|---|---|---|
| `invoice_number` | **yes** | Must be unique. This is the idempotency key |
| `invoice_date` | **yes** | `YYYY-MM-DD` |
| `line_items` | **yes** | At least 1, at most 500 |
| `line_items[].b1_item_code` | **yes** | |
| `line_items[].qty` | **yes** | Must be > 0 |

Everything else is optional. Unknown field names are **rejected** rather than ignored, so
a typo surfaces immediately instead of silently dropping data.

### 3.3 Linking lines back to the PO

Send `po_line_number` where you have it, otherwise `buyer_sku`. We match in this order:

```
po_line_number  →  buyer_sku  →  b1_item_code
```

A line that matches none of these is still stored, but it cannot participate in the
over-invoicing check (section 5).

---

## 4. Response

```json
{
  "created": 1,
  "updated": 0,
  "skipped": 0,
  "errors": [],
  "results": [
    {
      "invoice_number": "INV/2026/00871",
      "outcome": "CREATED",
      "invoice_id": "8cf2f5a3-bfe3-4e9f-997f-1e8caf1a2eac",
      "po_number": "P368477",
      "asn_number": "ASN-INV/2026/00871",
      "asn_dispatched": true,
      "issues": []
    }
  ]
}
```

| `outcome` | Meaning |
|---|---|
| `CREATED` | New invoice stored |
| `UPDATED` | Existing `invoice_number` updated |
| `ERROR` | Not stored — see `issues`. Almost always an unresolvable Sales Order |

`asn_dispatched: true` means the ASN is queued for the retailer. `false` with a populated
`issues` array means the invoice was stored but **held** — see below.

Check `results[]`, not just the counters. One bad invoice in a batch of fifty is reported
per-invoice so you know exactly which.

---

## 5. When an invoice is held

An ASN is outward-facing: once the retailer has it, retracting means a cancel-and-recreate
cycle with them. So we send automatically only when the invoice passes two checks.

| Check | Held when |
|---|---|
| **Total reconciliation** | `grand_total` differs from the sum of `line_total` by more than ₹1.00 |
| **Over-invoicing** | Cumulative invoiced qty for a PO line exceeds the ordered qty, counting all other invoices on that PO |

The ₹1.00 tolerance exists because B1 rounds centrally; a sub-rupee residue is expected,
not an error.

A held invoice is **stored, not rejected**. It appears in our exceptions queue for the ops
team, and on the PO's Invoices tab marked `Held`. Once resolved, the ASN goes out. Nothing
is lost — but nothing reaches the retailer until someone has looked.

---

## 6. Re-pushing an invoice

**Safe and expected.** `invoice_number` is the idempotency key: re-sending updates the
existing record rather than creating a duplicate.

The common case is the IRN. B1 usually has no IRN at the moment the invoice is posted, so:

1. Push the invoice immediately → we store it and send the ASN.
2. Push it again once the IRP returns the IRN → we fill it in.

A re-push **never raises a second ASN**. The original stands.

---

## 7. Errors

Standard envelope, same as master data:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "1 field failed validation.",
    "details": [
      {
        "field": "invoices[0].line_items[2].qty",
        "in": "body",
        "problem": "expected a number, e.g. 18.00",
        "received": "abc"
      }
    ],
    "hint": "Compare the payload against docs/sap-invoice-api.md.",
    "request_id": "f238f3e7871e"
  }
}
```

Quote `request_id` when reporting a problem — it locates the exact request in our logs.

| Status | Meaning |
|---|---|
| `200` | Processed — inspect `results[]` for per-invoice outcomes |
| `401` | Missing or expired token — call `/auth/login` again |
| `415` | `Content-Type` is not `application/json` |
| `422` | Payload shape is wrong — `details[]` names the exact field |

---

## 8. Sequencing

Push the invoice **after** the PO has reached `SAP_CONFIRMED` on our side — before that,
no Sales Order exists to attach it to and the invoice will be rejected with
`ERROR`/"No Sales Order found".

If you push early, simply re-push after confirmation. Nothing is lost.

---

## 9. Backup path

We also poll B1 hourly for invoices that were never pushed. This is a safety net, not the
primary path: polling costs a Service Layer session and is up to an hour behind. Please
push — the poll exists only so a failed push is never silent.

Both paths converge on the same idempotent upsert, so an invoice arriving twice is updated,
never duplicated.
