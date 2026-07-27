import { useEffect, useState } from "react";
import {
  getProductMappings, createProductMapping, deleteProductMapping,
  getProducts, getCompanies,
} from "../api";

interface Mapping {
  id: number;
  partner_code: string;
  partner_sku: string;
  partner_product_name: string | null;
  product_id: number;
  sap_material_code: string | null;
  is_active: boolean;
  confidence_score: number;
  mapped_by: string;
  notes: string | null;
  created_at: string;
  product: { id: number; name: string; sku: string } | null;
}

interface Product { id: number; name: string; sku: string; sap_material_code: string | null }
interface Company { id: number; name: string; code: string }

const CONFIDENCE_COLORS: Record<string, string> = {
  high: "bg-green-100 text-green-800",
  medium: "bg-yellow-100 text-yellow-800",
  low: "bg-red-100 text-red-800",
};

function confidenceLabel(score: number) {
  if (score >= 0.95) return { text: `${(score * 100).toFixed(0)}%`, color: CONFIDENCE_COLORS.high };
  if (score >= 0.7)  return { text: `${(score * 100).toFixed(0)}%`, color: CONFIDENCE_COLORS.medium };
  return { text: `${(score * 100).toFixed(0)}%`, color: CONFIDENCE_COLORS.low };
}

export default function ProductMappings() {
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterPartner, setFilterPartner] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [form, setForm] = useState({
    partner_code: "",
    partner_sku: "",
    partner_product_name: "",
    product_id: "",
    notes: "",
  });
  const [saving, setSaving] = useState(false);
  const [resolveInput, setResolveInput] = useState({ partner_code: "", partner_sku: "", partner_name: "" });
  const [resolveResult, setResolveResult] = useState<any>(null);
  const [resolving, setResolving] = useState(false);

  const load = async (partner?: string) => {
    setLoading(true);
    try {
      const params = partner ? { partner_code: partner } : {};
      const [mRes, pRes, cRes] = await Promise.all([
        getProductMappings(params),
        getProducts(),
        getCompanies(),
      ]);
      setMappings(mRes.data);
      setProducts(pRes.data);
      setCompanies(cRes.data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleFilter = (code: string) => {
    setFilterPartner(code);
    load(code || undefined);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      const product = products.find(p => p.id === Number(form.product_id));
      await createProductMapping({
        partner_code: form.partner_code,
        partner_sku: form.partner_sku,
        partner_product_name: form.partner_product_name || null,
        product_id: Number(form.product_id),
        sap_material_code: product?.sap_material_code || null,
        notes: form.notes || null,
      });
      setShowForm(false);
      setForm({ partner_code: "", partner_sku: "", partner_product_name: "", product_id: "", notes: "" });
      load(filterPartner || undefined);
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Error creating mapping");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this mapping?")) return;
    setDeleting(id);
    try {
      await deleteProductMapping(id);
      setMappings(prev => prev.filter(m => m.id !== id));
    } finally {
      setDeleting(null);
    }
  };

  const handleResolve = async (e: React.FormEvent) => {
    e.preventDefault();
    setResolving(true);
    setResolveResult(null);
    try {
      const res = await import("../api").then(m => m.resolveProductSKU({
        partner_code: resolveInput.partner_code,
        partner_sku: resolveInput.partner_sku,
        partner_name: resolveInput.partner_name || undefined,
      }));
      setResolveResult({ success: true, data: res.data });
    } catch (err: any) {
      setResolveResult({ success: false, error: err?.response?.data?.detail || "Not found" });
    } finally {
      setResolving(false);
    }
  };

  const partnerCodes = Array.from(new Set(companies.map(c => c.code)));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Product Mappings</h1>
          <p className="text-gray-500 text-sm mt-1">
            Map partner SKUs (Blinkit, Zepto, Swiggy) to your internal SAP product codes
          </p>
        </div>
        <button
          onClick={() => setShowForm(true)}
          className="bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 flex items-center gap-2 text-sm font-medium"
        >
          <span className="text-lg leading-none">+</span> Add Mapping
        </button>
      </div>

      {/* SKU Resolver Tool */}
      <div className="bg-white border border-indigo-100 rounded-xl p-5 shadow-sm">
        <h2 className="font-semibold text-gray-800 mb-3 flex items-center gap-2">
          <span className="text-indigo-500">🔍</span> Live SKU Resolver
        </h2>
        <form onSubmit={handleResolve} className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Partner Code</label>
            <select
              className="border rounded-lg px-3 py-2 text-sm focus:ring-indigo-400 focus:border-indigo-400 outline-none"
              value={resolveInput.partner_code}
              onChange={e => setResolveInput(p => ({ ...p, partner_code: e.target.value }))}
              required
            >
              <option value="">Select partner</option>
              {partnerCodes.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Partner SKU</label>
            <input
              className="border rounded-lg px-3 py-2 text-sm focus:ring-indigo-400 focus:border-indigo-400 outline-none"
              placeholder="e.g. BLK-NK-001"
              value={resolveInput.partner_sku}
              onChange={e => setResolveInput(p => ({ ...p, partner_sku: e.target.value }))}
              required
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Product Name (optional)</label>
            <input
              className="border rounded-lg px-3 py-2 text-sm focus:ring-indigo-400 focus:border-indigo-400 outline-none"
              placeholder="e.g. Let's Try Namkin"
              value={resolveInput.partner_name}
              onChange={e => setResolveInput(p => ({ ...p, partner_name: e.target.value }))}
            />
          </div>
          <button
            type="submit"
            disabled={resolving}
            className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
          >
            {resolving ? "Resolving..." : "Resolve →"}
          </button>
        </form>
        {resolveResult && (
          <div className={`mt-3 rounded-lg p-3 text-sm ${resolveResult.success ? "bg-green-50 border border-green-200" : "bg-red-50 border border-red-200"}`}>
            {resolveResult.success ? (
              <div className="space-y-1">
                <div className="font-medium text-green-800">✅ Resolved Successfully</div>
                <div className="text-green-700">Internal Product: <strong>{resolveResult.data.internal_product}</strong></div>
                <div className="text-green-700">SAP Material Code: <strong>{resolveResult.data.sap_material_code}</strong></div>
                <div className="text-green-700">Confidence: <strong>{(resolveResult.data.confidence * 100).toFixed(0)}%</strong></div>
                <div className="text-gray-600 text-xs mt-1 font-mono">{resolveResult.data.log}</div>
              </div>
            ) : (
              <div className="text-red-800">❌ {resolveResult.error}</div>
            )}
          </div>
        )}
      </div>

      {/* Filter bar */}
      <div className="flex gap-2 flex-wrap">
        <button
          onClick={() => handleFilter("")}
          className={`px-3 py-1.5 rounded-full text-sm font-medium transition ${!filterPartner ? "bg-indigo-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
        >
          All
        </button>
        {partnerCodes.map(code => (
          <button
            key={code}
            onClick={() => handleFilter(code)}
            className={`px-3 py-1.5 rounded-full text-sm font-medium transition ${filterPartner === code ? "bg-indigo-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
          >
            {code}
          </button>
        ))}
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: "Total Mappings", value: mappings.length, color: "text-indigo-600" },
          { label: "Manual (Verified)", value: mappings.filter(m => m.mapped_by === "MANUAL").length, color: "text-green-600" },
          { label: "Auto-Mapped (Review)", value: mappings.filter(m => m.mapped_by === "AUTO").length, color: "text-yellow-600" },
        ].map(s => (
          <div key={s.label} className="bg-white rounded-xl border p-4 shadow-sm">
            <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
            <div className="text-gray-500 text-sm">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-10 text-center text-gray-400">Loading mappings...</div>
        ) : mappings.length === 0 ? (
          <div className="p-10 text-center text-gray-400">
            No mappings found. Add your first mapping above.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  {["Partner", "Partner SKU", "Partner Name", "→ Internal Product", "SAP Code", "Confidence", "By", "Actions"].map(h => (
                    <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {mappings.map(m => {
                  const conf = confidenceLabel(m.confidence_score);
                  return (
                    <tr key={m.id} className="hover:bg-gray-50 transition">
                      <td className="px-4 py-3">
                        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-800">
                          {m.partner_code}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-gray-700">{m.partner_sku}</td>
                      <td className="px-4 py-3 text-gray-600 max-w-[160px] truncate" title={m.partner_product_name || ""}>{m.partner_product_name || "—"}</td>
                      <td className="px-4 py-3 font-medium text-gray-900">
                        {m.product?.name || <span className="text-red-400">Missing product</span>}
                        <div className="text-xs text-gray-400">{m.product?.sku}</div>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-indigo-700">{m.sap_material_code || "—"}</td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-semibold ${conf.color}`}>
                          {conf.text}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs font-medium ${m.mapped_by === "MANUAL" ? "text-green-700" : "text-yellow-700"}`}>
                          {m.mapped_by === "MANUAL" ? "✅ Manual" : "⚠️ Auto"}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <button
                          onClick={() => handleDelete(m.id)}
                          disabled={deleting === m.id}
                          className="text-red-500 hover:text-red-700 text-xs font-medium disabled:opacity-40"
                        >
                          {deleting === m.id ? "..." : "Delete"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Add Mapping Modal */}
      {showForm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-gray-900">Add Product Mapping</h2>
              <button onClick={() => setShowForm(false)} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
            </div>
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-gray-600 mb-1 font-medium">Partner Code *</label>
                  <select
                    className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-indigo-400 focus:border-indigo-400 outline-none"
                    value={form.partner_code}
                    onChange={e => setForm(p => ({ ...p, partner_code: e.target.value }))}
                    required
                  >
                    <option value="">Select partner</option>
                    {partnerCodes.map(c => <option key={c} value={c}>{c}</option>)}
                    <option value="OTHER">OTHER</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-600 mb-1 font-medium">Partner SKU *</label>
                  <input
                    className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-indigo-400 focus:border-indigo-400 outline-none"
                    placeholder="e.g. BLK-NK-001"
                    value={form.partner_sku}
                    onChange={e => setForm(p => ({ ...p, partner_sku: e.target.value }))}
                    required
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs text-gray-600 mb-1 font-medium">Partner Product Name (reference)</label>
                <input
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-indigo-400 focus:border-indigo-400 outline-none"
                  placeholder="e.g. Let's Try Namkin 200g"
                  value={form.partner_product_name}
                  onChange={e => setForm(p => ({ ...p, partner_product_name: e.target.value }))}
                />
              </div>
              <div>
                <label className="block text-xs text-gray-600 mb-1 font-medium">Internal Product *</label>
                <select
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-indigo-400 focus:border-indigo-400 outline-none"
                  value={form.product_id}
                  onChange={e => setForm(p => ({ ...p, product_id: e.target.value }))}
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
                <label className="block text-xs text-gray-600 mb-1 font-medium">Notes</label>
                <input
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-indigo-400 focus:border-indigo-400 outline-none"
                  placeholder="Optional notes"
                  value={form.notes}
                  onChange={e => setForm(p => ({ ...p, notes: e.target.value }))}
                />
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowForm(false)}
                  className="flex-1 border border-gray-200 text-gray-600 py-2 rounded-lg text-sm hover:bg-gray-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="flex-1 bg-indigo-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
                >
                  {saving ? "Saving..." : "Create Mapping"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
