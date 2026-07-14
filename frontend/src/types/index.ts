// TypeScript types mirroring app/schemas/api.py Pydantic models

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export interface User {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
}

// ── POs ───────────────────────────────────────────────────────────────────────

export interface POListItem {
  id: string;
  partner_code: string;
  partner_name: string;
  buyer_po_number: string;
  version: number;
  po_status: POStatus;
  issue_date: string | null;
  grand_total: string | null;
  currency: string;
  line_count: number;
  b1_sales_order_doc_num: number | null;
  created_at: string;
  updated_at: string;
}

export type POStatus =
  | "RAW"
  | "PARSED"
  | "VALIDATED"
  | "EXCEPTION"
  | "SAP_PENDING"
  | "SAP_CONFIRMED"
  | "SAP_REJECTED"
  | "CANCELLED"
  | "SUPERSEDED";

export interface POLineItem {
  id: string;
  line_number: number;
  buyer_sku: string;
  description: string | null;
  ordered_qty: string | null;
  uom: string | null;
  unit_price: string | null;
  line_total: string | null;
  taxable_amount: string | null;
  cgst_amount: string | null;
  sgst_amount: string | null;
  igst_amount: string | null;
  hsn_code: string | null;
  sap_material_no: string | null;
  mapping_status: string | null;
}

export interface ValidationIssue {
  id: string;
  issue_code: string;
  severity: "ERROR" | "WARNING" | "INFO";
  field_name: string | null;
  message: string;
  resolution_note: string | null;
  resolved_at: string | null;
  created_at: string;
}

export interface B1PushHistoryItem {
  id: string;
  http_method: string;
  endpoint: string;
  http_status: number | null;
  success: boolean;
  error_code: string | null;
  error_message: string | null;
  duration_ms: number | null;
  created_at: string;
}

export interface OutboundMessage {
  id: string;
  doc_type: string;
  status: string;
  channel: string;
  attempt_count: number;
  external_reference: string | null;
  ack_received_at: string | null;
  next_retry_at: string | null;
  error_message: string | null;
  created_at: string;
}

export interface PODetail {
  id: string;
  partner_code: string;
  partner_name: string;
  buyer_po_number: string;
  version: number;
  po_status: POStatus;
  source_channel: string;
  issue_date: string | null;
  delivery_date: string | null;
  ship_to_code: string | null;
  ship_to_name: string | null;
  buyer_gstin: string | null;
  seller_gstin: string | null;
  grand_total: string | null;
  currency: string;
  b1_sales_order_doc_entry: number | null;
  b1_sales_order_doc_num: number | null;
  raw_message_id: string | null;
  created_at: string;
  updated_at: string;
  lines: POLineItem[];
  validation_issues: ValidationIssue[];
  b1_push_history: B1PushHistoryItem[];
  outbound_messages: OutboundMessage[];
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export interface PartnerStat {
  partner_code: string;
  partner_name: string;
  po_count: number;
  error_count: number;
}

export interface DashboardToday {
  total_pos: number;
  confirmed_pos: number;
  exception_pos: number;
  pending_b1_push: number;
  partner_stats: PartnerStat[];
  last_updated: string;
}

export interface SLABreachItem {
  po_id: string;
  buyer_po_number: string;
  partner_code: string;
  po_status: string;
  hours_overdue: number;
  created_at: string;
}

export interface UnmappedSkuItem {
  buyer_sku: string;
  partner_code: string;
  description: string | null;
  occurrence_count: number;
  last_seen: string;
}

export interface ActivityItem {
  entity_type: string;
  entity_id: string;
  description: string;
  status: string;
  created_at: string;
}

// ── Exceptions ────────────────────────────────────────────────────────────────

export interface ExceptionItem {
  id: string;
  po_id: string;
  buyer_po_number: string;
  partner_code: string;
  issue_code: string;
  severity: "ERROR" | "WARNING" | "INFO";
  field_name: string | null;
  message: string;
  resolution_note: string | null;
  resolved_at: string | null;
  created_at: string;
}

// ── Master Data ───────────────────────────────────────────────────────────────

export interface TradingPartner {
  id: string;
  code: string;
  name: string;
  source_channel: string;
  is_active: boolean;
  gmail_label: string | null;
  b1_card_code: string | null;
  ack_sla_hours: number | null;
  created_at: string;
}

export interface MaterialMaster {
  id: string;
  b1_item_code: string;
  description: string | null;
  hsn_code: string | null;
  uom: string | null;
  is_active: boolean;
}

export interface SkuMapping {
  id: string;
  trading_partner_id: string;
  partner_code: string;
  buyer_sku: string;
  material_id: string | null;
  b1_item_code: string | null;
  qty_per_buyer_uom: string | null;
  mapping_status: string;
  confidence_score: number | null;
  notes: string | null;
  created_at: string;
}

export interface ShipToMapping {
  id: string;
  trading_partner_id: string;
  partner_code: string;
  buyer_whs_code: string;
  b1_whs_code: string | null;
  is_active: boolean;
}

// ── B1 Logs ───────────────────────────────────────────────────────────────────

export interface B1LogListItem {
  id: string;
  po_id: string | null;
  http_method: string;
  endpoint: string;
  http_status: number | null;
  success: boolean;
  error_code: string | null;
  error_message: string | null;
  duration_ms: number | null;
  created_at: string;
}

export interface B1LogDetail extends B1LogListItem {
  request_payload: Record<string, unknown> | null;
  response_payload: Record<string, unknown> | null;
}
