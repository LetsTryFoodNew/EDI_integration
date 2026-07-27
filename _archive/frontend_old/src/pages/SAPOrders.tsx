import { useEffect, useState } from "react";
import { getSAPOrders, createSAPOrderFromPO } from "../api";

interface SAPOrder {
  id: number;
  sap_order_id: string;
  po_id: number | null;
  company_id: number | null;
  sold_to_party: string | null;
  ship_to_party: string | null;
  order_type: string;
  sales_org: string;
  status: string;
  total_value: number;
  currency: string;
  line_items: any[] | null;
  raw_response: any | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  company: { name: string; code: string } | null;
}

const STATUS_COLORS: Record<string, string> = {
  CREATED: "bg-blue-100 text-blue-800",
  SENT: "bg-indigo-100 text-indigo-800",
  CONFIRMED: "bg-green-100 text-green-800",
  ERROR: "bg-red-100 text-red-800",
};

export default function SAPOrders() {
  const [orders, setOrders] = useState<SAPOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedOrder, setSelectedOrder] = useState<SAPOrder | null>(null);
  const [creating, setCreating] = useState(false);
  const [createPoId, setCreatePoId] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const res = await getSAPOrders();
      setOrders(res.data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleCreateFromPO = async () => {
    if (!createPoId) return;
    setCreating(true);
    try {
      await createSAPOrderFromPO(Number(createPoId));
      setCreatePoId("");
      load();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Error creating SAP order");
    } finally {
      setCreating(false);
    }
  };

  const totalValue = orders.reduce((sum, o) => sum + o.total_value, 0);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">SAP Sales Orders</h1>
          <p className="text-gray-500 text-sm mt-1">
            All sales orders created in SAP from inbound partner POs
          </p>
        </div>
        <div className="flex gap-2 items-center">
          <input
            className="border rounded-lg px-3 py-2 text-sm focus:ring-indigo-400 focus:border-indigo-400 outline-none"
            placeholder="PO ID to create SAP SO"
            value={createPoId}
            onChange={e => setCreatePoId(e.target.value)}
            type="number"
          />
          <button
            onClick={handleCreateFromPO}
            disabled={creating || !createPoId}
            className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
          >
            {creating ? "Creating..." : "Create SAP SO"}
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total SO", value: orders.length, color: "text-indigo-600" },
          { label: "Confirmed", value: orders.filter(o => o.status === "CONFIRMED").length, color: "text-green-600" },
          { label: "Created", value: orders.filter(o => o.status === "CREATED").length, color: "text-blue-600" },
          { label: "Total Value", value: `₹${totalValue.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`, color: "text-gray-800" },
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
          <div className="p-10 text-center text-gray-400">Loading SAP orders...</div>
        ) : orders.length === 0 ? (
          <div className="p-10 text-center text-gray-400">
            No SAP orders yet. SAP orders are auto-created when POs come in via webhook.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  {["SAP Order ID", "Partner", "Sold-To", "Ship-To", "Type", "Sales Org", "Value", "Status", "Created", "Actions"].map(h => (
                    <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {orders.map(o => (
                  <tr key={o.id} className="hover:bg-gray-50 transition">
                    <td className="px-4 py-3 font-mono text-sm font-semibold text-indigo-700">{o.sap_order_id}</td>
                    <td className="px-4 py-3">
                      {o.company ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-800">
                          {o.company.code}
                        </span>
                      ) : "—"}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-600">{o.sold_to_party || "—"}</td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-600">{o.ship_to_party || "—"}</td>
                    <td className="px-4 py-3 text-gray-700 font-mono text-xs">{o.order_type}</td>
                    <td className="px-4 py-3 text-gray-600 text-xs">{o.sales_org}</td>
                    <td className="px-4 py-3 font-semibold text-gray-800">
                      ₹{o.total_value.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-semibold ${STATUS_COLORS[o.status] || "bg-gray-100 text-gray-700"}`}>
                        {o.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-500 text-xs">
                      {new Date(o.created_at).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => setSelectedOrder(o)}
                        className="text-indigo-600 hover:text-indigo-800 text-xs font-medium"
                      >
                        View Details
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Detail Modal */}
      {selectedOrder && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto p-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-lg font-semibold text-gray-900">{selectedOrder.sap_order_id}</h2>
                <p className="text-gray-500 text-sm">{selectedOrder.company?.name} — {selectedOrder.order_type} / {selectedOrder.sales_org}</p>
              </div>
              <button onClick={() => setSelectedOrder(null)} className="text-gray-400 hover:text-gray-600 text-2xl">×</button>
            </div>

            {/* SAP Header Data */}
            <div className="grid grid-cols-2 gap-3 mb-5">
              {[
                ["Sold-To Party", selectedOrder.sold_to_party],
                ["Ship-To Party", selectedOrder.ship_to_party],
                ["Distribution Ch.", selectedOrder.raw_response?.distribution_ch || "10"],
                ["Division", selectedOrder.raw_response?.division || "00"],
                ["Currency", selectedOrder.currency],
                ["Total Value", `₹${selectedOrder.total_value.toLocaleString("en-IN")}`],
              ].map(([label, val]) => (
                <div key={label} className="bg-gray-50 rounded-lg p-3">
                  <div className="text-xs text-gray-500">{label}</div>
                  <div className="font-mono font-medium text-gray-800 text-sm">{val || "—"}</div>
                </div>
              ))}
            </div>

            {/* Line Items */}
            {selectedOrder.line_items && selectedOrder.line_items.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-gray-700 mb-2">SAP Line Items</h3>
                <div className="overflow-x-auto rounded-lg border">
                  <table className="w-full text-xs">
                    <thead className="bg-gray-100">
                      <tr>
                        {["Item #", "Material Code", "Description", "Ord Qty", "Ful Qty", "Unit", "Price", "Net Value"].map(h => (
                          <th key={h} className="text-left px-3 py-2 text-gray-500 font-semibold">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {selectedOrder.line_items.map((item: any, idx: number) => (
                        <tr key={idx} className="hover:bg-gray-50">
                          <td className="px-3 py-2 font-mono">{item.item_number}</td>
                          <td className="px-3 py-2 font-mono text-indigo-700">{item.material_code}</td>
                          <td className="px-3 py-2">{item.material_description}</td>
                          <td className="px-3 py-2 text-right">{item.order_quantity}</td>
                          <td className="px-3 py-2 text-right">{item.fulfilled_quantity}</td>
                          <td className="px-3 py-2">{item.sales_unit}</td>
                          <td className="px-3 py-2 text-right">₹{item.unit_price}</td>
                          <td className="px-3 py-2 text-right font-semibold">₹{item.net_value?.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* SAP Response */}
            {selectedOrder.raw_response && (
              <div className="mt-4">
                <h3 className="text-sm font-semibold text-gray-700 mb-2">SAP System Response</h3>
                <pre className="bg-gray-900 text-green-400 text-xs p-4 rounded-lg overflow-x-auto max-h-40">
                  {JSON.stringify(selectedOrder.raw_response, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
