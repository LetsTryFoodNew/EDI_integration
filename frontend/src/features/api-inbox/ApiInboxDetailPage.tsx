import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate, Link } from "react-router-dom";
import {
  ArrowLeft,
  ExternalLink,
  FileText,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Download,
  Building2,
  MapPin,
  Receipt,
  Package,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useToast } from "@/hooks/use-toast";
import DateDisplay from "@/components/shared/DateDisplay";
import StatusBadge from "@/components/shared/StatusBadge";
import { fetchApiMessage, retryApiParse } from "./api";

// ── Types ─────────────────────────────────────────────────────────────────────

interface ZeptoAddress {
  storeAddress?: string;
  storeShippingAddress?: string;
  storeBillingAddress?: string;
  vendorAddress?: string;
  vendorPinCode?: string;
}

interface ZeptoFinancial {
  entityGSTIN?: string;
  vendorGSTIN?: string;
  entityPAN?: string;
  vendorPAN?: string;
}

interface ZeptoLineItem {
  materialCode?: string;
  ean?: string;
  skuCode?: string;
  productName?: string;
  brandName?: string;
  hsnCode?: string;
  hsnText?: string;
  quantity?: number;
  packSize?: number;
  mrp?: number;
  costPrice?: number;
  taxExclusiveCost?: number;
  totalAmount?: number;
  cgstPercentage?: number;
  cgstValue?: number;
  sgstPercentage?: number;
  sgstValue?: number;
  igstPercentage?: number;
  igstValue?: number;
  cessValue?: number;
  margin?: number;
  subCategory?: string;
  taxLogic?: string;
}

interface ZeptoPayload {
  code?: string;
  type?: string;
  status?: string;
  eventId?: string;
  eventType?: string;
  orderDate?: string;
  deliveryDate?: string;
  expiryDate?: string;
  timestamp?: string;
  vendorCode?: string;
  vendorName?: string;
  vendorType?: string;
  entityCode?: string;
  entityName?: string;
  toStoreCode?: string;
  toStoreName?: string;
  fromStoreCode?: string;
  fromStoreName?: string;
  totalQty?: number;
  isInterstate?: boolean;
  address?: ZeptoAddress;
  financialDetails?: ZeptoFinancial;
  poLineItems?: ZeptoLineItem[];
  expiringUrlForPoPDF?: string;
  pdfFileName?: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n?: number | null, dp = 2) {
  if (n === undefined || n === null) return "—";
  return new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  }).format(n);
}

function fmtINR(n?: number | null) {
  if (n === undefined || n === null) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
  }).format(n);
}

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-2 py-1.5 border-b last:border-0 text-sm">
      <span className="text-muted-foreground text-xs font-medium self-start pt-0.5">{label}</span>
      <span className="break-words min-w-0">{value ?? "—"}</span>
    </div>
  );
}

// ── Zepto structured view ─────────────────────────────────────────────────────

function ZeptoPOView({ payload }: { payload: ZeptoPayload }) {
  const fin = payload.financialDetails ?? {};
  const addr = payload.address ?? {};
  const lines = payload.poLineItems ?? [];

  const grandTotal = lines.reduce((sum, l) => sum + (l.totalAmount ?? 0), 0);
  const totalTax = lines.reduce(
    (sum, l) => sum + (l.cgstValue ?? 0) + (l.sgstValue ?? 0) + (l.igstValue ?? 0) + (l.cessValue ?? 0),
    0,
  );
  const subtotal = grandTotal - totalTax;

  const interstate = payload.isInterstate;

  return (
    <div className="space-y-4">
      {/* ── PO header ─────────────────────────────── */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Order Details */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-1.5">
              <Receipt className="h-4 w-4 text-muted-foreground" />
              Order Details
            </CardTitle>
          </CardHeader>
          <CardContent>
            <InfoRow label="PO Number" value={<span className="font-mono font-semibold">{payload.code}</span>} />
            <InfoRow
              label="Status"
              value={
                <Badge
                  variant={payload.status === "RELEASED" ? "default" : "secondary"}
                  className="text-xs"
                >
                  {payload.status}
                </Badge>
              }
            />
            <InfoRow label="Event Type" value={payload.eventType} />
            <InfoRow
              label="Order Date"
              value={payload.orderDate ? <DateDisplay iso={payload.orderDate} format="dd MMM yyyy" /> : "—"}
            />
            <InfoRow
              label="Delivery Date"
              value={payload.deliveryDate ? <DateDisplay iso={payload.deliveryDate} format="dd MMM yyyy" /> : "—"}
            />
            <InfoRow
              label="Expiry Date"
              value={payload.expiryDate ? <DateDisplay iso={payload.expiryDate} format="dd MMM yyyy" /> : "—"}
            />
            <InfoRow label="Total Qty" value={payload.totalQty} />
            <InfoRow
              label="Interstate"
              value={
                <Badge variant={interstate ? "destructive" : "secondary"} className="text-xs">
                  {interstate ? "Yes — IGST" : "No — CGST+SGST"}
                </Badge>
              }
            />
          </CardContent>
        </Card>

        {/* Parties */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-1.5">
              <Building2 className="h-4 w-4 text-muted-foreground" />
              Parties
            </CardTitle>
          </CardHeader>
          <CardContent>
            <InfoRow label="Buyer" value={payload.entityName} />
            <InfoRow label="Buyer Code" value={payload.entityCode} />
            <InfoRow label="Buyer GSTIN" value={<span className="font-mono text-xs">{fin.entityGSTIN}</span>} />
            <InfoRow label="Buyer PAN" value={<span className="font-mono text-xs">{fin.entityPAN}</span>} />
            <div className="my-2 border-t" />
            <InfoRow label="Vendor" value={payload.vendorName} />
            <InfoRow label="Vendor Code" value={payload.vendorCode} />
            <InfoRow label="Vendor GSTIN" value={<span className="font-mono text-xs">{fin.vendorGSTIN}</span>} />
            <InfoRow label="Vendor PAN" value={<span className="font-mono text-xs">{fin.vendorPAN}</span>} />
            <InfoRow label="Vendor Type" value={payload.vendorType} />
          </CardContent>
        </Card>
      </div>

      {/* ── Addresses ─────────────────────────────── */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-1.5">
            <MapPin className="h-4 w-4 text-muted-foreground" />
            Addresses
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-3">
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Ship-to Store</p>
              <p className="text-sm font-semibold">{payload.toStoreName} <span className="text-muted-foreground font-normal">({payload.toStoreCode})</span></p>
              <p className="text-xs text-muted-foreground mt-0.5">{addr.storeShippingAddress}</p>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Bill-to Store</p>
              <p className="text-xs text-muted-foreground">{addr.storeBillingAddress || addr.storeAddress || "—"}</p>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Vendor / Ship-from</p>
              <p className="text-xs text-muted-foreground">{addr.vendorAddress || "—"}</p>
              {addr.vendorPinCode && <p className="text-xs text-muted-foreground">PIN: {addr.vendorPinCode}</p>}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ── Line Items ────────────────────────────── */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-1.5">
            <Package className="h-4 w-4 text-muted-foreground" />
            Line Items ({lines.length})
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="text-left px-4 py-2.5 font-medium text-muted-foreground w-6">#</th>
                  <th className="text-left px-4 py-2.5 font-medium text-muted-foreground min-w-[180px]">Product</th>
                  <th className="text-left px-4 py-2.5 font-medium text-muted-foreground">Material / EAN</th>
                  <th className="text-left px-4 py-2.5 font-medium text-muted-foreground">HSN</th>
                  <th className="text-right px-4 py-2.5 font-medium text-muted-foreground">Qty</th>
                  <th className="text-right px-4 py-2.5 font-medium text-muted-foreground">Unit Price</th>
                  <th className="text-right px-4 py-2.5 font-medium text-muted-foreground">MRP</th>
                  <th className="text-right px-4 py-2.5 font-medium text-muted-foreground">Tax</th>
                  <th className="text-right px-4 py-2.5 font-medium text-muted-foreground">Total</th>
                </tr>
              </thead>
              <tbody>
                {lines.map((line, i) => {
                  const tax = (line.cgstValue ?? 0) + (line.sgstValue ?? 0) + (line.igstValue ?? 0) + (line.cessValue ?? 0);
                  const taxRate = line.igstPercentage || ((line.cgstPercentage ?? 0) + (line.sgstPercentage ?? 0));
                  return (
                    <tr key={i} className="border-b hover:bg-muted/30 transition-colors">
                      <td className="px-4 py-3 text-muted-foreground">{i + 1}</td>
                      <td className="px-4 py-3">
                        <p className="font-medium leading-tight">{line.productName}</p>
                        <p className="text-muted-foreground text-[11px] mt-0.5">{line.brandName}</p>
                        {line.subCategory && (
                          <p className="text-muted-foreground text-[11px]">{line.subCategory}</p>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <p className="font-mono">{line.materialCode || "—"}</p>
                        <p className="text-muted-foreground text-[11px] mt-0.5">{line.ean}</p>
                      </td>
                      <td className="px-4 py-3">
                        <p className="font-mono">{line.hsnCode}</p>
                        {line.hsnText && (
                          <p
                            className="text-muted-foreground text-[10px] mt-0.5 max-w-[120px] truncate"
                            title={line.hsnText}
                          >
                            {line.hsnText}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {line.quantity}
                        {line.packSize && line.packSize > 1 && (
                          <span className="text-muted-foreground ml-1 text-[10px]">×{line.packSize}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">{fmtINR(line.taxExclusiveCost ?? line.costPrice)}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">{fmtINR(line.mrp)}</td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {tax > 0 ? (
                          <span>
                            {fmtINR(tax)}
                            <span className="text-muted-foreground ml-0.5 text-[10px]">({taxRate}%)</span>
                          </span>
                        ) : "—"}
                        {line.igstValue ? (
                          <p className="text-[10px] text-muted-foreground">IGST</p>
                        ) : (line.cgstValue || line.sgstValue) ? (
                          <p className="text-[10px] text-muted-foreground">CGST+SGST</p>
                        ) : null}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums font-medium">{fmtINR(line.totalAmount)}</td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr className="border-t bg-muted/30">
                  <td colSpan={4} className="px-4 py-2.5" />
                  <td className="px-4 py-2.5 text-right font-semibold tabular-nums">
                    {lines.reduce((s, l) => s + (l.quantity ?? 0), 0)}
                  </td>
                  <td colSpan={2} className="px-4 py-2.5" />
                  <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground text-xs">
                    {fmt(totalTax)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-bold tabular-nums">
                    {fmtINR(grandTotal)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>

          {/* Summary strip */}
          <div className="flex flex-wrap gap-x-6 gap-y-1 px-4 py-3 bg-muted/20 border-t text-xs text-muted-foreground">
            <span>Subtotal: <strong className="text-foreground">{fmtINR(subtotal)}</strong></span>
            <span>Tax: <strong className="text-foreground">{fmtINR(totalTax)}</strong></span>
            <span>Grand Total: <strong className="text-foreground font-bold">{fmtINR(grandTotal)}</strong></span>
            {interstate && <span className="text-amber-600 dark:text-amber-400 font-medium">Interstate — IGST applicable</span>}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ── Blinkit webhook view (minimal structured) ─────────────────────────────────

function GenericPayloadView({ payload }: { payload: Record<string, unknown> }) {
  // For Blinkit webhooks or unknown formats — render a readable key-value tree
  const entries = Object.entries(payload).filter(([k]) => k !== "items" && k !== "lineItems" && k !== "poLineItems");
  const lineKey = ["items", "lineItems", "poLineItems", "order_items"].find((k) => Array.isArray(payload[k]));
  const lines = lineKey ? (payload[lineKey] as Record<string, unknown>[]) : [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Order Fields</CardTitle>
        </CardHeader>
        <CardContent>
          {entries.map(([k, v]) => (
            <InfoRow
              key={k}
              label={k}
              value={
                typeof v === "object" ? (
                  <pre className="text-xs font-mono whitespace-pre-wrap break-all">
                    {JSON.stringify(v, null, 2)}
                  </pre>
                ) : String(v)
              }
            />
          ))}
        </CardContent>
      </Card>
      {lines.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Line Items ({lines.length})</CardTitle>
          </CardHeader>
          <CardContent className="p-0 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b bg-muted/50">
                  {Object.keys(lines[0] ?? {}).map((h) => (
                    <th key={h} className="text-left px-4 py-2 font-medium text-muted-foreground">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {lines.map((row, i) => (
                  <tr key={i} className="border-b hover:bg-muted/30">
                    {Object.values(row).map((v, j) => (
                      <td key={j} className="px-4 py-2">
                        {typeof v === "object" ? JSON.stringify(v) : String(v ?? "—")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ── Raw JSON collapsible ──────────────────────────────────────────────────────

function RawJsonSection({ payload }: { payload: Record<string, unknown> | null }) {
  const [open, setOpen] = useState(false);
  return (
    <Card>
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium hover:bg-muted/30 transition-colors rounded-t-lg"
        onClick={() => setOpen(!open)}
      >
        <span className="flex items-center gap-1.5 text-muted-foreground">
          <FileText className="h-3.5 w-3.5" />
          Raw JSON Payload
        </span>
        {open ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && (
        <CardContent className="pt-0">
          {payload ? (
            <pre className="text-xs font-mono whitespace-pre-wrap break-all overflow-x-auto rounded-md bg-muted/50 p-4 leading-relaxed max-h-[28rem] overflow-y-auto">
              {JSON.stringify(payload, null, 2)}
            </pre>
          ) : (
            <p className="text-sm text-muted-foreground italic">No payload stored.</p>
          )}
        </CardContent>
      )}
    </Card>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ApiInboxDetailPage() {
  const { messageId } = useParams<{ messageId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const { data: msg, isLoading, isError } = useQuery({
    queryKey: ["api-inbox", "message", messageId],
    queryFn: () => fetchApiMessage(messageId!),
    enabled: !!messageId,
  });

  const retryMutation = useMutation({
    mutationFn: () => retryApiParse(messageId!),
    onSuccess: () => {
      toast({ title: "Parse job queued", description: "The event will be re-parsed shortly." });
      queryClient.invalidateQueries({ queryKey: ["api-inbox"] });
    },
    onError: () => {
      toast({ title: "Failed to queue retry", variant: "destructive" });
    },
  });

  if (isLoading) {
    return (
      <div className="space-y-4 max-w-4xl">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-36 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (isError || !msg) {
    return (
      <Alert variant="destructive">
        <AlertDescription>Failed to load event details.</AlertDescription>
      </Alert>
    );
  }

  const payload = msg.payload as ZeptoPayload | null;
  const isZepto = msg.partner_code === "ZEPTO" || !!(payload && payload.poLineItems);
  const pdfUrl = isZepto ? (payload as ZeptoPayload)?.expiringUrlForPoPDF : undefined;

  return (
    <div className="space-y-4 max-w-4xl">
      {/* Back */}
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to API inbox
      </button>

      {/* Title row */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-lg font-bold leading-tight">
            PO{" "}
            <span className="font-mono">
              {(payload as ZeptoPayload)?.code || msg.po_number || msg.external_id}
            </span>
          </h1>
          <div className="flex items-center gap-2 mt-1.5 flex-wrap">
            <Badge variant="outline" className="text-xs">{msg.partner_name}</Badge>
            {msg.parse_status === "SUCCESS" ? (
              <Badge variant="default" className="text-xs bg-green-600">Parsed</Badge>
            ) : msg.parse_status === "FAILED" ? (
              <Badge variant="destructive" className="text-xs">Parse Failed</Badge>
            ) : (
              <Badge variant="secondary" className="text-xs">Pending</Badge>
            )}
            <span className="text-xs text-muted-foreground">
              Received: <DateDisplay iso={msg.received_at} format="dd MMM yyyy, HH:mm" />
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0 flex-wrap">
          {pdfUrl && (
            <a href={pdfUrl} target="_blank" rel="noreferrer">
              <Button size="sm" variant="outline" className="gap-1.5 text-xs">
                <Download className="h-3.5 w-3.5" />
                Download PDF
              </Button>
            </a>
          )}
          {msg.parse_status !== "SUCCESS" && (
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5 text-xs"
              disabled={retryMutation.isPending}
              onClick={() => retryMutation.mutate()}
            >
              <RefreshCw className={`h-3.5 w-3.5 ${retryMutation.isPending ? "animate-spin" : ""}`} />
              {retryMutation.isPending ? "Queueing…" : "Retry Parse"}
            </Button>
          )}
        </div>
      </div>

      {/* Linked canonical PO */}
      {msg.po_id && (
        <Card className="border-green-200 bg-green-50 dark:bg-green-950/20 dark:border-green-900">
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <FileText className="h-4 w-4 text-green-600" />
                <span className="text-sm font-medium text-green-700 dark:text-green-400">
                  Linked to Purchase Order
                </span>
              </div>
              <Link
                to={`/pos/${msg.po_id}`}
                className="flex items-center gap-1 text-sm text-primary hover:underline font-medium"
              >
                {msg.po_number}
                {msg.po_status && <StatusBadge status={msg.po_status} className="ml-1" />}
                <ExternalLink className="h-3 w-3 ml-0.5" />
              </Link>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Structured payload view */}
      {payload ? (
        isZepto ? (
          <ZeptoPOView payload={payload as ZeptoPayload} />
        ) : (
          <GenericPayloadView payload={payload as Record<string, unknown>} />
        )
      ) : (
        <Card>
          <CardContent className="pt-4">
            <p className="text-sm text-muted-foreground italic">No payload stored for this event.</p>
          </CardContent>
        </Card>
      )}

      {/* Raw JSON (collapsed by default) */}
      <RawJsonSection payload={msg.payload} />
    </div>
  );
}
