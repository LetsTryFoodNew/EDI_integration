import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import ExceptionsPage from "../ExceptionsPage";
import * as api from "../api";
import type { PaginatedResponse, ExceptionItem } from "@/types";

vi.mock("../api");

const mockFetchExceptions = vi.mocked(api.fetchExceptions);
const mockResolveException = vi.mocked(api.resolveException);

const errorException: ExceptionItem = {
  id: "exc-1",
  po_id: "po-1",
  buyer_po_number: "BL-20240101-001",
  partner_code: "BLINKIT",
  issue_code: "E001_SKU_UNMAPPED",
  severity: "ERROR",
  field_name: "buyer_sku",
  message: "SKU BL-9001 is not mapped to any internal item code.",
  resolution_note: null,
  resolved_at: null,
  created_at: "2024-01-01T08:00:00Z",
};

const mockExceptions: PaginatedResponse<ExceptionItem> = {
  items: [errorException],
  total: 1,
  limit: 200,
  offset: 0,
};

describe("ExceptionsPage", () => {
  beforeEach(() => {
    mockFetchExceptions.mockResolvedValue(mockExceptions);
    mockResolveException.mockResolvedValue({
      ...errorException,
      resolved_at: "2024-01-02T10:00:00Z",
      resolution_note: "Mapped to LTF-BUTTER-200",
    });
  });

  it("renders exception items grouped by severity", async () => {
    renderWithProviders(<ExceptionsPage />);

    await waitFor(() => {
      expect(
        screen.getByText("SKU BL-9001 is not mapped to any internal item code.")
      ).toBeInTheDocument();
    });

    expect(screen.getByText("E001_SKU_UNMAPPED")).toBeInTheDocument();
    expect(screen.getByText("BL-20240101-001")).toBeInTheDocument();
  });

  it("opens resolve dialog when Resolve button is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ExceptionsPage />);

    // Wait for the exception item to load
    await waitFor(() => {
      expect(screen.getByText("E001_SKU_UNMAPPED")).toBeInTheDocument();
    });

    // Use exact name "Resolve" to avoid matching "Show resolved" toggle button
    await user.click(screen.getByRole("button", { name: "Resolve" }));

    // Dialog title and textarea should appear after click
    await waitFor(() => {
      expect(screen.getByText("Resolve exception")).toBeInTheDocument();
    });
    expect(screen.getByPlaceholderText(/resolution note/i)).toBeInTheDocument();
  });

  it("calls resolveException with note and re-fetches on submit", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ExceptionsPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Resolve" })).toBeInTheDocument();
    });

    // Open dialog
    await user.click(screen.getByRole("button", { name: "Resolve" }));
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/resolution note/i)).toBeInTheDocument();
    });

    // Type resolution note
    await user.type(
      screen.getByPlaceholderText(/resolution note/i),
      "Mapped to LTF-BUTTER-200"
    );

    // Submit — "Mark resolved" button is inside the dialog footer
    await user.click(screen.getByRole("button", { name: "Mark resolved" }));

    await waitFor(() => {
      expect(mockResolveException).toHaveBeenCalledWith(
        "exc-1",
        "Mapped to LTF-BUTTER-200"
      );
    });

    // Query should be invalidated and re-fetched after success (at least one extra call)
    await waitFor(() => {
      expect(mockFetchExceptions.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("shows empty state when no exceptions exist", async () => {
    mockFetchExceptions.mockResolvedValue({
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
    });

    renderWithProviders(<ExceptionsPage />);

    await waitFor(() => {
      expect(screen.getByText("No exceptions")).toBeInTheDocument();
    });
  });
});
