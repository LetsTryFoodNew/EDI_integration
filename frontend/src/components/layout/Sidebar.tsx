import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Inbox,
  Zap,
  ClipboardEdit,
  ShoppingCart,
  AlertTriangle,
  Database,
  FileText,
  Activity,
  Warehouse,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Rendered indented beneath the parent. Each child is a route in its own right. */
  children?: NavItem[];
}

const NAV_ITEMS: NavItem[] = [
  { to: "/",             label: "Dashboard",       icon: LayoutDashboard },
  { to: "/inbox",        label: "Email Inbox",     icon: Inbox },
  { to: "/api-inbox",    label: "API Inbox",       icon: Zap },
  { to: "/manual-inbox", label: "Manual Inbox",    icon: ClipboardEdit },
  { to: "/pos",          label: "Purchase Orders", icon: ShoppingCart },
  { to: "/exceptions",   label: "Exceptions",      icon: AlertTriangle },
  {
    to: "/master-data",
    label: "Master Data",
    icon: Database,
    children: [
      { to: "/master-data/warehouses", label: "Warehouses", icon: Warehouse },
    ],
  },
  { to: "/b1-logs",      label: "B1 Logs",         icon: FileText },
];

const linkClass = (isActive: boolean, nested: boolean) =>
  cn(
    "flex items-center gap-2.5 rounded-md py-2 text-sm transition-colors",
    nested ? "pl-9 pr-3 text-[13px]" : "px-3",
    isActive
      ? "bg-primary text-primary-foreground font-medium"
      : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
  );

function NavRow({ item, nested = false }: { item: NavItem; nested?: boolean }) {
  const { to, label, icon: Icon, children } = item;
  return (
    <NavLink
      to={to}
      // Prefix matching by default, so a detail route keeps its parent highlighted
      // (/pos/:id → "Purchase Orders"). Exact matching only for "/" and for any row
      // that has its own child rows — otherwise both would light up at once.
      end={to === "/" || Boolean(children)}
      className={({ isActive }) => linkClass(isActive, nested)}
    >
      <Icon className="h-4 w-4 shrink-0" />
      {label}
    </NavLink>
  );
}

export default function Sidebar() {
  return (
    <aside className="w-56 shrink-0 border-r bg-muted/30 flex flex-col">
      {/* Logo */}
      <div className="h-14 flex items-center px-4 border-b font-semibold text-sm tracking-tight gap-2">
        <Activity className="h-4 w-4 text-primary" />
        EDI Middleware
      </div>

      {/* Nav */}
      <nav className="flex-1 p-2 space-y-0.5">
        {NAV_ITEMS.map((item) => (
          <div key={item.to} className="space-y-0.5">
            <NavRow item={item} />
            {item.children?.map((child) => (
              <NavRow key={child.to} item={child} nested />
            ))}
          </div>
        ))}
      </nav>

      <div className="p-3 border-t">
        <p className="text-xs text-muted-foreground text-center">Let's Try Foods</p>
      </div>
    </aside>
  );
}
