import apiClient from "@/lib/api-client";

export interface InboxPartner {
  code: string;
  name: string;
  source_channel: string;
  gmail_label: string | null;
  total: number;
  pending: number;
  failed: number;
  last_received_at: string | null;
}

export interface InboxMessageItem {
  id: string;
  external_id: string;
  subject: string | null;
  sender: string | null;
  received_at: string;
  attachment_count: number;
  parse_status: string;
  processed: boolean;
  po_id: string | null;
  po_number: string | null;
}

export interface AttachmentInfo {
  filename: string;
  url: string;
  mime_type: string;
  size_bytes: number;
}

export interface InboxMessageDetail {
  id: string;
  partner_code: string;
  partner_name: string;
  external_id: string;
  subject: string | null;
  sender: string | null;
  received_at: string;
  attachments: AttachmentInfo[];
  body_preview: string | null;
  parse_status: string;
  processed: boolean;
  po_id: string | null;
  po_number: string | null;
  po_status: string | null;
  created_at: string;
}

export interface PaginatedMessages {
  items: InboxMessageItem[];
  total: number;
  limit: number;
  offset: number;
}

export async function fetchInboxPartners(): Promise<InboxPartner[]> {
  const res = await apiClient.get<InboxPartner[]>("/api/inbox/partners");
  return res.data;
}

export interface InboxMessageFilters {
  search?: string;
  date_from?: string; // yyyy-MM-dd
  date_to?: string; // yyyy-MM-dd
}

export async function fetchInboxMessages(
  partner_code: string,
  offset = 0,
  limit = 50,
  filters: InboxMessageFilters = {},
): Promise<PaginatedMessages> {
  const res = await apiClient.get<PaginatedMessages>("/api/inbox/messages", {
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

export async function fetchInboxMessage(id: string): Promise<InboxMessageDetail> {
  const res = await apiClient.get<InboxMessageDetail>(`/api/inbox/messages/${id}`);
  return res.data;
}

export async function retryParse(messageId: string): Promise<{ status: string; message_id: string }> {
  const res = await apiClient.post(`/api/inbox/messages/${messageId}/retry-parse`);
  return res.data;
}

export async function retryAllFailed(partnerCode: string): Promise<{ queued_count: number; partner_code: string }> {
  const res = await apiClient.post("/api/inbox/retry-all-failed", null, {
    params: { partner_code: partnerCode },
  });
  return res.data;
}

export async function downloadAttachment(messageId: string, index: number): Promise<Blob> {
  const res = await apiClient.get(`/api/inbox/messages/${messageId}/attachments/${index}`, {
    responseType: "blob",
  });
  return res.data as Blob;
}
