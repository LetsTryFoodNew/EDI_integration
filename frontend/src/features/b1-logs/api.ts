import apiClient from "@/lib/api-client";
import type { PaginatedResponse, B1LogListItem, B1LogDetail } from "@/types";

export async function fetchB1Logs(params?: {
  po_id?: string;
  success?: boolean;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<B1LogListItem>> {
  const filtered = Object.fromEntries(
    Object.entries(params ?? {}).filter(([, v]) => v !== undefined && v !== "")
  );
  const res = await apiClient.get<PaginatedResponse<B1LogListItem>>("/api/b1-logs", { params: filtered });
  return res.data;
}

export async function fetchB1LogDetail(logId: string): Promise<B1LogDetail> {
  const res = await apiClient.get<B1LogDetail>(`/api/b1-logs/${logId}`);
  return res.data;
}
