# SAP Business One Setup Guide — EDI Middleware

This document describes the one-time configuration required in SAP Business One before the EDI middleware can push Sales Orders.

> **Do this before running Phase 6 for the first time.**

---

## 1. Service Layer Access

Ensure the Service Layer is enabled and reachable:

| Setting | Value |
|---|---|
| HTTP (dev) | `http://<b1-server>:50000` |
| HTTPS (prod) | `https://<b1-server>:50001` |
| Base path | `/b1s/v1` |
| Auth | `POST /b1s/v1/Login` → `SessionId` cookie |

Set the following env vars in `.env`:

```
B1_SERVICE_LAYER_URL=https://<b1-server>:50001
B1_COMPANY_DB=SBO_LETSTRY
B1_USERNAME=EDI_BOT
B1_PASSWORD=<strong-password>
B1_SESSION_POOL_SIZE=2
B1_VERIFY_SSL=true
```

Verify connectivity with:

```bash
python scripts/test_b1_connection.py
```

---

## 2. API User

Create a dedicated B1 user for the integration:

- **Username**: `EDI_BOT` (or as configured in `B1_USERNAME`)
- **Type**: Professional / Limited — whichever license tier allows Service Layer access
- **Required permissions** (Authorizations → Sales):
  - Add, Update — Sales Orders (`ORDR`)
  - Add — Delivery Notes (`ODLN`)
  - Add — A/R Invoices (`OINV`)
  - Add — A/R Returns (`ORDN`)
  - Add — A/R Credit Memos (`ORIN`)
- **Read-only** access to:
  - Item Master Data (`OITM`)
  - Business Partners (`OCRD`)
  - Warehouses (`OWHS`)
  - UoM Groups

---

## 3. Business Partners (Customers)

Create one Customer record per retailer partner. The `CardCode` is used as the key to link EDI partner records.

Minimum required fields per customer:

| Field | Example |
|---|---|
| CardCode | `C_BLINKIT` |
| CardName | `Blinkit (Grofers India Pvt Ltd)` |
| CardType | `cCustomer` |
| GSTIN (India localisation) | `27AABCG1234P1Z5` |
| Payment Terms | `Net 30` |
| Default Warehouse | `WH_DELHI` |
| BPL (Branch) | `1` (or appropriate branch ID) |

The `CardCode` must match `trading_partners.b1_card_code` in the EDI middleware DB.

---

## 4. Item Master

Each product SKU must exist in B1 before a Sales Order line can be created.

Required fields per item:

| Field | Notes |
|---|---|
| ItemCode | Must match `material_master.b1_item_code` in middleware DB |
| HSN/SAC Code | 6–8 digit HSN (India localisation field) |
| Tax Code | One of `GST5`, `GST12`, `GST18`, `GST28` (set up in tax codes) |
| UoM Group | e.g., `CASE_GROUP` where 1 Case = 24 PCS |
| Inventory UoM | The UoM used in B1 (middleware sends quantities in this unit) |

---

## 5. Warehouses

Each ship-to location sent by retailers must map to a B1 warehouse.

| Field | Notes |
|---|---|
| WhsCode | Must match `ship_to_mapping.b1_whs_code` in middleware DB |
| State | Determines CGST+SGST vs IGST calculation |

---

## 6. User-Defined Fields (UDFs)

UDFs must be created **before** any Sales Orders are pushed. Create them in:  
**Administration → Setup → General → User-Defined Fields**

### 6.1 Sales Order Header UDFs (`ORDR`)

| Field Name | Description | Type | Size |
|---|---|---|---|
| `U_EDI_SOURCE` | Partner code that originated the PO (e.g. `BLINKIT`) | Alpha-Numeric | 20 |
| `U_EDI_DOC_UUID` | UUID of the canonical EDI PO in the middleware DB | Alpha-Numeric | 36 |
| `U_EDI_RECEIVED_AT` | ISO timestamp when the PO was received by the middleware | Alpha-Numeric | 30 |
| `U_BUYER_GSTIN` | GSTIN of the buyer/retailer as stated on the PO | Alpha-Numeric | 15 |
| `U_EDI_PO_NUMBER` | Buyer's original PO number (for reference / reconciliation) | Alpha-Numeric | 50 |

### 6.2 Sales Order Line UDFs (`RDR1`)

| Field Name | Description | Type | Size |
|---|---|---|---|
| `U_EDI_LINE_NO` | Line number from the original retailer PO | Alpha-Numeric | 10 |
| `U_BUYER_SKU` | Buyer's own SKU/article code for this line item | Alpha-Numeric | 50 |

### How to create a UDF in SAP B1

1. Go to **Administration → Setup → General → User-Defined Fields — Management**.
2. Select the object (**Sales Order** for header, **Sales Order – Rows** for lines).
3. Click **Add**.
4. Enter the Field Name (without `U_` prefix — B1 adds it automatically), Type, and Size.
5. Click **Update**.

> **Note**: After creating UDFs, restart the Service Layer or perform a Company DB refresh for changes to be visible via the API.

---

## 7. Tax Codes

Ensure these standard India GST tax codes exist in B1:

| Code | Description | Rate |
|---|---|---|
| `GST5` | GST 5% | 5% |
| `GST12` | GST 12% | 12% |
| `GST18` | GST 18% | 18% |
| `GST28` | GST 28% | 28% |

B1 automatically splits CGST/SGST or applies IGST based on the branch (`BPLId`) and customer ship-to state.

---

## 8. India Localisation Checklist

- [ ] India Add-On installed and enabled for the Company DB
- [ ] GSTIN set on the Company (`Administration → Company Details`)
- [ ] GSTIN set on each Customer (Business Partner)
- [ ] HSN codes set on all Item Master records
- [ ] Tax codes (`GST5`, `GST12`, `GST18`, `GST28`) defined
- [ ] Branch Places (BPL) configured with correct state codes

---

## 9. Posting Period

Sales Orders will fail with **`-5002 Posting period is closed`** if pushed with a date outside an open period.

Workaround in middleware: the `B1ClosedPeriodError` exception is caught and the PO is marked `SAP_REJECTED` with the error message surfaced in the exception dashboard.

To avoid this: ensure the current month's posting period is open in B1:  
**Administration → Setup → Financials → Posting Periods**.

---

## 10. Verifying UDFs via API

After creating the UDFs, verify they appear on Sales Orders:

```bash
curl -X GET \
  "https://<b1-server>:50001/b1s/v1/Orders(1)?$select=U_EDI_SOURCE,U_EDI_DOC_UUID" \
  -H "Cookie: B1SESSION=<token>; CompanyDB=SBO_LETSTRY"
```

You should see the UDF keys in the response JSON.
