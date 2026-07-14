import { useQuery } from "@tanstack/react-query";
import { ShoppingCart, CheckCircle2, AlertTriangle, Clock, RefreshCw } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import DateDisplay from "@/components/shared/DateDisplay";
import { fetchDashboardToday, fetchSLABreaches, fetchUnmappedSkus, fetchActivity } from "./api";

function MetricCard({
  title,
  value,
  icon: Icon,
  variant = "default",
  loading,
}: {
  title: string;
  value: number | undefined;
  icon: React.ElementType;
  variant?: "default" | "warning" | "error" | "success";
  loading: boolean;
}) {
  const colorMap = {
    default: "text-foreground",
    warning: "text-yellow-600",
    error: "text-destructive",
    success: "text-green-600",
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className={`h-4 w-4 ${colorMap[variant]}`} />
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-16" />
        ) : (
          <div className={`text-2xl font-bold ${colorMap[variant]}`}>{value ?? "—"}</div>
        )}
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const { data: today, isLoading: loadingToday, isFetching: fetchingToday, error: errorToday } = useQuery({
    queryKey: ["dashboard", "today"],
    queryFn: fetchDashboardToday,
    refetchInterval: 30_000,
  });

  const { data: slaBreaches, isLoading: loadingSLA } = useQuery({
    queryKey: ["dashboard", "sla-breaches"],
    queryFn: fetchSLABreaches,
    refetchInterval: 30_000,
  });

  const { data: unmappedSkus, isLoading: loadingSkus } = useQuery({
    queryKey: ["dashboard", "unmapped-skus"],
    queryFn: fetchUnmappedSkus,
    refetchInterval: 60_000,
  });

  const { data: activity, isLoading: loadingActivity } = useQuery({
    queryKey: ["dashboard", "activity"],
    queryFn: fetchActivity,
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
          {today?.last_updated && (
            <p className="text-xs text-muted-foreground mt-0.5">
              Updated <DateDisplay iso={today.last_updated} format="HH:mm:ss" />
              {fetchingToday && <RefreshCw className="inline ml-1 h-3 w-3 animate-spin opacity-40" />}
            </p>
          )}
        </div>
      </div>

      {errorToday && (
        <Alert variant="destructive">
          <AlertDescription>Failed to load dashboard data.</AlertDescription>
        </Alert>
      )}

      {/* Metric cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          title="Today's POs"
          value={today?.total_pos}
          icon={ShoppingCart}
          loading={loadingToday}
        />
        <MetricCard
          title="Confirmed in B1"
          value={today?.confirmed_pos}
          icon={CheckCircle2}
          variant="success"
          loading={loadingToday}
        />
        <MetricCard
          title="Exceptions"
          value={today?.exception_pos}
          icon={AlertTriangle}
          variant={(today?.exception_pos ?? 0) > 0 ? "error" : "default"}
          loading={loadingToday}
        />
        <MetricCard
          title="Pending B1 Push"
          value={today?.pending_b1_push}
          icon={Clock}
          variant={(today?.pending_b1_push ?? 0) > 0 ? "warning" : "default"}
          loading={loadingToday}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Per-partner stats */}
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle className="text-sm font-medium">By Partner (Today)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {loadingToday ? (
              Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-7 w-full" />)
            ) : (today?.partner_stats ?? []).length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">No POs today</p>
            ) : (
              today!.partner_stats.map((p) => (
                <div
                  key={p.partner_code}
                  className="flex items-center justify-between text-sm"
                >
                  <span className="font-medium truncate max-w-[140px]" title={p.partner_name}>
                    {p.partner_name}
                  </span>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-muted-foreground">{p.po_count} POs</span>
                    {p.error_count > 0 && (
                      <Badge variant="destructive" className="text-xs px-1.5 py-0">
                        {p.error_count} err
                      </Badge>
                    )}
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        {/* SLA breaches */}
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle className="text-sm font-medium">SLA Breaches</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {loadingSLA ? (
              Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-7 w-full" />)
            ) : (slaBreaches ?? []).length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">No SLA breaches</p>
            ) : (
              slaBreaches!.map((b) => (
                <div key={b.po_id} className="flex items-center justify-between text-sm">
                  <span className="font-mono text-xs truncate max-w-[130px]" title={b.buyer_po_number}>
                    {b.buyer_po_number}
                  </span>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <span className="text-xs text-muted-foreground">{b.partner_code}</span>
                    <Badge variant="destructive" className="text-xs px-1.5 py-0">
                      +{Math.round(b.hours_overdue)}h
                    </Badge>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        {/* Unmapped SKUs */}
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Unmapped SKUs</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {loadingSkus ? (
              Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-7 w-full" />)
            ) : (unmappedSkus ?? []).length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">All SKUs mapped</p>
            ) : (
              unmappedSkus!.slice(0, 6).map((s) => (
                <div key={`${s.partner_code}-${s.buyer_sku}`} className="flex items-center justify-between text-sm">
                  <span className="font-mono text-xs truncate max-w-[130px]" title={s.buyer_sku}>
                    {s.buyer_sku}
                  </span>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <span className="text-xs text-muted-foreground">{s.partner_code}</span>
                    <Badge variant="secondary" className="text-xs px-1.5 py-0">
                      ×{s.occurrence_count}
                    </Badge>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      {/* Recent activity */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Recent Activity</CardTitle>
        </CardHeader>
        <CardContent>
          {loadingActivity ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-6 w-full" />
              ))}
            </div>
          ) : (activity ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-6">No recent activity</p>
          ) : (
            <div className="divide-y">
              {activity!.map((item, idx) => (
                <div key={idx} className="flex items-center justify-between py-2 text-sm">
                  <div className="flex items-center gap-2 min-w-0">
                    <Badge variant="outline" className="text-xs shrink-0">
                      {item.entity_type}
                    </Badge>
                    <span className="truncate text-muted-foreground">{item.description}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-2">
                    <Badge
                      variant={item.status === "SAP_CONFIRMED" ? "default" : "secondary"}
                      className="text-xs"
                    >
                      {item.status}
                    </Badge>
                    <span className="text-xs text-muted-foreground whitespace-nowrap">
                      <DateDisplay iso={item.created_at} format="HH:mm" />
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
