import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Package, Users } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
import { Alert, AlertDescription } from "@/components/ui/alert";
import { TableSkeleton } from "@/components/shared/LoadingSkeleton";
import EmptyState from "@/components/shared/EmptyState";
import DateDisplay from "@/components/shared/DateDisplay";
import { fetchCustomers, fetchCustomerDetail, fetchItems } from "./api";
import type { TradingPartner, CustomerSkuMapping, CustomerShipTo, CustomerBillTo } from "@/types";

// ── formatters ───────────────────────────────────────────────────────────────

const inr = (v: string | null) =>
  v === null || v === undefined
    ? "—"
    : new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: "INR",
        minimumFractionDigits: 2,
      }).format(Number(v));

const num = (v: string | number | null, dp = 2) =>
  v === null || v === undefined
    ? "—"
    : new Intl.NumberFormat("en-IN", {
        minimumFractionDigits: dp,
        maximumFractionDigits: dp,
      }).format(Number(v));

const dash = (v: string | null | undefined) =>
  v ? v : <span className="text-muted-foreground">—</span>;

function ActiveBadge({ active }: { active: boolean }) {
  return (
    <Badge variant={active ? "default" : "secondary"} className="text-xs">
      {active ? "Active" : "Inactive"}
    </Badge>
  );
}

// ── Expanded customer row: SKU mappings + ship-to addresses ──────────────────

function SkuMappingsTable({ rows }: { rows: CustomerSkuMapping[] }) {
  if (!rows.length) {
    return (
      <p className="text-xs text-muted-foreground italic py-3">
        No SKU mappings for this customer yet.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b text-muted-foreground">
            <th className="text-left font-medium py-2 pr-4">Buyer SKU Code</th>
            <th className="text-left font-medium py-2 pr-4">Item Name</th>
            <th className="text-left font-medium py-2 pr-4">B1 Item Code</th>
            <th className="text-left font-medium py-2 pr-4">EAN</th>
            <th className="text-right font-medium py-2 pr-4">Unit Price</th>
            <th className="text-right font-medium py-2 pr-4">Margin&nbsp;%</th>
            <th className="text-right font-medium py-2 pr-4">MRP</th>
            <th className="text-left font-medium py-2 pr-4">Created</th>
            <th className="text-left font-medium py-2 pr-4">Updated</th>
            <th className="text-left font-medium py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id} className="border-b last:border-0 hover:bg-muted/40">
              <td className="py-2 pr-4 font-mono">{s.buyer_sku}</td>
              <td className="py-2 pr-4">
                <p>{dash(s.item_name)}</p>
                {s.grammage && <p className="text-muted-foreground text-[11px]">{s.grammage}</p>}
              </td>
              <td className="py-2 pr-4 font-mono">
                <p>{s.b1_item_code}</p>
                {s.case_size !== null && (
                  <p className="text-muted-foreground text-[11px] font-sans">case of {s.case_size}</p>
                )}
              </td>
              <td className="py-2 pr-4 font-mono">{dash(s.ean_code)}</td>
              <td className="py-2 pr-4 text-right tabular-nums">{inr(s.unit_price)}</td>
              <td className="py-2 pr-4 text-right tabular-nums">{num(s.margin)}</td>
              {/* MRP is item data, joined from Item_master via the item code */}
              <td className="py-2 pr-4 text-right tabular-nums text-muted-foreground">{inr(s.mrp)}</td>
              <td className="py-2 pr-4 text-muted-foreground">
                <DateDisplay iso={s.created_at} format="dd MMM yyyy" />
              </td>
              <td className="py-2 pr-4 text-muted-foreground">
                <DateDisplay iso={s.updated_at} format="dd MMM yyyy" />
              </td>
              <td className="py-2"><ActiveBadge active={s.is_active} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ShipToTable({ rows }: { rows: CustomerShipTo[] }) {
  if (!rows.length) {
    return (
      <p className="text-xs text-muted-foreground italic py-3">
        No ship-to addresses for this customer yet.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b text-muted-foreground">
            <th className="text-left font-medium py-2 pr-4">DC Code</th>
            <th className="text-left font-medium py-2 pr-4">Address</th>
            <th className="text-left font-medium py-2 pr-4">City</th>
            <th className="text-left font-medium py-2 pr-4">State</th>
            <th className="text-left font-medium py-2 pr-4">Zip</th>
            <th className="text-left font-medium py-2 pr-4">GSTIN</th>
            <th className="text-left font-medium py-2 pr-4">GST Type</th>
            <th className="text-left font-medium py-2 pr-4">B1 Whs</th>
            <th className="text-left font-medium py-2 pr-4">POC</th>
            <th className="text-left font-medium py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id} className="border-b last:border-0 hover:bg-muted/40">
              <td className="py-2 pr-4 font-mono">{s.dc_code}</td>
              <td className="py-2 pr-4 max-w-[240px]">
                <p className="truncate" title={s.address ?? undefined}>
                  {dash(s.address ?? s.street)}
                </p>
                {s.warehouse_name && (
                  <p className="text-muted-foreground text-[11px]">{s.warehouse_name}</p>
                )}
              </td>
              <td className="py-2 pr-4">{dash(s.city)}</td>
              <td className="py-2 pr-4">{dash(s.state)}</td>
              <td className="py-2 pr-4 font-mono">{dash(s.zip_code)}</td>
              <td className="py-2 pr-4 font-mono">{dash(s.gst_regn_no)}</td>
              <td className="py-2 pr-4">
                {s.gst_type?.length ? s.gst_type.join(", ") : <span className="text-muted-foreground">—</span>}
              </td>
              <td className="py-2 pr-4 font-mono">{dash(s.b1_whs_code)}</td>
              <td className="py-2 pr-4">
                {s.poc_name ? (
                  <>
                    <p>{s.poc_name}</p>
                    <p className="text-muted-foreground text-[11px]">
                      {[s.poc_phone, s.poc_email].filter(Boolean).join(" · ")}
                    </p>
                  </>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td className="py-2"><ActiveBadge active={s.is_active} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BillToTable({ rows }: { rows: CustomerBillTo[] }) {
  if (!rows.length) {
    return (
      <p className="text-xs text-muted-foreground italic py-3">
        No bill-to addresses for this customer yet. SAP pushes these via
        POST /api/master-data/bill-to/sync.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b text-muted-foreground">
            <th className="text-left font-medium py-2 pr-4">Bill-to Code</th>
            <th className="text-left font-medium py-2 pr-4">Address</th>
            <th className="text-left font-medium py-2 pr-4">City</th>
            <th className="text-left font-medium py-2 pr-4">State</th>
            <th className="text-left font-medium py-2 pr-4">Zip</th>
            <th className="text-left font-medium py-2 pr-4">GSTIN</th>
            <th className="text-left font-medium py-2 pr-4">GST Type</th>
            <th className="text-left font-medium py-2 pr-4">B1 Bill-to</th>
            <th className="text-left font-medium py-2 pr-4">POC</th>
            <th className="text-left font-medium py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((b) => (
            <tr key={b.id} className="border-b last:border-0 hover:bg-muted/40">
              <td className="py-2 pr-4 font-mono">{b.bill_to_code}</td>
              <td className="py-2 pr-4 max-w-[240px]">
                <p className="truncate" title={b.address ?? undefined}>
                  {dash(b.address ?? b.street)}
                </p>
                {b.entity_name && (
                  <p className="text-muted-foreground text-[11px]">{b.entity_name}</p>
                )}
              </td>
              <td className="py-2 pr-4">{dash(b.city)}</td>
              <td className="py-2 pr-4">{dash(b.state)}</td>
              <td className="py-2 pr-4 font-mono">{dash(b.zip_code)}</td>
              <td className="py-2 pr-4 font-mono">{dash(b.gst_regn_no)}</td>
              <td className="py-2 pr-4">
                {b.gst_type?.length ? b.gst_type.join(", ") : <span className="text-muted-foreground">—</span>}
              </td>
              <td className="py-2 pr-4 font-mono">{dash(b.b1_bill_to_code)}</td>
              <td className="py-2 pr-4">
                {b.poc_name ? (
                  <>
                    <p>{b.poc_name}</p>
                    <p className="text-muted-foreground text-[11px]">
                      {[b.poc_phone, b.poc_email].filter(Boolean).join(" · ")}
                    </p>
                  </>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td className="py-2"><ActiveBadge active={b.is_active} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CustomerDetailPanel({ customerId }: { customerId: string }) {
  const [view, setView] = useState<"sku" | "shipto" | "billto">("sku");
  const { data, isLoading, isError } = useQuery({
    queryKey: ["master-data", "customer", customerId],
    queryFn: () => fetchCustomerDetail(customerId),
  });

  if (isLoading) {
    return <div className="py-4"><TableSkeleton rows={3} cols={6} /></div>;
  }
  if (isError || !data) {
    return (
      <Alert variant="destructive" className="my-3">
        <AlertDescription>Failed to load customer details.</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="py-3 space-y-3">
      {/* Customer attributes */}
      <div className="grid gap-x-8 gap-y-1.5 text-xs sm:grid-cols-2 lg:grid-cols-4">
        <div><span className="text-muted-foreground">Business Type: </span>{dash(data.business_type)}</div>
        <div><span className="text-muted-foreground">Group: </span>{dash(data.group_name)}</div>
        <div><span className="text-muted-foreground">Email: </span>{dash(data.email_address)}</div>
        <div>
          <span className="text-muted-foreground">Phone: </span>
          {data.phone_numbers?.length ? data.phone_numbers.join(", ") : <span className="text-muted-foreground">—</span>}
        </div>
        <div><span className="text-muted-foreground">GSTIN: </span><span className="font-mono">{dash(data.gstin)}</span></div>
        <div><span className="text-muted-foreground">B1 CardCode: </span><span className="font-mono">{dash(data.b1_card_code)}</span></div>
        <div><span className="text-muted-foreground">Channel: </span>{data.source_channel}</div>
        <div><span className="text-muted-foreground">PAN: </span><span className="font-mono">{dash(data.pan_card)}</span></div>
      </div>

      {/* Sub-tabs: SKU mappings | Ship-to | Bill-to */}
      <div className="flex gap-1 border-b">
        <button
          onClick={() => setView("sku")}
          className={`px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition-colors ${
            view === "sku"
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          SKU Mappings ({data.sku_mappings.length})
        </button>
        <button
          onClick={() => setView("shipto")}
          className={`px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition-colors ${
            view === "shipto"
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          Ship-to Addresses ({data.ship_to_mappings.length})
        </button>
        <button
          onClick={() => setView("billto")}
          className={`px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition-colors ${
            view === "billto"
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          Bill-to Addresses ({data.bill_to_mappings.length})
        </button>
      </div>

      {view === "sku" && <SkuMappingsTable rows={data.sku_mappings} />}
      {view === "shipto" && <ShipToTable rows={data.ship_to_mappings} />}
      {view === "billto" && <BillToTable rows={data.bill_to_mappings} />}
    </div>
  );
}

// ── Tab 1: Customers ─────────────────────────────────────────────────────────

function CustomersTab() {
  const [search, setSearch] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["master-data", "customers"],
    queryFn: () => fetchCustomers({ limit: 200 }),
  });

  if (isError) {
    return <Alert variant="destructive"><AlertDescription>Failed to load customers.</AlertDescription></Alert>;
  }

  const term = search.trim().toLowerCase();
  const rows: TradingPartner[] = (data?.items ?? []).filter(
    (c) => !term || c.code.toLowerCase().includes(term) || c.name.toLowerCase().includes(term),
  );

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Input
          placeholder="Search customer code or name…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-72"
        />
        {data && (
          <span className="text-xs text-muted-foreground ml-auto">
            {rows.length} of {data.total} customers
          </span>
        )}
      </div>

      {isLoading ? (
        <TableSkeleton rows={6} cols={7} />
      ) : !rows.length ? (
        <EmptyState
          title="No customers"
          description={term ? "No customers match your search." : "No customers loaded yet."}
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Customer Code</TableHead>
              <TableHead>Customer Name</TableHead>
              <TableHead>Business Type</TableHead>
              <TableHead>Group</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Channel</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((c) => {
              const open = expandedId === c.id;
              return (
                <Fragment key={c.id}>
                  <TableRow
                    className="cursor-pointer"
                    onClick={() => setExpandedId(open ? null : c.id)}
                  >
                    <TableCell className="pr-0">
                      {open ? (
                        <ChevronDown className="h-4 w-4 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="h-4 w-4 text-muted-foreground" />
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-sm">{c.code}</TableCell>
                    <TableCell>{c.name}</TableCell>
                    <TableCell className="text-sm">{dash(c.business_type)}</TableCell>
                    <TableCell className="text-sm">{dash(c.group_name)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{dash(c.email_address)}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">{c.source_channel}</Badge>
                    </TableCell>
                    <TableCell><ActiveBadge active={c.is_active} /></TableCell>
                  </TableRow>
                  {open && (
                    <TableRow className="hover:bg-transparent">
                      <TableCell colSpan={8} className="bg-muted/30 px-6">
                        <CustomerDetailPanel customerId={c.id} />
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

// ── Tab 2: Item Master ───────────────────────────────────────────────────────

const ITEMS_PAGE_SIZE = 50;

function ItemMasterTab() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["master-data", "items", search, page],
    queryFn: () =>
      fetchItems({
        search: search || undefined,
        limit: ITEMS_PAGE_SIZE,
        offset: (page - 1) * ITEMS_PAGE_SIZE,
      }),
    placeholderData: (prev) => prev,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / ITEMS_PAGE_SIZE)) : 1;

  if (isError) {
    return <Alert variant="destructive"><AlertDescription>Failed to load items.</AlertDescription></Alert>;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Input
          placeholder="Search item code, name or EAN…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          className="w-72"
        />
        {data && <span className="text-xs text-muted-foreground ml-auto">{data.total} items</span>}
      </div>

      {isLoading ? (
        <TableSkeleton rows={6} cols={8} />
      ) : !data?.items.length ? (
        <EmptyState
          title="No items"
          description={search ? "No items match your search." : "Item master is empty — load it via POST /api/master-data/materials/sync."}
        />
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Item Code</TableHead>
                <TableHead>Item Name</TableHead>
                <TableHead>Group</TableHead>
                <TableHead>HSN</TableHead>
                <TableHead className="text-right">Tax&nbsp;%</TableHead>
                <TableHead>UoM</TableHead>
                <TableHead>Sales UoM</TableHead>
                <TableHead>VAT (Pu/Sa)</TableHead>
                <TableHead className="text-right">MRP</TableHead>
                <TableHead className="text-right">Case</TableHead>
                <TableHead>EAN</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((m) => (
                <TableRow key={m.id}>
                  <TableCell className="font-mono text-sm">{m.item_code}</TableCell>
                  <TableCell className="text-sm">
                    <p>{dash(m.item_name)}</p>
                    {m.grammage && (
                      <p className="text-[11px] text-muted-foreground">{m.grammage}</p>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    {dash(m.items_group_name)}
                    {m.itms_grp_cod !== null && (
                      <span className="text-muted-foreground"> ({m.itms_grp_cod})</span>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{dash(m.hsn)}</TableCell>
                  <TableCell className="text-right tabular-nums text-sm">{num(m.tax_rate)}</TableCell>
                  <TableCell className="text-sm">{dash(m.invntry_uom)}</TableCell>
                  <TableCell className="text-sm">{dash(m.sal_unit_msr)}</TableCell>
                  <TableCell className="text-xs font-mono">
                    {m.vat_group_pu ?? "—"} / {m.vat_group_sa ?? "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-sm">{inr(m.mrp)}</TableCell>
                  <TableCell className="text-right tabular-nums text-sm">{m.case_size ?? "—"}</TableCell>
                  <TableCell className="font-mono text-xs">{dash(m.ean_code)}</TableCell>
                  <TableCell>
                    <div className="flex flex-col gap-1">
                      <ActiveBadge active={m.is_active} />
                      {m.frozen_for && (
                        <Badge variant="destructive" className="text-[10px]">Frozen</Badge>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2 text-xs text-muted-foreground">
          <button
            disabled={page <= 1}
            onClick={() => setPage(page - 1)}
            className="px-2 py-1 rounded border hover:bg-accent disabled:opacity-40"
          >
            ‹
          </button>
          <span>{page} / {totalPages}</span>
          <button
            disabled={page >= totalPages}
            onClick={() => setPage(page + 1)}
            className="px-2 py-1 rounded border hover:bg-accent disabled:opacity-40"
          >
            ›
          </button>
        </div>
      )}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function MasterDataPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Master Data</h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Expand a customer to see its SKU mappings and ship-to addresses.
        </p>
      </div>

      <Tabs defaultValue="customers">
        <TabsList>
          <TabsTrigger value="customers" className="gap-1.5">
            <Users className="h-3.5 w-3.5" />
            Customers / Partners
          </TabsTrigger>
          <TabsTrigger value="items" className="gap-1.5">
            <Package className="h-3.5 w-3.5" />
            Item Master
          </TabsTrigger>
        </TabsList>

        <TabsContent value="customers" className="mt-4"><CustomersTab /></TabsContent>
        <TabsContent value="items" className="mt-4"><ItemMasterTab /></TabsContent>
      </Tabs>
    </div>
  );
}
