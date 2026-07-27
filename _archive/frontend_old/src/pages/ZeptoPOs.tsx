import { Fragment, useEffect, useState } from "react";
import {
  ClipboardList, RefreshCcw, Wifi, WifiOff, Clock, ChevronDown, ChevronRight,
  FileText, Plus, PackageCheck, CheckCircle, XCircle, AlertCircle, Loader2,
  Pencil, Trash2,
} from "lucide-react";
import { getZeptoPOEvents, getZeptoHealth, createZeptoASN, cancelZeptoASN, getZeptoASNs, getZeptoPOSKUAllocations, requestZeptoPOAmendment, API_BASE } from "../api";
import type { ZeptoPO, ZeptoPOLineItem, ZeptoPOListData, ZeptoASN, ZeptoASNListData } from "../types";

const PO_STATUS_STYLE: Record<string, string> = {
  RELEASED:  "bg-green-100 text-green-700 border-green-300",
  OPEN:      "bg-yellow-100 text-yellow-700 border-yellow-300",
  LOCKED:    "bg-violet-100 text-violet-700 border-violet-300",
  EXPIRED:   "bg-gray-100 text-gray-600 border-gray-300",
  CANCELLED: "bg-red-100 text-red-700 border-red-300",
  CLOSED:    "bg-blue-100 text-blue-700 border-blue-300",
};

const EVENT_BADGE: Record<string, string> = {
  CreatePO: "bg-indigo-50 text-indigo-700",
  UpdatePO: "bg-amber-50 text-amber-700",
  CancelPO: "bg-red-50 text-red-700",
};

interface ASNLineForm {
  skuCode: string;
  materialCode: string;
  productName: string;
  orderedQty: number;
  invoicedQty: number;
  mrp: number;
  basePrice: number;
  batchNumber: string;
  expiryDate: string;
}

interface CreateASNForm {
  invoiceNumber: string;
  invoiceDate: string;
  deliveryDate: string;
  lines: ASNLineForm[];
}

// ── Amendment form types ──────────────────────────────────────────────────────
const PO_LEVEL_ATTRS   = ["EXPIRY_DATE", "VENDOR_GSTIN"];
const ITEM_LEVEL_ATTRS = ["MRP", "BASE_PRICE", "EAN", "CASE_SIZE", "EXPIRY_DATE"];

interface AmendRow {
  id: string;
  attributeName: string;
  previousValue: string;
  recommendedValue: string;
  reasonForAmendment: string;
}

interface ItemAmend {
  skuCode: string;
  materialCode: string;
  productName: string;
  ean: string;
  rows: AmendRow[];
}

interface AmendForm {
  poRows: AmendRow[];
  items: ItemAmend[];
}

function emptyRow(): AmendRow {
  return { id: `${Date.now()}-${Math.random()}`, attributeName: "", previousValue: "", recommendedValue: "", reasonForAmendment: "" };
}

function lineFromItem(item: ZeptoPOLineItem): ASNLineForm {
  return {
    skuCode:      item.skuCode,
    materialCode: item.materialCode ?? item.matetrialCode ?? "",
    productName:  item.productName,
    orderedQty:   item.quantity,
    invoicedQty:  item.quantity,
    mrp:          item.mrp,
    basePrice:    item.costPrice,
    batchNumber:  "",
    expiryDate:   "",
  };
}

// Build a SKU → allocated-qty map from itemDetails across all existing ASNs.
// Returns an empty map when Zepto didn't return item-level data.
function computeAllocatedBySku(existing: ZeptoASN[]): Record<string, number> {
  const map: Record<string, number> = {};
  for (const asn of existing) {
    for (const item of asn.itemDetails ?? []) {
      const sku = item.skuCode ?? "";
      if (sku) map[sku] = (map[sku] ?? 0) + (item.invoicedQuantity ?? 0);
    }
  }
  return map;
}

// How reliable is the allocation data we have?
//   "db"         — exact: our DB tracked every ASN create/cancel through this system
//   "zepto"      — exact: Zepto returned itemDetails in list_asns response
//   "estimated"  — proportional guess: existing ASNs but no per-SKU data (historical/external ASNs)
//   "none"       — no existing ASNs, full PO qty available
export type AllocSource = "db" | "zepto" | "estimated" | "none";

export function getAllocSource(existing: ZeptoASN[], dbAllocations: Record<string, number>): AllocSource {
  if (Object.keys(dbAllocations).length > 0) return "db";
  if (Object.keys(computeAllocatedBySku(existing)).length > 0) return "zepto";
  if (existing.length > 0) return "estimated";
  return "none";
}

// Recalculate per-line invoicedQty based on already-allocated quantities.
//
// Priority:
//   1. dbAllocations — our own DB (exact — written on every ASN create/cancel)
//   2. itemDetails   — from Zepto list_asns response (exact, rarely populated)
//   3. proportional  — distribute total allocated qty proportionally by PO ratio
//                      (for historical/external ASNs — conservative floor() keeps us safe)
//   4. none          — no existing ASNs, fill with full orderedQty
function recalcLineQtys(
  lines: ASNLineForm[],
  existing: ZeptoASN[],
  dbAllocations: Record<string, number> = {},
): ASNLineForm[] {
  const hasDb = Object.keys(dbAllocations).length > 0;
  if (existing.length === 0 && !hasDb) return lines.map(l => ({ ...l, invoicedQty: l.orderedQty }));

  // 1 & 2 — exact per-SKU data
  const allocated = hasDb ? dbAllocations : computeAllocatedBySku(existing);
  if (Object.keys(allocated).length > 0) {
    return lines.map(l => ({
      ...l,
      invoicedQty: Math.max(0, l.orderedQty - (allocated[l.skuCode] ?? 0)),
    }));
  }

  // 3 — proportional fallback: Zepto returned no per-SKU data (historical / external ASNs)
  // Distribute total allocated qty proportionally across SKUs by their PO qty ratio.
  // floor() is intentionally conservative — user adjusts up rather than failing Zepto's
  // per-SKU check by going over. A yellow "~Est." badge tells them these are estimates.
  const totalOrdered   = lines.reduce((s, l) => s + l.orderedQty, 0);
  const totalAllocated = existing.reduce((s, a) => s + (a.asnQuantity ?? 0), 0);
  const totalRemaining = Math.max(0, totalOrdered - totalAllocated);
  if (totalOrdered > 0 && totalAllocated > 0) {
    return lines.map(l => ({
      ...l,
      invoicedQty: Math.min(
        l.orderedQty,
        Math.floor(l.orderedQty / totalOrdered * totalRemaining),
      ),
    }));
  }

  return lines.map(l => ({ ...l, invoicedQty: 0 }));
}

export default function ZeptoPOs() {
  const [pos, setPOs]               = useState<ZeptoPO[]>([]);
  const [loading, setLoading]       = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [connected, setConnected]   = useState<boolean | null>(null);
  const [days, setDays]             = useState(7);
  const [includeAll, setIncludeAll] = useState(false);
  const [poSearch, setPoSearch]     = useState("");         // PO code search
  const [page, setPage]             = useState(1);
  const [hasNext, setHasNext]       = useState(false);
  const [expandedRows, setExpanded] = useState<Set<string>>(new Set());

  // Amendment modal state
  const [amendModal, setAmendModal]           = useState<ZeptoPO | null>(null);
  const [amendForm, setAmendForm]             = useState<AmendForm>({ poRows: [], items: [] });
  const [amendSubmitting, setAmendSubmitting] = useState(false);
  const [amendSuccess, setAmendSuccess]       = useState(false);
  const [amendError, setAmendError]           = useState<string | null>(null);

  // ASN modal state
  const [asnModal, setAsnModal]               = useState<ZeptoPO | null>(null);
  const [asnForm, setAsnForm]                 = useState<CreateASNForm>({ invoiceNumber: "", invoiceDate: "", deliveryDate: "", lines: [] });
  const [asnSubmitting, setAsnSubmitting]     = useState(false);
  const [asnResult, setAsnResult]             = useState<string | null>(null);
  const [asnError, setAsnError]               = useState<string | null>(null);
  const [asnExisting, setAsnExisting]         = useState<ZeptoASN[]>([]);
  const [asnExistingLoading, setAsnExistingLoading] = useState(false);
  const [cancellingASN, setCancellingASN]         = useState<string | null>(null);
  const [expandedASNItems, setExpandedASNItems]   = useState<Set<string>>(new Set());
  // DB-tracked per-SKU allocations — source of truth since Zepto list_asns has no item breakdown
  const [asnDbAllocations, setAsnDbAllocations]   = useState<Record<string, number>>({});

  // Detail pop-ups — usable from both the main table and the ASN modal
  const [detailPO, setDetailPO]   = useState<ZeptoPO | null>(null);
  const [skuDetail, setSkuDetail] = useState<ZeptoPOLineItem | null>(null);

  const checkHealth = async () => {
    try {
      const r = await getZeptoHealth();
      setConnected(r.data?.reachable !== false);
    } catch {
      setConnected(false);
    }
  };

  const fetchPOs = async (p = page, search = poSearch) => {
    setLoading(true);
    setFetchError(null);
    try {
      const res = await getZeptoPOEvents({
        days,
        include_all_po_events:     includeAll,
        include_line_item_details: true,
        page_size:   10,
        page_number: p,
        ...(search.trim() ? { po_codes: search.trim() } : {}),
      });
      // Surface Zepto-level errors (success:false comes back as 4xx, caught below,
      // but handle any unexpected shape here too)
      if (res.data?.success === false) {
        setFetchError(res.data?.error ?? "Zepto returned an error");
        setPOs([]);
        setHasNext(false);
      } else {
        const data = res.data?.data?.data as ZeptoPOListData | undefined;
        setPOs(data?.purchaseOrders ?? []);
        setHasNext(data?.hasNext ?? false);
      }
    } catch (e: any) {
      const msg = e.response?.data?.detail ?? e.response?.data?.error ?? e.message ?? "Failed to fetch POs from Zepto";
      setFetchError(msg);
      setPOs([]);
      setHasNext(false);
    }
    setLoading(false);
  };

  useEffect(() => {
    checkHealth();
    fetchPOs(1, poSearch);
    setPage(1);
  }, [days, includeAll]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleRow = (id: string) =>
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const openASNModal = async (po: ZeptoPO) => {
    setAsnResult(null);
    setAsnError(null);
    setAsnExisting([]);
    setAsnDbAllocations({});
    setExpandedASNItems(new Set());
    setAsnForm({
      invoiceNumber: "",
      invoiceDate:   new Date().toISOString().split("T")[0],
      deliveryDate:  po.deliveryDate ? po.deliveryDate.split("T")[0] : "",
      lines:         (po.poLineItems ?? []).map(lineFromItem),
    });
    setAsnModal(po);
    setAsnExistingLoading(true);
    try {
      const [asnRes, allocRes] = await Promise.all([
        getZeptoASNs(po.code, { page_size: 20 }),
        getZeptoPOSKUAllocations(po.code),
      ]);
      const asnData = asnRes.data?.data?.data as ZeptoASNListData | undefined;
      const active  = (asnData?.ASNs ?? []).filter(a => a.status !== "CANCELLED" && a.status !== "DELIVERED");
      const dbAlloc = (allocRes.data?.allocations ?? {}) as Record<string, number>;
      setAsnExisting(active);
      setAsnDbAllocations(dbAlloc);
      setAsnForm(f => ({ ...f, lines: recalcLineQtys(f.lines, active, dbAlloc) }));
    } catch { /* non-critical */ }
    setAsnExistingLoading(false);
  };

  const cancelExistingASN = async (asnNumber: string, poCode: string) => {
    if (!confirm(`Cancel ASN ${asnNumber}?\n\nThis cannot be undone.`)) return;
    setCancellingASN(asnNumber);
    try {
      await cancelZeptoASN(asnNumber);
      const [asnRes, allocRes] = await Promise.all([
        getZeptoASNs(poCode, { page_size: 20 }),
        getZeptoPOSKUAllocations(poCode),
      ]);
      const asnData = asnRes.data?.data?.data as ZeptoASNListData | undefined;
      const active  = (asnData?.ASNs ?? []).filter(a => a.status !== "CANCELLED" && a.status !== "DELIVERED");
      const dbAlloc = (allocRes.data?.allocations ?? {}) as Record<string, number>;
      setAsnExisting(active);
      setAsnDbAllocations(dbAlloc);
      setAsnForm(f => ({ ...f, lines: recalcLineQtys(f.lines, active, dbAlloc) }));
    } catch (e: any) {
      alert(e.response?.data?.detail ?? "Failed to cancel ASN");
    }
    setCancellingASN(null);
  };

  // ── Amendment helpers ────────────────────────────────────────────────────────
  const openAmendmentModal = (po: ZeptoPO) => {
    setAmendSuccess(false);
    setAmendError(null);
    setAmendForm({
      poRows: [],
      items: (po.poLineItems ?? []).map(item => ({
        skuCode:      item.skuCode,
        materialCode: item.materialCode ?? item.matetrialCode ?? "",
        productName:  item.productName,
        ean:          item.ean ?? "",
        rows:         [],
      })),
    });
    setAmendModal(po);
  };

  const addPoRow    = () => setAmendForm(f => ({ ...f, poRows: [...f.poRows, emptyRow()] }));
  const removePoRow = (id: string) => setAmendForm(f => ({ ...f, poRows: f.poRows.filter(r => r.id !== id) }));
  const updatePoRow = (id: string, key: keyof Omit<AmendRow, "id">, val: string) =>
    setAmendForm(f => ({ ...f, poRows: f.poRows.map(r => r.id === id ? { ...r, [key]: val } : r) }));

  const addItemRow    = (idx: number) => setAmendForm(f => {
    const items = [...f.items];
    items[idx] = { ...items[idx], rows: [...items[idx].rows, emptyRow()] };
    return { ...f, items };
  });
  const removeItemRow = (idx: number, id: string) => setAmendForm(f => {
    const items = [...f.items];
    items[idx] = { ...items[idx], rows: items[idx].rows.filter(r => r.id !== id) };
    return { ...f, items };
  });
  const updateItemRow = (idx: number, id: string, key: keyof Omit<AmendRow, "id">, val: string) =>
    setAmendForm(f => {
      const items = [...f.items];
      items[idx] = { ...items[idx], rows: items[idx].rows.map(r => r.id === id ? { ...r, [key]: val } : r) };
      return { ...f, items };
    });

  const handleAmendmentSubmit = async (e: { preventDefault(): void }) => {
    e.preventDefault();
    if (!amendModal) return;
    const filledPoRows   = amendForm.poRows.filter(r => r.attributeName && r.recommendedValue);
    const filledItems    = amendForm.items.filter(item => item.rows.some(r => r.attributeName && r.recommendedValue));
    if (!filledPoRows.length && !filledItems.length) {
      setAmendError("Add at least one amendment before submitting.");
      return;
    }
    setAmendSubmitting(true);
    setAmendError(null);
    const payload = {
      purchaseOrderAmendment: {
        purchaseOrderNumber: amendModal.code,
        poAttributes: filledPoRows.map(({ attributeName, previousValue, recommendedValue, reasonForAmendment }) =>
          ({ attributeName, previousValue, recommendedValue, reasonForAmendment })),
        itemDetails: filledItems.map(item => ({
          productIdentifier: {
            skuCode:      item.skuCode,
            materialCode: item.materialCode || undefined,
            skuName:      item.productName,
            identifier:   item.ean ? { identifierType: "EAN", identifierValue: item.ean } : undefined,
          },
          amendments: item.rows
            .filter(r => r.attributeName && r.recommendedValue)
            .map(({ attributeName, previousValue, recommendedValue, reasonForAmendment }) =>
              ({ attributeName, previousValue, recommendedValue, reasonForAmendment })),
        })),
      },
    };
    try {
      await requestZeptoPOAmendment(amendModal.code, payload);
      setAmendSuccess(true);
    } catch (err: any) {
      setAmendError(err.response?.data?.detail ?? JSON.stringify(err.response?.data) ?? "Failed to submit amendment");
    }
    setAmendSubmitting(false);
  };

  const handleASNSubmit = async (e: { preventDefault(): void }) => {
    e.preventDefault();
    if (!asnModal) return;
    setAsnSubmitting(true);
    setAsnError(null);
    const totalAmount = asnForm.lines.reduce((s, l) => s + l.invoicedQty * l.basePrice, 0);
    const payload = {
      purchaseOrderDetails: { purchaseOrderNumber: asnModal.code },
      invoiceDetails: {
        invoiceNumber: asnForm.invoiceNumber,
        invoiceType:   "SSI",
        invoiceDate:   asnForm.invoiceDate,
        ...(asnForm.deliveryDate ? { deliveryDate: asnForm.deliveryDate } : {}),
        dueDate:       asnForm.invoiceDate,
      },
      invoiceTotals: {
        currencyCode:    "INR",
        discountDetails: { totalDiscountAmount: 0 },
        taxableAmount:    parseFloat(totalAmount.toFixed(2)),
        grandTotalAmount: parseFloat(totalAmount.toFixed(2)),
      },
      itemDetails: asnForm.lines
        .filter(l => l.invoicedQty > 0)
        .map(l => ({
          productIdentifier: {
            buyerProductIdentifier: {
              skuCode:      l.skuCode,
              materialCode: l.materialCode || undefined,
            },
          },
          batchDetails: l.batchNumber
            ? { batchNumber: l.batchNumber, expiryDate: l.expiryDate || "" }
            : undefined,
          quantity: {
            invoicedQuantity: { amount: l.invoicedQty, unitOfMeasure: "PC" },
            freeQuantity:     { amount: 0,              unitOfMeasure: "PC" },
          },
          mrp:       l.mrp,
          basePrice: l.basePrice,
        })),
      seller: { soldFrom: { id: asnModal.vendorCode } },
    };
    try {
      const res = await createZeptoASN(payload);
      const num = res.data?.asn_number ?? res.data?.data?.data?.asnNumber ?? res.data?.data?.asnNumber;
      setAsnResult(num ?? "ASN Created");
    } catch (err: any) {
      setAsnError(err.response?.data?.detail ?? JSON.stringify(err.response?.data) ?? "Failed to create ASN");
    }
    setAsnSubmitting(false);
  };

  const stats = {
    total:     pos.length,
    active:    pos.filter(p => p.status === "RELEASED" || p.status === "OPEN").length,
    cancelled: pos.filter(p => p.status === "CANCELLED" || p.status === "EXPIRED").length,
    totalQty:  pos.reduce((s, p) => s + (p.totalQty ?? 0), 0),
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <ClipboardList size={24} className="text-violet-600" />
            Zepto PO Events
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Live purchase orders from Zepto Silk Route API · QA environment
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium ${
            connected === true  ? "bg-green-50 text-green-700" :
            connected === false ? "bg-red-50 text-red-700"    :
                                  "bg-gray-50 text-gray-500"
          }`}>
            {connected === true  ? <Wifi size={13} />    :
             connected === false ? <WifiOff size={13} /> : <Clock size={13} />}
            {connected === true  ? "Zepto Connected" :
             connected === false ? "Connection Error" : "Checking…"}
          </div>
          <button
            onClick={() => { checkHealth(); fetchPOs(page); }}
            className="flex items-center gap-2 px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 transition"
          >
            <RefreshCcw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <label className="text-sm text-gray-600 font-medium">Past</label>
          <select
            value={days}
            onChange={e => setDays(Number(e.target.value))}
            className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-violet-400"
          >
            {[7, 14, 30, 45].map(d => <option key={d} value={d}>{d} days</option>)}
          </select>
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={includeAll}
            onChange={e => setIncludeAll(e.target.checked)}
            className="rounded accent-violet-600"
          />
          Include all event history
        </label>
        {/* PO code search */}
        <div className="flex items-center gap-1.5">
          <input
            type="text"
            value={poSearch}
            onChange={e => setPoSearch(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") { setPage(1); fetchPOs(1, poSearch); } }}
            placeholder="Search PO code…"
            className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm w-44 focus:outline-none focus:ring-2 focus:ring-violet-400"
          />
          {poSearch && (
            <button
              onClick={() => { setPoSearch(""); setPage(1); fetchPOs(1, ""); }}
              className="text-gray-400 hover:text-gray-600 text-xs px-1.5"
              title="Clear search"
            >✕</button>
          )}
          <button
            onClick={() => { setPage(1); fetchPOs(1, poSearch); }}
            className="px-3 py-1.5 bg-violet-600 text-white text-xs rounded-lg hover:bg-violet-700 transition"
          >Search</button>
        </div>
        <div className="ml-auto flex items-center gap-2 text-sm text-gray-500">
          Page {page}
          <button
            disabled={page <= 1}
            onClick={() => { const p = page - 1; setPage(p); fetchPOs(p); }}
            className="px-2 py-1 border rounded disabled:opacity-40 hover:bg-gray-50"
          >←</button>
          <button
            disabled={!hasNext}
            onClick={() => { const p = page + 1; setPage(p); fetchPOs(p); }}
            className="px-2 py-1 border rounded disabled:opacity-40 hover:bg-gray-50"
          >→</button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total PO Events",      value: stats.total,                      icon: ClipboardList, color: "bg-violet-500" },
          { label: "Active / Released",    value: stats.active,                     icon: CheckCircle,   color: "bg-green-500"  },
          { label: "Cancelled / Expired",  value: stats.cancelled,                  icon: XCircle,       color: "bg-red-500"    },
          { label: "Total Qty Ordered",    value: stats.totalQty.toLocaleString(),  icon: PackageCheck,  color: "bg-blue-500"   },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="bg-white rounded-xl p-4 shadow-sm border border-gray-100 flex items-center gap-3">
            <div className={`w-10 h-10 ${color} rounded-lg flex items-center justify-center flex-shrink-0`}>
              <Icon size={18} className="text-white" />
            </div>
            <div>
              <p className="text-xs text-gray-500">{label}</p>
              <p className="text-xl font-bold text-gray-800">{value}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Error banner */}
      {fetchError && (
        <div className="flex items-start gap-3 bg-red-50 border border-red-200 text-red-700 rounded-xl px-4 py-3 text-sm">
          <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
          <div className="flex-1 min-w-0">
            <span className="font-semibold">Zepto API Error: </span>
            {fetchError}
          </div>
          <button onClick={() => setFetchError(null)} className="text-red-400 hover:text-red-600 flex-shrink-0 text-xs">✕</button>
        </div>
      )}

      {/* PO Table */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        {loading ? (
          <div className="text-center py-16 text-gray-400">
            <Loader2 size={28} className="mx-auto mb-3 animate-spin text-violet-400" />
            Fetching PO events from Zepto…
          </div>
        ) : pos.length === 0 ? (
          <div className="text-center py-16 text-gray-400">
            <ClipboardList size={40} className="mx-auto mb-3 text-gray-300" />
            <p className="font-medium">No PO events found</p>
            {poSearch.trim()
              ? <p className="text-sm mt-1">No PO matched <span className="font-mono text-gray-600">{poSearch.trim()}</span> — check the PO code and try again</p>
              : <p className="text-sm mt-1">Try increasing the days range or check Zepto connection</p>
            }
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
              <tr>
                <th className="px-4 py-3 text-left w-8"></th>
                <th className="px-4 py-3 text-left">Event</th>
                <th className="px-4 py-3 text-left">PO Number</th>
                <th className="px-4 py-3 text-left">Vendor</th>
                <th className="px-4 py-3 text-left">Store</th>
                <th className="px-4 py-3 text-left">Status</th>
                <th className="px-4 py-3 text-left">Delivery</th>
                <th className="px-4 py-3 text-right">Qty</th>
                <th className="px-4 py-3 text-center">PDF</th>
                <th className="px-4 py-3 text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {pos.map(po => {
                const expanded = expandedRows.has(po.eventId);
                const hasPdf = !!(po.expiringUrlForPoPDF || po.expiringPoPdfLink || po.pdfFileName);
                return (
                  <Fragment key={po.eventId}>
                    <tr className={`hover:bg-gray-50 ${expanded ? "bg-violet-50/30" : ""}`}>
                      <td className="px-4 py-3">
                        <button onClick={() => toggleRow(po.eventId)} className="text-gray-400 hover:text-violet-600 transition">
                          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                        </button>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${EVENT_BADGE[po.eventType] ?? "bg-gray-100 text-gray-600"}`}>
                          {po.eventType}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <button
                          onClick={() => setDetailPO(po)}
                          className="font-mono text-xs font-semibold text-violet-700 hover:text-violet-900 hover:underline"
                          title="View PO details"
                        >
                          {po.code}
                        </button>
                      </td>
                      <td className="px-4 py-3">
                        <div className="text-gray-800 text-xs font-medium">{po.vendorCode}</div>
                        {po.vendorName && <div className="text-gray-400 text-xs truncate max-w-[130px]">{po.vendorName}</div>}
                      </td>
                      <td className="px-4 py-3 text-gray-500 text-xs">{po.toStoreCode ?? "—"}</td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${PO_STATUS_STYLE[po.status] ?? "bg-gray-100 text-gray-600 border-gray-200"}`}>
                          {po.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                        {po.deliveryDate ? new Date(po.deliveryDate).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "—"}
                      </td>
                      <td className="px-4 py-3 text-right font-semibold text-gray-700">{(po.totalQty ?? 0).toLocaleString()}</td>
                      <td className="px-4 py-3 text-center">
                        {hasPdf ? (
                          <a
                            href={`${API_BASE}/zepto/po/${po.code}/pdf`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-violet-600 hover:text-violet-800 text-xs"
                          >
                            <FileText size={13} /> PDF
                          </a>
                        ) : (
                          <span className="text-gray-300 text-xs">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <div className="flex items-center gap-1.5 justify-center">
                          {(po.status === "RELEASED" || po.status === "OPEN") && (
                            <button
                              onClick={() => openASNModal(po)}
                              className="inline-flex items-center gap-1 px-2.5 py-1.5 bg-violet-600 text-white text-xs rounded-lg hover:bg-violet-700 transition"
                            >
                              <Plus size={11} /> ASN
                            </button>
                          )}
                          {po.status !== "CANCELLED" && po.status !== "EXPIRED" && (
                            <button
                              onClick={() => openAmendmentModal(po)}
                              className="inline-flex items-center gap-1 px-2.5 py-1.5 border border-amber-300 text-amber-700 text-xs rounded-lg hover:bg-amber-50 transition"
                              title="Request PO Amendment"
                            >
                              <Pencil size={11} /> Amend
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>

                    {/* Expanded line items */}
                    {expanded && (
                      <tr>
                        <td colSpan={10} className="bg-violet-50/40 px-8 py-3 border-b border-violet-100">
                          {!po.poLineItems?.length ? (
                            <p className="text-xs text-gray-400 italic py-1">No line item data available</p>
                          ) : (
                            <table className="w-full text-xs border-collapse">
                              <thead>
                                <tr className="text-gray-500 border-b border-violet-100">
                                  <th className="text-left pb-1.5 pr-4 font-medium">SKU Code</th>
                                  <th className="text-left pb-1.5 pr-4 font-medium">Product</th>
                                  <th className="text-left pb-1.5 pr-4 font-medium">Brand</th>
                                  <th className="text-right pb-1.5 pr-4 font-medium">Qty (PC)</th>
                                  <th className="text-right pb-1.5 pr-4 font-medium">MRP</th>
                                  <th className="text-right pb-1.5 pr-4 font-medium">Cost Price</th>
                                  <th className="text-left pb-1.5 font-medium">EAN</th>
                                </tr>
                              </thead>
                              <tbody>
                                {po.poLineItems.map((item, idx) => (
                                  <tr key={idx} className="border-b border-violet-50 last:border-0">
                                    <td className="py-1.5 pr-4">
                                      <button
                                        onClick={() => setSkuDetail(item)}
                                        className="font-mono text-violet-700 hover:text-violet-900 hover:underline text-left"
                                        title="View SKU details"
                                      >
                                        {item.skuCode}
                                      </button>
                                    </td>
                                    <td className="py-1.5 pr-4 text-gray-700 max-w-[200px] truncate">{item.productName}</td>
                                    <td className="py-1.5 pr-4 text-gray-500">{item.brandName ?? "—"}</td>
                                    <td className="py-1.5 pr-4 text-right font-semibold">{item.quantity}</td>
                                    <td className="py-1.5 pr-4 text-right text-gray-600">₹{item.mrp}</td>
                                    <td className="py-1.5 pr-4 text-right text-gray-600">₹{item.costPrice}</td>
                                    <td className="py-1.5 font-mono text-gray-400">{item.ean ?? "—"}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Create ASN Modal */}
      {asnModal && (
        <div
          className="fixed inset-0 bg-black/50 z-50 flex items-start justify-center p-4 overflow-y-auto"
          onClick={() => setAsnModal(null)}
        >
          <div
            className="bg-white rounded-xl w-full max-w-2xl shadow-2xl my-8"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between p-5 border-b">
              <div>
                <h3 className="font-bold text-gray-800">
                  Create ASN —{" "}
                  <button
                    type="button"
                    onClick={() => setDetailPO(asnModal)}
                    className="font-mono text-violet-700 hover:text-violet-900 hover:underline"
                    title="View full PO details"
                  >
                    {asnModal.code}
                  </button>
                </h3>
                <p className="text-xs text-gray-500 mt-0.5">
                  Vendor: {asnModal.vendorCode}{asnModal.toStoreCode ? ` · Store: ${asnModal.toStoreCode}` : ""}
                  {" "}· <button type="button" onClick={() => setDetailPO(asnModal)} className="text-violet-500 hover:underline">View PO details</button>
                </p>
              </div>
              <button onClick={() => setAsnModal(null)} className="text-gray-400 hover:text-gray-700 text-lg">✕</button>
            </div>

            {asnResult ? (
              <div className="p-10 text-center">
                <CheckCircle size={48} className="mx-auto mb-4 text-green-500" />
                <h4 className="font-bold text-gray-800 text-lg mb-1">ASN Created Successfully</h4>
                <p className="text-gray-500 text-sm mb-3">Zepto ASN Number</p>
                <div className="inline-block bg-green-50 border border-green-200 rounded-lg px-5 py-3 font-mono text-green-800 font-bold text-xl mb-5">
                  {asnResult}
                </div>
                <p className="text-xs text-gray-400 mb-6">
                  Save this ASN number — needed to cancel or reference this shipment in Zepto ASN Manager
                </p>
                <button onClick={() => setAsnModal(null)} className="px-6 py-2 bg-violet-600 text-white rounded-lg text-sm hover:bg-violet-700 transition">
                  Done
                </button>
              </div>
            ) : (
              <form onSubmit={handleASNSubmit} className="p-5 space-y-5">
                {asnError && (
                  <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
                    <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
                    <span className="break-all">{asnError}</span>
                  </div>
                )}

                {/* Existing ASNs banner */}
                {asnExistingLoading && (
                  <div className="flex items-center gap-2 text-xs text-gray-400 bg-gray-50 rounded-lg px-3 py-2">
                    <Loader2 size={12} className="animate-spin" />
                    Checking existing ASNs for this PO…
                  </div>
                )}
                {!asnExistingLoading && asnExisting.length > 0 && (() => {
                  const allocSource   = getAllocSource(asnExisting, asnDbAllocations);
                  const totalOrdered  = asnForm.lines.reduce((s, l) => s + l.orderedQty, 0);
                  const totalAllocd   = asnExisting.reduce((s, a) => s + (a.asnQuantity ?? 0), 0);
                  const totalRemain   = Math.max(0, totalOrdered - totalAllocd);
                  const sourceBadge: Record<AllocSource, { label: string; cls: string }> = {
                    db:        { label: "Exact · DB tracked",          cls: "bg-green-100 text-green-700 border-green-300" },
                    zepto:     { label: "Exact · Zepto API",            cls: "bg-green-100 text-green-700 border-green-300" },
                    estimated: { label: "~Estimated · proportional",   cls: "bg-amber-100 text-amber-700 border-amber-300" },
                    none:      { label: "No prior allocations",        cls: "bg-gray-100 text-gray-500 border-gray-300"  },
                  };
                  const badge = sourceBadge[allocSource];
                  return (
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-xs font-semibold text-amber-800 flex items-center gap-1.5">
                          <AlertCircle size={13} />
                          {asnExisting.length} active ASN{asnExisting.length > 1 ? "s" : ""} already exist for this PO
                        </p>
                        <span className={`text-xs px-2 py-0.5 rounded-full border font-medium flex-shrink-0 ${badge.cls}`}>
                          {badge.label}
                        </span>
                      </div>
                      <p className="text-xs text-amber-700">
                        {allocSource === "db" || allocSource === "zepto" ? (
                          <>Invoice Qty fields are auto-filled with the <strong>exact remaining per SKU</strong> — adjust if shipping a partial amount.</>
                        ) : allocSource === "estimated" ? (
                          <>
                            <strong>Per-SKU allocation unknown</strong> — these ASNs were created outside this system, so we have no per-SKU breakdown.
                            Quantities are <strong>proportionally estimated</strong> from the total remaining ({totalRemain} of {totalOrdered} PC).
                            {" "}<span className="font-semibold text-amber-800">Review each SKU before submitting</span> — Zepto rejects if any single SKU exceeds its individual PO qty.
                          </>
                        ) : (
                          <>Invoice Qty fields are reset to <strong>0</strong> — enter per-SKU quantities manually.</>
                        )}
                      </p>
                      <div className="border border-amber-200 rounded overflow-hidden">
                        <table className="w-full text-xs">
                          <thead className="bg-amber-100 text-amber-700">
                            <tr>
                              <th className="px-2 py-1.5 text-left font-medium w-5"></th>
                              <th className="px-2 py-1.5 text-left font-medium">ASN Number</th>
                              <th className="px-2 py-1.5 text-left font-medium">Invoice</th>
                              <th className="px-2 py-1.5 text-center font-medium">Status</th>
                              <th className="px-2 py-1.5 text-right font-medium">Qty</th>
                              <th className="px-2 py-1.5 text-center font-medium">Action</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-amber-100 bg-white">
                            {asnExisting.map(asn => {
                              const isExpanded = expandedASNItems.has(asn.asnNumber);
                              const hasItems   = (asn.itemDetails?.length ?? 0) > 0;
                              const toggleExpand = () => setExpandedASNItems(prev => {
                                const next = new Set(prev);
                                next.has(asn.asnNumber) ? next.delete(asn.asnNumber) : next.add(asn.asnNumber);
                                return next;
                              });
                              return (
                                <Fragment key={asn.asnNumber}>
                                  <tr className={isExpanded ? "bg-amber-50/40" : ""}>
                                    <td className="px-2 py-1.5 text-center">
                                      <button type="button" onClick={toggleExpand} className="text-amber-600 hover:text-amber-800">
                                        {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                                      </button>
                                    </td>
                                    <td className="px-2 py-1.5">
                                      <button
                                        type="button"
                                        onClick={toggleExpand}
                                        className="font-mono text-violet-700 hover:text-violet-900 hover:underline text-left"
                                      >
                                        {asn.asnNumber}
                                      </button>
                                    </td>
                                    <td className="px-2 py-1.5 text-gray-600">{asn.invoiceNumber ?? "—"}</td>
                                    <td className="px-2 py-1.5 text-center">
                                      <span className="px-1.5 py-0.5 rounded-full bg-blue-50 text-blue-700 border border-blue-200 text-xs font-medium">
                                        {asn.status}
                                      </span>
                                    </td>
                                    <td className="px-2 py-1.5 text-right font-semibold text-gray-700">
                                      {asn.asnQuantity ?? "—"}
                                    </td>
                                    <td className="px-2 py-1.5 text-center">
                                      <button
                                        type="button"
                                        disabled={cancellingASN === asn.asnNumber}
                                        onClick={() => cancelExistingASN(asn.asnNumber, asnModal!.code)}
                                        className="inline-flex items-center gap-1 px-2 py-1 border border-red-200 text-red-600 text-xs rounded hover:bg-red-50 disabled:opacity-50 transition"
                                      >
                                        <XCircle size={10} />
                                        {cancellingASN === asn.asnNumber ? "…" : "Cancel"}
                                      </button>
                                    </td>
                                  </tr>

                                  {/* Expanded detail panel — always clickable, shows whatever data is available */}
                                  {isExpanded && (
                                    <tr className="bg-amber-50/60">
                                      <td colSpan={6} className="px-4 py-3 border-t border-amber-100">
                                        {/* Metadata grid */}
                                        <div className="grid grid-cols-3 gap-x-4 gap-y-1 text-xs mb-3">
                                          <div><span className="text-gray-400">ASN #:</span> <span className="font-mono font-semibold text-violet-700">{asn.asnNumber}</span></div>
                                          <div><span className="text-gray-400">Invoice:</span> <span className="font-medium text-gray-700">{asn.invoiceNumber ?? "—"}</span></div>
                                          <div><span className="text-gray-400">Status:</span> <span className="font-medium text-gray-700">{asn.status}</span></div>
                                          <div><span className="text-gray-400">PO #:</span> <span className="font-mono text-violet-600">{asn.poNumber ?? asnModal?.code ?? "—"}</span></div>
                                          <div><span className="text-gray-400">Total Qty:</span> <span className="font-semibold text-gray-700">{asn.asnQuantity ?? 0} PC</span></div>
                                          <div><span className="text-gray-400">Amount:</span> <span className="font-medium text-gray-700">{asn.totalAmount != null ? `₹${asn.totalAmount.toLocaleString("en-IN")}` : "—"}</span></div>
                                          {asn.locationCode && (
                                            <div className="col-span-2"><span className="text-gray-400">Location:</span> <span className="font-medium text-gray-700">{asn.locationCode}{asn.locationName ? ` · ${asn.locationName}` : ""}</span></div>
                                          )}
                                          {asn.createdAt && (
                                            <div><span className="text-gray-400">Created:</span> <span className="font-medium text-gray-700">{new Date(asn.createdAt).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}</span></div>
                                          )}
                                        </div>

                                        {/* Item details */}
                                        {hasItems ? (
                                          <>
                                            <p className="text-xs font-semibold text-amber-700 mb-1.5">Item Breakdown</p>
                                            <table className="w-full text-xs">
                                              <thead>
                                                <tr className="text-amber-600 border-b border-amber-200">
                                                  <th className="text-left pb-1 pr-3 font-medium">SKU</th>
                                                  <th className="text-left pb-1 pr-3 font-medium">Product</th>
                                                  <th className="text-right pb-1 pr-3 font-medium">Invoiced Qty</th>
                                                  <th className="text-right pb-1 pr-3 font-medium">Free Qty</th>
                                                  <th className="text-right pb-1 pr-3 font-medium">MRP</th>
                                                  <th className="text-right pb-1 font-medium">Base Price</th>
                                                </tr>
                                              </thead>
                                              <tbody>
                                                {asn.itemDetails!.map((item, i) => (
                                                  <tr key={i} className="border-b border-amber-50 last:border-0">
                                                    <td className="py-1 pr-3 font-mono text-violet-600">{item.skuCode ?? "—"}</td>
                                                    <td className="py-1 pr-3 text-gray-600 max-w-[180px] truncate">{item.productName ?? "—"}</td>
                                                    <td className="py-1 pr-3 text-right font-semibold text-gray-700">{item.invoicedQuantity ?? 0} PC</td>
                                                    <td className="py-1 pr-3 text-right text-gray-500">{item.freeQuantity ?? 0} PC</td>
                                                    <td className="py-1 pr-3 text-right text-gray-600">{item.mrp != null ? `₹${item.mrp}` : "—"}</td>
                                                    <td className="py-1 text-right text-gray-600">{item.basePrice != null ? `₹${item.basePrice}` : "—"}</td>
                                                  </tr>
                                                ))}
                                              </tbody>
                                            </table>
                                          </>
                                        ) : (
                                          <p className="text-xs text-gray-400 italic">
                                            Zepto did not return per-item details for this ASN. Only the total quantity ({asn.asnQuantity ?? 0} PC) is available.
                                          </p>
                                        )}
                                      </td>
                                    </tr>
                                  )}
                                </Fragment>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  );
                })()}

                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Invoice Number *</label>
                    <input
                      required type="text" placeholder="e.g. INV-2025-001"
                      value={asnForm.invoiceNumber}
                      onChange={e => setAsnForm({ ...asnForm, invoiceNumber: e.target.value })}
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-400"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Invoice Date *</label>
                    <input
                      required type="date"
                      value={asnForm.invoiceDate}
                      onChange={e => setAsnForm({ ...asnForm, invoiceDate: e.target.value })}
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-400"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Delivery Date</label>
                    <input
                      type="date"
                      value={asnForm.deliveryDate}
                      onChange={e => setAsnForm({ ...asnForm, deliveryDate: e.target.value })}
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-400"
                    />
                  </div>
                </div>

                {/* Line Items */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <label className="text-xs font-semibold text-gray-700 uppercase tracking-wider">Line Items</label>
                    {(() => {
                      const allocSrc       = getAllocSource(asnExisting, asnDbAllocations);
                      const totalOrdered   = asnForm.lines.reduce((s, l) => s + l.orderedQty, 0);
                      const totalAllocated = asnExisting.reduce((s, a) => s + (a.asnQuantity ?? 0), 0);
                      const totalRemaining = Math.max(0, totalOrdered - totalAllocated);
                      if (totalAllocated === 0) return (
                        <span className="text-xs text-gray-400">{asnForm.lines.length} items · quantities in pieces (PC)</span>
                      );
                      const isEst = allocSrc === "estimated";
                      return (
                        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                          totalRemaining === 0 ? "bg-red-50 text-red-600"
                          : isEst         ? "bg-amber-100 text-amber-600"
                          :                  "bg-green-50 text-green-700"
                        }`}>
                          {totalRemaining === 0
                            ? `All ${totalOrdered} PC allocated — cancel existing ASN first`
                            : `${isEst ? "~" : ""}${totalRemaining} of ${totalOrdered} PC remaining${isEst ? " (est.)" : ""}`}
                        </span>
                      );
                    })()}
                  </div>

                  {asnForm.lines.length === 0 ? (
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-700">
                      No line items available. Expand the PO row in the table to load line items, then open this modal again.
                    </div>
                  ) : (() => {
                    const allocSource = getAllocSource(asnExisting, asnDbAllocations);
                    const isEstimated = allocSource === "estimated";
                    // For estimated: derive per-SKU allocation proportionally from total
                    const totalOrdered  = asnForm.lines.reduce((s, l) => s + l.orderedQty, 0);
                    const totalAllocd   = asnExisting.reduce((s, a) => s + (a.asnQuantity ?? 0), 0);
                    const estimatedBySku: Record<string, number> = {};
                    if (isEstimated && totalOrdered > 0) {
                      asnForm.lines.forEach(l => {
                        estimatedBySku[l.skuCode] = Math.floor(l.orderedQty / totalOrdered * totalAllocd);
                      });
                    }
                    const allocatedBySku = isEstimated
                      ? estimatedBySku
                      : Object.keys(asnDbAllocations).length > 0
                        ? asnDbAllocations
                        : computeAllocatedBySku(asnExisting);
                    const showAlloc = Object.keys(allocatedBySku).length > 0;
                    return (
                      <div className="border border-gray-100 rounded-lg overflow-hidden max-h-72 overflow-y-auto">
                        <table className="w-full text-xs">
                          <thead className="bg-gray-50 text-gray-500 sticky top-0 z-10">
                            <tr>
                              <th className="px-3 py-2 text-left">Product</th>
                              <th className="px-3 py-2 text-right">Ordered</th>
                              {showAlloc && (
                                <th className={`px-3 py-2 text-right ${isEstimated ? "text-amber-500" : "text-green-600"}`}>
                                  {isEstimated ? "~Remaining" : "Remaining"}
                                </th>
                              )}
                              <th className="px-3 py-2 text-center w-24">Invoice Qty *</th>
                              <th className="px-3 py-2 text-left w-28">Batch No.</th>
                              <th className="px-3 py-2 text-left w-28">Expiry Date</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-gray-50">
                            {asnForm.lines.map((line, idx) => {
                              const allocated  = allocatedBySku[line.skuCode] ?? 0;
                              const remaining  = Math.max(0, line.orderedQty - allocated);
                              const overLimit  = showAlloc && line.invoicedQty > remaining && !isEstimated;
                              return (
                                <tr key={idx} className={`hover:bg-gray-50 ${overLimit ? "bg-red-50" : ""}`}>
                                  <td className="px-3 py-2">
                                    <button
                                      type="button"
                                      onClick={() => {
                                        const full = asnModal?.poLineItems?.find(i => i.skuCode === line.skuCode);
                                        if (full) setSkuDetail(full);
                                      }}
                                      className="font-mono text-violet-700 hover:text-violet-900 hover:underline text-left block"
                                      title="View SKU details"
                                    >
                                      {line.skuCode}
                                    </button>
                                    <div className="text-gray-500 truncate max-w-[150px]">{line.productName}</div>
                                  </td>
                                  <td className="px-3 py-2 text-right text-gray-500">{line.orderedQty}</td>
                                  {showAlloc && (
                                    <td className="px-3 py-2 text-right">
                                      <span className={`font-semibold ${remaining === 0 ? "text-red-500" : isEstimated ? "text-amber-600" : "text-green-600"}`}>
                                        {isEstimated ? "~" : ""}{remaining}
                                      </span>
                                    </td>
                                  )}
                                  <td className="px-3 py-2">
                                    <input
                                      required type="number" min="0"
                                      max={isEstimated || !showAlloc ? line.orderedQty : remaining}
                                      value={line.invoicedQty}
                                      onChange={e => {
                                        const l = [...asnForm.lines];
                                        l[idx] = { ...l[idx], invoicedQty: Number(e.target.value) };
                                        setAsnForm({ ...asnForm, lines: l });
                                      }}
                                      className={`w-full border rounded px-2 py-1 text-center focus:outline-none focus:ring-1 ${
                                        overLimit
                                          ? "border-red-400 bg-red-50 focus:ring-red-400"
                                          : isEstimated && line.invoicedQty > 0
                                          ? "border-amber-300 focus:ring-amber-400"
                                          : "border-gray-200 focus:ring-violet-400"
                                      }`}
                                    />
                                    {overLimit && (
                                      <p className="text-red-500 mt-0.5 text-center">max {remaining}</p>
                                    )}
                                  </td>
                                  <td className="px-3 py-2">
                                    <input
                                      type="text" placeholder="Optional"
                                      value={line.batchNumber}
                                      onChange={e => {
                                        const l = [...asnForm.lines];
                                        l[idx] = { ...l[idx], batchNumber: e.target.value };
                                        setAsnForm({ ...asnForm, lines: l });
                                      }}
                                      className="w-full border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-violet-400"
                                    />
                                  </td>
                                  <td className="px-3 py-2">
                                    <input
                                      type="date"
                                      value={line.expiryDate}
                                      onChange={e => {
                                        const l = [...asnForm.lines];
                                        l[idx] = { ...l[idx], expiryDate: e.target.value };
                                        setAsnForm({ ...asnForm, lines: l });
                                      }}
                                      className="w-full border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-violet-400"
                                    />
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    );
                  })()}
                </div>

                <div className="flex gap-3 pt-2 border-t border-gray-100">
                  <button
                    type="button"
                    onClick={() => setAsnModal(null)}
                    className="flex-1 px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 transition"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={asnSubmitting || asnForm.lines.every(l => l.invoicedQty === 0)}
                    className="flex-1 px-4 py-2 bg-violet-600 text-white rounded-lg text-sm hover:bg-violet-700 disabled:opacity-50 transition"
                  >
                    {asnSubmitting ? "Creating ASN…" : "Create ASN"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {/* ── Amendment Modal ────────────────────────────────────────────────── */}
      {amendModal && (
        <div
          className="fixed inset-0 bg-black/50 z-50 flex items-start justify-center p-4 overflow-y-auto"
          onClick={() => setAmendModal(null)}
        >
          <div
            className="bg-white rounded-xl w-full max-w-3xl shadow-2xl my-8"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between p-5 border-b">
              <div>
                <h3 className="font-bold text-gray-800 flex items-center gap-2">
                  <Pencil size={16} className="text-amber-600" />
                  Request PO Amendment — {amendModal.code}
                </h3>
                <p className="text-xs text-gray-500 mt-0.5">
                  Vendor: {amendModal.vendorCode} · Store: {amendModal.toStoreCode ?? "—"}
                </p>
              </div>
              <button onClick={() => setAmendModal(null)} className="text-gray-400 hover:text-gray-700 text-lg">✕</button>
            </div>

            {amendSuccess ? (
              <div className="p-10 text-center">
                <CheckCircle size={48} className="mx-auto mb-4 text-green-500" />
                <h4 className="font-bold text-gray-800 text-lg mb-2">Amendment Request Submitted</h4>
                <p className="text-gray-500 text-sm mb-6">
                  Zepto will review your amendment request for PO <span className="font-mono font-bold text-violet-700">{amendModal.code}</span> and respond accordingly.
                </p>
                <button onClick={() => setAmendModal(null)} className="px-6 py-2 bg-amber-500 text-white rounded-lg text-sm hover:bg-amber-600 transition">
                  Done
                </button>
              </div>
            ) : (
              <form onSubmit={handleAmendmentSubmit} className="p-5 space-y-6 max-h-[80vh] overflow-y-auto">
                {amendError && (
                  <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
                    <AlertCircle size={15} className="mt-0.5 flex-shrink-0" />
                    <span>{amendError}</span>
                  </div>
                )}

                {/* ── PO-Level Amendments ─────────────────────────────── */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <h4 className="text-sm font-semibold text-gray-800">PO-Level Amendments</h4>
                      <p className="text-xs text-gray-400">Changes that apply to the whole PO (e.g. expiry date, GSTIN)</p>
                    </div>
                    <button type="button" onClick={addPoRow}
                      className="inline-flex items-center gap-1 px-3 py-1.5 border border-amber-300 text-amber-700 text-xs rounded-lg hover:bg-amber-50 transition">
                      <Plus size={11} /> Add Row
                    </button>
                  </div>

                  {amendForm.poRows.length === 0 ? (
                    <p className="text-xs text-gray-400 bg-gray-50 border border-dashed border-gray-200 rounded-lg p-4 text-center">
                      No PO-level amendments. Click <strong>Add Row</strong> to add one.
                    </p>
                  ) : (
                    <div className="border border-gray-100 rounded-lg overflow-hidden">
                      <table className="w-full text-xs">
                        <thead className="bg-gray-50 text-gray-500">
                          <tr>
                            <th className="px-3 py-2 text-left w-36">Attribute *</th>
                            <th className="px-3 py-2 text-left">Previous Value</th>
                            <th className="px-3 py-2 text-left">Recommended Value *</th>
                            <th className="px-3 py-2 text-left">Reason</th>
                            <th className="px-3 py-2 w-8"></th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-50">
                          {amendForm.poRows.map(row => (
                            <tr key={row.id}>
                              <td className="px-3 py-1.5">
                                <select required value={row.attributeName}
                                  onChange={e => updatePoRow(row.id, "attributeName", e.target.value)}
                                  className="w-full border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-amber-400">
                                  <option value="">Select…</option>
                                  {PO_LEVEL_ATTRS.map(a => <option key={a} value={a}>{a}</option>)}
                                </select>
                              </td>
                              <td className="px-3 py-1.5">
                                <input type="text" placeholder="Current value"
                                  value={row.previousValue}
                                  onChange={e => updatePoRow(row.id, "previousValue", e.target.value)}
                                  className="w-full border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-amber-400" />
                              </td>
                              <td className="px-3 py-1.5">
                                <input required type="text" placeholder="New value"
                                  value={row.recommendedValue}
                                  onChange={e => updatePoRow(row.id, "recommendedValue", e.target.value)}
                                  className="w-full border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-amber-400" />
                              </td>
                              <td className="px-3 py-1.5">
                                <input type="text" placeholder="Reason (optional)"
                                  value={row.reasonForAmendment}
                                  onChange={e => updatePoRow(row.id, "reasonForAmendment", e.target.value)}
                                  className="w-full border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-amber-400" />
                              </td>
                              <td className="px-3 py-1.5 text-center">
                                <button type="button" onClick={() => removePoRow(row.id)}
                                  className="text-red-400 hover:text-red-600 transition">
                                  <Trash2 size={13} />
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {/* ── Item-Level Amendments ───────────────────────────── */}
                <div>
                  <div className="mb-2">
                    <h4 className="text-sm font-semibold text-gray-800">Item-Level Amendments</h4>
                    <p className="text-xs text-gray-400">Per-SKU changes: MRP, BASE_PRICE, EAN, CASE_SIZE, EXPIRY_DATE</p>
                  </div>

                  {amendForm.items.length === 0 ? (
                    <p className="text-xs text-gray-400 bg-gray-50 border border-dashed border-gray-200 rounded-lg p-4 text-center">
                      No line items in this PO — expand the PO row to load items first.
                    </p>
                  ) : (
                    <div className="space-y-3">
                      {amendForm.items.map((item, idx) => (
                        <div key={item.skuCode} className="border border-gray-100 rounded-lg overflow-hidden">
                          <div className="flex items-center justify-between bg-gray-50 px-3 py-2">
                            <div>
                              <span className="font-mono text-violet-700 text-xs">{item.skuCode}</span>
                              <span className="text-gray-600 text-xs ml-2 truncate max-w-[280px] inline-block align-bottom">{item.productName}</span>
                            </div>
                            <button type="button" onClick={() => addItemRow(idx)}
                              className="inline-flex items-center gap-1 px-2.5 py-1 border border-amber-300 text-amber-700 text-xs rounded hover:bg-amber-50 transition flex-shrink-0">
                              <Plus size={10} /> Add
                            </button>
                          </div>

                          {item.rows.length > 0 && (
                            <table className="w-full text-xs">
                              <thead className="bg-white text-gray-500 border-b border-gray-100">
                                <tr>
                                  <th className="px-3 py-1.5 text-left w-36">Attribute *</th>
                                  <th className="px-3 py-1.5 text-left">Previous Value</th>
                                  <th className="px-3 py-1.5 text-left">Recommended Value *</th>
                                  <th className="px-3 py-1.5 text-left">Reason</th>
                                  <th className="px-3 py-1.5 w-8"></th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-gray-50">
                                {item.rows.map(row => (
                                  <tr key={row.id} className="bg-white">
                                    <td className="px-3 py-1.5">
                                      <select required value={row.attributeName}
                                        onChange={e => updateItemRow(idx, row.id, "attributeName", e.target.value)}
                                        className="w-full border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-amber-400">
                                        <option value="">Select…</option>
                                        {ITEM_LEVEL_ATTRS.map(a => <option key={a} value={a}>{a}</option>)}
                                      </select>
                                    </td>
                                    <td className="px-3 py-1.5">
                                      <input type="text" placeholder="Current value"
                                        value={row.previousValue}
                                        onChange={e => updateItemRow(idx, row.id, "previousValue", e.target.value)}
                                        className="w-full border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-amber-400" />
                                    </td>
                                    <td className="px-3 py-1.5">
                                      <input required type="text" placeholder="New value"
                                        value={row.recommendedValue}
                                        onChange={e => updateItemRow(idx, row.id, "recommendedValue", e.target.value)}
                                        className="w-full border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-amber-400" />
                                    </td>
                                    <td className="px-3 py-1.5">
                                      <input type="text" placeholder="Reason (optional)"
                                        value={row.reasonForAmendment}
                                        onChange={e => updateItemRow(idx, row.id, "reasonForAmendment", e.target.value)}
                                        className="w-full border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-amber-400" />
                                    </td>
                                    <td className="px-3 py-1.5 text-center">
                                      <button type="button" onClick={() => removeItemRow(idx, row.id)}
                                        className="text-red-400 hover:text-red-600 transition">
                                        <Trash2 size={13} />
                                      </button>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Attribute reference */}
                <div className="bg-amber-50 border border-amber-100 rounded-lg p-3 text-xs text-amber-800 space-y-0.5">
                  <p className="font-semibold mb-1">Amendment attribute guide</p>
                  <p><strong>MRP</strong> — Mismatch between PO and your master data</p>
                  <p><strong>BASE_PRICE</strong> — Margin / net-landing-cost mismatch (tax-inclusive)</p>
                  <p><strong>EAN</strong> — Incorrect EAN / barcode in the PO</p>
                  <p><strong>CASE_SIZE</strong> — Quantity not a multiple of product case size</p>
                  <p><strong>EXPIRY_DATE</strong> — Expiry date too short to fulfill within timeframe</p>
                  <p><strong>VENDOR_GSTIN</strong> — GSTIN correction (PO-level only)</p>
                </div>

                <div className="flex gap-3 pt-2 border-t border-gray-100">
                  <button type="button" onClick={() => setAmendModal(null)}
                    className="flex-1 px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 transition">
                    Cancel
                  </button>
                  <button type="submit" disabled={amendSubmitting}
                    className="flex-1 px-4 py-2 bg-amber-500 text-white rounded-lg text-sm hover:bg-amber-600 disabled:opacity-50 transition">
                    {amendSubmitting ? "Submitting…" : "Submit Amendment Request"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {/* ── PO Detail Modal ───────────────────────────────────────────────── */}
      {detailPO && (
        <div
          className="fixed inset-0 bg-black/60 z-[60] flex items-start justify-center p-4 overflow-y-auto"
          onClick={() => setDetailPO(null)}
        >
          <div
            className="bg-white rounded-xl w-full max-w-2xl shadow-2xl my-8"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between p-5 border-b">
              <div>
                <h3 className="font-bold text-gray-800 flex items-center gap-2">
                  <ClipboardList size={16} className="text-violet-600" />
                  PO Details — <span className="font-mono text-violet-700">{detailPO.code}</span>
                </h3>
                <p className="text-xs text-gray-500 mt-0.5">{detailPO.eventType} · {detailPO.status}</p>
              </div>
              <button onClick={() => setDetailPO(null)} className="text-gray-400 hover:text-gray-700 text-lg">✕</button>
            </div>
            <div className="p-5 space-y-5 max-h-[75vh] overflow-y-auto text-sm">

              {/* Basic Info */}
              <section>
                <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Order Info</h4>
                <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                  {[
                    ["PO Number",    detailPO.code],
                    ["Type",         detailPO.type],
                    ["Status",       detailPO.status],
                    ["Event Type",   detailPO.eventType],
                    ["Event ID",     detailPO.eventId],
                    ["Order Date",   detailPO.orderDate   ? new Date(detailPO.orderDate).toLocaleString("en-IN")   : null],
                    ["Delivery Date",detailPO.deliveryDate? new Date(detailPO.deliveryDate).toLocaleString("en-IN"): null],
                    ["Expiry Date",  detailPO.expiryDate  ? new Date(detailPO.expiryDate).toLocaleString("en-IN")  : null],
                    ["Timestamp",    detailPO.timestamp   ? new Date(detailPO.timestamp).toLocaleString("en-IN")   : null],
                    ["Total Qty",    detailPO.totalQty != null ? `${detailPO.totalQty} PC` : null],
                    ["Interstate",   detailPO.isInterstate != null ? (detailPO.isInterstate ? "Yes" : "No") : null],
                    ["Terms",        detailPO.terms || null],
                  ].filter(([, v]) => v).map(([label, value]) => (
                    <div key={label as string}>
                      <span className="text-gray-400">{label}: </span>
                      <span className="font-medium text-gray-800">{value as string}</span>
                    </div>
                  ))}
                </div>
              </section>

              {/* Vendor */}
              <section>
                <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Vendor</h4>
                <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                  {[
                    ["Vendor Code",    detailPO.vendorCode],
                    ["Vendor Name",    detailPO.vendorName],
                    ["Vendor Type",    detailPO.vendorType],
                    ["Vendor GSTIN",   detailPO.financialDetails?.vendorGSTIN],
                    ["Vendor PAN",     detailPO.financialDetails?.vendorPAN],
                    ["Vendor Address", detailPO.address?.vendorAddress],
                    ["Vendor Pin",     detailPO.address?.vendorPinCode],
                  ].filter(([, v]) => v).map(([label, value]) => (
                    <div key={label as string} className={label === "Vendor Address" ? "col-span-2" : ""}>
                      <span className="text-gray-400">{label}: </span>
                      <span className="font-medium text-gray-800">{value as string}</span>
                    </div>
                  ))}
                </div>
              </section>

              {/* Entity / Buyer */}
              <section>
                <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Buyer / Entity</h4>
                <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                  {[
                    ["Entity Code",  detailPO.entityCode],
                    ["Entity Name",  detailPO.entityName],
                    ["Entity GSTIN", detailPO.financialDetails?.entityGSTIN],
                    ["Entity PAN",   detailPO.financialDetails?.entityPAN],
                  ].filter(([, v]) => v).map(([label, value]) => (
                    <div key={label as string} className={label === "Entity Name" ? "col-span-2" : ""}>
                      <span className="text-gray-400">{label}: </span>
                      <span className="font-medium text-gray-800">{value as string}</span>
                    </div>
                  ))}
                </div>
              </section>

              {/* Store */}
              <section>
                <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Store / Location</h4>
                <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                  {[
                    ["To Store Code",   detailPO.toStoreCode],
                    ["To Store Name",   detailPO.toStoreName],
                    ["From Store Code", detailPO.fromStoreCode || null],
                    ["From Store Name", detailPO.fromStoreName || null],
                    ["Ship-to Address", detailPO.address?.storeShippingAddress],
                    ["Bill-to Address", (detailPO.address as any)?.storeBillingAddress],
                    ["Store Address",   detailPO.address?.storeAddress],
                  ].filter(([, v]) => v).map(([label, value]) => (
                    <div key={label as string} className={["Ship-to Address","Bill-to Address","Store Address"].includes(label as string) ? "col-span-2" : ""}>
                      <span className="text-gray-400">{label}: </span>
                      <span className="font-medium text-gray-800">{value as string}</span>
                    </div>
                  ))}
                </div>
              </section>

            </div>
            <div className="p-4 border-t flex justify-end">
              <button
                onClick={() => setDetailPO(null)}
                className="px-5 py-2 bg-violet-600 text-white rounded-lg text-sm hover:bg-violet-700 transition"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── SKU Detail Modal ─────────────────────────────────────────────────── */}
      {skuDetail && (
        <div
          className="fixed inset-0 bg-black/60 z-[60] flex items-start justify-center p-4 overflow-y-auto"
          onClick={() => setSkuDetail(null)}
        >
          <div
            className="bg-white rounded-xl w-full max-w-xl shadow-2xl my-8"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between p-5 border-b">
              <div>
                <h3 className="font-bold text-gray-800">{skuDetail.productName}</h3>
                <p className="font-mono text-xs text-violet-600 mt-0.5">{skuDetail.skuCode}</p>
              </div>
              <button onClick={() => setSkuDetail(null)} className="text-gray-400 hover:text-gray-700 text-lg">✕</button>
            </div>
            <div className="p-5 space-y-5 max-h-[75vh] overflow-y-auto text-sm">

              {/* Product Info */}
              <section>
                <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Product</h4>
                <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                  {[
                    ["SKU Code",     skuDetail.skuCode],
                    ["Material Code",skuDetail.materialCode || skuDetail.matetrialCode || null],
                    ["Product Name", skuDetail.productName],
                    ["Brand",        skuDetail.brandName],
                    ["Sub-Category", skuDetail.subCategory],
                    ["EAN",          skuDetail.ean],
                    ["Pack Size",    skuDetail.packSize != null ? `${skuDetail.packSize} g/ml` : null],
                    ["Ordered Qty",  `${skuDetail.quantity} PC`],
                  ].filter(([, v]) => v).map(([label, value]) => (
                    <div key={label as string} className={["Product Name","EAN"].includes(label as string) ? "col-span-2" : ""}>
                      <span className="text-gray-400">{label}: </span>
                      <span className="font-medium text-gray-800">{value as string}</span>
                    </div>
                  ))}
                </div>
              </section>

              {/* Pricing */}
              <section>
                <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Pricing</h4>
                <div className="grid grid-cols-3 gap-x-4 gap-y-2 text-xs">
                  {[
                    ["MRP",              skuDetail.mrp         != null ? `₹${skuDetail.mrp}`              : null],
                    ["Cost Price",       skuDetail.costPrice   != null ? `₹${skuDetail.costPrice}`        : null],
                    ["Total Amount",     skuDetail.totalAmount != null ? `₹${skuDetail.totalAmount?.toFixed(4)}` : null],
                    ["Tax-Excl. Cost",   skuDetail.taxExclusiveCost != null ? `₹${skuDetail.taxExclusiveCost?.toFixed(6)}` : null],
                    ["Margin",           skuDetail.margin      != null ? `${skuDetail.margin}%`            : null],
                    ["Dispatch Margin",  skuDetail.dispatchMargin != null ? `${skuDetail.dispatchMargin}%` : null],
                    ["Outbound Margin",  skuDetail.outboundMargin != null ? `${skuDetail.outboundMargin}%` : null],
                    ["Holding Margin",   skuDetail.holdingMargin  != null ? `${skuDetail.holdingMargin}%`  : null],
                    ["BFD Margin",       skuDetail.bfdMargin      != null ? `${skuDetail.bfdMargin}%`      : null],
                  ].filter(([, v]) => v).map(([label, value]) => (
                    <div key={label as string}>
                      <span className="text-gray-400">{label}: </span>
                      <span className="font-medium text-gray-800">{value as string}</span>
                    </div>
                  ))}
                </div>
              </section>

              {/* Tax */}
              <section>
                <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Tax</h4>
                <div className="grid grid-cols-3 gap-x-4 gap-y-2 text-xs">
                  {[
                    ["HSN Code",    skuDetail.hsnCode],
                    ["HSN Text",    skuDetail.hsnText],
                    ["Tax Logic",   skuDetail.taxLogic],
                    ["IGST %",      skuDetail.igstPercentage != null ? `${skuDetail.igstPercentage}%` : null],
                    ["CGST %",      skuDetail.cgstPercentage != null ? `${skuDetail.cgstPercentage}%` : null],
                    ["SGST %",      skuDetail.sgstPercentage != null ? `${skuDetail.sgstPercentage}%` : null],
                    ["CESS %",      skuDetail.cessPercentage != null ? `${skuDetail.cessPercentage}%` : null],
                    ["Abs. Cess",   skuDetail.absoluteCess  != null ? `₹${skuDetail.absoluteCess}`   : null],
                    ["IGST Val.",   skuDetail.igstValue      != null ? `₹${skuDetail.igstValue?.toFixed(6)}`  : null],
                    ["CGST Val.",   skuDetail.cgstValue      != null ? `₹${skuDetail.cgstValue?.toFixed(6)}`  : null],
                    ["SGST Val.",   skuDetail.sgstValue      != null ? `₹${skuDetail.sgstValue?.toFixed(6)}`  : null],
                    ["CESS Val.",   skuDetail.cessValue      != null ? `₹${skuDetail.cessValue?.toFixed(6)}`  : null],
                    ["Abs.Cess Val.",skuDetail.absoluteCessValue != null ? `₹${skuDetail.absoluteCessValue?.toFixed(6)}` : null],
                  ].filter(([, v]) => v).map(([label, value]) => (
                    <div key={label as string}>
                      <span className="text-gray-400">{label}: </span>
                      <span className="font-medium text-gray-800">{value as string}</span>
                    </div>
                  ))}
                </div>
              </section>

            </div>
            <div className="p-4 border-t flex justify-end">
              <button
                onClick={() => setSkuDetail(null)}
                className="px-5 py-2 bg-violet-600 text-white rounded-lg text-sm hover:bg-violet-700 transition"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
