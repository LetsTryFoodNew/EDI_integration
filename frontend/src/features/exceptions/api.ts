import apiClient from "@/lib/api-client";
import type { PaginatedResponse, ExceptionItem } from "@/types";

export async function fetchExceptions(params?: {
  severity?: string;
  resolved?: boolean;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<ExceptionItem>> {
  const res = await apiClient.get<PaginatedResponse<ExceptionItem>>("/api/exceptions", { params });
  return res.data;
}

export async function resolveException(
  issueId: string,
  note: string
): Promise<ExceptionItem> {
  const res = await apiClient.post<ExceptionItem>(`/api/exceptions/${issueId}/resolve`, {
    resolution_note: note,
  });
  return res.data;
}
