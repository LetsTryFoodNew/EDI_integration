import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import DateDisplay from "@/components/shared/DateDisplay";
import StatusBadge from "@/components/shared/StatusBadge";
import { TableSkeleton } from "@/components/shared/LoadingSkeleton";
import EmptyState from "@/components/shared/EmptyState";
import { fetchExceptions, resolveException } from "./api";
import type { ExceptionItem } from "@/types";

function severityOrder(s: string) {
  return s === "ERROR" ? 0 : s === "WARNING" ? 1 : 2;
}

export default function ExceptionsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const [resolving, setResolving] = useState<ExceptionItem | null>(null);
  const [note, setNote] = useState("");
  const [showResolved, setShowResolved] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["exceptions", { resolved: showResolved }],
    queryFn: () => fetchExceptions({ resolved: showResolved || undefined, limit: 200 }),
  });

  const resolveMutation = useMutation({
    mutationFn: () => resolveException(resolving!.id, note),
    onSuccess: () => {
      toast({ title: "Exception resolved" });
      queryClient.invalidateQueries({ queryKey: ["exceptions"] });
      setResolving(null);
      setNote("");
    },
    onError: () => toast({ title: "Failed to resolve", variant: "destructive" }),
  });

  const sorted = [...(data?.items ?? [])].sort(
    (a, b) => severityOrder(a.severity) - severityOrder(b.severity)
  );

  // Group by severity
  const groups: Record<string, ExceptionItem[]> = {};
  for (const item of sorted) {
    (groups[item.severity] ??= []).push(item);
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Exceptions</h1>
        <div className="flex items-center gap-2">
          <Button
            variant={showResolved ? "secondary" : "outline"}
            size="sm"
            onClick={() => setShowResolved(!showResolved)}
          >
            {showResolved ? "Hide resolved" : "Show resolved"}
          </Button>
          {data && (
            <span className="text-sm text-muted-foreground">{data.total} total</span>
          )}
        </div>
      </div>

      {isError && (
        <Alert variant="destructive">
          <AlertDescription>Failed to load exceptions.</AlertDescription>
        </Alert>
      )}

      {isLoading ? (
        <TableSkeleton rows={6} cols={5} />
      ) : sorted.length === 0 ? (
        <EmptyState
          title="No exceptions"
          description="All POs are processing normally."
        />
      ) : (
        <div className="space-y-6">
          {(["ERROR", "WARNING", "INFO"] as const).map((sev) => {
            const items = groups[sev];
            if (!items?.length) return null;
            return (
              <div key={sev}>
                <div className="flex items-center gap-2 mb-3">
                  <StatusBadge status={sev} />
                  <span className="text-sm text-muted-foreground">{items.length} issue{items.length !== 1 ? "s" : ""}</span>
                </div>

                <div className="divide-y border rounded-md">
                  {items.map((item) => (
                    <div key={item.id} className="flex items-start justify-between gap-4 p-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span
                            className="font-mono text-xs text-primary cursor-pointer hover:underline"
                            onClick={() => navigate(`/pos/${item.po_id}`)}
                          >
                            {item.buyer_po_number}
                          </span>
                          <Badge variant="outline" className="text-xs">{item.partner_code}</Badge>
                          <span className="font-mono text-xs text-muted-foreground">{item.issue_code}</span>
                          {item.field_name && (
                            <Badge variant="secondary" className="text-xs">{item.field_name}</Badge>
                          )}
                        </div>
                        <p className="text-sm mt-1">{item.message}</p>
                        {item.resolved_at && (
                          <p className="text-xs text-green-600 mt-0.5">
                            Resolved <DateDisplay iso={item.resolved_at} format="dd MMM HH:mm" />
                            {item.resolution_note && ` — ${item.resolution_note}`}
                          </p>
                        )}
                        <p className="text-xs text-muted-foreground mt-0.5">
                          <DateDisplay iso={item.created_at} format="dd MMM yyyy HH:mm" />
                        </p>
                      </div>

                      {!item.resolved_at && (
                        <Button
                          variant="outline"
                          size="sm"
                          className="shrink-0"
                          onClick={() => {
                            setResolving(item);
                            setNote("");
                          }}
                        >
                          <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
                          Resolve
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Resolve dialog */}
      <Dialog open={!!resolving} onOpenChange={(open) => !open && setResolving(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Resolve exception</DialogTitle>
          </DialogHeader>
          {resolving && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">{resolving.message}</p>
              <Textarea
                placeholder="Add a resolution note (optional)…"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={3}
              />
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setResolving(null)}>
              Cancel
            </Button>
            <Button
              onClick={() => resolveMutation.mutate()}
              disabled={resolveMutation.isPending}
            >
              {resolveMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-1" />
              ) : (
                <CheckCircle2 className="h-4 w-4 mr-1" />
              )}
              Mark resolved
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
