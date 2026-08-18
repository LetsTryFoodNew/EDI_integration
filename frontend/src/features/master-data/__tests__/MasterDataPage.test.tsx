import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import MasterDataPage from "../MasterDataPage";
import * as api from "../api";
import type {
  PaginatedResponse,
  TradingPartner,
  MaterialMaster,
  CustomerDetail,
} from "@/types";

vi.mock("../api");

const mockFetchCustomers = vi.mocked(api.fetchCustomers);
const mockFetchCustomerDetail = vi.mocked(api.fetchCustomerDetail);
const mockFetchItems = vi.mocked(api.fetchItems);

const customer: TradingPartner = {
  id: "cust-1",
  code: "BLINKIT",
  name: "Blinkit (Grofers India Pvt Ltd)",
  source_channel: "WEBHOOK",
  is_active: true,
  gmail_label: null,
  b1_card_code: "C00012",
  gstin: "27AAECG1234K1Z5",
  business_type: "Quick Commerce",
  group_name: "Modern Trade",
  phone_numbers: ["+919812345678"],
  email_address: "vendors@blinkit.com",
  pan_card: "AAECG1234K",
  created_at: "2026-01-01T00:00:00Z",
};

const customerDetail: CustomerDetail = {
  ...customer,
  sku_mappings: [
    {
      id: "sku-1",
      buyer_sku: "8901234560001",
      item_name: "Peri Peri Makhana 30g",
      b1_item_code: "LTFM001",
      unit_price: "32.50",
      margin: "35.0",
      mrp: "50.00",
      ean_code: "8901234567890",
      case_size: 24,
      grammage: "30g",
      qty_per_buyer_uom: "1",
      is_active: true,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-07-18T00:00:00Z",
    },
  ],
  ship_to_mappings: [
    {
      id: "st-1",
      dc_code: "BL-MUM-001",
      warehouse_name: "Blinkit Mumbai DC",
      b1_whs_code: "WH01",
      address: "Plot 14, MIDC, Andheri East, Mumbai 400093",
      address_type: ["SHIP_TO"],
      street: "Plot 14, MIDC",
      block: "Andheri East",
      city: "Mumbai",
      zip_code: "400093",
      state: "Maharashtra",
      country: "India",
      gst_regn_no: "27AAECG1234K1Z5",
      gst_type: ["Regular"],
      poc_name: "Rakesh Sharma",
      poc_email: "rakesh.s@blinkit.com",
      poc_phone: "+919812345601",
      mapping_status: "MANUALLY_MAPPED",
      is_active: true,
    },
  ],
  bill_to_mappings: [
    {
      id: "bt-1",
      bill_to_code: "BL-HO",
      entity_name: "Blink Commerce Private Limited",
      b1_bill_to_code: "BILLTO-HO",
      address: "6th Floor, Tower A, Gurugram 122002",
      address_type: ["BILL_TO"],
      street: "Tower A",
      block: "Sector 32",
      city: "Gurugram",
      zip_code: "122002",
      state: "Haryana",
      country: "India",
      gst_regn_no: "06AAECG1234K1Z3",
      gst_type: ["Regular"],
      poc_name: "Accounts Payable",
      poc_email: "ap@blinkit.com",
      poc_phone: "+919812345699",
      mapping_status: "MANUALLY_MAPPED",
      is_active: true,
    },
  ],
};

const item: MaterialMaster = {
  id: "item-1",
  item_code: "LTFM001",
  item_name: "Peri Peri Makhana 30g",
  frgn_name: null,
  hsn: "20089900",
  tax_rate: "12.00",
  itms_grp_cod: 103,
  items_group_name: "Makhana",
  invntry_uom: "PCS",
  sal_unit_msr: "CASE",
  vat_group_pu: "GST12",
  vat_group_sa: "GST12",
  case_size: 24,
  lot_size: 24,
  grammage: "30g",
  ean_code: "8901234560001",
  mrp: "50.00",
  frozen_for: false,
  valid_for: 1,
  is_active: true,
};

function page<T>(items: T[]): PaginatedResponse<T> {
  return { items, total: items.length, limit: 100, offset: 0 };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockFetchCustomers.mockResolvedValue(page([customer]));
  mockFetchCustomerDetail.mockResolvedValue(customerDetail);
  mockFetchItems.mockResolvedValue(page([item]));
});

describe("MasterDataPage", () => {
  it("shows only the Customers and Item Master tabs", async () => {
    renderWithProviders(<MasterDataPage />);
    expect(await screen.findByRole("tab", { name: /customers/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /item master/i })).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(2);
  });

  it("lists customers with their Customer-table fields", async () => {
    renderWithProviders(<MasterDataPage />);
    expect(await screen.findByText("BLINKIT")).toBeInTheDocument();
    expect(screen.getByText("Quick Commerce")).toBeInTheDocument();
    expect(screen.getByText("Modern Trade")).toBeInTheDocument();
  });

  it("loads SKU mappings and ship-to addresses when a customer row is expanded", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MasterDataPage />);

    await user.click(await screen.findByText("BLINKIT"));

    await waitFor(() => expect(mockFetchCustomerDetail).toHaveBeenCalledWith("cust-1"));

    // SKU mappings shown by default
    expect(await screen.findByText("8901234560001")).toBeInTheDocument();
    expect(screen.getByText("LTFM001")).toBeInTheDocument();

    // Switch to the ship-to sub-tab
    await user.click(screen.getByRole("button", { name: /ship-to addresses/i }));
    expect(await screen.findByText("BL-MUM-001")).toBeInTheDocument();
    expect(screen.getByText("Maharashtra")).toBeInTheDocument();
  });

  it("shows bill-to addresses on their own sub-tab, separate from ship-to", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MasterDataPage />);

    await user.click(await screen.findByText("BLINKIT"));
    await waitFor(() => expect(mockFetchCustomerDetail).toHaveBeenCalledWith("cust-1"));

    await user.click(screen.getByRole("button", { name: /bill-to addresses/i }));

    expect(await screen.findByText("BL-HO")).toBeInTheDocument();
    expect(screen.getByText("Blink Commerce Private Limited")).toBeInTheDocument();
    // The billing GSTIN is a different registration from the ship-to one — that
    // distinction is the whole reason bill-to is a separate table.
    expect(screen.getByText("06AAECG1234K1Z3")).toBeInTheDocument();
    // Ship-to rows must not bleed into this tab.
    expect(screen.queryByText("BL-MUM-001")).not.toBeInTheDocument();
  });

  it("renders item master rows using the Item_master field names", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MasterDataPage />);

    await user.click(screen.getByRole("tab", { name: /item master/i }));

    expect(await screen.findByText("LTFM001")).toBeInTheDocument();
    expect(screen.getByText("20089900")).toBeInTheDocument();
    expect(screen.getByText("GST12 / GST12")).toBeInTheDocument();
  });
});
