import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, Send } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/hooks/use-toast";
import { fetchDispatchOptions, previewSapPayload, pushToSAPWith } from "../api";

// Choosing a branch is not a formality. Under the India localization the branch is the
// "from" state for place of supply, so it decides whether the order is taxed CGST+SGST
// or IGST — and a wrong choice produces a perfectly valid-looking document with the
// wrong tax code, discovered at GST filing rather than at push time. So the dialog
// shows the tax consequence of every branch before the operator picks one, and offers a
// preview of the exact JSON before anything is sent.

const NONE = "__none__";

function TaxBadge({ effect }: { effect: string | undefined }) {
  if (!effect || effect === "UNKNOWN") {
    return <Badge variant="destructive" className="text-[10px]">tax unknown</Badge>;
  }
  return (
    <Badge variant={effect === "CSGST" ? "default" : "secondary"} className="text-[10px]">
      {effect === "CSGST" ? "CGST+SGST" : "IGST"}
    </Badge>
  );
}

export default function SapPushDialog({
  poId,
  open,
  onOpenChange,
}: {
  poId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const [bplId, setBplId] = useState<string>("");
  const [whsCode, setWhsCode] = useState<string>("");
  const [shipTo, setShipTo] = useState<string>(NONE);
  const [payTo, setPayTo] = useState<string>(NONE);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["po", poId, "dispatch-options"],
    queryFn: () => fetchDispatchOptions(poId),
    enabled: open,
    staleTime: 0,
  });

  // Seed from a previous attempt. Nothing is auto-picked when there is no prior
  // choice — a default branch would be a silent tax decision.
  useEffect(() => {
    if (!data) return;
    setBplId(data.selected_bpl_id ? String(data.selected_bpl_id) : "");
    setWhsCode(data.selected_whs_code ?? "");
    setShipTo(data.selected_ship_to_code ?? NONE);
    setPayTo(data.selected_pay_to_code ?? NONE);
  }, [data]);

  const branch = useMemo(
    () => data?.branches.find((b) => String(b.bpl_id) === bplId),
    [data, bplId],
  );

  // Changing branch invalidates a warehouse that belonged to the old one.
  useEffect(() => {
    if (branch && whsCode && !branch.warehouses.some((w) => w.whs_code === whsCode)) {
      setWhsCode("");
    }
  }, [branch, whsCode]);

  const selection = {
    bpl_id: Number(bplId),
    whs_code: whsCode,
    ship_to_code: shipTo === NONE ? null : shipTo,
    pay_to_code: payTo === NONE ? null : payTo,
  };
  const ready = Boolean(bplId && whsCode);

  const preview = useMutation({
    mutationFn: () => previewSapPayload(poId, selection),
    onError: (e: unknown) => toast({ title: describe(e), variant: "destructive" }),
  });

  const push = useMutation({
    mutationFn: () => pushToSAPWith(poId, selection),
    onSuccess: (res) => {
      toast({ title: "Pushed to SAP", description: res.message });
      queryClient.invalidateQueries({ queryKey: ["po", poId] });
      onOpenChange(false);
    },
    onError: (e: unknown) =>
      toast({ title: "SAP rejected the order", description: describe(e), variant: "destructive" }),
  });

  const shipToOptions = data?.addresses.filter((a) => a.address_type === "bo_ShipTo") ?? [];
  const payToOptions = data?.addresses.filter((a) => a.address_type === "bo_BillTo") ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Push to SAP</DialogTitle>
          <DialogDescription>
            {data
              ? `PO ${data.buyer_po_number} → Sales Order for ${data.b1_card_code ?? "(no CardCode)"}`
              : "Choose the branch and warehouse for this Sales Order."}
          </DialogDescription>
        </DialogHeader>

        {isLoading && (
          <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading branches and SAP addresses…
          </div>
        )}

        {isError && (
          <Alert variant="destructive">
            <AlertDescription>{describe(error)}</AlertDescription>
          </Alert>
        )}

        {data && (
          <div className="space-y-4">
            {data.address_lookup_error && (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>{data.address_lookup_error}</AlertDescription>
              </Alert>
            )}

            <p className="text-xs text-muted-foreground">
              Delivering to{" "}
              <span className="font-medium">{data.ship_to_state ?? "an unknown state"}</span>
              {data.ship_to_pincode ? ` (${data.ship_to_pincode})` : ""}. The branch you pick
              is the “from” state, so it decides the tax split.
            </p>

            {/* ── Branch ── */}
            <div className="space-y-1.5">
              <Label htmlFor="branch">Branch (BPL_IDAssignedToInvoice)</Label>
              <Select value={bplId} onValueChange={(v) => setBplId(v ?? "")}>
                <SelectTrigger id="branch">
                  {/* Base UI renders the raw value unless given a formatter. */}
                  <SelectValue placeholder="Select a branch…">
                    {(v) => {
                      const b = data.branches.find((x) => String(x.bpl_id) === v);
                      return b
                        ? `${b.bpl_id} · ${b.bpl_name}${b.state ? ` (${b.state})` : ""}`
                        : "Select a branch…";
                    }}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {data.branches.map((b) => (
                    <SelectItem key={b.bpl_id} value={String(b.bpl_id)}>
                      {b.bpl_id} · {b.bpl_name}
                      {b.state ? ` (${b.state})` : ""} —{" "}
                      {data.tax_by_branch[String(b.bpl_id)] === "CSGST"
                        ? "CGST+SGST"
                        : data.tax_by_branch[String(b.bpl_id)] === "IGST"
                          ? "IGST"
                          : "tax unknown"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {branch && (
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  This order will be taxed{" "}
                  <TaxBadge effect={data.tax_by_branch[String(branch.bpl_id)]} />
                  {branch.gstin && <span className="font-mono">{branch.gstin}</span>}
                </p>
              )}
            </div>

            {/* ── Warehouse ── */}
            <div className="space-y-1.5">
              <Label htmlFor="warehouse">Warehouse (WarehouseCode)</Label>
              <Select
                value={whsCode}
                onValueChange={(v) => setWhsCode(v ?? "")}
                disabled={!branch}
              >
                <SelectTrigger id="warehouse">
                  <SelectValue placeholder={branch ? "Select a warehouse…" : "Pick a branch first"}>
                    {(v) => {
                      const w = branch?.warehouses.find((x) => x.whs_code === v);
                      return w
                        ? `${w.whs_code} · ${w.whs_name}`
                        : branch
                          ? "Select a warehouse…"
                          : "Pick a branch first";
                    }}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {(branch?.warehouses ?? []).map((w) => (
                    <SelectItem key={w.whs_code} value={w.whs_code}>
                      {w.whs_code} · {w.whs_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {branch && branch.warehouses.length === 0 && (
                <p className="text-xs text-destructive">
                  This branch has no active warehouses. Re-run the B1 org sync, or pick
                  another branch.
                </p>
              )}
            </div>

            {/* ── Addresses ── */}
            <div className="grid gap-4 sm:grid-cols-2">
              <AddressPicker
                id="shipto" label="Ship-to (ShipToCode)" value={shipTo}
                onChange={setShipTo} options={shipToOptions}
              />
              <AddressPicker
                id="payto" label="Bill-to (PayToCode)" value={payTo}
                onChange={setPayTo} options={payToOptions}
              />
            </div>

            {preview.data && (
              <div className="space-y-2">
                {preview.data.warnings.length > 0 && (
                  <Alert>
                    <AlertTriangle className="h-4 w-4" />
                    <AlertDescription>
                      <ul className="list-disc pl-4 text-xs">
                        {preview.data.warnings.map((w) => <li key={w}>{w}</li>)}
                      </ul>
                    </AlertDescription>
                  </Alert>
                )}
                <div>
                  <p className="mb-1 text-xs text-muted-foreground">
                    POST <span className="font-mono">{preview.data.endpoint}</span>
                  </p>
                  <pre className="max-h-64 overflow-auto rounded border bg-muted/40 p-3 text-[11px] leading-relaxed">
                    {JSON.stringify(preview.data.payload, null, 2)}
                  </pre>
                </div>
              </div>
            )}
          </div>
        )}

        <DialogFooter className="gap-2 sm:gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="secondary"
            onClick={() => preview.mutate()}
            disabled={!ready || preview.isPending}
          >
            {preview.isPending && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
            Preview payload
          </Button>
          <Button onClick={() => push.mutate()} disabled={!ready || push.isPending}>
            {push.isPending ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Send className="mr-1 h-4 w-4" />
            )}
            Push to SAP
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AddressPicker({
  id, label, value, onChange, options,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { address_name: string; city: string | null; state: string | null; zip_code: string | null; matches_po: boolean }[];
}) {
  // A single customer can carry well over a hundred addresses; the ones whose PIN or
  // state matches the PO are sorted first by the API and flagged here.
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Select value={value} onValueChange={(v) => onChange(v ?? NONE)}>
        <SelectTrigger id={id}>
          <SelectValue placeholder="Use SAP default">
            {(v) => (!v || v === NONE ? "Use SAP default" : String(v))}
          </SelectValue>
        </SelectTrigger>
        <SelectContent className="max-h-72">
          <SelectItem value={NONE}>Use SAP default</SelectItem>
          {options.map((a, i) => (
            <SelectItem key={`${a.address_name}-${i}`} value={a.address_name}>
              {a.matches_po ? "★ " : ""}
              {a.address_name}
              {a.zip_code ? ` · ${a.zip_code}` : ""}
              {a.state ? ` (${a.state})` : ""}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

/** Server messages are the useful ones here — a bare "Request failed" hides the cause. */
function describe(e: unknown): string {
  const r = (e as { response?: { data?: { error?: { message?: string }; detail?: string } } })?.response;
  return (
    r?.data?.error?.message ??
    r?.data?.detail ??
    (e as Error)?.message ??
    "Something went wrong."
  );
}
