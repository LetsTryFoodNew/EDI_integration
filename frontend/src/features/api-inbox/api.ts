import apiClient from "@/lib/api-client";
import type { InboxMessageItem, PaginatedMessages, InboxMessageFilters } from "../inbox/api";

export type { InboxMessageItem };

export interface ApiPartner {
  code: string;
  name: string;
  source_channel: string;
  total: number;
  pending: number;
  failed: number;
  last_received_at: string | null;
}

export interface ApiMessageDetail {
  id: string;
  partner_code: string;
  partner_name: string;
  external_id: string;
  received_at: string;
  payload: Record<string, unknown> | null;
  parse_status: string;
  processed: boolean;
  po_id: string | null;
  po_number: string | null;
  po_status: string | null;
  created_at: string;
}

export async function fetchApiPartners(): Promise<ApiPartner[]> {
  const res = await apiClient.get<ApiPartner[]>("/api/api-inbox/partners");
  return res.data;
}

export async function fetchApiMessages(
  partner_code: string,
  offset = 0,
  limit = 50,
  filters: InboxMessageFilters = {},
): Promise<PaginatedMessages> {
  const res = await apiClient.get<PaginatedMessages>("/api/api-inbox/messages", {
    params: {
      partner_code,
      offset,
      limit,
      search: filters.search || undefined,
      date_from: filters.date_from || undefined,
      date_to: filters.date_to || undefined,
    },
  });
  return res.data;
}

export async function fetchApiMessage(id: string): Promise<ApiMessageDetail> {
  const res = await apiClient.get<ApiMessageDetail>(`/api/api-inbox/messages/${id}`);
  return res.data;
}

export async function retryApiParse(
  messageId: string,
): Promise<{ status: string; message_id: string }> {
  const res = await apiClient.post(`/api/api-inbox/messages/${messageId}/retry-parse`);
  return res.data;
}

export interface ApiPartnerStatus {
  code: string;
  name: string;
  source_channel: string;
  last_fetched_at: string | null;
  last_message_at: string | null;
  messages_last_24h: number;
  failed_last_24h: number;
  webhook_url: string | null;
  is_configured: boolean;
}

export async function fetchApiPartnerStatus(): Promise<ApiPartnerStatus[]> {
  const res = await apiClient.get<ApiPartnerStatus[]>("/api/api-inbox/status");
  return res.data;
}

export async function triggerFetch(
  partner_code: string,
): Promise<{ status: string; partner_code: string; job_id: string; message: string }> {
  const res = await apiClient.post("/api/api-inbox/trigger-fetch", null, {
    params: { partner_code },
  });
  return res.data;
}
