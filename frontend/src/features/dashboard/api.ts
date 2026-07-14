import apiClient from "@/lib/api-client";
import type { DashboardToday, SLABreachItem, UnmappedSkuItem, ActivityItem } from "@/types";

export async function fetchDashboardToday(): Promise<DashboardToday> {
  const res = await apiClient.get<DashboardToday>("/api/dashboard/today");
  return res.data;
}

export async function fetchSLABreaches(): Promise<SLABreachItem[]> {
  const res = await apiClient.get<SLABreachItem[]>("/api/dashboard/sla-breaches");
  return res.data;
}

export async function fetchUnmappedSkus(): Promise<UnmappedSkuItem[]> {
  const res = await apiClient.get<UnmappedSkuItem[]>("/api/dashboard/unmapped-skus");
  return res.data;
}

export async function fetchActivity(): Promise<ActivityItem[]> {
  const res = await apiClient.get<ActivityItem[]>("/api/dashboard/activity");
  return res.data;
}
