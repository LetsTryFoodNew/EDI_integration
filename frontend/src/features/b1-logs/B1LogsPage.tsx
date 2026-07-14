import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import DateDisplay from "@/components/shared/DateDisplay";
import { TableSkeleton } from "@/components/shared/LoadingSkeleton";
import EmptyState from "@/components/shared/EmptyState";
import { fetchB1Logs, fetchB1LogDetail } from "./api";
import type { B1LogListItem } from "@/types";

const PAGE_SIZE = 50;

function JsonBlock({ data }: { data: Record<string, unknown> | null }) {
  if (!data) return <p className="text-sm text-muted-foreground">—</p>;
  return (
    <pre className="text-xs overflow-x-auto bg-muted rounded p-3 max-h-96">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

function LogDetailDialog({ logId, onClose }: { logId: string; onClose: () => void }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["b1-logs", logId],
    queryFn: () => fetchB1LogDetail(logId),
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>B1 Log Detail</DialogTitle>
        </DialogHeader>
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : isError ? (
          <Alert variant="destructive"><AlertDescription>Failed to load.</AlertDescription></Alert>
        ) : data ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div><span className="text-muted-foreground">Method: </span><span className="font-mono">{data.http_method}</span></div>
              <div><span className="text-muted-foreground">Status: </span><span className={data.success ? "text-green-600" : "text-destructive"}>{data.http_status ?? "—"}</span></div>
              <div className="col-span-2"><span className="text-muted-foreground">Endpoint: </span><span className="font-mono text-xs break-all">{data.endpoint}</span></div>
              {data.duration_ms != null && (
                <div><span className="text-muted-foreground">Duration: </span>{data.duration_ms}ms</div>
              )}
              {data.error_code && (
                <div><span className="text-muted-foreground">Error: </span><span className="text-destructive">{data.error_code}: {data.error_message}</span></div>
              )}
            </div>

            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1.5">Request Payload</p>
              <JsonBlock data={data.request_payload} />
            </div>

            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1.5">Response Payload</p>
              <JsonBlock data={data.response_payload} />
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

export default function B1LogsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedLog, setSelectedLog] = useState<string | null>(null);

  const poId = searchParams.get("po_id") ?? "";
  const errorsOnly = searchParams.get("errors_only") === "1";
  const page = parseInt(searchParams.get("page") ?? "1", 10);
  const offset = (page - 1) * PAGE_SIZE;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["b1-logs-list", { poId, errorsOnly, page }],
    queryFn: () =>
      fetchB1Logs({
        po_id: poId || undefined,
        success: errorsOnly ? false : undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    placeholderData: (prev) => prev,
  });

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("page");
    setSearchParams(next, { replace: true });
  }

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">B1 Logs</h1>
        {data && <span className="text-sm text-muted-foreground">{data.total} entries</span>}
      </div>

      <div className="flex flex-wrap gap-2">
        <Input
          placeholder="Filter by PO ID…"
          value={poId}
          onChange={(e) => setParam("po_id", e.target.value)}
          className="w-72 font-mono text-xs"
        />
        <Button
          variant={errorsOnly ? "destructive" : "outline"}
          size="sm"
          onClick={() => setParam("errors_only", errorsOnly ? "" : "1")}
        >
          Errors only
        </Button>
      </div>

      {isError && (
        <Alert variant="destructive">
          <AlertDescription>Failed to load B1 logs.</AlertDescription>
        </Alert>
      )}

      {isLoading ? (
        <TableSkeleton rows={8} cols={7} />
      ) : (data?.items ?? []).length === 0 ? (
        <EmptyState title="No logs" description="No B1 API logs match your filters." />
      ) : (
        <>
          <div className="rounded-md border overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>PO ID</TableHead>
                  <TableHead>Method</TableHead>
                  <TableHead>Endpoint</TableHead>
                  <TableHead>HTTP</TableHead>
                  <TableHead>Result</TableHead>
                  <TableHead>Duration</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data?.items ?? []).map((log: B1LogListItem) => (
                  <TableRow
                    key={log.id}
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => setSelectedLog(log.id)}
                  >
                    <TableCell className="text-xs whitespace-nowrap">
                      <DateDisplay iso={log.created_at} format="dd MMM HH:mm:ss" />
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground max-w-[120px] truncate">
                      {log.po_id ? log.po_id.slice(0, 8) + "…" : "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{log.http_method}</TableCell>
                    <TableCell className="font-mono text-xs max-w-[200px] truncate" title={log.endpoint}>
                      {log.endpoint}
                    </TableCell>
                    <TableCell>
                      <span className={log.http_status && log.http_status < 300 ? "text-green-600" : "text-destructive"}>
                        {log.http_status ?? "—"}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={log.success ? "default" : "destructive"}
                        className="text-xs"
                      >
                        {log.success ? "OK" : log.error_code ?? "FAIL"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {log.duration_ms != null ? `${log.duration_ms}ms` : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Page {page} of {totalPages}</span>
            <div className="flex gap-1">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setParam("page", String(page - 1))}>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setParam("page", String(page + 1))}>
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </>
      )}

      {selectedLog && (
        <LogDetailDialog logId={selectedLog} onClose={() => setSelectedLog(null)} />
      )}
    </div>
  );
}
