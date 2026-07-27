import { useEffect, useState } from "react";
import { Webhook, RefreshCcw, CheckCircle, XCircle, Clock, Play } from "lucide-react";
import { getWebhookLogs, simulateWebhook, getCompanies } from "../api";
import type { WebhookLog, Company } from "../types";
import CompanyAvatar from "../components/CompanyAvatar";
import { formatDate } from "../utils";

const STATUS_STYLE: Record<string, string> = {
  SUCCESS: "bg-green-100 text-green-700 border-green-300",
  FAILED: "bg-red-100 text-red-700 border-red-300",
  PENDING: "bg-yellow-100 text-yellow-700 border-yellow-300",
};

const PARTNER_CODES = ["ZPT", "SWG", "BLK", "BBK"];

export default function WebhookLogs() {
  const [logs, setLogs] = useState<WebhookLog[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [simulating, setSimulating] = useState<string | null>(null);
  const [selectedLog, setSelectedLog] = useState<WebhookLog | null>(null);
  const [filterStatus, setFilterStatus] = useState("");

  const load = async () => {
    setLoading(true);
    const [l, c] = await Promise.all([getWebhookLogs(), getCompanies()]);
    setLogs(l.data);
    setCompanies(c.data);
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const simulate = async (code: string) => {
    setSimulating(code);
    try {
      await simulateWebhook(code);
      await load();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Simulation failed");
    }
    setSimulating(null);
  };

  const filtered = filterStatus ? logs.filter((l) => l.status === filterStatus) : logs;

  const stats = {
    total: logs.length,
    success: logs.filter((l) => l.status === "SUCCESS").length,
    failed: logs.filter((l) => l.status === "FAILED").length,
    pending: logs.filter((l) => l.status === "PENDING").length,
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Webhook size={24} className="text-indigo-600" />
            Webhook Logs
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Inbound PO webhooks from Blinkit, Zepto, Swiggy & BigBasket
          </p>
        </div>
        <button onClick={load} className="flex items-center gap-2 px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 transition">
          <RefreshCcw size={14} /> Refresh
        </button>
      </div>

      {/* Endpoint Info Banner */}
      <div className="bg-indigo-900 text-white rounded-xl p-5">
        <div className="flex items-start gap-4">
          <div className="bg-indigo-700 rounded-lg p-2 flex-shrink-0"><Webhook size={20} /></div>
          <div className="flex-1">
            <p className="font-semibold text-sm mb-1">Your Inbound Webhook Endpoint</p>
            <p className="font-mono text-indigo-200 text-sm bg-indigo-800 px-3 py-2 rounded-lg inline-block">
              POST http://your-server/api/webhook/inbound/po
            </p>
            <p className="text-indigo-300 text-xs mt-2">
              Share this URL with your partner's tech team (Blinkit Step 1). Secured via HTTP Basic Auth.
              Partners POST PO payloads here — system auto-checks stock, creates PO, sends ACK.
            </p>
          </div>
        </div>
      </div>

      {/* Simulate Panel */}
      <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
        <p className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
          <Play size={14} className="text-green-600" />
          Simulate Inbound PO — Test without real partner
        </p>
        <div className="flex flex-wrap gap-3">
          {PARTNER_CODES.map((code) => {
            const co = companies.find((c) => c.code === code);
            return (
              <button
                key={code}
                onClick={() => simulate(code)}
                disabled={simulating === code}
                className="flex items-center gap-2 px-4 py-2 rounded-lg border-2 border-dashed border-indigo-300 text-sm text-indigo-700 font-medium hover:bg-indigo-50 disabled:opacity-50 transition"
              >
                {co && <CompanyAvatar name={co.name} color={co.logo_color} size={22} />}
                {simulating === code ? "Sending..." : `Simulate ${code}`}
              </button>
            );
          })}
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total Hits", value: stats.total, icon: Webhook, color: "bg-indigo-500" },
          { label: "Accepted", value: stats.success, icon: CheckCircle, color: "bg-green-500" },
          { label: "Failed", value: stats.failed, icon: XCircle, color: "bg-red-500" },
          { label: "Pending", value: stats.pending, icon: Clock, color: "bg-yellow-500" },
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

      {/* Filter */}
      <div className="flex items-center gap-3">
        {["", "SUCCESS", "FAILED", "PENDING"].map((s) => (
          <button
            key={s}
            onClick={() => setFilterStatus(s)}
            className={`px-3 py-1.5 rounded-full text-xs font-medium border transition ${filterStatus === s ? "bg-indigo-600 text-white border-indigo-600" : "border-gray-200 text-gray-600 hover:bg-gray-50"}`}
          >
            {s || "All"}
          </button>
        ))}
      </div>

      {/* Logs Table */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        {loading ? (
          <div className="text-center py-16 text-gray-400">Loading webhook logs...</div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-16 text-gray-400">
            <Webhook size={40} className="mx-auto mb-3 text-gray-300" />
            <p className="font-medium">No webhook logs yet</p>
            <p className="text-sm mt-1">Use the simulate buttons above to test inbound POs</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
              <tr>
                <th className="px-5 py-3 text-left">Time</th>
                <th className="px-5 py-3 text-left">Partner</th>
                <th className="px-5 py-3 text-left">PO Number</th>
                <th className="px-5 py-3 text-left">Event</th>
                <th className="px-5 py-3 text-left">Source IP</th>
                <th className="px-5 py-3 text-center">HTTP</th>
                <th className="px-5 py-3 text-center">Status</th>
                <th className="px-5 py-3 text-center">Payload</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {filtered.map((log) => (
                <tr key={log.id} className="hover:bg-gray-50">
                  <td className="px-5 py-3 text-gray-500 text-xs whitespace-nowrap">{formatDate(log.created_at)}</td>
                  <td className="px-5 py-3">
                    {log.company ? (
                      <div className="flex items-center gap-2">
                        <CompanyAvatar name={log.company.name} color={log.company.logo_color} size={24} />
                        <span className="text-gray-700 font-medium">{log.company.name}</span>
                      </div>
                    ) : (
                      <span className="text-gray-400 text-xs">Unknown</span>
                    )}
                  </td>
                  <td className="px-5 py-3 font-mono text-xs text-indigo-600 font-semibold">
                    {log.po_number || "—"}
                  </td>
                  <td className="px-5 py-3">
                    <span className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs font-mono">
                      {log.event_type}
                    </span>
                  </td>
                  <td className="px-5 py-3 font-mono text-xs text-gray-400">{log.source_ip || "—"}</td>
                  <td className="px-5 py-3 text-center">
                    <span className={`text-xs font-bold ${log.response_status === 200 ? "text-green-600" : "text-red-500"}`}>
                      {log.response_status || "—"}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-center">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${STATUS_STYLE[log.status]}`}>
                      {log.status}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-center">
                    <button
                      onClick={() => setSelectedLog(log)}
                      className="text-xs text-indigo-600 hover:underline"
                    >
                      View
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Payload Modal */}
      {selectedLog && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setSelectedLog(null)}>
          <div className="bg-white rounded-xl w-full max-w-2xl max-h-[80vh] overflow-auto shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-5 border-b">
              <h3 className="font-bold text-gray-800">Webhook Payload — {selectedLog.po_number}</h3>
              <button onClick={() => setSelectedLog(null)} className="text-gray-400 hover:text-gray-700 text-lg">✕</button>
            </div>
            <div className="p-5">
              {selectedLog.error_message && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                  <strong>Error:</strong> {selectedLog.error_message}
                </div>
              )}
              <pre className="bg-gray-900 text-green-300 text-xs p-4 rounded-lg overflow-auto max-h-96 font-mono">
                {JSON.stringify(selectedLog.payload, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
