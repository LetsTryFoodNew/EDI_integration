import { useEffect, useState } from "react";
import { Settings, CheckCircle, Circle, Wifi, WifiOff, Save, RefreshCcw } from "lucide-react";
import { getCompanies, updateCompanyIntegration } from "../api";
import type { Company } from "../types";
import CompanyAvatar from "../components/CompanyAvatar";

const STEPS = [
  {
    num: 1,
    title: "Share your HTTP endpoint",
    desc: "Set up a Basic Auth enabled HTTP endpoint on your system. Share this URL with the partner tech team.",
    color: "bg-green-600",
  },
  {
    num: 2,
    title: "Check IP whitelisting",
    desc: "Consult your IT / Security team to confirm whether IP whitelisting is required for partner servers to connect.",
    color: "bg-green-600",
  },
  {
    num: 3,
    title: "Share your public IP(s)",
    desc: "Send your public IP addresses to the partner Tech Team so the connection can be configured at their end.",
    color: "bg-green-600",
  },
  {
    num: 4,
    title: "Integration goes live",
    desc: "Once the partner configures the endpoint, data will start flowing automatically — no further action needed.",
    color: "bg-green-600",
  },
];

export default function IntegrationSetup() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<number | null>(null);
  const [saving, setSaving] = useState<number | null>(null);
  const [forms, setForms] = useState<Record<number, any>>({});

  const load = async () => {
    setLoading(true);
    const res = await getCompanies();
    setCompanies(res.data);
    const initial: Record<number, any> = {};
    res.data.forEach((c: Company) => {
      initial[c.id] = {
        webhook_endpoint: c.webhook_endpoint || "",
        webhook_username: c.webhook_username || "",
        webhook_password: "",
        integration_active: c.integration_active,
      };
    });
    setForms(initial);
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const save = async (id: number) => {
    setSaving(id);
    try {
      const data = { ...forms[id] };
      if (!data.webhook_password) delete data.webhook_password;
      await updateCompanyIntegration(id, data);
      await load();
      setEditing(null);
    } catch (e: any) {
      alert(e.response?.data?.detail || "Save failed");
    }
    setSaving(null);
  };

  const updateForm = (id: number, key: string, value: any) => {
    setForms((prev) => ({ ...prev, [id]: { ...prev[id], [key]: value } }));
  };

  const INBOUND_URL = `http://your-server:8000/api/webhook/inbound/po`;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <Settings size={24} className="text-indigo-600" />
          Integration Setup
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Configure EDI connections with Blinkit, Zepto, Swiggy and other quick-commerce partners
        </p>
      </div>

      {/* Blinkit 4-Step Process */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
          <div>
            <h2 className="font-bold text-gray-800">Technical Setup — 4 Steps to Go Live</h2>
            <p className="text-xs text-gray-500 mt-0.5">Based on Blinkit Self Integration process</p>
          </div>
          <span className="bg-yellow-400 text-yellow-900 text-xs font-bold px-3 py-1 rounded-full">blinkit</span>
        </div>
        <div className="grid grid-cols-2 gap-4 p-6">
          {STEPS.map((step) => (
            <div key={step.num} className="flex gap-4 p-4 border border-gray-100 rounded-xl hover:bg-gray-50 transition">
              <div className={`w-8 h-8 ${step.color} rounded-full flex items-center justify-center text-white font-bold text-sm flex-shrink-0`}>
                {step.num}
              </div>
              <div>
                <p className="font-semibold text-gray-800 text-sm">{step.title}</p>
                <p className="text-xs text-gray-500 mt-1 leading-relaxed">{step.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Your Inbound Endpoint */}
      <div className="bg-indigo-900 text-white rounded-xl p-6">
        <p className="text-xs text-indigo-300 uppercase tracking-wider mb-2 font-semibold">Step 1 — Your Inbound Endpoint URL</p>
        <p className="font-mono text-lg text-white bg-indigo-800 px-4 py-3 rounded-lg break-all">{INBOUND_URL}</p>
        <div className="grid grid-cols-2 gap-4 mt-4 text-sm">
          <div className="bg-indigo-800 rounded-lg p-3">
            <p className="text-indigo-300 text-xs mb-1">Method</p>
            <p className="font-bold text-green-300">POST</p>
          </div>
          <div className="bg-indigo-800 rounded-lg p-3">
            <p className="text-indigo-300 text-xs mb-1">Authentication</p>
            <p className="font-bold text-yellow-300">HTTP Basic Auth</p>
          </div>
          <div className="bg-indigo-800 rounded-lg p-3">
            <p className="text-indigo-300 text-xs mb-1">Content-Type</p>
            <p className="font-bold text-white">application/json</p>
          </div>
          <div className="bg-indigo-800 rounded-lg p-3">
            <p className="text-indigo-300 text-xs mb-1">Response</p>
            <p className="font-bold text-white">ACK JSON + SAP Order ID</p>
          </div>
        </div>
        <p className="text-indigo-300 text-xs mt-3">
          Share this endpoint + Basic Auth credentials with each partner's tech team.
          They will POST PO payloads here. System auto-checks stock, creates PO in SAP, and returns acknowledgement.
        </p>
      </div>

      {/* Partner Config Cards */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-bold text-gray-800">Partner ASN Webhook Configuration</h2>
          <button onClick={load} className="flex items-center gap-2 px-3 py-1.5 border border-gray-200 rounded-lg text-xs hover:bg-gray-50">
            <RefreshCcw size={12} /> Refresh
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Configure each partner's HTTP endpoint so we can push ASN (Advance Shipment Notifications) to them after confirming shipment.
        </p>

        {loading ? (
          <div className="text-center py-10 text-gray-400">Loading companies...</div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {companies.map((company) => (
              <div key={company.id} className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
                  <div className="flex items-center gap-3">
                    <CompanyAvatar name={company.name} color={company.logo_color} size={36} />
                    <div>
                      <p className="font-semibold text-gray-800">{company.name}</p>
                      <p className="text-xs text-gray-400 font-mono">{company.code}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1.5">
                      {company.integration_active ? (
                        <><Wifi size={14} className="text-green-500" /><span className="text-xs text-green-600 font-medium">Active</span></>
                      ) : (
                        <><WifiOff size={14} className="text-gray-400" /><span className="text-xs text-gray-400">Inactive</span></>
                      )}
                    </div>
                    <button
                      onClick={() => setEditing(editing === company.id ? null : company.id)}
                      className="text-xs text-indigo-600 hover:underline"
                    >
                      {editing === company.id ? "Cancel" : "Configure"}
                    </button>
                  </div>
                </div>

                {editing === company.id ? (
                  <div className="p-5 space-y-3 bg-gray-50">
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">ASN Webhook Endpoint (their server)</label>
                      <input
                        type="url" placeholder="https://partner.api.com/webhook/asn"
                        value={forms[company.id]?.webhook_endpoint || ""}
                        onChange={(e) => updateForm(company.id, "webhook_endpoint", e.target.value)}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 bg-white"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Basic Auth Username</label>
                        <input
                          type="text" placeholder="username"
                          value={forms[company.id]?.webhook_username || ""}
                          onChange={(e) => updateForm(company.id, "webhook_username", e.target.value)}
                          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 bg-white"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Basic Auth Password</label>
                        <input
                          type="password" placeholder="••••••••"
                          value={forms[company.id]?.webhook_password || ""}
                          onChange={(e) => updateForm(company.id, "webhook_password", e.target.value)}
                          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 bg-white"
                        />
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox" id={`active-${company.id}`}
                        checked={forms[company.id]?.integration_active || false}
                        onChange={(e) => updateForm(company.id, "integration_active", e.target.checked)}
                        className="w-4 h-4 text-indigo-600"
                      />
                      <label htmlFor={`active-${company.id}`} className="text-sm text-gray-700">Integration Active</label>
                    </div>
                    <button
                      onClick={() => save(company.id)}
                      disabled={saving === company.id}
                      className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 disabled:opacity-50 transition"
                    >
                      <Save size={14} />
                      {saving === company.id ? "Saving..." : "Save Configuration"}
                    </button>
                  </div>
                ) : (
                  <div className="px-5 py-4 space-y-2">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="text-gray-400 text-xs w-24 flex-shrink-0">Endpoint:</span>
                      <span className={`font-mono text-xs truncate ${company.webhook_endpoint ? "text-indigo-600" : "text-gray-300"}`}>
                        {company.webhook_endpoint || "Not configured"}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 text-sm">
                      <span className="text-gray-400 text-xs w-24 flex-shrink-0">Auth User:</span>
                      <span className="text-xs text-gray-600">{company.webhook_username || "—"}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-gray-400 text-xs w-24 flex-shrink-0">Status:</span>
                      {company.integration_active ? (
                        <span className="flex items-center gap-1 text-xs text-green-600"><CheckCircle size={12} />Ready to sync ASN</span>
                      ) : (
                        <span className="flex items-center gap-1 text-xs text-gray-400"><Circle size={12} />Not active</span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* API Contract Reference */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
        <h2 className="font-bold text-gray-800 mb-4">API Contract Reference</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase mb-2">Inbound PO Payload (from partner)</p>
            <pre className="bg-gray-900 text-green-300 text-xs p-4 rounded-lg overflow-auto font-mono">
{`POST /api/webhook/inbound/po
Authorization: Basic <base64(user:pass)>

{
  "po_number": "BLK-20260417-001",
  "partner_code": "BLK",
  "order_date": "2026-04-17T10:30:00",
  "delivery_address": "Warehouse, Mumbai",
  "items": [
    {
      "sku": "AMUL-BTR-500",
      "product_name": "Amul Butter 500g",
      "quantity": 100,
      "unit_price": 275.00
    }
  ]
}`}
            </pre>
          </div>
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase mb-2">Acknowledgement Response (to partner)</p>
            <pre className="bg-gray-900 text-blue-300 text-xs p-4 rounded-lg overflow-auto font-mono">
{`HTTP 200 OK

{
  "status": "ACCEPTED",
  "po_number": "BLK-20260417-001",
  "sap_order_id": "SAP-4521983",
  "message": "PO accepted. Stock status: STOCK_AVAILABLE",
  "timestamp": "2026-04-17T10:30:05"
}

// OR if rejected:
{
  "status": "REJECTED",
  "message": "Duplicate PO number"
}`}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
}
