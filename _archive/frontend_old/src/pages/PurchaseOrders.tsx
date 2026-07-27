import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, Search, Filter, RefreshCcw, Eye } from "lucide-react";
import { getPurchaseOrders, getCompanies, updatePOStatus } from "../api";
import type { PurchaseOrder, Company, POStatus } from "../types";
import StatusBadge from "../components/StatusBadge";
import CompanyAvatar from "../components/CompanyAvatar";
import { formatCurrency, formatDate } from "../utils";

const STATUSES: POStatus[] = [
  "PENDING", "STOCK_AVAILABLE", "STOCK_PARTIAL", "OUT_OF_STOCK", "CONFIRMED", "DISPATCHED",
];

export default function PurchaseOrders() {
  const [pos, setPOs] = useState<PurchaseOrder[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [search, setSearch] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterCompany, setFilterCompany] = useState("");
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    const params: any = {};
    if (filterStatus) params.status = filterStatus;
    if (filterCompany) params.company_id = filterCompany;
    const [p, c] = await Promise.all([getPurchaseOrders(params), getCompanies()]);
    setPOs(p.data);
    setCompanies(c.data);
    setLoading(false);
  };

  useEffect(() => { load(); }, [filterStatus, filterCompany]);

  const handleStatusChange = async (id: number, status: string) => {
    await updatePOStatus(id, status);
    load();
  };

  const filtered = pos.filter(
    (po) =>
      po.po_number.toLowerCase().includes(search.toLowerCase()) ||
      po.company.name.toLowerCase().includes(search.toLowerCase()) ||
      (po.sap_order_id || "").toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Purchase Orders</h1>
          <p className="text-sm text-gray-500">All incoming POs from partner companies</p>
        </div>
        <Link
          to="/purchase-orders/new"
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 transition"
        >
          <Plus size={16} /> New PO
        </Link>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-100 flex flex-wrap gap-3 items-center">
        <div className="flex items-center gap-2 border border-gray-200 rounded-lg px-3 py-2 text-sm flex-1 min-w-48">
          <Search size={15} className="text-gray-400" />
          <input
            className="outline-none w-full text-sm"
            placeholder="Search PO number, company, SAP ID..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-2 border border-gray-200 rounded-lg px-3 py-2 text-sm">
          <Filter size={15} className="text-gray-400" />
          <select className="outline-none text-sm bg-transparent" value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            <option value="">All Statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
          </select>
        </div>
        <select
          className="border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none"
          value={filterCompany}
          onChange={(e) => setFilterCompany(e.target.value)}
        >
          <option value="">All Companies</option>
          {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <button onClick={load} className="p-2 border border-gray-200 rounded-lg hover:bg-gray-50">
          <RefreshCcw size={15} className="text-gray-500" />
        </button>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wide">
              <tr>
                <th className="px-5 py-3 text-left">PO Number</th>
                <th className="px-5 py-3 text-left">Company</th>
                <th className="px-5 py-3 text-left">SAP Order</th>
                <th className="px-5 py-3 text-left">Items</th>
                <th className="px-5 py-3 text-left">Status</th>
                <th className="px-5 py-3 text-right">Amount</th>
                <th className="px-5 py-3 text-left">Date</th>
                <th className="px-5 py-3 text-left">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {loading ? (
                <tr><td colSpan={8} className="text-center py-10 text-gray-400"><RefreshCcw className="inline animate-spin mr-2" />Loading...</td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan={8} className="text-center py-10 text-gray-400">No purchase orders found</td></tr>
              ) : filtered.map((po) => (
                <tr key={po.id} className="hover:bg-gray-50 transition">
                  <td className="px-5 py-3">
                    <p className="font-mono font-semibold text-indigo-700">{po.po_number}</p>
                    <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${po.source === "WEBHOOK" ? "bg-violet-100 text-violet-700" : "bg-gray-100 text-gray-500"}`}>
                      {po.source}
                    </span>
                  </td>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <CompanyAvatar name={po.company.name} color={po.company.logo_color} size={28} />
                      <span className="font-medium text-gray-700">{po.company.name}</span>
                    </div>
                  </td>
                  <td className="px-5 py-3 font-mono text-gray-500 text-xs">{po.sap_order_id || "—"}</td>
                  <td className="px-5 py-3 text-gray-600">{po.items.length} items</td>
                  <td className="px-5 py-3"><StatusBadge status={po.status} /></td>
                  <td className="px-5 py-3 text-right font-semibold text-gray-800">{formatCurrency(po.total_amount)}</td>
                  <td className="px-5 py-3 text-gray-500 text-xs whitespace-nowrap">{formatDate(po.created_at)}</td>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <Link
                        to={`/purchase-orders/${po.id}`}
                        className="p-1.5 rounded-lg border border-gray-200 hover:bg-indigo-50 hover:border-indigo-300 text-gray-500 hover:text-indigo-600 transition"
                        title="View Details"
                      >
                        <Eye size={14} />
                      </Link>
                      <select
                        className="text-xs border border-gray-200 rounded-lg px-2 py-1 outline-none bg-white"
                        value={po.status}
                        onChange={(e) => handleStatusChange(po.id, e.target.value)}
                      >
                        {STATUSES.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
                      </select>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="px-5 py-3 border-t border-gray-100 text-xs text-gray-400">
          Showing {filtered.length} of {pos.length} purchase orders
        </div>
      </div>
    </div>
  );
}
