import type { POStatus } from "./types";

export const STATUS_CONFIG: Record<
  POStatus,
  { label: string; color: string; bg: string; border: string }
> = {
  PENDING: {
    label: "Pending",
    color: "text-yellow-700",
    bg: "bg-yellow-50",
    border: "border-yellow-300",
  },
  STOCK_AVAILABLE: {
    label: "Stock Available",
    color: "text-green-700",
    bg: "bg-green-50",
    border: "border-green-300",
  },
  STOCK_PARTIAL: {
    label: "Partial Stock",
    color: "text-orange-700",
    bg: "bg-orange-50",
    border: "border-orange-300",
  },
  OUT_OF_STOCK: {
    label: "Out of Stock",
    color: "text-red-700",
    bg: "bg-red-50",
    border: "border-red-300",
  },
  CONFIRMED: {
    label: "Confirmed",
    color: "text-blue-700",
    bg: "bg-blue-50",
    border: "border-blue-300",
  },
  DISPATCHED: {
    label: "Dispatched",
    color: "text-purple-700",
    bg: "bg-purple-50",
    border: "border-purple-300",
  },
};

export const formatCurrency = (n: number) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(n);

export const formatDate = (d: string) =>
  new Date(d).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
