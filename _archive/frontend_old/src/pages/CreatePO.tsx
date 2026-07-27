import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Trash2, ShoppingCart, ArrowLeft } from "lucide-react";
import { getCompanies, getProducts, createPO } from "../api";
import type { Company, Product } from "../types";
import { formatCurrency } from "../utils";

interface LineItem { product_id: number; requested_qty: number }

export default function CreatePO() {
  const navigate = useNavigate();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [companyId, setCompanyId] = useState("");
  const [notes, setNotes] = useState("");
  const [items, setItems] = useState<LineItem[]>([{ product_id: 0, requested_qty: 1 }]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([getCompanies(), getProducts()]).then(([c, p]) => {
      setCompanies(c.data);
      setProducts(p.data);
    });
  }, []);

  const addItem = () => setItems([...items, { product_id: 0, requested_qty: 1 }]);
  const removeItem = (i: number) => setItems(items.filter((_, idx) => idx !== i));
  const updateItem = (i: number, field: keyof LineItem, val: number) => {
    const updated = [...items];
    updated[i] = { ...updated[i], [field]: val };
    setItems(updated);
  };

  const getProduct = (id: number) => products.find((p) => p.id === id);

  const total = items.reduce((sum, item) => {
    const p = getProduct(item.product_id);
    return sum + (p ? Math.min(p.stock_quantity, item.requested_qty) * p.price_per_unit : 0);
  }, 0);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!companyId) { setError("Please select a company"); return; }
    const validItems = items.filter((i) => i.product_id > 0 && i.requested_qty > 0);
    if (validItems.length === 0) { setError("Add at least one product"); return; }
    setSubmitting(true);
    try {
      const r = await createPO({ company_id: Number(companyId), notes, items: validItems });
      navigate(`/purchase-orders/${r.data.id}`);
    } catch (e: any) {
      setError(e.response?.data?.detail || "Failed to create PO");
    }
    setSubmitting(false);
  };

  return (
    <div className="max-w-3xl space-y-5">
      <div className="flex items-center gap-3">
        <button onClick={() => navigate(-1)} className="p-2 rounded-lg border border-gray-200 hover:bg-gray-50">
          <ArrowLeft size={16} />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Create Purchase Order</h1>
          <p className="text-sm text-gray-500">Manually simulate an incoming PO from a partner company</p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5">
        {/* Company & Notes */}
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100 space-y-4">
          <h2 className="font-semibold text-gray-700">Order Details</h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Company *</label>
              <select
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-300"
                value={companyId}
                onChange={(e) => setCompanyId(e.target.value)}
                required
              >
                <option value="">Select company...</option>
                {companies.map((c) => (
                  <option key={c.id} value={c.id}>{c.name} ({c.code})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Notes</label>
              <input
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-300"
                placeholder="Optional notes..."
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </div>
          </div>
        </div>

        {/* Line Items */}
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-gray-700">Order Items</h2>
            <button type="button" onClick={addItem} className="flex items-center gap-1 text-sm text-indigo-600 hover:text-indigo-800">
              <Plus size={15} /> Add Item
            </button>
          </div>

          {items.map((item, i) => {
            const prod = getProduct(item.product_id);
            const stockOk = prod && item.requested_qty <= prod.stock_quantity;
            return (
              <div key={i} className="grid grid-cols-12 gap-2 items-center">
                <div className="col-span-6">
                  <select
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-300"
                    value={item.product_id}
                    onChange={(e) => updateItem(i, "product_id", Number(e.target.value))}
                  >
                    <option value={0}>Select product...</option>
                    {products.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} (Stock: {p.stock_quantity})
                      </option>
                    ))}
                  </select>
                </div>
                <div className="col-span-3">
                  <input
                    type="number"
                    min={1}
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-300"
                    placeholder="Qty"
                    value={item.requested_qty}
                    onChange={(e) => updateItem(i, "requested_qty", Number(e.target.value))}
                  />
                </div>
                <div className="col-span-2 text-xs text-right">
                  {prod ? (
                    <span className={stockOk ? "text-green-600" : "text-orange-500"}>
                      {stockOk ? `✓ ${formatCurrency(prod.price_per_unit)}` : `⚠ Low stock`}
                    </span>
                  ) : null}
                </div>
                <div className="col-span-1 flex justify-end">
                  <button
                    type="button"
                    onClick={() => removeItem(i)}
                    className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 rounded"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            );
          })}

          <div className="border-t border-gray-100 pt-3 flex justify-between items-center text-sm">
            <span className="text-gray-500">Estimated Total (available stock only)</span>
            <span className="font-bold text-green-700 text-base">{formatCurrency(total)}</span>
          </div>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">{error}</div>
        )}

        <div className="flex gap-3">
          <button
            type="submit"
            disabled={submitting}
            className="flex items-center gap-2 px-6 py-2.5 bg-indigo-600 text-white rounded-lg text-sm font-semibold hover:bg-indigo-700 transition disabled:opacity-50"
          >
            <ShoppingCart size={16} />
            {submitting ? "Processing..." : "Submit Purchase Order"}
          </button>
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="px-6 py-2.5 border border-gray-200 rounded-lg text-sm text-gray-600 hover:bg-gray-50"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
