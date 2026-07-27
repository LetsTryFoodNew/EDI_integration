import { useState } from "react";
import {
  PackageCheck, Search, XCircle, AlertCircle, Loader2,
  CheckCircle, Info,
} from "lucide-react";
import { getBlinkitASNs, cancelBlinkitASN } from "../api";
import type { BlinkitTrackedASN } from "../types";
import blinkitLogo from "../assets/blinkit-logo.svg";

export default function BlinkitASN() {
  const [poNumber, setPONumber]           = useState("");
  const [asns, setASNs]                   = useState<BlinkitTrackedASN[]>([]);
  const [loading, setLoading]             = useState(false);
  const [searched, setSearched]           = useState(false);
  const [error, setError]                 = useState<string | null>(null);
  const [cancelling, setCancelling]       = useState<string | null>(null);
  const [searchedCode, setSearchedCode]   = useState("");
  const [cancelSuccess, setCancelSuccess] = useState<string | null>(null);

  const fetchASNs = async (code: string) => {
    if (!code.trim()) return;
    setLoading(true);
    setError(null);
    setCancelSuccess(null);
    try {
      const res  = await getBlinkitASNs(code.trim());
      const body = res.data?.data ?? res.data;
      const list = (body?.asns ?? []) as BlinkitTrackedASN[];
      setASNs(list);
      setSearched(true);
      setSearchedCode(code.trim());
    } catch (e: any) {
      setError(
        e.response?.data?.detail ??
        "Failed to fetch ASNs — check the PO number and try again",
      );
      setASNs([]);
      setSearched(true);
    }
    setLoading(false);
  };

  const handleSearch = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    fetchASNs(poNumber);
  };

  const handleCancel = async (asnId: string) => {
    if (!confirm(`Cancel ASN ${asnId}?\n\nThis removes it from local tracking so you can re-submit against the same PO.\nBlinkit has no cancel API — contact them if the shipment is already in transit.`)) return;
    setCancelling(asnId);
    try {
      const res = await cancelBlinkitASN(asnId, "VENDOR_REQUEST");
      if (res.data?.success === false) {
        alert(res.data?.message ?? "ASN not found in local tracking");
      } else {
        setCancelSuccess(res.data?.message ?? `ASN ${asnId} cancelled — allocated qty released.`);
        await fetchASNs(searchedCode);
      }
    } catch (e: any) {
      alert(e.response?.data?.detail ?? "Failed to cancel ASN");
    }
    setCancelling(null);
  };

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-center gap-3">
        <img src={blinkitLogo} alt="Blinkit" className="w-10 h-10 rounded-xl" />
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Blinkit ASN Manager</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Search locally-tracked ASNs submitted to Blinkit
          </p>
        </div>
      </div>

      {/* Info banner */}
      <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-800 space-y-1.5">
        <div className="flex items-start gap-2">
          <Info size={15} className="mt-0.5 flex-shrink-0" />
          <div className="space-y-1.5">
            <p>
              <strong>ASN endpoint (testing):</strong>{" "}
              <code className="bg-white border border-amber-200 rounded px-1 text-xs">
                POST https://dev.partnersbiz.com/webhook/public/v1/asn
              </code>
            </p>
            <p>
              To <strong>create</strong> an ASN → go to{" "}
              <a href="/blinkit/pos" className="underline font-medium">Blinkit PO Events</a>{" "}
              and click <strong>ASN</strong> on any received PO.
            </p>
            <p className="text-amber-700">
              <strong>Note:</strong> Blinkit has no List-ASNs API. Records shown here
              are tracked locally in our database when you submit an ASN through this system.
            </p>
          </div>
        </div>
      </div>

      {/* Search */}
      <form onSubmit={handleSearch} className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 flex gap-3">
        <input
          type="text"
          placeholder="Enter Blinkit PO ID (e.g. 2576310032189)"
          value={poNumber}
          onChange={e => setPONumber(e.target.value)}
          className="flex-1 border border-gray-200 rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
        />
        <button
          type="submit"
          disabled={loading || !poNumber.trim()}
          className="flex items-center gap-2 px-5 py-2 bg-amber-500 text-white rounded-lg text-sm hover:bg-amber-600 disabled:opacity-50 transition"
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
          Search
        </button>
      </form>

      {/* Cancel success */}
      {cancelSuccess && (
        <div className="flex items-center gap-2 bg-green-50 border border-green-200 rounded-xl p-3 text-sm text-green-700">
          <CheckCircle size={16} className="flex-shrink-0" />
          {cancelSuccess}
        </div>
      )}

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
              <p className="font-medium">No locally-tracked ASNs for PO {searchedCode}</p>
              <p className="text-sm mt-1 max-w-xs mx-auto">
                ASNs submitted before local tracking was enabled won't appear here.
                Create a new one from the{" "}
                <a href="/blinkit/pos" className="text-amber-600 hover:underline">Blinkit PO Events</a> page.
              </p>
            </div>
          ) : (
            <>
              <div className="px-5 py-3 border-b bg-gray-50">
                <span className="text-sm font-medium text-gray-700">
                  {asns.length} ASN{asns.length !== 1 ? "s" : ""} for PO{" "}
                  <span className="font-mono text-amber-700">{searchedCode}</span>
                </span>
              </div>

              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
                  <tr>
                    <th className="px-5 py-3 text-left">ASN ID</th>
                    <th className="px-5 py-3 text-left">Invoice Number</th>
                    <th className="px-5 py-3 text-right">Total Qty</th>
                    <th className="px-5 py-3 text-left">Submitted At</th>
                    <th className="px-5 py-3 text-center">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {asns.map(asn => (
                    <tr key={asn.asn_id} className="hover:bg-gray-50">
                      <td className="px-5 py-3 font-mono text-xs font-semibold text-amber-700">
                        {asn.asn_id}
                      </td>
                      <td className="px-5 py-3 font-mono text-xs text-gray-600">
                        {asn.invoice_number ?? "—"}
                      </td>
                      <td className="px-5 py-3 text-right font-semibold text-gray-700">
                        {asn.total_qty}
                      </td>
                      <td className="px-5 py-3 text-xs text-gray-500">
                        {asn.created_at
                          ? new Date(asn.created_at).toLocaleString("en-IN", {
                              day: "2-digit", month: "short", year: "numeric",
                              hour: "2-digit", minute: "2-digit",
                            })
                          : "—"}
                      </td>
                      <td className="px-5 py-3 text-center">
                        <button
                          onClick={() => handleCancel(asn.asn_id)}
                          disabled={cancelling === asn.asn_id}
                          className="inline-flex items-center gap-1 px-3 py-1.5 border border-red-200 text-red-600 text-xs rounded-lg hover:bg-red-50 disabled:opacity-50 transition"
                        >
                          <XCircle size={11} />
                          {cancelling === asn.asn_id ? "Cancelling…" : "Cancel"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* Item breakdown */}
              <div className="border-t border-gray-100 p-5 space-y-4">
                <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
                  ASN Item Breakdown
                </h4>
                {asns.map(asn => (
                  <div key={asn.asn_id} className="border border-gray-100 rounded-lg overflow-hidden">
                    <div className="bg-amber-50 px-4 py-2 flex items-center gap-3 text-xs">
                      <span className="font-mono font-semibold text-amber-700">{asn.asn_id}</span>
                      <span className="text-gray-500">Invoice: {asn.invoice_number ?? "—"}</span>
                    </div>
                    <table className="w-full text-xs">
                      <thead className="bg-gray-50 text-gray-500">
                        <tr>
                          <th className="px-4 py-2 text-left">Product ID</th>
                          <th className="px-4 py-2 text-left">SKU Code</th>
                          <th className="px-4 py-2 text-right">Invoiced Qty</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-50">
                        {asn.items.map((item, i) => (
                          <tr key={i} className="hover:bg-gray-50">
                            <td className="px-4 py-1.5 font-mono text-amber-700">{item.item_id || "—"}</td>
                            <td className="px-4 py-1.5 text-gray-600">{item.sku_code || "—"}</td>
                            <td className="px-4 py-1.5 text-right font-semibold text-gray-700">{item.invoiced_qty}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
