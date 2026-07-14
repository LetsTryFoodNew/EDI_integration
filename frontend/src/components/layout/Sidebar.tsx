import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Inbox,
  ShoppingCart,
  AlertTriangle,
  Database,
  FileText,
  Activity,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/",             label: "Dashboard",       icon: LayoutDashboard },
  { to: "/inbox",        label: "Email Inbox",     icon: Inbox },
  { to: "/pos",          label: "Purchase Orders", icon: ShoppingCart },
  { to: "/exceptions",   label: "Exceptions",      icon: AlertTriangle },
  { to: "/master-data",  label: "Master Data",     icon: Database },
  { to: "/b1-logs",      label: "B1 Logs",         icon: FileText },
];

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
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-primary text-primary-foreground font-medium"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              )
            }
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="p-3 border-t">
        <p className="text-xs text-muted-foreground text-center">Let's Try Foods</p>
      </div>
    </aside>
  );
}
