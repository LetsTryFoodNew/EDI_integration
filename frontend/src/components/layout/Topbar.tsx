import { LogOut, User } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/features/auth/useAuth";

const ENV = (import.meta.env.VITE_ENVIRONMENT as string | undefined) ?? "local";

const ENV_BADGE: Record<string, { label: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
  production: { label: "PROD",    variant: "destructive" },
  staging:    { label: "STAGING", variant: "secondary" },
  local:      { label: "LOCAL",   variant: "outline" },
};

export default function Topbar() {
  const { user, logout } = useAuth();
  const envBadge = ENV_BADGE[ENV] ?? { label: ENV.toUpperCase(), variant: "outline" as const };

  return (
    <header className="h-14 border-b flex items-center justify-between px-4 bg-background">
      <div />

      <div className="flex items-center gap-3">
        <Badge variant={envBadge.variant}>{envBadge.label}</Badge>

        <DropdownMenu>
          <DropdownMenuTrigger className="flex items-center gap-2 rounded-md px-3 py-1.5 text-sm hover:bg-accent transition-colors">
            <User className="h-4 w-4" />
            <span className="hidden sm:inline">{user?.email ?? "…"}</span>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuGroup>
              <DropdownMenuLabel className="font-normal">
                <p className="text-sm font-medium">{user?.full_name || user?.email}</p>
                <p className="text-xs text-muted-foreground">{user?.email}</p>
              </DropdownMenuLabel>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={logout} className="text-destructive gap-2 cursor-pointer">
              <LogOut className="h-4 w-4" />
              Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
