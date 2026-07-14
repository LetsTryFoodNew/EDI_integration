import apiClient from "@/lib/api-client";
import type { PaginatedResponse, TradingPartner, MaterialMaster, SkuMapping, ShipToMapping } from "@/types";

// Partners
export async function fetchPartners(params?: { limit?: number; offset?: number }): Promise<PaginatedResponse<TradingPartner>> {
  const res = await apiClient.get<PaginatedResponse<TradingPartner>>("/api/master-data/partners", { params });
  return res.data;
}

export async function updatePartner(id: string, payload: Partial<TradingPartner>): Promise<TradingPartner> {
  const res = await apiClient.patch<TradingPartner>(`/api/master-data/partners/${id}`, payload);
  return res.data;
}

// Materials
export async function fetchMaterials(params?: { search?: string; limit?: number; offset?: number }): Promise<PaginatedResponse<MaterialMaster>> {
  const res = await apiClient.get<PaginatedResponse<MaterialMaster>>("/api/master-data/materials", { params });
  return res.data;
}

export async function createMaterial(payload: { b1_item_code: string; description?: string; hsn_code?: string; uom?: string }): Promise<MaterialMaster> {
  const res = await apiClient.post<MaterialMaster>("/api/master-data/materials", payload);
  return res.data;
}

// SKU Mappings
export async function fetchSkuMappings(params?: { partner_code?: string; search?: string; mapping_status?: string; limit?: number; offset?: number }): Promise<PaginatedResponse<SkuMapping>> {
  const filtered = Object.fromEntries(Object.entries(params ?? {}).filter(([, v]) => v !== undefined && v !== ""));
  const res = await apiClient.get<PaginatedResponse<SkuMapping>>("/api/master-data/sku-mappings", { params: filtered });
  return res.data;
}

export async function updateSkuMapping(id: string, payload: { b1_item_code: string; qty_per_buyer_uom?: string }): Promise<SkuMapping> {
  const res = await apiClient.patch<SkuMapping>(`/api/master-data/sku-mappings/${id}`, payload);
  return res.data;
}

// Ship-to Mappings
export async function fetchShipToMappings(params?: { partner_code?: string; limit?: number; offset?: number }): Promise<PaginatedResponse<ShipToMapping>> {
  const filtered = Object.fromEntries(Object.entries(params ?? {}).filter(([, v]) => v !== undefined && v !== ""));
  const res = await apiClient.get<PaginatedResponse<ShipToMapping>>("/api/master-data/ship-to-mappings", { params: filtered });
  return res.data;
}

export async function updateShipToMapping(id: string, payload: { b1_whs_code: string }): Promise<ShipToMapping> {
  const res = await apiClient.patch<ShipToMapping>(`/api/master-data/ship-to-mappings/${id}`, payload);
  return res.data;
}
