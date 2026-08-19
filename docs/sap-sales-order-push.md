# Pushing a PO to SAP B1 as a Sales Order

**Audience:** ops team and the SAP integration team
**Company verified against:** `TESTECPL260422` on `vh0801.centralindia.cloudapp.azure.com:50000`
**Last verified:** 2026-08-19 — Sales Order **3000044** (DocEntry 1767) created from Blinkit PO `2264110001442`

---

## 1. What the middleware adds

A retailer's PO tells us what they want and where to send it. It cannot tell us two things a B1 Sales Order requires, because both describe **our** side of the trade:

| B1 field | What it means | Where it comes from |
|---|---|---|
| `BPL_IDAssignedToInvoice` | Which of our GST branches books the order | Chosen by the operator |
| `WarehouseCode` | Which of that branch's warehouses ships it | Chosen by the operator |

### Why the branch is not a formality

Under the India localization the branch is the **"from" state** for place of supply. So the branch decides the tax code:

```
branch state == ship-to state   →  CSGST@5   (CGST 2.5 + SGST 2.5)
branch state != ship-to state   →  IGST@5
```

Booking a Maharashtra delivery against the Haryana branch produces a document B1 accepts without complaint — with the wrong tax code, in the wrong ledger, on the wrong GST return. It surfaces at filing, not at push.

That is why **nothing is defaulted**. There is no "first active branch" fallback: a default branch is a silent tax decision. The UI shows the tax consequence of every branch before one is picked, and a PO with an undeterminable ship-to state is refused rather than taxed on a guess.

The warehouse must belong to the chosen branch — B1 rejects a document where they disagree, so we check it locally first and say so in a sentence rather than relaying a Service Layer error.

---

## 2. The flow

```
1. GET  /api/pos/{id}/dispatch-options   → branches, their warehouses, B1 addresses,
                                            and CSGST/IGST per branch
2. POST /api/pos/{id}/preview-sap        → the exact JSON, sent nowhere
3. POST /api/pos/{id}/push-to-sap-with   → creates the Sales Order, returns DocNum
```

Step 2 is optional but cheap. A wrong branch shows up as a wrong `VatGroup` on every line, and that is far easier to fix before the document exists than after.

### Preconditions

- The partner has a `b1_card_code` (Master Data → Customers).
- Every line has a B1 item code — fix unmapped SKUs in SAP, then re-sync.
- No unresolved **ERROR**-severity validation issues.
- Branch Master and Warehouse Master are populated: `python scripts/sync_b1_org_from_sap.py`.

---

## 3. The payload

Verified field-for-field against posted documents in the live company, not against the generic Service Layer reference — B1 installations differ in which UDFs exist, and an undefined property fails the whole POST.

```json
{
  "CardCode": "D00086",
  "CardName": "BLINK COMMERCE PRIVATE LIMITED",
  "DocDate": "2026-08-19",
  "DocDueDate": "2026-08-24",
  "TaxDate": "2026-08-19",
  "BPL_IDAssignedToInvoice": 5,
  "NumAtCard": "2264110001442",
  "Comments": "EDI BLINKIT PO 2264110001442 via middleware",
  "DocCurrency": "INR",
  "U_OrdType": "N",
  "U_DC_TAT": 5,
  "ShipToCode": "421302-HOT",
  "PayToCode": "421302-HOT",
  "DocumentLines": [
    {
      "ItemCode": "FG00310",
      "Quantity": 240.0,
      "WarehouseCode": "FG_MH",
      "Price": 31.01,
      "VatGroup": "CSGST@5",
      "DiscountPercent": 0.0,
      "Currency": "INR",
      "ShipDate": "2026-08-24"
    }
  ]
}
```

| Field | Source |
|---|---|
| `NumAtCard` | The retailer's own PO number — how finance reconciles back to them |
| `U_OrdType` | `"N"` (normal) |
| `U_DC_TAT` | Turnaround in days, PO date → requested delivery |
| `U_POEXP_DT` | PO expiry, only when the retailer sent one |
| `VatGroup` | Derived from the branch/ship-to state pair and the line's combined GST rate |

### Two things deliberately **not** sent

- **`U_MWOrderID`** appears in the draft integration spec but is **not defined on `ORDR`** in this company. Service Layer rejects the whole document for an unknown property, so it is omitted. Create the UDF in B1 and it can be added.
- **UoM fields.** Items here have `UoMGroupEntry -1` and posted lines carry `UoMCode "Manual"`. Sending a UoM would be rejected or silently reinterpreted.

### The rate is the combined total

B1's tax code carries the **combined** GST rate: a line split 2.5% CGST + 2.5% SGST is `CSGST@5`, not `CSGST@2.5`.

---

## 4. Errors you will actually see

| Message | Fix |
|---|---|
| `Warehouse FG_HR does not belong to branch 5 (Maharashtra)` | Pick a warehouse under that branch |
| `Branch 2 (Main) is disabled in SAP` | Pick another branch |
| `Cannot determine the place of supply` | The PO has no usable buyer GSTIN or ship-to state |
| `Line 1 (10116317) has no B1 item code` | Add the SKU mapping in SAP, re-sync, retry |
| `Partner 'X' has no b1_card_code` | Set it in Master Data → Customers |
| `Already in SAP as Sales Order 3000044` | Idempotency guard — the PO is already pushed |

---

## 5. Configuration

```ini
B1_SERVICE_LAYER_URL=https://vh0801.centralindia.cloudapp.azure.com:50000/b1s/v2
B1_COMPANY_DB=TESTECPL260422
B1_USERNAME=ECPL.prof02
B1_PASSWORD=...
B1_SESSION_POOL_SIZE=2
B1_VERIFY_SSL=false      # test server has a self-signed cert; MUST be true in production
```

Include the `/b1s/vN` suffix — the client honours whichever version is named there rather than forcing v1.

> **Before production:** this password has been shared in plaintext during development and must be rotated. `B1_VERIFY_SSL` must be `true`.
