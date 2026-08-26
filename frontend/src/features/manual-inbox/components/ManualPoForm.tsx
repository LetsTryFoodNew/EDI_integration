import { useMemo } from "react";
import { useForm, useFieldArray, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { z } from "zod";
import { Plus, Trash2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { createManualEntry } from "../api";
import type { ManualPoEntry } from "../api";

// The operator types quantity, price and one GST rate. Everything arithmetic — the
// taxable amount, the CGST/SGST-vs-IGST split, the totals — is derived server-side in
// ManualEntryParser, so a keyed total can never disagree with its own lines. The
// preview below runs the same arithmetic purely so they can sanity-check against the
// paper PO before submitting; nothing it computes is sent.

const decimalish = (label: string, { min = 0 }: { min?: number } = {}) =>
  z
    .string()
    .trim()
    .min(1, `${label} is required`)
    .refine((v) => !Number.isNaN(Number(v)), `${label} must be a number`)
    .refine((v) => Number(v) >= min, `${label} must be at least ${min}`);

const optionalDecimal = (label: string, max: number) =>
  z
    .string()
    .trim()
    .refine((v) => v === "" || !Number.isNaN(Number(v)), `${label} must be a number`)
    .refine((v) => v === "" || (Number(v) >= 0 && Number(v) <= max), `${label} must be 0–${max}`);

const lineSchema = z.object({
  buyer_sku: z.string().trim().min(1, "SKU is required"),
  description: z.string().trim(),
  hsn_code: z.string().trim(),
  buyer_uom: z.string().trim(),
  ordered_qty: decimalish("Quantity").refine((v) => Number(v) > 0, "Quantity must be above zero"),
  unit_price: decimalish("Unit price"),
  gst_rate: optionalDecimal("GST rate", 100),
  discount_pct: optionalDecimal("Discount", 100),
});

const formSchema = z
  .object({
    buyer_po_number: z.string().trim().min(1, "PO number is required"),
    buyer_po_date: z.string(),
    requested_delivery_date: z.string(),
    buyer_name: z.string().trim(),
    buyer_gstin: z.string().trim(),
    ship_to_warehouse_code: z.string().trim(),
    ship_to_name: z.string().trim(),
    ship_to_line1: z.string().trim(),
    ship_to_city: z.string().trim(),
    ship_to_state: z.string().trim(),
    ship_to_pincode: z.string().trim(),
    ship_to_gstin: z.string().trim(),
    notes: z.string().trim(),
    line_items: z.array(lineSchema).min(1, "Add at least one line"),
  })
  // Both ends of the movement decide CGST+SGST vs IGST. The backend refuses an entry
  // it cannot decide; catching it here means the message lands on the field rather
  // than as a parse failure minutes later.
  .refine((v) => !!(v.ship_to_state || v.ship_to_gstin || v.buyer_gstin), {
    message: "Enter the delivery state or a GSTIN — the GST split cannot be decided without one",
    path: ["ship_to_state"],
  });

type FormValues = z.infer<typeof formSchema>;

const EMPTY_LINE = {
  buyer_sku: "",
  description: "",
  hsn_code: "",
  buyer_uom: "",
  ordered_qty: "",
  unit_price: "",
  gst_rate: "5",
  discount_pct: "",
};

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });

function num(value: string | undefined): number {
  const n = Number(value ?? "");
  return Number.isFinite(n) ? n : 0;
}

export default function ManualPoForm({
  partnerCode,
  partnerName,
  open,
  onClose,
  onCreated,
}: {
  partnerCode: string;
  partnerName: string;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { toast } = useToast();

  const {
    register,
    control,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      buyer_po_number: "",
      buyer_po_date: new Date().toISOString().slice(0, 10),
      requested_delivery_date: "",
      buyer_name: partnerName,
      buyer_gstin: "",
      ship_to_warehouse_code: "",
      ship_to_name: "",
      ship_to_line1: "",
      ship_to_city: "",
      ship_to_state: "",
      ship_to_pincode: "",
      ship_to_gstin: "",
      notes: "",
      line_items: [{ ...EMPTY_LINE }],
    },
  });

  const { fields, append, remove } = useFieldArray({ control, name: "line_items" });
  const watched = useWatch({ control, name: "line_items" });

  const totals = useMemo(() => {
    let taxable = 0;
    let tax = 0;
    for (const line of watched ?? []) {
      const gross = num(line?.ordered_qty) * num(line?.unit_price);
      const net = gross - (gross * num(line?.discount_pct)) / 100;
      taxable += net;
      tax += (net * num(line?.gst_rate)) / 100;
    }
    return { taxable, tax, grand: taxable + tax };
  }, [watched]);

  const mutation = useMutation({
    mutationFn: (values: FormValues) => createManualEntry(toEntry(values, partnerCode)),
    onSuccess: (result) => {
      toast({ title: "Purchase order recorded", description: result.message });
      reset();
      onCreated();
      onClose();
    },
    onError: (error: unknown) => {
      toast({
        title: "Could not record the order",
        description: detailOf(error),
        variant: "destructive",
      });
    },
  });

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-5xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>New purchase order — {partnerName}</DialogTitle>
          <DialogDescription>
            Enter what is printed on the order. Tax amounts and totals are worked out
            for you, so they cannot disagree with the lines.
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={handleSubmit((values) => mutation.mutate(values))}
          className="space-y-6"
        >
          {/* ── Order ── */}
          <section className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Order
            </h3>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="PO number" error={errors.buyer_po_number?.message} required>
                <Input {...register("buyer_po_number")} placeholder="LOTS-2026-0117" />
              </Field>
              <Field label="PO date">
                <Input type="date" {...register("buyer_po_date")} />
              </Field>
              <Field label="Delivery date">
                <Input type="date" {...register("requested_delivery_date")} />
              </Field>
              <Field label="Buyer name">
                <Input {...register("buyer_name")} />
              </Field>
              <Field label="Buyer GSTIN">
                <Input {...register("buyer_gstin")} placeholder="06AABCL1234C1ZX" />
              </Field>
            </div>
          </section>

          {/* ── Ship to ── */}
          <section className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Deliver to
            </h3>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Warehouse / DC code">
                <Input {...register("ship_to_warehouse_code")} placeholder="LOTS-DEL-01" />
              </Field>
              <Field label="Location name">
                <Input {...register("ship_to_name")} />
              </Field>
              <Field label="GSTIN">
                <Input {...register("ship_to_gstin")} />
              </Field>
              <Field label="Address" className="sm:col-span-2">
                <Input {...register("ship_to_line1")} />
              </Field>
              <Field label="City">
                <Input {...register("ship_to_city")} />
              </Field>
              <Field
                label="State"
                error={errors.ship_to_state?.message}
                hint="Decides CGST+SGST vs IGST"
              >
                <Input {...register("ship_to_state")} placeholder="Haryana" />
              </Field>
              <Field label="PIN code">
                <Input {...register("ship_to_pincode")} />
              </Field>
            </div>
          </section>

          {/* ── Lines ── */}
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Line items
              </h3>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => append({ ...EMPTY_LINE })}
              >
                <Plus className="h-3.5 w-3.5 mr-1" />
                Add line
              </Button>
            </div>

            {errors.line_items?.message && (
              <Alert variant="destructive">
                <AlertDescription>{errors.line_items.message}</AlertDescription>
              </Alert>
            )}

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-muted-foreground border-b">
                    <th className="py-2 pr-2 font-medium w-8">#</th>
                    <th className="py-2 pr-2 font-medium min-w-[8rem]">Buyer SKU *</th>
                    <th className="py-2 pr-2 font-medium min-w-[12rem]">Description</th>
                    <th className="py-2 pr-2 font-medium w-24">HSN</th>
                    <th className="py-2 pr-2 font-medium w-20">UoM</th>
                    <th className="py-2 pr-2 font-medium w-24">Qty *</th>
                    <th className="py-2 pr-2 font-medium w-28">Unit price *</th>
                    <th className="py-2 pr-2 font-medium w-20">GST %</th>
                    <th className="py-2 pr-2 font-medium w-20">Disc %</th>
                    <th className="py-2 pr-2 font-medium w-28 text-right">Line total</th>
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody>
                  {fields.map((field, i) => {
                    const line = watched?.[i];
                    const gross = num(line?.ordered_qty) * num(line?.unit_price);
                    const net = gross - (gross * num(line?.discount_pct)) / 100;
                    const lineTotal = net + (net * num(line?.gst_rate)) / 100;
                    const lineErrors = errors.line_items?.[i];
                    return (
                      <tr key={field.id} className="border-b last:border-0 align-top">
                        <td className="py-2 pr-2 text-muted-foreground">{i + 1}</td>
                        <Cell error={lineErrors?.buyer_sku?.message}>
                          <Input {...register(`line_items.${i}.buyer_sku`)} className="h-8" />
                        </Cell>
                        <Cell>
                          <Input {...register(`line_items.${i}.description`)} className="h-8" />
                        </Cell>
                        <Cell>
                          <Input {...register(`line_items.${i}.hsn_code`)} className="h-8" />
                        </Cell>
                        <Cell>
                          <Input {...register(`line_items.${i}.buyer_uom`)} className="h-8" />
                        </Cell>
                        <Cell error={lineErrors?.ordered_qty?.message}>
                          <Input
                            {...register(`line_items.${i}.ordered_qty`)}
                            inputMode="decimal"
                            className="h-8"
                          />
                        </Cell>
                        <Cell error={lineErrors?.unit_price?.message}>
                          <Input
                            {...register(`line_items.${i}.unit_price`)}
                            inputMode="decimal"
                            className="h-8"
                          />
                        </Cell>
                        <Cell error={lineErrors?.gst_rate?.message}>
                          <Input
                            {...register(`line_items.${i}.gst_rate`)}
                            inputMode="decimal"
                            className="h-8"
                          />
                        </Cell>
                        <Cell error={lineErrors?.discount_pct?.message}>
                          <Input
                            {...register(`line_items.${i}.discount_pct`)}
                            inputMode="decimal"
                            className="h-8"
                          />
                        </Cell>
                        <td className="py-2 pr-2 text-right tabular-nums">
                          {lineTotal > 0 ? inr.format(lineTotal) : "—"}
                        </td>
                        <td className="py-2">
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            aria-label={`Remove line ${i + 1}`}
                            disabled={fields.length === 1}
                            onClick={() => remove(i)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="flex justify-end">
              <dl className="text-sm space-y-1 w-56">
                <Total label="Taxable" value={inr.format(totals.taxable)} />
                <Total label="GST" value={inr.format(totals.tax)} />
                <Total label="Grand total" value={inr.format(totals.grand)} bold />
              </dl>
            </div>
            <p className="text-xs text-muted-foreground text-right">
              Indicative — the saved figures are computed server-side from these lines.
            </p>
          </section>

          <Field label="Notes" hint="Where the order came from, who phoned it in">
            <Textarea {...register("notes")} rows={2} />
          </Field>

          <div className="flex justify-end gap-2 pt-2 border-t">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
              Record order
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  children,
  error,
  hint,
  required,
  className,
}: {
  label: string;
  children: React.ReactNode;
  error?: string;
  hint?: string;
  required?: boolean;
  className?: string;
}) {
  return (
    <div className={`space-y-1 ${className ?? ""}`}>
      <Label className="text-xs">
        {label}
        {required && <span className="text-destructive"> *</span>}
      </Label>
      {children}
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

function Cell({ children, error }: { children: React.ReactNode; error?: string }) {
  return (
    <td className="py-2 pr-2">
      {children}
      {error && <p className="text-[11px] text-destructive mt-0.5">{error}</p>}
    </td>
  );
}

function Total({ label, value, bold }: { label: string; value: string; bold?: boolean }) {
  return (
    <div className="flex justify-between">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={`tabular-nums ${bold ? "font-semibold" : ""}`}>{value}</dd>
    </div>
  );
}

/** Blank optional fields are dropped rather than sent as "" — the API forbids extras
 *  and an empty string is not the same as "not supplied". */
function toEntry(values: FormValues, partnerCode: string): ManualPoEntry {
  const clean = (v: string) => (v.trim() === "" ? null : v.trim());
  return {
    partner_code: partnerCode,
    buyer_po_number: values.buyer_po_number.trim(),
    buyer_po_date: clean(values.buyer_po_date),
    requested_delivery_date: clean(values.requested_delivery_date),
    buyer_name: clean(values.buyer_name),
    buyer_gstin: clean(values.buyer_gstin),
    notes: clean(values.notes),
    ship_to: {
      warehouse_code: clean(values.ship_to_warehouse_code),
      name: clean(values.ship_to_name),
      line1: clean(values.ship_to_line1),
      city: clean(values.ship_to_city),
      state: clean(values.ship_to_state),
      pincode: clean(values.ship_to_pincode),
      gstin: clean(values.ship_to_gstin),
    },
    line_items: values.line_items.map((line) => ({
      buyer_sku: line.buyer_sku.trim(),
      ordered_qty: line.ordered_qty.trim(),
      unit_price: line.unit_price.trim(),
      gst_rate: line.gst_rate.trim() === "" ? "0" : line.gst_rate.trim(),
      description: clean(line.description),
      hsn_code: clean(line.hsn_code),
      buyer_uom: clean(line.buyer_uom),
      discount_pct: clean(line.discount_pct),
    })),
  };
}

function detailOf(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return "Something went wrong. Check the fields and try again.";
}
