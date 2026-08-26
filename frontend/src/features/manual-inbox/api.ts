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
