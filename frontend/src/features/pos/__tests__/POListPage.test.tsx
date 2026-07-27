import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import POListPage from "../POListPage";
import * as api from "../api";
import type { PaginatedResponse, POListItem } from "@/types";

vi.mock("../api");

const mockFetchPOs = vi.mocked(api.fetchPOs);

const basePO: POListItem = {
  id: "po-1",
  partner_code: "BLINKIT",
  partner_name: "Blinkit",
  buyer_po_number: "BL-20240101-001",
  version: 1,
  po_status: "SAP_CONFIRMED",
  issue_date: "2024-01-01T00:00:00Z",
  grand_total: "12500.00",
  currency: "INR",
  line_count: 3,
  b1_sales_order_doc_num: 1001,
  received_at: "2024-01-01T08:00:00Z",
  created_at: "2024-01-01T08:00:00Z",
  updated_at: "2024-01-01T09:00:00Z",
};

const mockPage: PaginatedResponse<POListItem> = {
  items: [basePO],
  total: 1,
  limit: 25,
  offset: 0,
};

describe("POListPage", () => {
  beforeEach(() => {
    mockFetchPOs.mockResolvedValue(mockPage);
  });

  it("renders the PO table with fetched rows", async () => {
    renderWithProviders(<POListPage />);

    // PO number and partner name are both displayed
    await waitFor(() => {
      expect(screen.getByText("BL-20240101-001")).toBeInTheDocument();
    });

    expect(screen.getByText("Blinkit")).toBeInTheDocument();
    // Money display formats as ₹12,500.00 — check the numeric part
    expect(screen.getByText(/12,500/)).toBeInTheDocument();
  });

  it("shows loading state while fetching", () => {
    mockFetchPOs.mockImplementation(() => new Promise(() => undefined));

    renderWithProviders(<POListPage />);

    // Table rows not yet present during load
    expect(screen.queryByText("BL-20240101-001")).not.toBeInTheDocument();
  });

  it("passes po_status filter from URL search params to fetchPOs", async () => {
    renderWithProviders(<POListPage />, {
      initialEntries: ["/pos?status=EXCEPTION"],
    });

    await waitFor(() => {
      expect(mockFetchPOs).toHaveBeenCalledWith(
        expect.objectContaining({ po_status: "EXCEPTION" })
      );
    });
  });

  it("shows empty state when no POs are returned", async () => {
    mockFetchPOs.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });

    renderWithProviders(<POListPage />);

    await waitFor(() => {
      expect(screen.getByText(/No purchase orders/i)).toBeInTheDocument();
    });
  });
});
