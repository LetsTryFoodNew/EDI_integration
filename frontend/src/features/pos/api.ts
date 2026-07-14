import apiClient from "@/lib/api-client";
import type { PaginatedResponse, POListItem, PODetail } from "@/types";

export interface POFilters {
  partner_code?: string;
  po_status?: string;
  date_from?: string;
  date_to?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

export async function fetchPOs(filters: POFilters = {}): Promise<PaginatedResponse<POListItem>> {
  const params = Object.fromEntries(
    Object.entries(filters).filter(([, v]) => v !== undefined && v !== "")
  );
  const res = await apiClient.get<PaginatedResponse<POListItem>>("/api/pos", { params });
  return res.data;
}

export async function fetchPODetail(poId: string): Promise<PODetail> {
  const res = await apiClient.get<PODetail>(`/api/pos/${poId}`);
  return res.data;
}

export async function retrySAPPush(poId: string): Promise<void> {
  await apiClient.post(`/api/pos/${poId}/retry-sap`);
}

export async function cancelPO(poId: string): Promise<void> {
  await apiClient.post(`/api/pos/${poId}/cancel`);
}

export interface POUpdatePayload {
  buyer_po_number?: string;
  buyer_po_date?: string;
  buyer_name?: string;
  buyer_gstin?: string;
  ship_to_name?: string;
  ship_to_code?: string;
  requested_delivery_date?: string;
  grand_total?: number;
  currency?: string;
}

export async function updatePO(poId: string, data: POUpdatePayload): Promise<void> {
  await apiClient.patch(`/api/pos/${poId}`, data);
}

export async function pushToSAP(poId: string): Promise<void> {
  await apiClient.post(`/api/pos/${poId}/push-to-sap`);
}

export async function revalidatePO(poId: string): Promise<{ success: boolean; message: string }> {
  const res = await apiClient.post<{ success: boolean; message: string }>(
    `/api/pos/${poId}/revalidate`
  );
  return res.data;
}
