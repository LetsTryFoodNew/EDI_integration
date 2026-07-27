import { useEffect, useState } from "react";
import { Package, Edit3, AlertTriangle, RefreshCcw } from "lucide-react";
import { getProducts, updateProduct } from "../api";
import type { Product } from "../types";
import { formatCurrency } from "../utils";

export default function Inventory() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [editId, setEditId] = useState<number | null>(null);
  const [editQty, setEditQty] = useState(0);

  const load = async () => {
    setLoading(true);
    const r = await getProducts();
    setProducts(r.data);
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const handleSave = async (id: number) => {
    await updateProduct(id, { stock_quantity: editQty });
    setEditId(null);
    load();
  };

  const getStockStatus = (p: Product) => {
    if (p.stock_quantity === 0) return { label: "Out of Stock", color: "text-red-600", bg: "bg-red-50" };
    if (p.stock_quantity <= p.reorder_level) return { label: "Low Stock", color: "text-orange-600", bg: "bg-orange-50" };
    return { label: "In Stock", color: "text-green-600", bg: "bg-green-50" };
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Inventory Management</h1>
          <p className="text-sm text-gray-500">SAP-synced product stock levels</p>
        </div>
        <button onClick={load} className="flex items-center gap-2 px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">
          <RefreshCcw size={15} /> Refresh
        </button>
      </div>

      {/* Alert for low stock */}
      {products.some((p) => p.stock_quantity <= p.reorder_level) && (
        <div className="bg-orange-50 border border-orange-200 rounded-xl p-4 flex items-center gap-3 text-orange-800 text-sm">
          <AlertTriangle size={18} className="text-orange-500 flex-shrink-0" />
          <strong>{products.filter((p) => p.stock_quantity <= p.reorder_level).length} products</strong> are below reorder level. Consider restocking soon.
        </div>
      )}

      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wide">
              <tr>
                <th className="px-5 py-3 text-left">Product</th>
                <th className="px-5 py-3 text-left">SKU</th>
                <th className="px-5 py-3 text-left">SAP Code</th>
                <th className="px-5 py-3 text-left">Category</th>
                <th className="px-5 py-3 text-right">Price/Unit</th>
                <th className="px-5 py-3 text-center">Stock</th>
                <th className="px-5 py-3 text-center">Reorder Lvl</th>
                <th className="px-5 py-3 text-left">Status</th>
                <th className="px-5 py-3 text-left">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {loading ? (
                <tr><td colSpan={9} className="text-center py-10 text-gray-400"><RefreshCcw className="inline animate-spin mr-2" />Loading...</td></tr>
              ) : products.map((p) => {
                const status = getStockStatus(p);
                const pct = Math.min(100, (p.stock_quantity / (p.reorder_level * 3)) * 100);
                return (
                  <tr key={p.id} className={`hover:bg-gray-50 transition ${p.stock_quantity === 0 ? "bg-red-50/30" : p.stock_quantity <= p.reorder_level ? "bg-orange-50/30" : ""}`}>
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 bg-indigo-100 rounded-lg flex items-center justify-center">
                          <Package size={14} className="text-indigo-600" />
                        </div>
                        <span className="font-medium text-gray-800">{p.name}</span>
                      </div>
                    </td>
                    <td className="px-5 py-3 font-mono text-xs text-gray-500">{p.sku}</td>
                    <td className="px-5 py-3 font-mono text-xs text-indigo-600">{p.sap_material_code || "—"}</td>
                    <td className="px-5 py-3 text-gray-500">{p.category}</td>
                    <td className="px-5 py-3 text-right font-semibold text-gray-700">{formatCurrency(p.price_per_unit)}</td>
                    <td className="px-5 py-3 text-center">
                      {editId === p.id ? (
                        <input
                          type="number"
                          className="w-20 border border-indigo-300 rounded px-2 py-1 text-sm text-center outline-none"
                          value={editQty}
                          onChange={(e) => setEditQty(Number(e.target.value))}
                        />
                      ) : (
                        <div>
                          <span className={`font-bold ${status.color}`}>{p.stock_quantity}</span>
                          <span className="text-gray-400 text-xs ml-1">{p.unit}</span>
                          <div className="w-20 h-1.5 bg-gray-200 rounded-full mx-auto mt-1">
                            <div
                              className={`h-full rounded-full ${pct > 50 ? "bg-green-500" : pct > 20 ? "bg-orange-400" : "bg-red-400"}`}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        </div>
                      )}
                    </td>
                    <td className="px-5 py-3 text-center text-gray-500">{p.reorder_level}</td>
                    <td className="px-5 py-3">
                      <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${status.color} ${status.bg}`}>
                        {status.label}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      {editId === p.id ? (
                        <div className="flex gap-1">
                          <button onClick={() => handleSave(p.id)} className="px-2 py-1 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700">Save</button>
                          <button onClick={() => setEditId(null)} className="px-2 py-1 border border-gray-200 rounded text-xs hover:bg-gray-50">Cancel</button>
                        </div>
                      ) : (
                        <button onClick={() => { setEditId(p.id); setEditQty(p.stock_quantity); }} className="flex items-center gap-1 text-xs text-gray-500 hover:text-indigo-600 border border-gray-200 rounded px-2 py-1 hover:border-indigo-300">
                          <Edit3 size={12} /> Update Stock
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
