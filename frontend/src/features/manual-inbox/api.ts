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
