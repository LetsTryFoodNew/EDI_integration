import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { ArrowLeft, Package, Building2, Hash, Calendar } from "lucide-react";
import { getPurchaseOrder, updatePOStatus } from "../api";
import type { PurchaseOrder, POStatus } from "../types";
import StatusBadge from "../components/StatusBadge";
import CompanyAvatar from "../components/CompanyAvatar";
import { formatCurrency, formatDate } from "../utils";

const STATUSES: POStatus[] = [
  "PENDING", "STOCK_AVAILABLE", "STOCK_PARTIAL", "OUT_OF_STOCK", "CONFIRMED", "DISPATCHED",
];

export default function PODetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [po, setPO] = useState<PurchaseOrder | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getPurchaseOrder(Number(id)).then((r) => { setPO(r.data); setLoading(false); });
  }, [id]);

  const handleStatus = async (status: string) => {
    if (!po) return;
    const r = await updatePOStatus(po.id, status);
    setPO(r.data);
  };

  if (loading) return <div className="text-center py-20 text-gray-400">Loading...</div>;
  if (!po) return <div className="text-center py-20 text-red-400">PO not found</div>;

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <button onClick={() => navigate(-1)} className="p-2 rounded-lg border border-gray-200 hover:bg-gray-50">
          <ArrowLeft size={16} />
        </button>
        <div>
          <h1 className="text-xl font-bold text-gray-800 font-mono">{po.po_number}</h1>
          <p className="text-sm text-gray-500">Purchase Order Details</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Main Info */}
        <div className="lg:col-span-2 space-y-5">
          {/* Info card */}
          <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
            <h2 className="font-semibold text-gray-700 mb-4 flex items-center gap-2"><Hash size={16} />Order Information</h2>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-gray-400 text-xs mb-1">Company</p>
                <div className="flex items-center gap-2">
                  <CompanyAvatar name={po.company.name} color={po.company.logo_color} size={28} />
                  <span className="font-semibold text-gray-800">{po.company.name}</span>
                </div>
              </div>
              <div>
                <p className="text-gray-400 text-xs mb-1">SAP Order ID</p>
                <p className="font-mono font-semibold text-indigo-700">{po.sap_order_id || "—"}</p>
              </div>
              <div>
                <p className="text-gray-400 text-xs mb-1">Status</p>
                <StatusBadge status={po.status} />
              </div>
              <div>
                <p className="text-gray-400 text-xs mb-1">Total Amount</p>
                <p className="font-bold text-green-700 text-lg">{formatCurrency(po.total_amount)}</p>
              </div>
              <div>
                <p className="text-gray-400 text-xs mb-1 flex items-center gap-1"><Calendar size={11} />Created</p>
                <p className="text-gray-700">{formatDate(po.created_at)}</p>
              </div>
              <div>
                <p className="text-gray-400 text-xs mb-1 flex items-center gap-1"><Calendar size={11} />Last Updated</p>
                <p className="text-gray-700">{formatDate(po.updated_at)}</p>
              </div>
            </div>
            {po.notes && (
              <div className="mt-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg text-sm text-yellow-800">
                <strong>Notes:</strong> {po.notes}
              </div>
            )}
          </div>

          {/* Items */}
          <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
            <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-2">
              <Package size={16} className="text-gray-500" />
              <h2 className="font-semibold text-gray-700">Order Items ({po.items.length})</h2>
            </div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
                <tr>
                  <th className="px-5 py-3 text-left">Product</th>
                  <th className="px-5 py-3 text-left">SKU</th>
                  <th className="px-5 py-3 text-center">Requested</th>
                  <th className="px-5 py-3 text-center">Fulfilled</th>
                  <th className="px-5 py-3 text-right">Unit Price</th>
                  <th className="px-5 py-3 text-right">Subtotal</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {po.items.map((item) => {
                  const fulfillPct = item.requested_qty > 0 ? (item.fulfilled_qty / item.requested_qty) * 100 : 0;
                  return (
                    <tr key={item.id} className="hover:bg-gray-50">
                      <td className="px-5 py-3">
                        <p className="font-medium text-gray-800">{item.product.name}</p>
                        <p className="text-xs text-gray-400">{item.product.category}</p>
                      </td>
                      <td className="px-5 py-3 font-mono text-xs text-gray-500">{item.product.sku}</td>
                      <td className="px-5 py-3 text-center text-gray-700">{item.requested_qty} {item.product.unit}</td>
                      <td className="px-5 py-3 text-center">
                        <div>
                          <span className={`font-semibold ${fulfillPct === 100 ? "text-green-600" : fulfillPct === 0 ? "text-red-500" : "text-orange-500"}`}>
                            {item.fulfilled_qty} {item.product.unit}
                          </span>
                          <div className="w-16 h-1.5 bg-gray-200 rounded-full mx-auto mt-1">
                            <div className={`h-full rounded-full ${fulfillPct === 100 ? "bg-green-500" : fulfillPct === 0 ? "bg-red-400" : "bg-orange-400"}`} style={{ width: `${fulfillPct}%` }} />
                          </div>
                        </div>
                      </td>
                      <td className="px-5 py-3 text-right text-gray-600">{formatCurrency(item.unit_price)}</td>
                      <td className="px-5 py-3 text-right font-semibold text-gray-800">{formatCurrency(item.subtotal)}</td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot className="bg-gray-50 border-t border-gray-200">
                <tr>
                  <td colSpan={5} className="px-5 py-3 text-right font-semibold text-gray-700">Total</td>
                  <td className="px-5 py-3 text-right font-bold text-green-700 text-base">{formatCurrency(po.total_amount)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>

        {/* Side Panel */}
        <div className="space-y-4">
          <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
            <h2 className="font-semibold text-gray-700 mb-3 flex items-center gap-2"><Building2 size={16} />Update Status</h2>
            <div className="space-y-2">
              {STATUSES.map((s) => (
                <button
                  key={s}
                  onClick={() => handleStatus(s)}
                  className={`w-full text-left px-3 py-2 rounded-lg text-sm border transition ${po.status === s ? "border-indigo-400 bg-indigo-50 text-indigo-700 font-semibold" : "border-gray-200 hover:bg-gray-50 text-gray-600"}`}
                >
                  {s.replace(/_/g, " ")}
                </button>
              ))}
            </div>
          </div>

          <div className="bg-indigo-50 rounded-xl p-5 border border-indigo-100">
            <p className="text-xs text-indigo-600 font-semibold uppercase mb-1">SAP Integration</p>
            <p className="font-mono text-indigo-800 font-bold">{po.sap_order_id}</p>
            <p className="text-xs text-indigo-500 mt-2">Auto-generated SAP reference for this purchase order</p>
          </div>

          <Link to="/purchase-orders" className="block text-center px-4 py-2 border border-gray-200 rounded-lg text-sm text-gray-600 hover:bg-gray-50 transition">
            ← Back to All Orders
          </Link>
        </div>
      </div>
    </div>
  );
}
