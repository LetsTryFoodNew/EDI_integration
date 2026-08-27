import apiClient from "@/lib/api-client";
import type { ApiPartner } from "../api-inbox/api";

// Manual Inbox = partners whose orders arrive by hand: MANUAL (no integration)
// and PORTAL (scraping not built yet — Phase 9). Message listing/detail reuse the
// channel-agnostic /api/inbox/messages* endpoints via features/inbox/api.ts.
export type ManualPartner = ApiPartner;

export async function fetchManualPartners(): Promise<ManualPartner[]> {
  const res = await apiClient.get<{ items: ManualPartner[] }>("/api/manual-inbox/partners", {
    params: { limit: 200 },
  });
  return res.data.items;
}

// ── Manual PO entry ──────────────────────────────────────────────────────────

export interface ManualPoLine {
  buyer_sku: string;
  ordered_qty: string;
  unit_price: string;
  gst_rate: string;
  /** The SAP item this line ships, chosen from the material master. Only meaningful
   *  on a keyed-in order: a manual partner has no catalogue to map a buyer SKU from. */
  b1_item_code?: string | null;
  description?: string | null;
  hsn_code?: string | null;
  buyer_uom?: string | null;
  discount_pct?: string | null;
  cess_rate?: string | null;
}

export interface ManualShipTo {
  warehouse_code?: string | null;
  name?: string | null;
  line1?: string | null;
  city?: string | null;
  state?: string | null;
  pincode?: string | null;
  gstin?: string | null;
}

export interface ManualPoEntry {
  partner_code: string;
  buyer_po_number: string;
  line_items: ManualPoLine[];
  buyer_po_date?: string | null;
  requested_delivery_date?: string | null;
  buyer_name?: string | null;
  buyer_gstin?: string | null;
  ship_to: ManualShipTo;
  notes?: string | null;
  replace_existing?: boolean;
}

export interface ManualPoEntryResult {
  raw_message_id: string;
  partner_code: string;
  buyer_po_number: string;
  revision: number;
  queued: boolean;
  message: string;
}

export async function createManualEntry(entry: ManualPoEntry): Promise<ManualPoEntryResult> {
  const res = await apiClient.post<ManualPoEntryResult>("/api/manual-inbox/entries", entry);
  return res.data;
}

export interface ManualPoEntryDetail {
  po_id: string;
  partner_code: string;
  partner_name: string;
  revision: number;
  po_status: string;
  editable: boolean;
  locked_reason: string | null;
  entry: ManualPoEntry;
}

export async function fetchManualEntry(poId: string): Promise<ManualPoEntryDetail> {
  const res = await apiClient.get<ManualPoEntryDetail>(`/api/manual-inbox/entries/${poId}`);
  return res.data;
}

export interface MaterialOption {
  id: string;
  item_code: string;
  item_name: string;
  hsn: string | null;
  invntry_uom: string;
  mrp: string | null;
  ean_code: string | null;
}

/** Item picker for a keyed-in line. Server-side search: the master runs to
 *  thousands of rows, so it is never pulled down whole. */
export async function searchMaterials(search: string): Promise<MaterialOption[]> {
  const res = await apiClient.get<{ items: MaterialOption[] }>("/api/master-data/materials", {
    params: { search: search || undefined, valid_for: 1, limit: 20 },
  });
  return res.data.items;
}
