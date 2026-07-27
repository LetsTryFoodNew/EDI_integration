import { Link, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  ShoppingCart,
  Package,
  Building2,
  Plus,
  Zap,
  Webhook,
  FileOutput,
  Settings,
  GitMerge,
  AlertTriangle,
  ReceiptText,
  ClipboardList,
  PackageCheck,
} from "lucide-react";

const coreNavItems = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/purchase-orders", icon: ShoppingCart, label: "Purchase Orders" },
  { to: "/purchase-orders/new", icon: Plus, label: "Create PO" },
  { to: "/products", icon: Package, label: "Inventory" },
  { to: "/companies", icon: Building2, label: "Companies" },
];

const ediNavItems = [
  { to: "/webhooks", icon: Webhook, label: "Webhook Logs" },
  { to: "/asn", icon: FileOutput, label: "ASN — Shipments" },
  { to: "/product-mappings", icon: GitMerge, label: "Product Mappings" },
  { to: "/unmapped-skus", icon: AlertTriangle, label: "Unmapped SKUs" },
  { to: "/sap-orders", icon: ReceiptText, label: "SAP Sales Orders" },
  { to: "/integration", icon: Settings, label: "Integration Setup" },
];

const zeptoNavItems = [
  { to: "/zepto/pos", icon: ClipboardList, label: "PO Events" },
  { to: "/zepto/asn", icon: PackageCheck,  label: "ASN Manager" },
];

const blinkitNavItems = [
  { to: "/blinkit/pos", icon: ClipboardList, label: "PO Events" },
  { to: "/blinkit/asn", icon: PackageCheck,  label: "ASN Manager" },
];


export default function Sidebar() {
  const { pathname } = useLocation();

  return (
    <aside className="w-64 bg-gray-900 text-white flex flex-col min-h-screen fixed left-0 top-0 z-40">
      {/* Logo */}
      <div className="flex items-center gap-3 px-6 py-5 border-b border-gray-700">
        <div className="w-9 h-9 bg-indigo-500 rounded-lg flex items-center justify-center">
          <Zap size={20} />
        </div>
        <div>
          <div className="font-bold text-sm leading-tight">EDI Integration</div>
          <div className="text-xs text-gray-400">FMCG · SAP Connect</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-4 px-3 overflow-y-auto">
        <p className="text-xs text-gray-500 uppercase tracking-wider px-3 mb-2">Core</p>
        {coreNavItems.map(({ to, icon: Icon, label }) => {
          const active = to === "/" ? pathname === "/" : pathname.startsWith(to) && to !== "/purchase-orders/new" || pathname === to;
          return (
            <Link
              key={to}
              to={to}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg mb-1 text-sm transition-all ${
                active ? "bg-indigo-600 text-white font-semibold" : "text-gray-400 hover:bg-gray-800 hover:text-white"
              }`}
            >
              <Icon size={18} />
              {label}
            </Link>
          );
        })}

        <p className="text-xs text-gray-500 uppercase tracking-wider px-3 mb-2 mt-4">EDI & Integration</p>
        {ediNavItems.map(({ to, icon: Icon, label }) => {
          const active = pathname === to || pathname.startsWith(to + "/");
          return (
            <Link
              key={to}
              to={to}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg mb-1 text-sm transition-all ${
                active ? "bg-indigo-600 text-white font-semibold" : "text-gray-400 hover:bg-gray-800 hover:text-white"
              }`}
            >
              <Icon size={18} />
              {label}
            </Link>
          );
        })}

        <p className="text-xs text-gray-500 uppercase tracking-wider px-3 mb-2 mt-4">
          <span className="inline-flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-violet-500"></span>
            Zepto
          </span>
        </p>
        {zeptoNavItems.map(({ to, icon: Icon, label }) => {
          const active = pathname === to || pathname.startsWith(to + "/");
          return (
            <Link
              key={to}
              to={to}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg mb-1 text-sm transition-all ${
                active ? "bg-violet-600 text-white font-semibold" : "text-gray-400 hover:bg-gray-800 hover:text-white"
              }`}
            >
              <Icon size={18} />
              {label}
            </Link>
          );
        })}

        <p className="text-xs text-gray-500 uppercase tracking-wider px-3 mb-2 mt-4">
          <span className="inline-flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400"></span>
            Blinkit
          </span>
        </p>
        {blinkitNavItems.map(({ to, icon: Icon, label }) => {
          const active = pathname === to || pathname.startsWith(to + "/");
          return (
            <Link
              key={to}
              to={to}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg mb-1 text-sm transition-all ${
                active ? "bg-amber-600 text-white font-semibold" : "text-gray-400 hover:bg-gray-800 hover:text-white"
              }`}
            >
              <Icon size={18} />
              {label}
            </Link>
          );
        })}
      </nav>
      {/* SAP Badge */}
      <div className="m-4 p-3 bg-gray-800 rounded-lg border border-gray-700">
        <div className="text-xs text-gray-400 mb-1">SAP Connection</div>
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse"></span>
          <span className="text-xs text-green-400 font-medium">Live Sync Active</span>
        </div>
        <div className="text-xs text-gray-500 mt-1.5">Webhook: /api/webhook/inbound/po</div>
      </div>
    </aside>
  );
}
