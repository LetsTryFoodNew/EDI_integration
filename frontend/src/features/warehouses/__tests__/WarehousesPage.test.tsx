import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import WarehousesPage from "../WarehousesPage";
import * as api from "../api";
import type { BranchMaster, PaginatedResponse, WarehouseMaster } from "@/types";

vi.mock("../api");

const mockFetchBranches = vi.mocked(api.fetchBranches);
const mockFetchWarehouses = vi.mocked(api.fetchWarehouses);
const mockUpdateWarehouse = vi.mocked(api.updateWarehouse);

const branch = (over: Partial<BranchMaster> = {}): BranchMaster => ({
  id: "br-1",
  bpl_id: 1,
  bpl_name: "Let's Try Foods — Mumbai (HO)",
  disabled: false,
  address: "Unit 5, Andheri Industrial Estate, Mumbai 400053",
  street: "Unit 5, Andheri Industrial Estate",
  block: "Andheri West",
  city: "Mumbai",
  zip_code: "400053",
  state: "Maharashtra",
  country: "India",
  gstin: "27AADCL9999Q1ZY",
  is_active: true,
  notes: null,
  warehouse_count: 2,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  ...over,
});

const warehouse = (over: Partial<WarehouseMaster> = {}): WarehouseMaster => ({
  id: "wh-1",
  whs_code: "MUM-FG",
  whs_name: "Mumbai Finished Goods",
  bpl_id: 1,
  branch_name: "Let's Try Foods — Mumbai (HO)",
  inactive: false,
  location: 1,
  street: "Unit 5, Andheri Industrial Estate",
  block: "Andheri West",
  city: "Mumbai",
  zip_code: "400053",
  state: "Maharashtra",
  country: "India",
  is_active: true,
  notes: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  ...over,
});

function page<T>(items: T[]): PaginatedResponse<T> {
  return { items, total: items.length, limit: 500, offset: 0 };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockFetchBranches.mockResolvedValue(page([branch()]));
  mockFetchWarehouses.mockResolvedValue(page([warehouse()]));
});

describe("WarehousesPage", () => {
  it("lists warehouses with their parent branch", async () => {
    renderWithProviders(<WarehousesPage />);

    expect(await screen.findByText("MUM-FG")).toBeInTheDocument();
    expect(screen.getByText("Mumbai Finished Goods")).toBeInTheDocument();
    expect(screen.getByText("BPLId 1")).toBeInTheDocument();
  });

  it("shows SAP's flag and ours as separate columns", async () => {
    // The point of the screen: a warehouse can be live in SAP yet parked by us.
    mockFetchWarehouses.mockResolvedValue(
      page([warehouse({ inactive: false, is_active: false, notes: "Dock under repair" })]),
    );
    renderWithProviders(<WarehousesPage />);

    expect(await screen.findByText("Live")).toBeInTheDocument();     // SAP
    expect(screen.getByText("Parked")).toBeInTheDocument();          // ours
    expect(screen.getByText("Dock under repair")).toBeInTheDocument();
  });

  it("marks a SAP-deactivated warehouse as Inactive", async () => {
    mockFetchWarehouses.mockResolvedValue(page([warehouse({ inactive: true })]));
    renderWithProviders(<WarehousesPage />);

    expect(await screen.findByText("Inactive")).toBeInTheDocument();
    expect(screen.getByText("In use")).toBeInTheDocument();
  });

  it("parks a warehouse through the ops-only PUT", async () => {
    mockUpdateWarehouse.mockResolvedValue(warehouse({ is_active: false }));
    const user = userEvent.setup();
    renderWithProviders(<WarehousesPage />);

    await user.click(await screen.findByRole("button", { name: /park this record/i }));

    await waitFor(() =>
      // Only is_active — sending a SAP-owned field would come back 409.
      expect(mockUpdateWarehouse).toHaveBeenCalledWith("wh-1", { is_active: false }),
    );
  });

  it("renders an empty state rather than a blank table", async () => {
    mockFetchWarehouses.mockResolvedValue(page([]));
    renderWithProviders(<WarehousesPage />);

    expect(await screen.findByText("No warehouses")).toBeInTheDocument();
  });

  it("surfaces a load failure", async () => {
    mockFetchWarehouses.mockRejectedValue(new Error("boom"));
    renderWithProviders(<WarehousesPage />);

    expect(await screen.findByText(/failed to load warehouses/i)).toBeInTheDocument();
  });

  it("shows branches on the Branches tab, with a disabled one flagged", async () => {
    mockFetchBranches.mockResolvedValue(
      page([
        branch(),
        branch({
          id: "br-2", bpl_id: 5, bpl_name: "Hyderabad",
          disabled: true, gstin: "36AADCL9999Q1ZC",
        }),
      ]),
    );
    const user = userEvent.setup();
    renderWithProviders(<WarehousesPage />);

    await user.click(screen.getByRole("tab", { name: /branches/i }));

    expect(await screen.findByText("Hyderabad")).toBeInTheDocument();
    expect(screen.getByText("27AADCL9999Q1ZY")).toBeInTheDocument();
    expect(screen.getByText("Disabled")).toBeInTheDocument();
  });
});
