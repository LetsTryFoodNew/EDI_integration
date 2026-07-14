import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate } from "react-router-dom";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { ArrowLeft, RotateCcw, XCircle, Loader2, ExternalLink, Pencil, Send, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useToast } from "@/hooks/use-toast";
import StatusBadge from "@/components/shared/StatusBadge";
import DateDisplay from "@/components/shared/DateDisplay";
import MoneyDisplay from "@/components/shared/MoneyDisplay";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchPODetail, retrySAPPush, cancelPO, updatePO, pushToSAP, revalidatePO } from "./api";
import type { POUpdatePayload } from "./api";
import type { PODetail } from "@/types";

function EditPODialog({
  po,
  open,
  onClose,
  onSaved,
}: {
  po: PODetail;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { toast } = useToast();
  const { register, handleSubmit, formState: { isSubmitting } } = useForm<POUpdatePayload>({
    defaultValues: {
      buyer_po_number: po.buyer_po_number ?? "",
      buyer_po_date: po.issue_date ? po.issue_date.split("T")[0] : "",
      buyer_name: po.buyer_name ?? "",
      buyer_gstin: po.buyer_gstin ?? "",
      ship_to_name: po.ship_to_name ?? "",
      ship_to_code: po.ship_to_code ?? "",
      requested_delivery_date: po.delivery_date ? po.delivery_date.split("T")[0] : "",
      grand_total: po.grand_total ? parseFloat(po.grand_total) : undefined,
      currency: po.currency ?? "INR",
    },
  });

  async function onSubmit(data: POUpdatePayload) {
    // Only send non-empty values
    const payload: POUpdatePayload = {};
    for (const [k, v] of Object.entries(data)) {
      if (v !== "" && v !== undefined && v !== null) {
        (payload as Record<string, unknown>)[k] = v;
      }
    }
    try {
      await updatePO(po.id, payload);
      toast({ title: "PO updated successfully" });
      onSaved();
      onClose();
    } catch {
      toast({ title: "Failed to update PO", variant: "destructive" });
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit Purchase Order</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">PO Number</Label>
              <Input {...register("buyer_po_number")} placeholder="PO-XXXX" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">PO Date</Label>
              <Input type="date" {...register("buyer_po_date")} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Buyer Name</Label>
              <Input {...register("buyer_name")} placeholder="Buyer name" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Buyer GSTIN</Label>
              <Input {...register("buyer_gstin")} placeholder="27XXXXX" className="font-mono" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Ship-to Name</Label>
              <Input {...register("ship_to_name")} placeholder="Warehouse name" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Ship-to Code</Label>
              <Input {...register("ship_to_code")} placeholder="WH01" className="font-mono" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Delivery Date</Label>
              <Input type="date" {...register("requested_delivery_date")} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Grand Total (₹)</Label>
              <Input
                type="number"
                step="0.01"
                {...register("grand_total", { valueAsNumber: true })}
                placeholder="0.00"
              />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin mr-1" />}
              Save Changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function OverviewTab({ po }: { po: PODetail }) {
  const fields: [string, React.ReactNode][] = [
    ["Partner", po.partner_name],
    ["PO Number", <span key="po-num" className="font-mono">{po.buyer_po_number}</span>],
    ["Status", <StatusBadge key="status" status={po.po_status} />],
    ["Source", <Badge key="source" variant="outline">{po.source_channel}</Badge>],
    ["Issue Date", po.issue_date ? <DateDisplay key="issue" iso={po.issue_date} format="dd MMM yyyy" /> : "—"],
    ["Delivery Date", po.delivery_date ? <DateDisplay key="delivery" iso={po.delivery_date} format="dd MMM yyyy" /> : "—"],
    ["Ship-to", po.ship_to_name ?? po.ship_to_code ?? "—"],
    ["Buyer GSTIN", <span key="buyer-gstin" className="font-mono text-xs">{po.buyer_gstin ?? "—"}</span>],
    ["Seller GSTIN", <span key="seller-gstin" className="font-mono text-xs">{po.seller_gstin ?? "—"}</span>],
    ["Grand Total", po.grand_total ? <MoneyDisplay key="total" amount={parseFloat(po.grand_total)} /> : "—"],
    ["B1 Sales Order", po.b1_sales_order_doc_num ? `SO #${po.b1_sales_order_doc_num}` : "—"],
    ["Received", <DateDisplay key="received" iso={po.created_at} format="dd MMM yyyy HH:mm" />],
  ];

  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {fields.map(([label, value]) => (
        <div key={label} className="flex flex-col gap-0.5 py-2 border-b last:border-0">
          <span className="text-xs text-muted-foreground">{label}</span>
          <span className="text-sm">{value}</span>
        </div>
      ))}
    </div>
  );
}

function LineItemsTab({ po }: { po: PODetail }) {
  if (po.lines.length === 0)
    return <p className="text-sm text-muted-foreground py-4">No line items.</p>;

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>#</TableHead>
            <TableHead>Buyer SKU</TableHead>
            <TableHead>Description</TableHead>
            <TableHead>B1 Item</TableHead>
            <TableHead className="text-right">Qty</TableHead>
            <TableHead>UoM</TableHead>
            <TableHead className="text-right">Unit Price</TableHead>
            <TableHead className="text-right">Line Total</TableHead>
            <TableHead>HSN</TableHead>
            <TableHead>Mapping</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {po.lines.map((line) => (
            <TableRow key={line.id}>
              <TableCell className="text-muted-foreground">{line.line_number}</TableCell>
              <TableCell className="font-mono text-xs">{line.buyer_sku}</TableCell>
              <TableCell className="text-sm max-w-[150px] truncate" title={line.description ?? ""}>
                {line.description ?? "—"}
              </TableCell>
              <TableCell className="font-mono text-xs">{line.sap_material_no ?? "—"}</TableCell>
              <TableCell className="text-right">{line.ordered_qty ?? "—"}</TableCell>
              <TableCell>{line.uom ?? "—"}</TableCell>
              <TableCell className="text-right">
                {line.unit_price ? <MoneyDisplay amount={parseFloat(line.unit_price)} /> : "—"}
              </TableCell>
              <TableCell className="text-right">
                {line.line_total ? <MoneyDisplay amount={parseFloat(line.line_total)} /> : "—"}
              </TableCell>
              <TableCell className="font-mono text-xs">{line.hsn_code ?? "—"}</TableCell>
              <TableCell>
                {line.mapping_status ? (
                  <Badge
                    variant={line.mapping_status === "MAPPED" ? "default" : "secondary"}
                    className="text-xs"
                  >
                    {line.mapping_status}
                  </Badge>
                ) : "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function ValidationIssueCard({ issue, resolved }: { issue: PODetail["validation_issues"][number]; resolved: boolean }) {
  return (
    <div className={`flex items-start gap-3 p-3 rounded-md border ${resolved ? "opacity-60 bg-muted/30" : ""}`}>
      {resolved ? (
        <Badge className="text-xs bg-green-100 text-green-700 hover:bg-green-100">Resolved</Badge>
      ) : (
        <StatusBadge status={issue.severity as "ERROR" | "WARNING" | "INFO"} />
      )}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-muted-foreground">{issue.issue_code}</span>
          {issue.field_name && (
            <Badge variant="outline" className="text-xs">{issue.field_name}</Badge>
          )}
        </div>
        <p className={`text-sm mt-0.5 ${resolved ? "line-through decoration-muted-foreground/50" : ""}`}>{issue.message}</p>
        {issue.resolved_at && (
          <p className="text-xs text-green-600 mt-1">
            Resolved <DateDisplay iso={issue.resolved_at} format="dd MMM HH:mm" />
            {issue.resolution_note ? ` — ${issue.resolution_note}` : ""}
          </p>
        )}
      </div>
    </div>
  );
}

function ValidationTab({ po }: { po: PODetail }) {
  const open = po.validation_issues.filter((i) => !i.resolved_at);
  const resolved = po.validation_issues.filter((i) => i.resolved_at);

  if (po.validation_issues.length === 0)
    return <p className="text-sm text-muted-foreground py-4">No validation issues.</p>;

  return (
    <div className="space-y-2">
      {open.length === 0 && (
        <p className="text-sm text-green-600 py-2">All issues resolved. ✓</p>
      )}
      {open.map((issue) => (
        <ValidationIssueCard key={issue.id} issue={issue} resolved={false} />
      ))}
      {resolved.length > 0 && (
        <>
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground pt-3">
            Resolved ({resolved.length})
          </p>
          {resolved.map((issue) => (
            <ValidationIssueCard key={issue.id} issue={issue} resolved={true} />
          ))}
        </>
      )}
    </div>
  );
}

function B1PushTab({ po }: { po: PODetail }) {
  if (po.b1_push_history.length === 0)
    return <p className="text-sm text-muted-foreground py-4">No B1 push attempts.</p>;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Time</TableHead>
          <TableHead>Method</TableHead>
          <TableHead>Endpoint</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Result</TableHead>
          <TableHead>Duration</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {po.b1_push_history.map((h) => (
          <TableRow key={h.id}>
            <TableCell className="text-xs whitespace-nowrap">
              <DateDisplay iso={h.created_at} format="dd MMM HH:mm:ss" />
            </TableCell>
            <TableCell className="font-mono text-xs">{h.http_method}</TableCell>
            <TableCell className="font-mono text-xs max-w-[200px] truncate" title={h.endpoint}>
              {h.endpoint}
            </TableCell>
            <TableCell>{h.http_status ?? "—"}</TableCell>
            <TableCell>
              <Badge variant={h.success ? "default" : "destructive"} className="text-xs">
                {h.success ? "OK" : h.error_code ?? "FAIL"}
              </Badge>
            </TableCell>
            <TableCell className="text-muted-foreground text-xs">
              {h.duration_ms != null ? `${h.duration_ms}ms` : "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function OutboundTab({ po }: { po: PODetail }) {
  if (po.outbound_messages.length === 0)
    return <p className="text-sm text-muted-foreground py-4">No outbound messages.</p>;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Type</TableHead>
          <TableHead>Channel</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Attempts</TableHead>
          <TableHead>ACK Received</TableHead>
          <TableHead>Next Retry</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {po.outbound_messages.map((msg) => (
          <TableRow key={msg.id}>
            <TableCell>
              <Badge variant="outline" className="text-xs font-mono">{msg.doc_type}</Badge>
            </TableCell>
            <TableCell className="text-xs">{msg.channel}</TableCell>
            <TableCell>
              <StatusBadge status={msg.status as "PENDING" | "SENT" | "FAILED"} />
            </TableCell>
            <TableCell>{msg.attempt_count}</TableCell>
            <TableCell>
              {msg.ack_received_at ? (
                <DateDisplay iso={msg.ack_received_at} format="dd MMM HH:mm" />
              ) : "—"}
            </TableCell>
            <TableCell>
              {msg.next_retry_at ? (
                <DateDisplay iso={msg.next_retry_at} format="dd MMM HH:mm" />
              ) : "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function RawSourceTab({ po }: { po: PODetail }) {
  if (!po.raw_message_id)
    return <p className="text-sm text-muted-foreground py-4">No raw source attached.</p>;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">Message ID:</span>
        <span className="font-mono text-xs">{po.raw_message_id}</span>
        <a
          href={`/inbox/${po.raw_message_id}`}
          className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-accent transition-colors"
        >
          <ExternalLink className="h-3 w-3" />
          View source email &amp; attachments
        </a>
      </div>
    </div>
  );
}

export default function PODetailPage() {
  const { poId } = useParams<{ poId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [editOpen, setEditOpen] = useState(false);

  const { data: po, isLoading, isError } = useQuery({
    queryKey: ["pos", poId],
    queryFn: () => fetchPODetail(poId!),
    enabled: !!poId,
  });

  const retryMutation = useMutation({
    mutationFn: () => retrySAPPush(poId!),
    onSuccess: () => {
      toast({ title: "SAP push re-queued" });
      queryClient.invalidateQueries({ queryKey: ["pos", poId] });
    },
    onError: () => toast({ title: "Retry failed", variant: "destructive" }),
  });

  const pushMutation = useMutation({
    mutationFn: () => pushToSAP(poId!),
    onSuccess: () => {
      toast({ title: "Pushed to SAP", description: "SAP push job queued successfully." });
      queryClient.invalidateQueries({ queryKey: ["pos", poId] });
    },
    onError: () => toast({ title: "Push to SAP failed", variant: "destructive" }),
  });

  const revalidateMutation = useMutation({
    mutationFn: () => revalidatePO(poId!),
    onSuccess: (data) => {
      toast({ title: "Validation re-run", description: data.message });
      queryClient.invalidateQueries({ queryKey: ["pos", poId] });
      queryClient.invalidateQueries({ queryKey: ["exceptions"] });
    },
    onError: () => toast({ title: "Re-validation failed", variant: "destructive" }),
  });

  const cancelMutation = useMutation({
    mutationFn: () => cancelPO(poId!),
    onSuccess: () => {
      toast({ title: "PO cancelled" });
      queryClient.invalidateQueries({ queryKey: ["pos", poId] });
    },
    onError: () => toast({ title: "Cancel failed", variant: "destructive" }),
  });

  const [activeTab, setActiveTab] = useState("overview");

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (isError || !po) {
    return (
      <Alert variant="destructive">
        <AlertDescription>Failed to load purchase order.</AlertDescription>
      </Alert>
    );
  }

  const canRetry = po.po_status === "SAP_REJECTED";
  const canRevalidate = ["PARSED", "VALIDATED", "EXCEPTION"].includes(po.po_status);
  const openErrorCount = po.validation_issues.filter(
    (i) => !i.resolved_at && i.severity === "ERROR"
  ).length;
  const canPushToSap = ["PARSED", "VALIDATED", "EXCEPTION"].includes(po.po_status);
  const pushBlocked = openErrorCount > 0;
  const canCancel = ["PARSED", "VALIDATED", "EXCEPTION", "SAP_REJECTED"].includes(po.po_status);
  const canEdit = !["SAP_CONFIRMED", "CANCELLED", "SUPERSEDED"].includes(po.po_status);

  return (
    <div className="space-y-4">
      {/* Edit dialog */}
      {editOpen && (
        <EditPODialog
          po={po}
          open={editOpen}
          onClose={() => setEditOpen(false)}
          onSaved={() => queryClient.invalidateQueries({ queryKey: ["pos", poId] })}
        />
      )}

      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-start gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="mt-0.5">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold font-mono">{po.buyer_po_number}</h1>
              {po.version > 1 && (
                <Badge variant="secondary" className="text-xs">v{po.version}</Badge>
              )}
              <StatusBadge status={po.po_status} />
            </div>
            <p className="text-sm text-muted-foreground mt-0.5">{po.partner_name}</p>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0 flex-wrap">
          {/* Edit button */}
          {canEdit && (
            <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
              <Pencil className="h-4 w-4 mr-1" />
              Edit
            </Button>
          )}

          {/* Re-validate */}
          {canRevalidate && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => revalidateMutation.mutate()}
              disabled={revalidateMutation.isPending}
            >
              {revalidateMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-1" />
              ) : (
                <ShieldCheck className="h-4 w-4 mr-1" />
              )}
              Re-validate
            </Button>
          )}

          {/* Push to SAP — blocked while validation errors are unresolved */}
          {canPushToSap && (
            <Button
              size="sm"
              variant="default"
              onClick={() => pushMutation.mutate()}
              disabled={pushMutation.isPending || pushBlocked}
              title={
                pushBlocked
                  ? `Resolve ${openErrorCount} validation error(s) before pushing to SAP`
                  : undefined
              }
            >
              {pushMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-1" />
              ) : (
                <Send className="h-4 w-4 mr-1" />
              )}
              Push to SAP
              {pushBlocked && (
                <Badge variant="destructive" className="ml-1.5 text-xs">{openErrorCount}</Badge>
              )}
            </Button>
          )}

          {canRetry && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => retryMutation.mutate()}
              disabled={retryMutation.isPending}
            >
              {retryMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-1" />
              ) : (
                <RotateCcw className="h-4 w-4 mr-1" />
              )}
              Retry SAP Push
            </Button>
          )}

          {canCancel && (
            <AlertDialog>
              <AlertDialogTrigger className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-accent transition-colors">
                <XCircle className="h-4 w-4" />
                Cancel PO
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Cancel this PO?</AlertDialogTitle>
                  <AlertDialogDescription>
                    PO <strong>{po.buyer_po_number}</strong> will be marked as cancelled. This cannot
                    be undone.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Keep PO</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={() => cancelMutation.mutate()}
                    className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  >
                    {cancelMutation.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      "Cancel PO"
                    )}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
        </div>
      </div>

      {/* Tabs */}
      <Card>
        <CardContent className="pt-4">
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList className="mb-4 flex-wrap h-auto">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="lines">
                Line Items
                <Badge variant="secondary" className="ml-1.5 text-xs">{po.lines.length}</Badge>
              </TabsTrigger>
              <TabsTrigger value="validation">
                Validation
                {po.validation_issues.filter((i) => !i.resolved_at).length > 0 && (
                  <Badge variant="destructive" className="ml-1.5 text-xs">
                    {po.validation_issues.filter((i) => !i.resolved_at).length}
                  </Badge>
                )}
              </TabsTrigger>
              <TabsTrigger value="b1-push">B1 Push History</TabsTrigger>
              <TabsTrigger value="outbound">Outbound Messages</TabsTrigger>
              <TabsTrigger value="raw">Raw Source</TabsTrigger>
            </TabsList>

            <TabsContent value="overview"><OverviewTab po={po} /></TabsContent>
            <TabsContent value="lines"><LineItemsTab po={po} /></TabsContent>
            <TabsContent value="validation"><ValidationTab po={po} /></TabsContent>
            <TabsContent value="b1-push"><B1PushTab po={po} /></TabsContent>
            <TabsContent value="outbound"><OutboundTab po={po} /></TabsContent>
            <TabsContent value="raw"><RawSourceTab po={po} /></TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}
