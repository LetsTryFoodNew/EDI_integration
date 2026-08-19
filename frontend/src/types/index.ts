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

export interface LoginResponse {
  user: User;
  access_token: string;
  token_type: string;
  expires_in: number;
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
  received_at: string;
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
  // Present only on a SUPERSEDED PO: points at the live version that replaced it.
  current_version_id: string | null;
  current_version: number | null;
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

// Customer (parent). Fields mirror the Customer table; source_channel / gmail_label /
// b1_card_code / ack_sla_hours are integration config the middleware owns.
export interface TradingPartner {
  id: string;
  code: string;                        // customerCode
  name: string;                        // customerName
  source_channel: string;
  is_active: boolean;                  // status
  gmail_label: string | null;
  b1_card_code: string | null;
  gstin: string | null;
  pan_card: string | null;
  business_type: string | null;        // customerBusinessType
  group_name: string | null;           // customerGroupName
  phone_numbers: string[] | null;      // phoneNumber[]
  email_address: string | null;        // emailAddress
  created_at: string;
}

// Item_master — mirrors SAP B1 OITM 1:1.
export interface MaterialMaster {
  id: string;
  item_code: string;                   // OITM.ItemCode
  item_name: string | null;            // OITM.ItemName
  frgn_name: string | null;
  hsn: string | null;
  tax_rate: string | null;
  itms_grp_cod: number | null;
  items_group_name: string | null;
  invntry_uom: string | null;
  sal_unit_msr: string | null;
  vat_group_pu: string | null;
  vat_group_sa: string | null;
  case_size: number | null;
  lot_size: number | null;
  grammage: string | null;
  ean_code: string | null;
  mrp: string | null;
  frozen_for: boolean;
  valid_for: number;                   // OITM validFor, 0/1 as SAP sends it
  is_active: boolean;                  // our operational flag
}

// SKU_Mapping row nested under its parent customer.
// No mapping_status: SAP is the sole author and b1ItemCode is not-null, so every row
// is a confirmed mapping. `mrp` is joined from Item_master via the item code.
export interface CustomerSkuMapping {
  id: string;
  buyer_sku: string;                   // buyerSKUCode
  item_name: string | null;            // itemName
  b1_item_code: string;                // b1ItemCode
  unit_price: string | null;           // unitPrice
  margin: string | null;
  mrp: string | null;                  // joined from Item_master
  ean_code: string | null;             // joined from Item_master
  case_size: number | null;            // joined from Item_master
  grammage: string | null;             // joined from Item_master                  // from Item_master
  qty_per_buyer_uom: string | null;
  is_active: boolean;                  // status
  created_at: string;
  updated_at: string;
}

// Ship_to_mapping row nested under its parent customer.
export interface CustomerShipTo {
  id: string;
  dc_code: string;                     // dcCode
  warehouse_name: string | null;
  b1_whs_code: string | null;
  address: string | null;
  address_type: string[] | null;       // addressType[]
  street: string | null;
  block: string | null;
  city: string | null;
  zip_code: string | null;             // zipCode
  state: string | null;
  country: string | null;
  gst_regn_no: string | null;          // GSTRegnNO
  gst_type: string[] | null;           // gstType[]
  poc_name: string | null;
  poc_email: string | null;
  poc_phone: string | null;
  mapping_status: string;
  is_active: boolean;
}

// Bill_to_mapping row nested under its parent customer.
// Separate from CustomerShipTo because the invoicing entity and the delivery point
// are routinely different addresses — and when their states differ it is the ship-to
// state that drives CGST/SGST vs IGST, while this GSTIN is what prints on the invoice.
export interface CustomerBillTo {
  id: string;
  bill_to_code: string;
  entity_name: string | null;
  b1_bill_to_code: string | null;      // B1 business-partner address name
  address: string | null;
  address_type: string[] | null;
  street: string | null;
  block: string | null;
  city: string | null;
  zip_code: string | null;
  state: string | null;
  country: string | null;
  gst_regn_no: string | null;
  gst_type: string[] | null;
  poc_name: string | null;
  poc_email: string | null;
  poc_phone: string | null;
  mapping_status: string;
  is_active: boolean;
}

export interface CustomerDetail extends TradingPartner {
  sku_mappings: CustomerSkuMapping[];
  ship_to_mappings: CustomerShipTo[];
  bill_to_mappings: CustomerBillTo[];
}

// ── Branch Master (SAP OBPL) & Warehouse Master (SAP OWHS) ────────────────────
// Our own org structure, unlike CustomerShipTo / CustomerBillTo which describe the
// retailer's locations. There is no mapping decision here: SAP owns every business
// field, and `is_active` / `notes` are the only columns the dashboard may write.

export interface BranchMaster {
  id: string;
  bpl_id: number;                      // OBPL.BPLId — the SAP key
  bpl_name: string;                    // OBPL.BPLName
  disabled: boolean;                   // OBPL.Disabled — SAP's flag, Y/N on the wire
  address: string | null;
  street: string | null;
  block: string | null;
  city: string | null;
  zip_code: string | null;
  state: string | null;
  country: string | null;
  gstin: string | null;
  is_active: boolean;                  // ours — ops can park a branch locally
  notes: string | null;                // ours
  warehouse_count: number;             // derived
  created_at: string;
  updated_at: string;
}

export interface WarehouseMaster {
  id: string;
  whs_code: string;                    // OWHS.WhsCode — the SAP key
  whs_name: string;                    // OWHS.WhsName
  bpl_id: number;                      // resolved from the parent branch
  branch_name: string | null;          // resolved from the parent branch
  inactive: boolean;                   // OWHS.Inactive — SAP's flag
  location: number | null;             // OWHS.Location
  street: string | null;
  block: string | null;
  city: string | null;
  zip_code: string | null;
  state: string | null;
  country: string | null;
  is_active: boolean;                  // ours
  notes: string | null;                // ours
  created_at: string;
  updated_at: string;
}

export interface MasterDataSyncResult {
  created: number;
  updated: number;
  skipped: number;
  errors: string[];
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

// Mirrors app/schemas/api.py InvoiceResponse / InvoiceLineItemResponse.
// Invoices are pushed to us by SAP (POST /api/invoices); one PO can carry several,
// one per partial dispatch.
export interface InvoiceLineItem {
  id: string;
  b1_item_code: string | null;
  description: string | null;
  hsn_code: string | null;
  qty: string;
  uom: string | null;
  unit_price: string | null;
  taxable_amount: string | null;
  cgst_amount: string | null;
  sgst_amount: string | null;
  igst_amount: string | null;
  line_total: string | null;
}

export interface Invoice {
  id: string;
  po_id: string;
  asn_id: string | null;
  invoice_number: string;
  invoice_date: string;
  b1_invoice_doc_entry: number | null;
  b1_invoice_doc_num: number | null;
  irn: string | null;
  eway_bill_number: string | null;
  subtotal_amount: string | null;
  cgst_amount: string | null;
  sgst_amount: string | null;
  igst_amount: string | null;
  round_off: string | null;
  grand_total: string | null;
  status: string;
  created_at: string;
  asn_number: string | null;
  asn_status: string | null;
  outbound_status: string | null;
  line_items: InvoiceLineItem[];
}

// ── SAP push: branch / warehouse selection ────────────────────────────────────
// A B1 Sales Order needs routing the retailer's PO cannot supply. The branch is the
// from-state for place of supply, so it decides CGST+SGST vs IGST; the warehouse must
// belong to it or B1 rejects the document. Both are chosen by the operator.

export interface B1AddressOption {
  address_name: string;
  address_type: string;              // bo_ShipTo | bo_BillTo
  city: string | null;
  state: string | null;
  zip_code: string | null;
  gstin: string | null;
  matches_po: boolean;               // PIN or state matches what the PO stated
}

export interface WarehouseOption {
  whs_code: string;
  whs_name: string;
  bpl_id: number;
}

export interface BranchOption {
  bpl_id: number;
  bpl_name: string;
  state: string | null;
  gstin: string | null;
  warehouses: WarehouseOption[];
}

export interface DispatchOptions {
  po_id: string;
  buyer_po_number: string;
  partner_code: string;
  b1_card_code: string | null;
  ship_to_state: string | null;
  ship_to_pincode: string | null;
  branches: BranchOption[];
  addresses: B1AddressOption[];
  address_lookup_error: string | null;
  selected_bpl_id: number | null;
  selected_whs_code: string | null;
  selected_ship_to_code: string | null;
  selected_pay_to_code: string | null;
  /** bpl_id (as string) -> "CSGST" | "IGST" | "UNKNOWN" */
  tax_by_branch: Record<string, string>;
}

export interface SapPushSelection {
  bpl_id: number;
  whs_code: string;
  ship_to_code?: string | null;
  pay_to_code?: string | null;
}

export interface SapPreview {
  po_id: string;
  endpoint: string;
  payload: Record<string, unknown>;
  warnings: string[];
}
