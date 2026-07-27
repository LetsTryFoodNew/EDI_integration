import { useEffect, useState } from "react";
import { FileOutput, RefreshCcw, Send, Plus, CheckCircle, XCircle, Clock } from "lucide-react";
import { getASNRecords, createASN, syncASN, getPurchaseOrders } from "../api";
import type { ASNRecord, PurchaseOrder } from "../types";
import CompanyAvatar from "../components/CompanyAvatar";
import { formatDate, formatCurrency } from "../utils";

const STATUS_STYLE: Record<string, string> = {
  CREATED: "bg-blue-100 text-blue-700 border-blue-300",
  SYNCED: "bg-green-100 text-green-700 border-green-300",
  FAILED: "bg-red-100 text-red-700 border-red-300",
};

export default function ASNPage() {
  const [asns, setAsns] = useState<ASNRecord[]>([]);
  const [pos, setPOs] = useState<PurchaseOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [syncing, setSyncing] = useState<number | null>(null);
  const [form, setForm] = useState({
    po_id: "",
    shipment_date: "",
    expected_delivery: "",
    carrier: "",
    tracking_number: "",
  });

  const load = async () => {
    setLoading(true);
    const [a, p] = await Promise.all([getASNRecords(), getPurchaseOrders()]);
    setAsns(a.data);
    setPOs(p.data.filter((po: PurchaseOrder) => po.status === "CONFIRMED" || po.status === "STOCK_AVAILABLE" || po.status === "STOCK_PARTIAL"));
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createASN({ ...form, po_id: Number(form.po_id) });
      setShowForm(false);
      setForm({ po_id: "", shipment_date: "", expected_delivery: "", carrier: "", tracking_number: "" });
      await load();
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed to create ASN");
    }
  };

  const handleSync = async (id: number) => {
    setSyncing(id);
    try {
      await syncASN(id);
      await load();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Sync failed");
    }
    setSyncing(null);
  };

  const stats = {
    total: asns.length,
    synced: asns.filter((a) => a.status === "SYNCED").length,
    failed: asns.filter((a) => a.status === "FAILED").length,
    created: asns.filter((a) => a.status === "CREATED").length,
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <FileOutput size={24} className="text-indigo-600" />
            ASN — Advance Shipment Notifications
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Outbound shipment notifications pushed to partner webhooks (Blinkit / Zepto / Swiggy)
          </p>
        </div>
        <div className="flex gap-3">
          <button onClick={load} className="flex items-center gap-2 px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 transition">
            <RefreshCcw size={14} /> Refresh
          </button>
          <button onClick={() => setShowForm(true)} className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 transition">
            <Plus size={14} /> Create ASN
          </button>
        </div>
      </div>

      {/* Info Banner */}
      <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-800">
        <strong>ASN Flow:</strong> After confirming a PO, create an ASN with shipment details.
        Hit <strong>Sync to Partner</strong> to push ASN to the partner's HTTP endpoint (Basic Auth).
        This is Step 4 of Blinkit's integration — data flows automatically once configured.
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total ASNs", value: stats.total, icon: FileOutput, color: "bg-indigo-500" },
          { label: "Synced", value: stats.synced, icon: CheckCircle, color: "bg-green-500" },
          { label: "Failed", value: stats.failed, icon: XCircle, color: "bg-red-500" },
          { label: "Pending Sync", value: stats.created, icon: Clock, color: "bg-blue-500" },
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

      {/* ASN Table */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        {loading ? (
          <div className="text-center py-16 text-gray-400">Loading ASN records...</div>
        ) : asns.length === 0 ? (
          <div className="text-center py-16 text-gray-400">
            <FileOutput size={40} className="mx-auto mb-3 text-gray-300" />
            <p className="font-medium">No ASN records yet</p>
            <p className="text-sm mt-1">Confirm a PO first, then create an ASN</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
              <tr>
                <th className="px-5 py-3 text-left">ASN Number</th>
                <th className="px-5 py-3 text-left">Partner</th>
                <th className="px-5 py-3 text-left">Carrier</th>
                <th className="px-5 py-3 text-left">Tracking</th>
                <th className="px-5 py-3 text-left">Shipment Date</th>
                <th className="px-5 py-3 text-left">ETA</th>
                <th className="px-5 py-3 text-center">Attempts</th>
                <th className="px-5 py-3 text-center">Status</th>
                <th className="px-5 py-3 text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {asns.map((asn) => (
                <tr key={asn.id} className="hover:bg-gray-50">
                  <td className="px-5 py-3 font-mono text-xs font-semibold text-indigo-700">{asn.asn_number}</td>
                  <td className="px-5 py-3">
                    {asn.company ? (
                      <div className="flex items-center gap-2">
                        <CompanyAvatar name={asn.company.name} color={asn.company.logo_color} size={24} />
                        <span className="text-gray-700">{asn.company.name}</span>
                      </div>
                    ) : "—"}
                  </td>
                  <td className="px-5 py-3 text-gray-600">{asn.carrier || "—"}</td>
                  <td className="px-5 py-3 font-mono text-xs text-gray-500">{asn.tracking_number || "—"}</td>
                  <td className="px-5 py-3 text-gray-500 text-xs">{asn.shipment_date ? formatDate(asn.shipment_date) : "—"}</td>
                  <td className="px-5 py-3 text-gray-500 text-xs">{asn.expected_delivery ? formatDate(asn.expected_delivery) : "—"}</td>
                  <td className="px-5 py-3 text-center text-gray-500">{asn.sync_attempts}</td>
                  <td className="px-5 py-3 text-center">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${STATUS_STYLE[asn.status]}`}>
                      {asn.status}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-center">
                    {asn.status !== "SYNCED" && (
                      <button
                        onClick={() => handleSync(asn.id)}
                        disabled={syncing === asn.id}
                        className="flex items-center gap-1 px-3 py-1.5 bg-indigo-600 text-white text-xs rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition mx-auto"
                      >
                        <Send size={11} />
                        {syncing === asn.id ? "Syncing..." : "Sync"}
                      </button>
                    )}
                    {asn.status === "SYNCED" && <span className="text-xs text-green-600 font-medium">✓ Delivered</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Create ASN Modal */}
      {showForm && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setShowForm(false)}>
          <div className="bg-white rounded-xl w-full max-w-lg shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-5 border-b">
              <h3 className="font-bold text-gray-800">Create Advance Shipment Notification</h3>
              <button onClick={() => setShowForm(false)} className="text-gray-400 hover:text-gray-700">✕</button>
            </div>
            <form onSubmit={handleCreate} className="p-5 space-y-4">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Purchase Order *</label>
                <select
                  required
                  value={form.po_id}
                  onChange={(e) => setForm({ ...form, po_id: e.target.value })}
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
                >
                  <option value="">Select a PO</option>
                  {pos.map((po) => (
                    <option key={po.id} value={po.id}>
                      {po.po_number} — {po.company.name} ({formatCurrency(po.total_amount)})
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Shipment Date *</label>
                  <input
                    required type="datetime-local"
                    value={form.shipment_date}
                    onChange={(e) => setForm({ ...form, shipment_date: e.target.value })}
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Expected Delivery *</label>
                  <input
                    required type="datetime-local"
                    value={form.expected_delivery}
                    onChange={(e) => setForm({ ...form, expected_delivery: e.target.value })}
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Carrier *</label>
                <select
                  required
                  value={form.carrier}
                  onChange={(e) => setForm({ ...form, carrier: e.target.value })}
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
                >
                  <option value="">Select carrier</option>
                  {["BlueDart", "Delhivery", "DTDC", "Ekart", "Shadowfax", "Ecom Express", "FedEx India"].map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Tracking Number *</label>
                <input
                  required type="text" placeholder="e.g. BLU123456789IN"
                  value={form.tracking_number}
                  onChange={(e) => setForm({ ...form, tracking_number: e.target.value })}
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
                />
              </div>
              <div className="flex gap-3 pt-2">
                <button type="button" onClick={() => setShowForm(false)} className="flex-1 px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">
                  Cancel
                </button>
                <button type="submit" className="flex-1 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 transition">
                  Create ASN
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
