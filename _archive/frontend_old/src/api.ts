import axios from "axios";

export const API_BASE = (import.meta.env.VITE_API_URL as string) ?? "http://localhost:8000/api";

const api = axios.create({ baseURL: API_BASE });
export default api;

// Dashboard
export const getDashboard = () => api.get("/dashboard");

// Companies
export const getCompanies = () => api.get("/companies");
export const createCompany = (data: any) => api.post("/companies", data);
export const updateCompanyIntegration = (id: number, data: any) =>
  api.patch(`/companies/${id}/integration`, data);

// Products
export const getProducts = () => api.get("/products");
export const createProduct = (data: any) => api.post("/products", data);
export const updateProduct = (id: number, data: any) => api.patch(`/products/${id}`, data);

// Purchase Orders
export const getPurchaseOrders = (params?: any) => api.get("/purchase-orders", { params });
export const getPurchaseOrder = (id: number) => api.get(`/purchase-orders/${id}`);
export const createPO = (data: any) => api.post("/purchase-orders", data);
export const updatePOStatus = (id: number, status: string) =>
  api.patch(`/purchase-orders/${id}/status`, { status });

// Webhooks
export const getWebhookLogs = (params?: any) => api.get("/webhook/logs", { params });
export const simulateWebhook = (partnerCode: string) =>
  api.post(`/webhook/simulate/${partnerCode}`);

// ASN
export const getASNRecords = (params?: any) => api.get("/asn", { params });
export const createASN = (data: any) => api.post("/asn", data);
export const syncASN = (id: number) => api.post(`/asn/${id}/sync`);

// Product Mappings
export const getProductMappings = (params?: any) => api.get("/product-mappings", { params });
export const createProductMapping = (data: any) => api.post("/product-mappings", data);
export const deleteProductMapping = (id: number) => api.delete(`/product-mappings/${id}`);
export const resolveProductSKU = (params: { partner_code: string; partner_sku: string; partner_name?: string }) =>
  api.get("/product-mappings/resolve", { params });

// SAP Sales Orders
export const getSAPOrders = (params?: any) => api.get("/sap-orders", { params });
export const getSAPOrder = (sapOrderId: string) => api.get(`/sap-orders/${sapOrderId}`);
export const createSAPOrderFromPO = (poId: number) =>
  api.post(`/sap-orders/create-from-po/${poId}`);

// Unmapped SKU Alerts
export const getUnmappedSKUs = (params?: any) => api.get("/unmapped-skus", { params });
export const resolveUnmappedSKU = (alertId: number, data: { product_id: number; resolution_notes?: string }) =>
  api.post(`/unmapped-skus/${alertId}/resolve`, data);

// ── Zepto Silk Route ───────────────────────────────────────────────────────────
export const getZeptoHealth = () => api.get("/zepto/health");
export const getZeptoConnectionInfo = () => api.get("/zepto/connection-info");

export const getZeptoPOEvents = (params?: {
  days?: number;
  vendor_codes?: string;
  po_codes?: string;
  include_all_po_events?: boolean;
  include_line_item_details?: boolean;
  page_size?: number;
  page_number?: number;
}) => api.get("/zepto/po-events", { params });

export const getZeptoASNs = (po_code: string, params?: { page_size?: number; page_number?: number }) =>
  api.get("/zepto/asn", { params: { po_code, ...params } });

export const createZeptoASN = (payload: any) => api.post("/zepto/asn", payload);

export const cancelZeptoASN = (asn_number: string) => api.delete(`/zepto/asn/${asn_number}`);

// Returns { po_code, allocations: { skuCode: allocatedQty } } — our own DB tracking
// because Zepto's list_asns API never returns per-SKU breakdowns.
export const getZeptoPOSKUAllocations = (po_code: string) =>
  api.get(`/zepto/po/${encodeURIComponent(po_code)}/sku-allocations`);

export const requestZeptoPOAmendment = (po_number: string, payload: any) =>
  api.post(`/zepto/po/${po_number}/amendment`, payload);

// ── Blinkit Vendor API ────────────────────────────────────────────────────────
export const getBlinkitHealth = () => api.get("/blinkit/health");
export const getBlinkitConnectionInfo = () => api.get("/blinkit/connection-info");

export const getBlinkitPOs = () => api.get("/blinkit/pos");

export const getBlinkitPO = (po_number: string) =>
  api.get(`/blinkit/pos/${encodeURIComponent(po_number)}`);

export const createBlinkitASN = (payload: any) =>
  api.post("/blinkit/asn", payload);

export const getBlinkitASNs = (po_number: string) =>
  api.get("/blinkit/asn", { params: { po_number } });

export const getBlinkitPOSKUAllocations = (po_number: string) =>
  api.get(`/blinkit/po/${encodeURIComponent(po_number)}/sku-allocations`);

export const cancelBlinkitASN = (asn_id: string, reason?: string) =>
  api.delete(`/blinkit/asn/${encodeURIComponent(asn_id)}`, {
    params: reason ? { reason } : undefined,
  });

export const requestBlinkitPOAmendment = (po_number: string, payload: any) =>
  api.post(`/blinkit/po/${encodeURIComponent(po_number)}/amendment`, payload);
