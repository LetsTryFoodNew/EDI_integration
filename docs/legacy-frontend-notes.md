# Legacy Frontend Notes

> Derived from `_archive/frontend_old/` (formerly `frontend/`).
> Source files: `src/App.tsx`, `src/api.ts`, `src/types.ts`, `src/pages/*.tsx`, `src/components/*.tsx`.
> Stack: React 18 + TypeScript + Vite + Tailwind CSS. Already using the target stack — no migration needed.

---

## Screens / Routes

| Route | Component | Description |
|---|---|---|
| `/` | `Dashboard` | Stats cards: PO counts by status, revenue, unmapped SKUs, webhook failures |
| `/purchase-orders` | `PurchaseOrders` | Generic PO list (all sources: MANUAL + WEBHOOK) |
| `/purchase-orders/new` | `CreatePO` | Manual PO creation form |
| `/purchase-orders/:id` | `PODetail` | Single PO detail view |
| `/products` | `Inventory` | Internal product catalogue + stock levels |
| `/companies` | `Companies` | Partner company list with integration setup |
| `/webhooks` | `WebhookLogs` | Raw inbound webhook event log |
| `/asn` | `ASNPage` | Generic ASN list (non-partner-specific) |
| `/integration` | `IntegrationSetup` | Per-partner webhook URL + credential config |
| `/product-mappings` | `ProductMappings` | Partner SKU → SAP material code mapping CRUD |
| `/sap-orders` | `SAPOrders` | SAP Sales Order audit log |
| `/unmapped-skus` | `UnmappedSKUs` | Unmapped SKU alerts + inline resolution |
| `/zepto/pos` | `ZeptoPOs` | Zepto PO list (polled from Silk Route API) |
| `/zepto/asn` | `ZeptoASN` | Zepto ASN manager (create, cancel, SKU allocations) |
| `/blinkit/pos` | `BlinkitPOs` | Blinkit PO list (from local webhook store) |
| `/blinkit/asn` | `BlinkitASN` | Blinkit ASN manager |
| `/api-doc` | `ApiDoc` | Internal API documentation viewer |

---

## Shared Components

### `Sidebar` (`src/components/Sidebar.tsx`)
- Navigation with sections: General (Dashboard, POs, Products), Partners (Blinkit, Zepto), Operations (Webhooks, ASN, Mappings, SAP Orders, Unmapped SKUs).
- Uses `NavLink` for active-state highlighting. Fixed width (`w-64`).
- Partner entries have sub-items (PO Events, ASN Manager).

### `StatusBadge` (`src/components/StatusBadge.tsx`)
- Maps `POStatus`, `WebhookStatus`, `ASNStatus` to colored pill badges.
- Re-use pattern: `<StatusBadge status={po.status} />`

### `CompanyAvatar` (`src/components/CompanyAvatar.tsx`)
- Displays a colored circle with company initials.
- Takes `name` and `color` props.

---

## API Layer (`src/api.ts`)

Single axios instance with `baseURL` from `VITE_API_URL` env var (default: `http://localhost:8000/api`).

All API calls are typed functions. No direct axios calls from components. Key groupings:

**Standard CRUD:** dashboard, companies, products, purchase-orders, asn, product-mappings, sap-orders, unmapped-skus, webhook/logs.

**Zepto-specific:**
- `getZeptoPOEvents(params)` — list POs (paginated, filterable by days/vendor/PO codes)
- `createZeptoASN(payload)` — submit ASN
- `cancelZeptoASN(asn_number)` — cancel
- `getZeptoASNs(po_code)` — list ASNs for a PO
- `getZeptoPOSKUAllocations(po_code)` — per-SKU invoiced qty map (local DB)
- `requestZeptoPOAmendment(po_number, payload)`

**Blinkit-specific:**
- `getBlinkitPOs()` — list from local webhook store
- `getBlinkitPO(po_number)` — single PO detail
- `createBlinkitASN(payload)` — submit ASN + persist allocations
- `getBlinkitASNs(po_number)` — locally-tracked ASNs for a PO
- `getBlinkitPOSKUAllocations(po_number)` — `{ allocations: { item_id: qty } }`
- `cancelBlinkitASN(asn_id)` — local cancel only (no Blinkit cancel API)
- `requestBlinkitPOAmendment(po_number, payload)`

---

## TypeScript Types (`src/types.ts`)

Key types to re-implement in the new `src/types/` folder:

### Partner-agnostic
- `POStatus` — `"PENDING" | "STOCK_AVAILABLE" | "STOCK_PARTIAL" | "OUT_OF_STOCK" | "CONFIRMED" | "DISPATCHED"`
- `PurchaseOrder`, `POItem`, `Company`, `Product`, `ASNRecord`, `WebhookLog`, `DashboardStats`

### Zepto
- `ZeptoPO` — `eventId`, `eventType`, `code` (PO number), `status`, `financialDetails.vendorGSTIN`, `poLineItems[]`
- `ZeptoPOLineItem` — `skuCode`, `matetrialCode` (typo in API), `mrp`, `costPrice`, `hsnCode`, GST breakdown fields
- `ZeptoASN` — `asnNumber`, `status`, `poNumber`
- Note: Zepto uses camelCase throughout.

### Blinkit
- `BlinkitPO` — `purchaseOrderId`, `status` (`OPEN | CLOSED | CANCELLED | DRAFT | EXPIRED`), `items[]`
- `BlinkitPOItem` — `productId` (string), `skuCode`, `upc`, `requestedQty`, `rate`, `mrp`, `hsnCode`, GST fields
- `BlinkitTrackedASN` — local DB model (since Blinkit has no List-ASNs API)
- Note: Blinkit uses snake_case for inbound webhook fields, but the frontend normalizes to camelCase.

---

## Business Logic Worth Re-Implementing

### Fill Rate Tracking
Both Blinkit and Zepto pages compute fill rate per PO:
```
fillRate = sum(invoicedQty) / sum(requestedQty) × 100
```
UI:
- Green badge if fillRate === 0% (nothing invoiced yet)
- Yellow badge if 0% < fillRate < 100% (partial)
- Red badge if fillRate === 100% (fully invoiced, ASN button disabled)

### Remaining Qty Calculation (for ASN form pre-fill)
```
remainingQty[item] = requestedQty[item] - allocations[item.id] ?? 0
```
- For Blinkit: `allocations` from `GET /blinkit/po/{po_number}/sku-allocations` → `{ item_id: qty }`.
- For Zepto: `allocations` from `GET /zepto/po/{po_code}/sku-allocations` → `{ skuCode: qty }`.

### PO Status Derivation (Blinkit, since status not sent directly)
```typescript
if (eventType.includes("CANCEL")) return "CANCELLED";
if (expiryDate && expiryDate < now) return "EXPIRED";
return "RELEASED";
```

### Zepto PDF Viewer
- Never cache the PDF URL — always call `GET /zepto/po/{po_number}/pdf` which redirects to a fresh pre-signed S3 URL.
- Use `<iframe>` or `window.open()` to display.

### Blinkit ASN Form Fields
Required by Blinkit API (all must be present, no optional skips):
- Header: `po_number`, `invoice_number`, `invoice_date`, `delivery_date`
- `supplier_details`: `name`, `gstin`, `supplier_address` (full string, required)
- `buyer_details`: `gstin`
- `shipment_details`: `delivery_type`, optionally `delivery_partner`, `tracking_code`, `e_way_bill`, `license_number`, `driver_phone`
- `items[]`: `item_id` (string!), `sku_code`, `batch_number`, `sku_description`, `upc`, `quantity`, `mrp`, `unit_basic_price` (float), `unit_landing_price`, `expiry_date`, `uom`, `tax_distribution` (float fields)

---

## Observations for the New Frontend

1. **The existing frontend has no TanStack Query** — all data fetching is raw `useEffect` + `useState`. The new code must use TanStack Query as specified in CLAUDE.md.
2. **No shadcn/ui** in the old code — uses vanilla Tailwind. New code uses shadcn/ui components.
3. **No React Hook Form / Zod** — forms use uncontrolled local state. New code uses RHF + Zod.
4. **No routing-level auth guard** — any route is accessible. Phase 8 adds JWT auth + protected routes.
5. **`Dashboard` page** is a direct stats fetch — same stats will map to the new `GET /dashboard/today` endpoint.
6. **`ProductMappings` page** does partner-SKU → internal-product CRUD — re-implement in `features/master-data/` as the SKU Mapping tab.
7. **`UnmappedSKUs` page** does inline resolution with a product dropdown — re-implement in `features/exceptions/` with the same UX but using RHF.
8. **`ZeptoPOs` and `BlinkitPOs`** are the most complex pages — they embed ASN creation modals with multi-step forms, fill-rate badges, and existing-ASNs panels. Ports to `features/pos/` in Phase 8 with the same structure.
9. **No pagination UI** in the old frontend — the new PO list must implement TanStack Table pagination from the start.
10. **No empty/loading/error states** in most old components — every new component must handle all three per CLAUDE.md requirements.
