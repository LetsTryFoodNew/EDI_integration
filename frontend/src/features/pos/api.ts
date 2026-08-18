import apiClient from "@/lib/api-client";
import type { Invoice, PaginatedResponse, POListItem, PODetail } from "@/types";

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

export async function fetchPOInvoices(poId: string): Promise<Invoice[]> {
  const res = await apiClient.get<Invoice[]>(`/api/pos/${poId}/invoices`);
  return res.data;
}

export interface InvoiceAsnActionResult {
  invoice_id: string;
  asn_number: string;
  queued: boolean;
  validation_override: boolean;
  message: string;
}

export async function sendInvoiceAsn(invoiceId: string): Promise<InvoiceAsnActionResult> {
  const res = await apiClient.post<InvoiceAsnActionResult>(`/api/invoices/${invoiceId}/send-asn`);
  return res.data;
}

/**
 * Download the invoice PDF.
 *
 * Fetched through the axios instance rather than a plain <a href> so the request
 * carries auth the same way every other call does — the endpoint is protected, and a
 * bare link would land on the login page instead of a file.
 */
export async function downloadInvoicePdf(invoiceId: string, invoiceNumber: string): Promise<void> {
  const res = await apiClient.get(`/api/invoices/${invoiceId}/pdf`, { responseType: "blob" });
  const url = URL.createObjectURL(new Blob([res.data], { type: "application/pdf" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `invoice-${invoiceNumber.replace(/[/\\]/g, "-")}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
