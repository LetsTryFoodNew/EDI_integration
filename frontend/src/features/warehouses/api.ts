import apiClient from "@/lib/api-client";
import type { PaginatedResponse, BranchMaster, WarehouseMaster } from "@/types";

// Branch Master (SAP OBPL) and Warehouse Master (SAP OWHS).
//
// SAP pushes these through POST .../sync; the dashboard never calls those endpoints.
// The only writable fields here are `is_active` and `notes` — everything else belongs
// to SAP, and the API returns 409 rather than silently dropping a changed value.

export async function fetchBranches(params?: {
  is_active?: boolean;
  disabled?: boolean;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<BranchMaster>> {
  const res = await apiClient.get<PaginatedResponse<BranchMaster>>(
    "/api/master-data/branches",
    { params },
  );
  return res.data;
}

export async function fetchWarehouses(params?: {
  bpl_id?: number;
  is_active?: boolean;
  inactive?: boolean;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<WarehouseMaster>> {
  const filtered = Object.fromEntries(
    Object.entries(params ?? {}).filter(([, v]) => v !== undefined),
  );
  const res = await apiClient.get<PaginatedResponse<WarehouseMaster>>(
    "/api/master-data/warehouses",
    { params: filtered },
  );
  return res.data;
}

/** Ops-owned fields only. Sending anything SAP owns comes back as 409. */
export async function updateBranch(
  id: string,
  payload: { is_active?: boolean; notes?: string },
): Promise<BranchMaster> {
  const res = await apiClient.put<BranchMaster>(`/api/master-data/branches/${id}`, payload);
  return res.data;
}

export async function updateWarehouse(
  id: string,
  payload: { is_active?: boolean; notes?: string },
): Promise<WarehouseMaster> {
  const res = await apiClient.put<WarehouseMaster>(`/api/master-data/warehouses/${id}`, payload);
  return res.data;
}
