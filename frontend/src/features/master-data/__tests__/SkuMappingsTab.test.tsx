import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import MasterDataPage from "../MasterDataPage";
import * as api from "../api";
import type { PaginatedResponse, SkuMapping, TradingPartner, MaterialMaster, ShipToMapping } from "@/types";

vi.mock("../api");

const mockFetchSkuMappings = vi.mocked(api.fetchSkuMappings);
const mockFetchPartners = vi.mocked(api.fetchPartners);
const mockFetchMaterials = vi.mocked(api.fetchMaterials);
const mockFetchShipToMappings = vi.mocked(api.fetchShipToMappings);
const mockUpdateSkuMapping = vi.mocked(api.updateSkuMapping);

const unmappedSku: SkuMapping = {
  id: "sku-1",
  partner_code: "BLINKIT",
  buyer_sku: "BL-SKU-9001",
  buyer_sku_description: "Butter Chicken Paste 200g",
  b1_item_code: null,
  qty_per_buyer_uom: null,
  mapping_status: "UNMAPPED",
  confidence_score: null,
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

const emptyList = <T,>(limit = 50): PaginatedResponse<T> => ({
  items: [],
  total: 0,
  limit,
  offset: 0,
});

describe("SkuMappingsTab (via MasterDataPage)", () => {
  beforeEach(() => {
    mockFetchSkuMappings.mockResolvedValue({
      items: [unmappedSku],
      total: 1,
      limit: 50,
      offset: 0,
    });
    mockFetchPartners.mockResolvedValue(emptyList<TradingPartner>());
    mockFetchMaterials.mockResolvedValue(emptyList<MaterialMaster>());
    mockFetchShipToMappings.mockResolvedValue(emptyList<ShipToMapping>(100));
    mockUpdateSkuMapping.mockResolvedValue({
      ...unmappedSku,
      b1_item_code: "LTF-BUTTER-200",
      mapping_status: "MANUALLY_MAPPED",
    });
  });

  it("renders the unmapped SKU row in the table", async () => {
    renderWithProviders(<MasterDataPage />);

    await waitFor(() => {
      expect(screen.getByText("BL-SKU-9001")).toBeInTheDocument();
    });

    expect(screen.getByText("UNMAPPED")).toBeInTheDocument();
  });

  it("shows inline edit inputs when Edit is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MasterDataPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Edit" }));

    // Two inputs appear: b1_item_code (autofocus) and qty_per_buyer_uom
    const inputs = screen.getAllByRole("textbox");
    expect(inputs.length).toBeGreaterThanOrEqual(2);
  });

  it("calls updateSkuMapping with the entered value when Save is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MasterDataPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Edit" }));

    // Three textboxes exist after edit mode: search ("Search buyer SKU…"),
    // b1_item_code (no placeholder), qty_per_buyer_uom (placeholder "1").
    // Find b1_item_code by the absence of a placeholder.
    const b1Input = screen.getAllByRole("textbox").find(
      (input) => !input.getAttribute("placeholder")
    )!;
    await user.clear(b1Input);
    await user.type(b1Input, "LTF-BUTTER-200");

    // Save button has no text (icon only); cancel button has text "✕"
    // Scope to the action cell (div.flex.gap-1) that holds Save + ✕
    const cancelBtn = screen.getByRole("button", { name: "✕" });
    const actionCell = cancelBtn.parentElement!;
    const buttons = actionCell.querySelectorAll("button");
    // First button in the cell is Save, second is ✕
    await user.click(buttons[0]);

    await waitFor(() => {
      expect(mockUpdateSkuMapping).toHaveBeenCalledWith("sku-1", {
        b1_item_code: "LTF-BUTTER-200",
        qty_per_buyer_uom: undefined,
      });
    });
  });
});
