import { useEffect, useState } from "react";
import { getUnmappedSKUs, resolveUnmappedSKU, getProducts } from "../api";

interface Alert {
  id: number;
  partner_code: string;
  partner_sku: string;
  partner_product_name: string | null;
  po_number: string | null;
  occurrences: number;
  resolved: boolean;
  resolution_notes: string | null;
  created_at: string;
}

interface Product { id: number; name: string; sku: string; sap_material_code: string | null }

export default function UnmappedSKUs() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [showResolved, setShowResolved] = useState(false);
  const [resolveModal, setResolveModal] = useState<Alert | null>(null);
  const [resolveForm, setResolveForm] = useState({ product_id: "", notes: "" });
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [aRes, pRes] = await Promise.all([
        getUnmappedSKUs(showResolved ? undefined : { resolved: false }),
        getProducts(),
      ]);
      setAlerts(aRes.data);
      setProducts(pRes.data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [showResolved]);

  const handleResolve = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resolveModal) return;
    setSaving(true);
    try {
      await resolveUnmappedSKU(resolveModal.id, {
        product_id: Number(resolveForm.product_id),
        resolution_notes: resolveForm.notes || undefined,
      });
      setResolveModal(null);
      setResolveForm({ product_id: "", notes: "" });
      load();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Error resolving SKU");
    } finally {
      setSaving(false);
    }
  };

  const unresolved = alerts.filter(a => !a.resolved).length;
  const resolved = alerts.filter(a => a.resolved).length;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Unmapped SKU Alerts</h1>
          <p className="text-gray-500 text-sm mt-1">
            Partner SKUs that couldn't be automatically mapped — requires human review
          </p>
        </div>
        <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-600">
          <input
            type="checkbox"
            checked={showResolved}
            onChange={e => setShowResolved(e.target.checked)}
            className="rounded"
          />
          Show resolved
        </label>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: "Pending Review", value: unresolved, color: "text-red-600", bg: unresolved > 0 ? "bg-red-50 border-red-200" : "bg-white border" },
          { label: "Resolved", value: resolved, color: "text-green-600", bg: "bg-white border" },
          { label: "Total Alerts", value: alerts.length, color: "text-gray-700", bg: "bg-white border" },
        ].map(s => (
          <div key={s.label} className={`rounded-xl ${s.bg} p-4 shadow-sm`}>
            <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
            <div className="text-gray-500 text-sm">{s.label}</div>
          </div>
        ))}
      </div>

      {unresolved > 0 && (
        <div className="flex items-center gap-3 bg-amber-50 border border-amber-200 rounded-xl p-4">
          <span className="text-2xl">⚠️</span>
          <div>
            <div className="font-semibold text-amber-800">Action Required</div>
            <div className="text-amber-700 text-sm">
              {unresolved} partner SKU{unresolved !== 1 ? "s" : ""} couldn't be matched to an internal product.
              POs containing these SKUs may have incomplete line items. Please review and map them.
            </div>
          </div>
        </div>
      )}

      {/* Alerts Table */}
      <div className="bg-white rounded-xl border shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-10 text-center text-gray-400">Loading alerts...</div>
        ) : alerts.length === 0 ? (
          <div className="p-10 text-center">
            <div className="text-4xl mb-3">🎉</div>
            <div className="text-gray-700 font-medium">All clear!</div>
            <div className="text-gray-400 text-sm">No unmapped SKU alerts.</div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  {["Partner", "Partner SKU", "Product Name", "Seen in PO", "Occurrences", "Status", "Flagged", "Action"].map(h => (
                    <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {alerts.map(a => (
                  <tr key={a.id} className={`hover:bg-gray-50 transition ${a.resolved ? "opacity-60" : ""}`}>
                    <td className="px-4 py-3">
                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-800">
                        {a.partner_code}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-700">{a.partner_sku}</td>
                    <td className="px-4 py-3 text-gray-600">{a.partner_product_name || "—"}</td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-500">{a.po_number || "—"}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-semibold ${a.occurrences > 3 ? "bg-red-100 text-red-700" : "bg-gray-100 text-gray-700"}`}>
                        {a.occurrences}×
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {a.resolved ? (
                        <span className="inline-flex px-2 py-0.5 rounded-full text-xs font-semibold bg-green-100 text-green-800">✅ Resolved</span>
                      ) : (
                        <span className="inline-flex px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800">⚠️ Pending</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-gray-500 text-xs">
                      {new Date(a.created_at).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}
                    </td>
                    <td className="px-4 py-3">
                      {!a.resolved ? (
                        <button
                          onClick={() => { setResolveModal(a); setResolveForm({ product_id: "", notes: "" }); }}
                          className="text-indigo-600 hover:text-indigo-800 text-xs font-medium bg-indigo-50 px-2 py-1 rounded"
                        >
                          Map → Resolve
                        </button>
                      ) : (
                        <span className="text-gray-400 text-xs">{a.resolution_notes || "—"}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Resolve Modal */}
      {resolveModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-gray-900">Resolve Unmapped SKU</h2>
              <button onClick={() => setResolveModal(null)} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
            </div>
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4 text-sm">
              <div className="font-medium text-amber-800">[{resolveModal.partner_code}] {resolveModal.partner_sku}</div>
              <div className="text-amber-700">{resolveModal.partner_product_name || "No name provided"}</div>
              {resolveModal.po_number && <div className="text-gray-500 text-xs mt-1">From PO: {resolveModal.po_number}</div>}
            </div>
            <form onSubmit={handleResolve} className="space-y-4">
              <div>
                <label className="block text-xs text-gray-600 mb-1 font-medium">Map to Internal Product *</label>
                <select
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-indigo-400 focus:border-indigo-400 outline-none"
                  value={resolveForm.product_id}
                  onChange={e => setResolveForm(p => ({ ...p, product_id: e.target.value }))}
                  required
                >
                  <option value="">Select product</option>
                  {products.map(p => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.sku}) {p.sap_material_code ? `— SAP: ${p.sap_material_code}` : ""}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-600 mb-1 font-medium">Resolution Notes</label>
                <input
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-indigo-400 focus:border-indigo-400 outline-none"
                  placeholder="e.g. Confirmed with Blinkit catalog team"
                  value={resolveForm.notes}
                  onChange={e => setResolveForm(p => ({ ...p, notes: e.target.value }))}
                />
              </div>
              <div className="flex gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => setResolveModal(null)}
                  className="flex-1 border border-gray-200 text-gray-600 py-2 rounded-lg text-sm hover:bg-gray-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="flex-1 bg-green-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
                >
                  {saving ? "Saving..." : "✅ Resolve & Create Mapping"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
