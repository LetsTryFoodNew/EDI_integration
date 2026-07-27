import { useEffect, useState } from "react";
import {
  ShoppingCart,
  Clock,
  CheckCircle,
  Truck,
  AlertTriangle,
  TrendingUp,
  Package,
  RefreshCcw,
  ReceiptText,
  GitMerge,
} from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from "recharts";
import { getDashboard, getPurchaseOrders } from "../api";
import type { DashboardStats, PurchaseOrder } from "../types";
import StatusBadge from "../components/StatusBadge";
import CompanyAvatar from "../components/CompanyAvatar";
import { formatCurrency, formatDate } from "../utils";

const StatCard = ({
  icon: Icon,
  label,
  value,
  color,
  sub,
}: {
  icon: any;
  label: string;
  value: string | number;
  color: string;
  sub?: string;
}) => (
  <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100 flex items-start gap-4">
    <div className={`w-12 h-12 rounded-xl flex items-center justify-center flex-shrink-0 ${color}`}>
      <Icon size={22} className="text-white" />
    </div>
    <div>
      <p className="text-sm text-gray-500">{label}</p>
      <p className="text-2xl font-bold text-gray-800">{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  </div>
);

const PIE_COLORS = ["#6366f1", "#22c55e", "#f97316", "#ef4444", "#3b82f6", "#a855f7"];

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [recentPOs, setRecentPOs] = useState<PurchaseOrder[]>([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    const [s, p] = await Promise.all([getDashboard(), getPurchaseOrders()]);
    setStats(s.data);
    setRecentPOs(p.data.slice(0, 6));
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const pieData = stats
    ? [
        { name: "Pending", value: stats.pending_pos },
        { name: "Confirmed", value: stats.confirmed_pos },
        { name: "Dispatched", value: stats.dispatched_pos },
        { name: "Out of Stock", value: stats.out_of_stock_pos },
      ].filter((d) => d.value > 0)
    : [];

  const barData = recentPOs.slice(0, 5).map((po) => ({
    name: po.company.name,
    amount: po.total_amount,
  }));

  if (loading)
    return (
      <div className="flex items-center justify-center h-64 text-gray-400">
        <RefreshCcw className="animate-spin mr-2" /> Loading Dashboard...
      </div>
    );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">EDI Dashboard</h1>
          <p className="text-sm text-gray-500">
            Real-time Purchase Order tracking across all partner companies
          </p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 transition"
        >
          <RefreshCcw size={15} /> Refresh
        </button>
      </div>

      {/* Stat Cards */}
      {stats && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard icon={ShoppingCart} label="Total POs" value={stats.total_pos} color="bg-indigo-500" />
          <StatCard icon={Clock} label="Pending" value={stats.pending_pos} color="bg-yellow-500" />
          <StatCard icon={CheckCircle} label="Confirmed" value={stats.confirmed_pos} color="bg-blue-500" />
          <StatCard icon={Truck} label="Dispatched" value={stats.dispatched_pos} color="bg-purple-500" />
          <StatCard icon={AlertTriangle} label="Out of Stock" value={stats.out_of_stock_pos} color="bg-red-500" />
          <StatCard icon={Package} label="Low Stock Items" value={stats.low_stock_products} color="bg-orange-500" sub="Below reorder level" />
          <StatCard icon={TrendingUp} label="Total Revenue" value={formatCurrency(stats.total_revenue)} color="bg-green-500" sub="All confirmed orders" />
          <StatCard icon={ShoppingCart} label="Webhook Hits" value={stats.total_webhooks} color="bg-teal-500" sub={`${stats.failed_webhooks} failed`} />
          <StatCard icon={Truck} label="ASN Records" value={stats.total_asn} color="bg-violet-500" sub="Shipment notifications" />
          <StatCard icon={ReceiptText} label="SAP Sales Orders" value={stats.total_sap_orders ?? 0} color="bg-blue-600" sub="Created in SAP" />
          <StatCard icon={GitMerge} label="Unmapped SKUs" value={stats.unmapped_skus ?? 0} color={stats.unmapped_skus > 0 ? "bg-red-500" : "bg-green-600"} sub={stats.unmapped_skus > 0 ? "Needs review" : "All clear"} />
        </div>
      )}

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
          <h2 className="font-semibold text-gray-700 mb-4">Recent PO Amounts (₹)</h2>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={barData}>
              <XAxis dataKey="name" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip formatter={(v: any) => formatCurrency(v)} />
              <Bar dataKey="amount" fill="#6366f1" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
          <h2 className="font-semibold text-gray-700 mb-4">PO Status Distribution</h2>
          {pieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80} label>
                  {pieData.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Legend />
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-52 text-gray-400 text-sm">No data yet</div>
          )}
        </div>
      </div>

      {/* Recent POs Table */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
          <h2 className="font-semibold text-gray-700">Recent Purchase Orders</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wide">
              <tr>
                <th className="px-5 py-3 text-left">PO Number</th>
                <th className="px-5 py-3 text-left">Company</th>
                <th className="px-5 py-3 text-left">SAP Order ID</th>
                <th className="px-5 py-3 text-left">Status</th>
                <th className="px-5 py-3 text-right">Amount</th>
                <th className="px-5 py-3 text-left">Date</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {recentPOs.map((po) => (
                <tr key={po.id} className="hover:bg-gray-50 transition">
                  <td className="px-5 py-3 font-mono font-medium text-indigo-700">{po.po_number}</td>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <CompanyAvatar name={po.company.name} color={po.company.logo_color} size={28} />
                      <span className="font-medium text-gray-700">{po.company.name}</span>
                    </div>
                  </td>
                  <td className="px-5 py-3 font-mono text-gray-500 text-xs">{po.sap_order_id}</td>
                  <td className="px-5 py-3"><StatusBadge status={po.status} /></td>
                  <td className="px-5 py-3 text-right font-semibold text-gray-800">{formatCurrency(po.total_amount)}</td>
                  <td className="px-5 py-3 text-gray-500 text-xs">{formatDate(po.created_at)}</td>
                </tr>
              ))}
              {recentPOs.length === 0 && (
                <tr>
                  <td colSpan={6} className="text-center py-10 text-gray-400">No purchase orders yet</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
