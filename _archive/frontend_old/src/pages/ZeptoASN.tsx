import { useState } from "react";
import {
  PackageCheck, Search, XCircle, AlertCircle, Loader2,
  Pencil, CheckCircle, Info,
} from "lucide-react";
import { getZeptoASNs, cancelZeptoASN, createZeptoASN, getZeptoPOEvents } from "../api";
import type { ZeptoASN, ZeptoASNListData, ZeptoPO, ZeptoPOListData, ZeptoPOLineItem } from "../types";

const ASN_STATUS_STYLE: Record<string, string> = {
  CREATED:   "bg-blue-100 text-blue-700 border-blue-300",
  ACCEPTED:  "bg-green-100 text-green-700 border-green-300",
  INVOICED:  "bg-purple-100 text-purple-700 border-purple-300",
  REJECTED:  "bg-red-100 text-red-700 border-red-300",
  CANCELLED: "bg-gray-100 text-gray-600 border-gray-300",
  DELIVERED: "bg-emerald-100 text-emerald-700 border-emerald-300",
};

// Statuses where Zepto still allows cancellation (= editable via cancel+recreate)
const EDITABLE_STATUSES = new Set(["CREATED", "ACCEPTED"]);

interface EditLine {
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

interface EditForm {
  newInvoiceNumber: string;
  invoiceDate: string;
  deliveryDate: string;
  lines: EditLine[];
}

function linesFromPO(po: ZeptoPO, asnQty: number): EditLine[] {
  const items: ZeptoPOLineItem[] = po.poLineItems ?? [];
  const totalPoQty = items.reduce((s, i) => s + i.quantity, 0) || 1;
  let distributed = 0;
  return items.map((item, idx) => {
    const isLast = idx === items.length - 1;
    const qty = isLast
      ? asnQty - distributed
      : Math.floor((item.quantity / totalPoQty) * asnQty);
    distributed += qty;
    return {
      skuCode:      item.skuCode,
      materialCode: item.materialCode ?? (item as any).matetrialCode ?? "",
      productName:  item.productName,
      orderedQty:   item.quantity,
      invoicedQty:  Math.max(0, qty),
      mrp:          item.mrp,
      basePrice:    item.costPrice,
      batchNumber:  "",
      expiryDate:   "",
    };
  });
}

export default function ZeptoASN() {
  const [poCode, setPOCode]             = useState("");
  const [asns, setASNs]                 = useState<ZeptoASN[]>([]);
  const [loading, setLoading]           = useState(false);
  const [searched, setSearched]         = useState(false);
  const [error, setError]               = useState<string | null>(null);
  const [cancelling, setCancelling]     = useState<string | null>(null);
  const [hasNext, setHasNext]           = useState(false);
  const [page, setPage]                 = useState(1);
  const [searchedCode, setSearchedCode] = useState("");

  // Edit modal state
  const [editModal, setEditModal]         = useState<ZeptoASN | null>(null);
  const [editPO, setEditPO]               = useState<ZeptoPO | null>(null);
  const [editForm, setEditForm]           = useState<EditForm>({ newInvoiceNumber: "", invoiceDate: "", deliveryDate: "", lines: [] });
  const [editLoading, setEditLoading]     = useState(false);
  const [editSubmitting, setEditSubmitting] = useState(false);
  const [editError, setEditError]         = useState<string | null>(null);
  const [editResult, setEditResult]       = useState<string | null>(null);

  const fetchASNs = async (code: string, p = 1) => {
    if (!code.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await getZeptoASNs(code.trim(), { page_size: 10, page_number: p });
      const data = res.data?.data?.data as ZeptoASNListData | undefined;
      setASNs(data?.ASNs ?? []);
      setHasNext(data?.hasNext ?? false);
      setSearched(true);
      setSearchedCode(code.trim());
    } catch (e: any) {
      setError(e.response?.data?.detail ?? "Failed to fetch ASNs — check the PO number and try again");
      setASNs([]);
      setSearched(true);
    }
    setLoading(false);
  };

  const handleSearch = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setPage(1);
    fetchASNs(poCode, 1);
  };

  const handleCancel = async (asnNumber: string) => {
    if (!confirm(`Cancel ASN ${asnNumber}?\n\nThis cannot be undone. To update an ASN use the Edit button instead.`)) return;
    setCancelling(asnNumber);
    try {
      await cancelZeptoASN(asnNumber);
      await fetchASNs(searchedCode, page);
    } catch (e: any) {
      alert(e.response?.data?.detail ?? "Failed to cancel ASN");
    }
    setCancelling(null);
  };

  // ── Edit flow ────────────────────────────────────────────────────────────────
  const openEditModal = async (asn: ZeptoASN) => {
    setEditError(null);
    setEditResult(null);
    setEditPO(null);
    const suggestedInvoice = asn.invoiceNumber
      ? `${asn.invoiceNumber}-R1`
      : "";
    setEditForm({ newInvoiceNumber: suggestedInvoice, invoiceDate: new Date().toISOString().split("T")[0], deliveryDate: "", lines: [] });
    setEditModal(asn);
    setEditLoading(true);
    try {
      const poNum = asn.poNumber ?? searchedCode;
      const res = await getZeptoPOEvents({ days: 45, po_codes: poNum, include_line_item_details: true, page_size: 1 });
      const data = res.data?.data?.data as ZeptoPOListData | undefined;
      const po   = data?.purchaseOrders?.[0] ?? null;
      setEditPO(po);
      if (po) {
        setEditForm(f => ({
          ...f,
          deliveryDate: po.deliveryDate ? po.deliveryDate.split("T")[0] : "",
          lines: linesFromPO(po, asn.asnQuantity ?? 0),
        }));
      }
    } catch {
      setEditError("Could not load PO line items — enter quantities manually below.");
    }
    setEditLoading(false);
  };

  const handleEditSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!editModal) return;
    if (editForm.newInvoiceNumber.trim() === (editModal.invoiceNumber ?? "").trim()) {
      setEditError("New invoice number must be different from the original.");
      return;
    }
    setEditSubmitting(true);
    setEditError(null);

    // Step 1 — cancel original
    try {
      await cancelZeptoASN(editModal.asnNumber);
    } catch (e: any) {
      setEditError(`Failed to cancel original ASN: ${e.response?.data?.detail ?? "Unknown error"}`);
      setEditSubmitting(false);
      return;
    }

    // Step 2 — create replacement
    const totalAmount = editForm.lines.reduce((s, l) => s + l.invoicedQty * l.basePrice, 0);
    const poNumber    = editModal.poNumber ?? searchedCode;
    const vendorId    = editPO?.vendorCode ?? editModal.vendor ?? "";
    const payload = {
      purchaseOrderDetails: { purchaseOrderNumber: poNumber },
      invoiceDetails: {
        invoiceNumber: editForm.newInvoiceNumber.trim(),
        invoiceType:   "SSI",
        invoiceDate:   editForm.invoiceDate,
        ...(editForm.deliveryDate ? { deliveryDate: editForm.deliveryDate } : {}),
        dueDate:       editForm.invoiceDate,
      },
      invoiceTotals: {
        currencyCode:     "INR",
        discountDetails:  { totalDiscountAmount: 0 },
        taxableAmount:    parseFloat(totalAmount.toFixed(2)),
        grandTotalAmount: parseFloat(totalAmount.toFixed(2)),
      },
      itemDetails: editForm.lines
        .filter(l => l.invoicedQty > 0)
        .map(l => ({
          productIdentifier: {
            buyerProductIdentifier: {
              skuCode:      l.skuCode,
              materialCode: l.materialCode || undefined,
            },
          },
          ...(l.batchNumber ? { batchDetails: { batchNumber: l.batchNumber, expiryDate: l.expiryDate || "" } } : {}),
          quantity: {
            invoicedQuantity: { amount: l.invoicedQty, unitOfMeasure: "PC" },
            freeQuantity:     { amount: 0,              unitOfMeasure: "PC" },
          },
          mrp:       l.mrp,
          basePrice: l.basePrice,
        })),
      seller: { soldFrom: { id: vendorId } },
    };

    try {
      const res = await createZeptoASN(payload);
      const num = res.data?.asn_number ?? res.data?.data?.data?.asnNumber ?? res.data?.data?.asnNumber;
      setEditResult(num ?? "ASN Updated");
      await fetchASNs(searchedCode, page);
    } catch (err: any) {
      // Original ASN is already cancelled at this point — warn the user explicitly
      setEditError(
        `⚠️ Original ASN ${editModal.asnNumber} was cancelled but the new ASN could not be created: ` +
        (err.response?.data?.detail ?? "Unknown error") +
        " — please create the new ASN manually from the PO Events page."
      );
    }
    setEditSubmitting(false);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <PackageCheck size={24} className="text-violet-600" />
          Zepto ASN Manager
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Search and manage Advance Shipment Notifications submitted to Zepto
        </p>
      </div>

      {/* Info banner */}
      <div className="bg-violet-50 border border-violet-200 rounded-xl p-4 text-sm text-violet-800 space-y-1">
        <div>
          <strong>How to use:</strong> Enter a Zepto PO Number (e.g.{" "}
          <code className="bg-white border border-violet-200 rounded px-1 text-xs">P364929</code>
          ) to see all ASNs against it.
        </div>
        <div>
          To <strong>create</strong> an ASN → go to{" "}
          <a href="/zepto/pos" className="underline font-medium">Zepto PO Events</a> and click <strong>ASN</strong> on a released PO.
        </div>
        <div>
          To <strong>edit</strong> an ASN → click <strong>Edit</strong> on a CREATED or ACCEPTED ASN.
          Zepto has no direct update API — editing cancels the original and creates a new one with a different invoice number.
        </div>
      </div>

      {/* Search */}
      <form onSubmit={handleSearch} className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 flex gap-3">
        <input
          type="text"
          placeholder="Enter Zepto PO Number (e.g. P364929)"
          value={poCode}
          onChange={e => setPOCode(e.target.value)}
          className="flex-1 border border-gray-200 rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-400"
        />
        <button
          type="submit"
          disabled={loading || !poCode.trim()}
          className="flex items-center gap-2 px-5 py-2 bg-violet-600 text-white rounded-lg text-sm hover:bg-violet-700 disabled:opacity-50 transition"
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
          Search
        </button>
      </form>

      {/* Error */}
      {error && (
        <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
          <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
          {error}
        </div>
      )}

      {/* Results */}
      {searched && !error && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
          {asns.length === 0 ? (
            <div className="text-center py-16 text-gray-400">
              <PackageCheck size={40} className="mx-auto mb-3 text-gray-300" />
              <p className="font-medium">No ASNs found for {searchedCode}</p>
              <p className="text-sm mt-1">
                Create one from the{" "}
                <a href="/zepto/pos" className="text-violet-600 hover:underline">Zepto PO Events</a> page
              </p>
            </div>
          ) : (
            <>
              <div className="px-5 py-3 border-b bg-gray-50 flex items-center justify-between">
                <span className="text-sm font-medium text-gray-700">
                  {asns.length} ASN{asns.length !== 1 ? "s" : ""} for PO{" "}
                  <span className="font-mono text-violet-700">{searchedCode}</span>
                </span>
                <div className="flex items-center gap-2 text-sm text-gray-500">
                  Page {page}
                  <button
                    disabled={page <= 1}
                    onClick={() => { const p = page - 1; setPage(p); fetchASNs(searchedCode, p); }}
                    className="px-2 py-1 border rounded disabled:opacity-40 hover:bg-gray-100"
                  >←</button>
                  <button
                    disabled={!hasNext}
                    onClick={() => { const p = page + 1; setPage(p); fetchASNs(searchedCode, p); }}
                    className="px-2 py-1 border rounded disabled:opacity-40 hover:bg-gray-100"
                  >→</button>
                </div>
              </div>

              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
                  <tr>
                    <th className="px-5 py-3 text-left">ASN Number</th>
                    <th className="px-5 py-3 text-left">Invoice Number</th>
                    <th className="px-5 py-3 text-left">Vendor</th>
                    <th className="px-5 py-3 text-right">PO Qty</th>
                    <th className="px-5 py-3 text-right">ASN Qty</th>
                    <th className="px-5 py-3 text-center">Status</th>
                    <th className="px-5 py-3 text-right">Total Amount</th>
                    <th className="px-5 py-3 text-center">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {asns.map(asn => {
                    const isFinal   = asn.status === "CANCELLED" || asn.status === "DELIVERED";
                    const isEditable = EDITABLE_STATUSES.has(asn.status);
                    return (
                      <tr key={asn.asnNumber} className="hover:bg-gray-50">
                        <td className="px-5 py-3 font-mono text-xs font-semibold text-violet-700">
                          {asn.asnNumber}
                        </td>
                        <td className="px-5 py-3 font-mono text-xs text-gray-600">
                          {asn.invoiceNumber ?? "—"}
                        </td>
                        <td className="px-5 py-3 text-xs text-gray-600">
                          <div className="font-medium">{asn.vendor ?? "—"}</div>
                          {asn.locationCode && <div className="text-gray-400">{asn.locationCode}</div>}
                        </td>
                        <td className="px-5 py-3 text-right text-gray-600 text-xs">{asn.poQuantity ?? "—"}</td>
                        <td className="px-5 py-3 text-right text-gray-600 text-xs font-semibold">{asn.asnQuantity ?? "—"}</td>
                        <td className="px-5 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${ASN_STATUS_STYLE[asn.status] ?? "bg-gray-100 text-gray-600 border-gray-200"}`}>
                            {asn.status}
                          </span>
                        </td>
                        <td className="px-5 py-3 text-right text-gray-700 font-medium text-xs">
                          {asn.asnTotalAmount != null
                            ? `₹${asn.asnTotalAmount.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
                            : "—"}
                        </td>
                        <td className="px-5 py-3 text-center">
                          {isFinal ? (
                            <span className="text-xs text-gray-400">
                              {asn.status === "DELIVERED" ? "✓ Delivered" : "Cancelled"}
                            </span>
                          ) : (
                            <div className="flex items-center gap-1.5 justify-center">
                              {isEditable && (
                                <button
                                  onClick={() => openEditModal(asn)}
                                  className="inline-flex items-center gap-1 px-3 py-1.5 border border-violet-200 text-violet-700 text-xs rounded-lg hover:bg-violet-50 transition"
                                >
                                  <Pencil size={11} /> Edit
                                </button>
                              )}
                              <button
                                onClick={() => handleCancel(asn.asnNumber)}
                                disabled={cancelling === asn.asnNumber}
                                className="inline-flex items-center gap-1 px-3 py-1.5 border border-red-200 text-red-600 text-xs rounded-lg hover:bg-red-50 disabled:opacity-50 transition"
                              >
                                <XCircle size={11} />
                                {cancelling === asn.asnNumber ? "Cancelling…" : "Cancel"}
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}

      {/* ── Edit ASN Modal ───────────────────────────────────────────────────── */}
      {editModal && (
        <div
          className="fixed inset-0 bg-black/50 z-50 flex items-start justify-center p-4 overflow-y-auto"
          onClick={() => !editSubmitting && setEditModal(null)}
        >
          <div
            className="bg-white rounded-xl w-full max-w-2xl shadow-2xl my-8"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between p-5 border-b">
              <div>
                <h3 className="font-bold text-gray-800 flex items-center gap-2">
                  <Pencil size={16} className="text-violet-600" />
                  Edit ASN — {editModal.asnNumber}
                </h3>
                <p className="text-xs text-gray-500 mt-0.5">
                  PO: {editModal.poNumber ?? searchedCode} · Original invoice: {editModal.invoiceNumber ?? "—"}
                </p>
              </div>
              {!editSubmitting && (
                <button onClick={() => setEditModal(null)} className="text-gray-400 hover:text-gray-700 text-lg">✕</button>
              )}
            </div>

            {editResult ? (
              <div className="p-10 text-center">
                <CheckCircle size={48} className="mx-auto mb-4 text-green-500" />
                <h4 className="font-bold text-gray-800 text-lg mb-1">ASN Updated Successfully</h4>
                <p className="text-gray-500 text-sm mb-1">Original ASN cancelled. New Zepto ASN Number:</p>
                <div className="inline-block bg-green-50 border border-green-200 rounded-lg px-5 py-3 font-mono text-green-800 font-bold text-xl mb-5">
                  {editResult}
                </div>
                <div className="block">
                  <button onClick={() => setEditModal(null)} className="px-6 py-2 bg-violet-600 text-white rounded-lg text-sm hover:bg-violet-700 transition">
                    Done
                  </button>
                </div>
              </div>
            ) : (
              <form onSubmit={handleEditSubmit} className="p-5 space-y-5">
                {/* How edit works */}
                <div className="flex items-start gap-2 bg-blue-50 border border-blue-200 rounded-lg p-3 text-xs text-blue-800">
                  <Info size={14} className="mt-0.5 flex-shrink-0" />
                  <span>
                    Zepto has no direct update API. Clicking <strong>Update ASN</strong> will:
                    (1) cancel <span className="font-mono">{editModal.asnNumber}</span>, then
                    (2) create a new ASN with your new invoice number. The new invoice number must be unique.
                  </span>
                </div>

                {editError && (
                  <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
                    <AlertCircle size={15} className="mt-0.5 flex-shrink-0" />
                    <span className="break-all">{editError}</span>
                  </div>
                )}

                {/* Invoice fields */}
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">New Invoice Number *</label>
                    <input
                      required type="text"
                      placeholder="Must be different"
                      value={editForm.newInvoiceNumber}
                      onChange={e => setEditForm({ ...editForm, newInvoiceNumber: e.target.value })}
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-400"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Invoice Date *</label>
                    <input
                      required type="date"
                      value={editForm.invoiceDate}
                      onChange={e => setEditForm({ ...editForm, invoiceDate: e.target.value })}
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-400"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Delivery Date</label>
                    <input
                      type="date"
                      value={editForm.deliveryDate}
                      onChange={e => setEditForm({ ...editForm, deliveryDate: e.target.value })}
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-400"
                    />
                  </div>
                </div>

                {/* Line Items */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <label className="text-xs font-semibold text-gray-700 uppercase tracking-wider">Line Items</label>
                    <span className="text-xs text-gray-400">
                      {editLoading ? "Loading PO items…" : `${editForm.lines.length} items · quantities in pieces (PC)`}
                    </span>
                  </div>

                  {editLoading ? (
                    <div className="flex items-center gap-2 text-xs text-gray-400 bg-gray-50 rounded-lg px-3 py-4 justify-center">
                      <Loader2 size={14} className="animate-spin" /> Loading PO line items from Zepto…
                    </div>
                  ) : editForm.lines.length === 0 ? (
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-700">
                      Could not load PO line items. Check the error above or try again.
                    </div>
                  ) : (
                    <div className="border border-gray-100 rounded-lg overflow-hidden max-h-64 overflow-y-auto">
                      <table className="w-full text-xs">
                        <thead className="bg-gray-50 text-gray-500 sticky top-0">
                          <tr>
                            <th className="px-3 py-2 text-left">Product</th>
                            <th className="px-3 py-2 text-right">PO Ordered</th>
                            <th className="px-3 py-2 text-center w-24">Invoice Qty *</th>
                            <th className="px-3 py-2 text-left w-28">Batch No.</th>
                            <th className="px-3 py-2 text-left w-28">Expiry Date</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-50">
                          {editForm.lines.map((line, idx) => (
                            <tr key={idx} className="hover:bg-gray-50">
                              <td className="px-3 py-2">
                                <div className="font-mono text-violet-700">{line.skuCode}</div>
                                <div className="text-gray-500 truncate max-w-[180px]">{line.productName}</div>
                              </td>
                              <td className="px-3 py-2 text-right text-gray-500">{line.orderedQty} PC</td>
                              <td className="px-3 py-2">
                                <input
                                  required type="number" min="0" max={line.orderedQty}
                                  value={line.invoicedQty}
                                  onChange={e => {
                                    const l = [...editForm.lines];
                                    l[idx] = { ...l[idx], invoicedQty: Number(e.target.value) };
                                    setEditForm({ ...editForm, lines: l });
                                  }}
                                  className="w-full border border-gray-200 rounded px-2 py-1 text-center focus:outline-none focus:ring-1 focus:ring-violet-400"
                                />
                              </td>
                              <td className="px-3 py-2">
                                <input
                                  type="text" placeholder="Optional"
                                  value={line.batchNumber}
                                  onChange={e => {
                                    const l = [...editForm.lines];
                                    l[idx] = { ...l[idx], batchNumber: e.target.value };
                                    setEditForm({ ...editForm, lines: l });
                                  }}
                                  className="w-full border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-violet-400"
                                />
                              </td>
                              <td className="px-3 py-2">
                                <input
                                  type="date"
                                  value={line.expiryDate}
                                  onChange={e => {
                                    const l = [...editForm.lines];
                                    l[idx] = { ...l[idx], expiryDate: e.target.value };
                                    setEditForm({ ...editForm, lines: l });
                                  }}
                                  className="w-full border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-violet-400"
                                />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <div className="flex gap-3 pt-2 border-t border-gray-100">
                  <button
                    type="button"
                    onClick={() => setEditModal(null)}
                    disabled={editSubmitting}
                    className="flex-1 px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50 transition"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={editSubmitting || editLoading || editForm.lines.every(l => l.invoicedQty === 0)}
                    className="flex-1 px-4 py-2 bg-violet-600 text-white rounded-lg text-sm hover:bg-violet-700 disabled:opacity-50 transition"
                  >
                    {editSubmitting ? (
                      <span className="flex items-center justify-center gap-2">
                        <Loader2 size={14} className="animate-spin" /> Updating…
                      </span>
                    ) : "Update ASN (Cancel + Recreate)"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
