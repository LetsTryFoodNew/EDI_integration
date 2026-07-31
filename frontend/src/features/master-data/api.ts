import apiClient from "@/lib/api-client";
import type {
  PaginatedResponse,
  TradingPartner,
  CustomerDetail,
  MaterialMaster,
  MasterDataSyncResult,
} from "@/types";

// ── Customers (parent) ────────────────────────────────────────────────────────

export async function fetchCustomers(params?: {
  is_active?: boolean;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<TradingPartner>> {
  const res = await apiClient.get<PaginatedResponse<TradingPartner>>(
    "/api/master-data/partners",
    { params },
  );
  return res.data;
}

/** One customer plus its SKU-mapping and ship-to arrays — backs the expanded row. */
export async function fetchCustomerDetail(id: string): Promise<CustomerDetail> {
  const res = await apiClient.get<CustomerDetail>(`/api/master-data/partners/${id}`);
  return res.data;
}

export async function updateCustomer(
  id: string,
  payload: Partial<TradingPartner>,
): Promise<TradingPartner> {
  const res = await apiClient.put<TradingPartner>(`/api/master-data/partners/${id}`, payload);
  return res.data;
}

// ── Item master ───────────────────────────────────────────────────────────────

export async function fetchItems(params?: {
  search?: string;
  valid_for?: number;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<MaterialMaster>> {
  const filtered = Object.fromEntries(
    Object.entries(params ?? {}).filter(([, v]) => v !== undefined && v !== ""),
  );
  const res = await apiClient.get<PaginatedResponse<MaterialMaster>>(
    "/api/master-data/materials",
    { params: filtered },
  );
  return res.data;
}

export async function createItem(payload: {
  item_code: string;
  item_name: string;
  hsn?: string;
  invntry_uom?: string;
}): Promise<MaterialMaster> {
  const res = await apiClient.post<MaterialMaster>("/api/master-data/materials", payload);
  return res.data;
}

export async function updateItem(
  id: string,
  payload: Partial<Omit<MaterialMaster, "id" | "item_code">>,
): Promise<MaterialMaster> {
  const res = await apiClient.put<MaterialMaster>(`/api/master-data/materials/${id}`, payload);
  return res.data;
}

// ── Mapping edits (from the expanded customer row) ────────────────────────────

export async function updateShipToMapping(
  id: string,
  payload: { b1_whs_code: string; is_active?: boolean },
): Promise<unknown> {
  const res = await apiClient.put(`/api/master-data/ship-to/${id}`, payload);
  return res.data;
}

// ── Bulk sync (SAP pushes into these; not called by the ops UI) ───────────────

export async function syncCustomers(partners: Record<string, unknown>[]): Promise<MasterDataSyncResult> {
  const res = await apiClient.post<MasterDataSyncResult>("/api/master-data/partners/sync", { partners });
  return res.data;
}

export async function syncItems(items: Record<string, unknown>[]): Promise<MasterDataSyncResult> {
  const res = await apiClient.post<MasterDataSyncResult>("/api/master-data/materials/sync", { items });
  return res.data;
}

export async function syncSkuMappings(mappings: Record<string, unknown>[]): Promise<MasterDataSyncResult> {
  const res = await apiClient.post<MasterDataSyncResult>("/api/master-data/sku-mappings/sync", { mappings });
  return res.data;
}

export async function syncShipTo(mappings: Record<string, unknown>[]): Promise<MasterDataSyncResult> {
  const res = await apiClient.post<MasterDataSyncResult>("/api/master-data/ship-to/sync", { mappings });
  return res.data;
}
