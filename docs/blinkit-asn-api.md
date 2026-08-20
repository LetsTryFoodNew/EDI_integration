# Blinkit ASN (856) — outbound contract

**Source:** "POVMS - ASN Sync API Contracts", rev 100226-093807 — archived at
`_archive/backend_old/assets/POVMS-ASN Sync API Contracts-100226-093807_blinkit.txt`
(a PDF despite the `.txt` extension; 13 pages).
**Implemented in:** `app/adapters/outbound/blinkit_asn.py`
**Last verified:** 2026-08-20, invoice `LTF/26-27/001842` against PO `2264110009002`.

```
POST {base}/webhook/public/v1/asn
Content-Type: application/json

Pre-prod  https://dev.partnersbiz.com
Prod      https://api.partnersbiz.com
```

---

## 1. The thing that will bite you

> **A 2xx does not mean the ASN was accepted.**

The contract is explicit: full acceptance, partial acceptance **and rejection** all
return HTTP 2xx. Rejection is signalled in the body. Its own example response reads:

```json
{ "successful": true, "asn_sync_status": "REJECTED", "error_count": 2, ... }
```

`successful: true` there means *the request was processed* — the response field table
spells it out: "true = operation executed; does not mean all items succeeded".

A second rule compounds it: **one `level: "asn"` error rejects the whole submission**,
even when item rows succeeded and even when `asn_sync_status` says otherwise.

`interpret_asn_response()` therefore reads the body, never the status line. Getting this
wrong marks a rejected ASN as delivered, and the first anyone hears of it is a truck
turned away at the DC.

Rejections are **not retried** — a rejection is a verdict on the content, so resending
the identical body earns the identical answer.

---

## 2. Where each field comes from

### Header

| Field | Source |
|---|---|
| `po_number` | `edi_purchase_orders.buyer_po_number` |
| `invoice_number` / `invoice_date` | the A/R Invoice SAP pushed |
| `delivery_date` | ASN shipment date, falling back to invoice date |
| `tax_distribution[]` | invoice lines grouped by (GST type, rate), amounts summed |
| `basic_price` | invoice subtotal — pre-tax |
| `landing_price` | invoice grand total |
| `quantity` / `item_count` | totals across the invoice lines |
| `po_status` | cumulative invoiced vs ordered across **all** invoices on the PO |
| `supplier_details` | the seller entity |
| `buyer_details.gstin` | the PO's buyer GSTIN |
| `shipment_details` | ASN carrier / tracking + the invoice's e-way bill |

`tax_distribution` groups rather than emitting one row per line: a five-line invoice all
at 5% is two rows (CGST 2.5, SGST 2.5), which is what the retailer reconciles against.
Mixed-rate invoices get a row per rate.

`delivery_type` is derived — a carrier on the ASN means `COURIER` (which makes
`delivery_partner` and `delivery_tracking_code` mandatory), no carrier means `SELF`.

### Items

| Field | Source |
|---|---|
| `item_id` / `sku_code` | the PO line's buyer SKU — what Blinkit matches on |
| `batch_number`, `expiry_date` | the ASN line (from the invoice push) |
| `upc`, `mrp`, `case_config`, `hsn_code` | `material_master` |
| `uom` | split from `material_master.grammage` — `"57g"` → `{"unit": "g", "value": 57}` |
| `unit_basic_price` | invoice line unit price |
| `unit_landing_price` | line total ÷ qty, so it always agrees with the invoice |
| `tax_distribution` | the invoice line's rates |

**All six item tax percentages are always sent, zeros included** (§12.11 marks each
mandatory). Omitting a key is not the same as sending `0`.

`uom` is an object, not a bare code (§12.19). Where `grammage` is missing we send the
UoM code with value `1` — honest about one selling unit rather than inventing a volume.

---

## 3. Two spellings in one document

The field-detail table names the supplier address keys `addressLine1` / `addressLine2`,
while the contract's own JSON **and** XML examples use `address_line_1` /
`address_line_2`. We follow the examples, since those are the wire format. Worth
confirming with Blinkit before go-live.

---

## 4. What we warn about but still send

Blinkit's validation is the backstop, so a missing optional does not block a shipment.
These land in the log and in the invoice-push `issues[]`:

- no `batch_number` (§12.3 mandatory)
- neither `expiry_date` nor `mfg_date` + `shelf_life` (§12.16–12.18)
- no EAN/UPC in the item master (§12.5 mandatory)
- `COURIER` with no tracking code (§11.4)

---

## 5. Error codes

| Code | Meaning | Level |
|---|---|---|
| `E106` | PO already processed; duplicate blocked | asn |
| `E107` | Invoice number already exists | asn |
| `E108` | Invoice date before PO date | asn |
| `E109` | Supplier GSTIN mismatch | asn |
| `E110` | Buyer GSTIN mismatch | asn |
| `E112` | Item ID incorrect — revise mapping | item |
| `E113` | Code category incorrect | item |
| `E114` | Codes mandatory for this item | item |
| `W102` | Item near or past expiry | warning |
| `W103` | No variant matching mrp/upc/grammage/uom | warning |

The contract states these are immutable; more may be added in the `EXXX` range.

---

## 6. Flow

```
SAP posts A/R Invoice  →  POST /api/invoices
                            ├─ invoice stored (idempotent on invoice_number)
                            ├─ ASN raised, one per invoice
                            ├─ payload built in Blinkit's shape at creation time,
                            │  so it is visible in the outbound tab before dispatch
                            └─ queued as EdiOutboundMessage(ASN_856, WEBHOOK)
                                 └─ send_outbound → BlinkitOutboundAdapter → POST /asn
                                      └─ interpret_asn_response decides accepted or not
```

Dispatch is automatic **only when the invoice passes validation** — header total
reconciles with its lines within ₹1.00, and cumulative invoiced qty does not exceed
ordered. A failing invoice is stored and held in the exceptions queue rather than sent,
because an ASN cannot be quietly retracted once the retailer has it.
