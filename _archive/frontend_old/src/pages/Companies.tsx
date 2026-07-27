import { useEffect, useState } from "react";
import { Building2, Plus, RefreshCcw, Mail, Wifi, WifiOff } from "lucide-react";
import { getCompanies, createCompany } from "../api";
import { Link } from "react-router-dom";
import type { Company } from "../types";
import CompanyAvatar from "../components/CompanyAvatar";
import { formatDate } from "../utils";

const COLORS = ["#6366f1", "#f97316", "#eab308", "#22c55e", "#3b82f6", "#ec4899", "#14b8a6"];

export default function Companies() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: "", code: "", contact_email: "", logo_color: COLORS[0] });
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    const r = await getCompanies();
    setCompanies(r.data);
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await createCompany(form);
      setShowForm(false);
      setForm({ name: "", code: "", contact_email: "", logo_color: COLORS[0] });
      load();
    } catch (e: any) {
      setError(e.response?.data?.detail || "Failed to add company");
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Partner Companies</h1>
          <p className="text-sm text-gray-500">Zepto, Swiggy, Blinkit & other EDI partners</p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 transition"
        >
          <Plus size={16} /> Add Company
        </button>
      </div>

      {/* Add Form */}
      {showForm && (
        <div className="bg-white rounded-xl p-5 shadow-sm border border-indigo-100">
          <h2 className="font-semibold text-gray-700 mb-4">Add New Company</h2>
          <form onSubmit={handleSubmit} className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Company Name *</label>
              <input className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-300" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Zepto" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Code *</label>
              <input className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-300 uppercase" required value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} placeholder="e.g. ZPT" maxLength={10} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Contact Email</label>
              <input type="email" className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-300" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} placeholder="po@company.com" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Brand Color</label>
              <div className="flex gap-2 flex-wrap">
                {COLORS.map((c) => (
                  <button type="button" key={c} onClick={() => setForm({ ...form, logo_color: c })} className={`w-7 h-7 rounded-full border-2 transition ${form.logo_color === c ? "border-gray-800 scale-110" : "border-transparent"}`} style={{ background: c }} />
                ))}
              </div>
            </div>
            {error && <div className="col-span-2 text-sm text-red-600 bg-red-50 p-2 rounded">{error}</div>}
            <div className="col-span-2 flex gap-2">
              <button type="submit" className="px-5 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700">Add Company</button>
              <button type="button" onClick={() => setShowForm(false)} className="px-5 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">Cancel</button>
            </div>
          </form>
        </div>
      )}

      {/* Companies Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {loading ? (
          <div className="col-span-3 text-center py-10 text-gray-400"><RefreshCcw className="inline animate-spin mr-2" />Loading...</div>
        ) : companies.map((c) => (
          <div key={c.id} className="bg-white rounded-xl p-5 shadow-sm border border-gray-100 hover:shadow-md transition">
            <div className="flex items-center gap-3 mb-3">
              <CompanyAvatar name={c.name} color={c.logo_color} size={44} />
              <div>
                <h3 className="font-bold text-gray-800">{c.name}</h3>
                <span className="text-xs font-mono bg-gray-100 text-gray-600 px-2 py-0.5 rounded">{c.code}</span>
              </div>
            </div>
            {c.contact_email && (
              <div className="flex items-center gap-2 text-xs text-gray-500 mb-2">
                <Mail size={12} /> {c.contact_email}
              </div>
            )}
            <div className="flex items-center gap-2 text-xs text-gray-400">
              <Building2 size={12} /> Partner since {formatDate(c.created_at).split(",")[0]}
            </div>
            <div className="mt-3 pt-3 border-t border-gray-100 flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                {c.integration_active ? (
                  <><Wifi size={13} className="text-green-500" /><span className="text-xs text-green-600 font-medium">EDI Active</span></>
                ) : (
                  <><WifiOff size={13} className="text-gray-400" /><span className="text-xs text-gray-400">Not configured</span></>
                )}
              </div>
              <Link to="/integration" className="text-xs text-indigo-600 hover:underline">Setup →</Link>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
