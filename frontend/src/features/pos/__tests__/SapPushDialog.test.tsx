import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import SapPushDialog from "../components/SapPushDialog";
import * as api from "../api";
import type { DispatchOptions } from "@/types";

vi.mock("../api");

const mockOptions = vi.mocked(api.fetchDispatchOptions);
const mockPreview = vi.mocked(api.previewSapPayload);
const mockPush = vi.mocked(api.pushToSAPWith);

const options = (over: Partial<DispatchOptions> = {}): DispatchOptions => ({
  po_id: "po-1",
  buyer_po_number: "2264110001442",
  partner_code: "BLINKIT",
  b1_card_code: "D00086",
  ship_to_state: "MH",
  ship_to_pincode: "421302",
  branches: [
    {
      bpl_id: 1, bpl_name: "Haryana", state: "HR", gstin: "06AADCL9999Q1ZK",
      warehouses: [{ whs_code: "FG_HR", whs_name: "Finished Goods _RAI Haryana", bpl_id: 1 }],
    },
    {
      bpl_id: 5, bpl_name: "Maharashtra", state: "MH", gstin: "27AADCL9999Q1ZY",
      warehouses: [{ whs_code: "FG_MH", whs_name: "Finished Goods _Maharashtra", bpl_id: 5 }],
    },
  ],
  addresses: [
    {
      address_name: "421302-HOT", address_type: "bo_ShipTo", city: "Thane",
      state: "MH", zip_code: "421302", gstin: null, matches_po: true,
    },
  ],
  address_lookup_error: null,
  selected_bpl_id: null,
  selected_whs_code: null,
  selected_ship_to_code: null,
  selected_pay_to_code: null,
  tax_by_branch: { "1": "IGST", "5": "CSGST" },
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  mockOptions.mockResolvedValue(options());
});

function render(props: Partial<React.ComponentProps<typeof SapPushDialog>> = {}) {
  return renderWithProviders(
    <SapPushDialog poId="po-1" open onOpenChange={() => {}} {...props} />,
  );
}

describe("SapPushDialog", () => {
  it("shows the tax consequence of each branch before one is picked", async () => {
    // The whole reason the operator chooses: branch = from-state = CGST+SGST vs IGST.
    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole("combobox", { name: /branch/i }));
    expect(await screen.findByRole("option", { name: /Haryana \(HR\) — IGST/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Maharashtra \(MH\) — CGST\+SGST/ })).toBeInTheDocument();
  });

  it("nothing is preselected, so no branch is chosen by default", async () => {
    render();
    await screen.findByRole("combobox", { name: /branch/i });
    expect(screen.getByText("Select a branch…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^push to sap$/i })).toBeDisabled();
  });

  it("warehouse stays disabled until a branch is chosen", async () => {
    render();
    expect(await screen.findByRole("combobox", { name: /warehouse/i })).toBeDisabled();
  });

  it("offers only the chosen branch's warehouses", async () => {
    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole("combobox", { name: /branch/i }));
    await user.click(await screen.findByRole("option", { name: /Maharashtra/ }));
    await user.click(screen.getByRole("combobox", { name: /warehouse/i }));

    expect(await screen.findByRole("option", { name: /FG_MH/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /FG_HR/ })).not.toBeInTheDocument();
  });

  it("pushes the selected branch and warehouse", async () => {
    mockPush.mockResolvedValue({ success: true, message: "Sales Order 3000044 created" });
    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole("combobox", { name: /branch/i }));
    await user.click(await screen.findByRole("option", { name: /Maharashtra/ }));
    await user.click(screen.getByRole("combobox", { name: /warehouse/i }));
    await user.click(await screen.findByRole("option", { name: /FG_MH/ }));
    await user.click(screen.getByRole("button", { name: /^push to sap$/i }));

    await waitFor(() =>
      expect(mockPush).toHaveBeenCalledWith("po-1", {
        bpl_id: 5, whs_code: "FG_MH", ship_to_code: null, pay_to_code: null,
      }),
    );
  });

  it("previews the payload without sending it", async () => {
    mockPreview.mockResolvedValue({
      po_id: "po-1",
      endpoint: "/b1s/v2/Orders",
      payload: { CardCode: "D00086", DocumentLines: [{ VatGroup: "CSGST@5" }] },
      warnings: [],
    });
    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole("combobox", { name: /branch/i }));
    await user.click(await screen.findByRole("option", { name: /Maharashtra/ }));
    await user.click(screen.getByRole("combobox", { name: /warehouse/i }));
    await user.click(await screen.findByRole("option", { name: /FG_MH/ }));
    await user.click(screen.getByRole("button", { name: /preview payload/i }));

    expect(await screen.findByText(/CSGST@5/)).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("surfaces a SAP address-lookup failure without blocking the push", async () => {
    mockOptions.mockResolvedValue(
      options({ address_lookup_error: "Could not read addresses from SAP: timeout" }),
    );
    render();

    expect(await screen.findByText(/Could not read addresses from SAP/)).toBeInTheDocument();
    // Branch and warehouse are still selectable — the address is optional.
    expect(screen.getByRole("combobox", { name: /branch/i })).toBeEnabled();
  });

  it("reuses the selection from a previous attempt", async () => {
    mockOptions.mockResolvedValue(
      options({ selected_bpl_id: 5, selected_whs_code: "FG_MH" }),
    );
    render();

    // Scoped to the triggers: Base UI keeps the options mounted, so the same label
    // text also exists in the popup. Assert the trigger shows a readable label rather
    // than the raw value ("5", "__none__").
    const branchTrigger = await screen.findByRole("combobox", { name: /branch/i });
    await waitFor(() => expect(branchTrigger).toHaveTextContent("5 · Maharashtra (MH)"));
    expect(screen.getByRole("combobox", { name: /warehouse/i }))
      .toHaveTextContent("FG_MH · Finished Goods _Maharashtra");
    expect(screen.getByRole("button", { name: /^push to sap$/i })).toBeEnabled();
  });
});
