import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Loader2, Save } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
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
import { useToast } from "@/hooks/use-toast";
import { TableSkeleton } from "@/components/shared/LoadingSkeleton";
import EmptyState from "@/components/shared/EmptyState";
import {
  fetchPartners,
  fetchMaterials,
  fetchSkuMappings,
  fetchShipToMappings,
  updateSkuMapping,
  updateShipToMapping,
} from "./api";
import type { SkuMapping, ShipToMapping } from "@/types";

// ── Partners tab ────────────────────────────────────────────────────────────

function PartnersTab() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["master-data", "partners"],
    queryFn: () => fetchPartners({ limit: 50 }),
  });

  if (isLoading) return <TableSkeleton rows={5} cols={6} />;
  if (isError) return <Alert variant="destructive"><AlertDescription>Failed to load.</AlertDescription></Alert>;
  if (!data?.items.length) return <EmptyState title="No partners" description="No trading partners configured." />;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Code</TableHead>
          <TableHead>Name</TableHead>
          <TableHead>Channel</TableHead>
          <TableHead>Gmail Label</TableHead>
          <TableHead>B1 CardCode</TableHead>
          <TableHead>SLA (h)</TableHead>
          <TableHead>Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.items.map((p) => (
          <TableRow key={p.id}>
            <TableCell className="font-mono text-sm">{p.code}</TableCell>
            <TableCell>{p.name}</TableCell>
            <TableCell><Badge variant="outline" className="text-xs">{p.source_channel}</Badge></TableCell>
            <TableCell className="text-xs text-muted-foreground">{p.gmail_label ?? "—"}</TableCell>
            <TableCell className="font-mono text-xs">{p.b1_card_code ?? "—"}</TableCell>
            <TableCell>{p.ack_sla_hours ?? "—"}</TableCell>
            <TableCell>
              <Badge variant={p.is_active ? "default" : "secondary"} className="text-xs">
                {p.is_active ? "Active" : "Inactive"}
              </Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ── Materials tab ───────────────────────────────────────────────────────────

const MATERIALS_PAGE_SIZE = 50;

function MaterialsTab() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["master-data", "materials", search, page],
    queryFn: () =>
      fetchMaterials({
        search: search || undefined,
        limit: MATERIALS_PAGE_SIZE,
        offset: (page - 1) * MATERIALS_PAGE_SIZE,
      }),
    placeholderData: (prev) => prev,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / MATERIALS_PAGE_SIZE)) : 1;

  if (isError) return <Alert variant="destructive"><AlertDescription>Failed to load.</AlertDescription></Alert>;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Input
          placeholder="Search item code or description…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          className="w-64"
        />
        {data && (
          <span className="text-xs text-muted-foreground ml-auto">{data.total} items</span>
        )}
      </div>
      {isLoading ? (
        <TableSkeleton rows={5} cols={5} />
      ) : !data?.items.length ? (
        <EmptyState title="No materials" description="No materials match your search." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>B1 Item Code</TableHead>
              <TableHead>Description</TableHead>
              <TableHead>HSN</TableHead>
              <TableHead>UoM</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.items.map((m) => (
              <TableRow key={m.id}>
                <TableCell className="font-mono text-sm">{m.b1_item_code}</TableCell>
                <TableCell className="text-sm">{m.description ?? "—"}</TableCell>
                <TableCell className="font-mono text-xs">{m.hsn_code ?? "—"}</TableCell>
                <TableCell>{m.uom ?? "—"}</TableCell>
                <TableCell>
                  <Badge variant={m.is_active ? "default" : "secondary"} className="text-xs">
                    {m.is_active ? "Active" : "Inactive"}
                  </Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
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

// ── SKU Mappings tab ─────────────────────────────────────────────────────────

const SKU_PAGE_SIZE = 50;

function SkuMappingsTab() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const [partnerFilter, setPartnerFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<{ b1_item_code: string; qty_per_buyer_uom: string }>({ b1_item_code: "", qty_per_buyer_uom: "" });

  const { data: partnersData } = useQuery({
    queryKey: ["master-data", "partners"],
    queryFn: () => fetchPartners({ limit: 50 }),
    staleTime: 300_000,
  });

  const { data, isLoading, isError } = useQuery({
    queryKey: ["master-data", "sku-mappings", search, partnerFilter, statusFilter, page],
    queryFn: () =>
      fetchSkuMappings({
        search: search || undefined,
        partner_code: partnerFilter || undefined,
        mapping_status: statusFilter || undefined,
        limit: SKU_PAGE_SIZE,
        offset: (page - 1) * SKU_PAGE_SIZE,
      }),
    placeholderData: (prev) => prev,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / SKU_PAGE_SIZE)) : 1;

  const saveMutation = useMutation({
    mutationFn: (id: string) =>
      updateSkuMapping(id, {
        b1_item_code: editValues.b1_item_code,
        qty_per_buyer_uom: editValues.qty_per_buyer_uom || undefined,
      }),
    onSuccess: () => {
      toast({ title: "SKU mapping saved" });
      queryClient.invalidateQueries({ queryKey: ["master-data", "sku-mappings"] });
      setEditingId(null);
    },
    onError: () => toast({ title: "Save failed", variant: "destructive" }),
  });

  function startEdit(row: SkuMapping) {
    setEditingId(row.id);
    setEditValues({
      b1_item_code: row.b1_item_code ?? "",
      qty_per_buyer_uom: row.qty_per_buyer_uom ?? "",
    });
  }

  if (isError) return <Alert variant="destructive"><AlertDescription>Failed to load.</AlertDescription></Alert>;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder="Search buyer SKU…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          className="w-48"
        />
        <select
          value={partnerFilter}
          onChange={(e) => { setPartnerFilter(e.target.value); setPage(1); }}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value="">All platforms</option>
          {(partnersData?.items ?? []).map((p) => (
            <option key={p.code} value={p.code}>{p.name}</option>
          ))}
        </select>
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value="">All statuses</option>
          <option value="MANUALLY_MAPPED">Mapped</option>
          <option value="AUTO_MAPPED">Auto-mapped</option>
          <option value="UNMAPPED">Unmapped</option>
        </select>
        {data && (
          <span className="text-xs text-muted-foreground ml-auto">
            {data.total} mappings
          </span>
        )}
      </div>

      {isLoading ? (
        <TableSkeleton rows={6} cols={6} />
      ) : !data?.items.length ? (
        <EmptyState title="No SKU mappings" description="No mappings match your filters." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Partner</TableHead>
              <TableHead>Buyer SKU</TableHead>
              <TableHead>B1 Item Code</TableHead>
              <TableHead>Qty/UoM</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Confidence</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.items.map((row) => (
              <TableRow key={row.id}>
                <TableCell className="text-xs text-muted-foreground">{row.partner_code}</TableCell>
                <TableCell className="font-mono text-xs">{row.buyer_sku}</TableCell>
                <TableCell>
                  {editingId === row.id ? (
                    <Input
                      value={editValues.b1_item_code}
                      onChange={(e) => setEditValues((v) => ({ ...v, b1_item_code: e.target.value }))}
                      className="h-7 text-xs font-mono w-32"
                      autoFocus
                    />
                  ) : (
                    <span className="font-mono text-xs">{row.b1_item_code ?? <span className="text-muted-foreground">—</span>}</span>
                  )}
                </TableCell>
                <TableCell>
                  {editingId === row.id ? (
                    <Input
                      value={editValues.qty_per_buyer_uom}
                      onChange={(e) => setEditValues((v) => ({ ...v, qty_per_buyer_uom: e.target.value }))}
                      className="h-7 text-xs w-20"
                      placeholder="1"
                    />
                  ) : (
                    <span className="text-xs">{row.qty_per_buyer_uom ?? "—"}</span>
                  )}
                </TableCell>
                <TableCell>
                  <Badge
                    variant={row.mapping_status === "MANUALLY_MAPPED" ? "default" : row.mapping_status === "AUTO_MAPPED" ? "secondary" : "destructive"}
                    className="text-xs"
                  >
                    {row.mapping_status}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {row.confidence_score != null ? `${Math.round(row.confidence_score * 100)}%` : "—"}
                </TableCell>
                <TableCell>
                  {editingId === row.id ? (
                    <div className="flex gap-1">
                      <Button size="sm" className="h-7 text-xs" onClick={() => saveMutation.mutate(row.id)} disabled={saveMutation.isPending}>
                        {saveMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                      </Button>
                      <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => setEditingId(null)}>
                        ✕
                      </Button>
                    </div>
                  ) : (
                    <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => startEdit(row)}>
                      Edit
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
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

// ── Ship-to Mappings tab ─────────────────────────────────────────────────────

function ShipToTab() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [partnerFilter, setPartnerFilter] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  const { data, isLoading, isError } = useQuery({
    queryKey: ["master-data", "ship-to", partnerFilter],
    queryFn: () => fetchShipToMappings({ partner_code: partnerFilter || undefined, limit: 100 }),
    placeholderData: (prev) => prev,
  });

  const saveMutation = useMutation({
    mutationFn: (id: string) => updateShipToMapping(id, { b1_whs_code: editValue }),
    onSuccess: () => {
      toast({ title: "Ship-to mapping saved" });
      queryClient.invalidateQueries({ queryKey: ["master-data", "ship-to"] });
      setEditingId(null);
    },
    onError: () => toast({ title: "Save failed", variant: "destructive" }),
  });

  function startEdit(row: ShipToMapping) {
    setEditingId(row.id);
    setEditValue(row.b1_whs_code ?? "");
  }

  if (isError) return <Alert variant="destructive"><AlertDescription>Failed to load.</AlertDescription></Alert>;

  return (
    <div className="space-y-3">
      <Input
        placeholder="Filter by partner code"
        value={partnerFilter}
        onChange={(e) => setPartnerFilter(e.target.value)}
        className="w-48"
      />
      {isLoading ? (
        <TableSkeleton rows={4} cols={4} />
      ) : !data?.items.length ? (
        <EmptyState title="No ship-to mappings" description="No mappings match your filters." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Partner</TableHead>
              <TableHead>Buyer Warehouse</TableHead>
              <TableHead>B1 WhsCode</TableHead>
              <TableHead>Status</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.items.map((row) => (
              <TableRow key={row.id}>
                <TableCell className="text-xs text-muted-foreground">{row.partner_code}</TableCell>
                <TableCell className="font-mono text-sm">{row.buyer_whs_code}</TableCell>
                <TableCell>
                  {editingId === row.id ? (
                    <Input
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      className="h-7 text-xs font-mono w-28"
                      autoFocus
                    />
                  ) : (
                    <span className="font-mono text-sm">{row.b1_whs_code ?? <span className="text-muted-foreground">—</span>}</span>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant={row.is_active ? "default" : "secondary"} className="text-xs">
                    {row.is_active ? "Active" : "Inactive"}
                  </Badge>
                </TableCell>
                <TableCell>
                  {editingId === row.id ? (
                    <div className="flex gap-1">
                      <Button size="sm" className="h-7 text-xs" onClick={() => saveMutation.mutate(row.id)} disabled={saveMutation.isPending}>
                        {saveMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                      </Button>
                      <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => setEditingId(null)}>
                        ✕
                      </Button>
                    </div>
                  ) : (
                    <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => startEdit(row)}>
                      Edit
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function MasterDataPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Master Data</h1>

      <Tabs defaultValue="sku-mappings">
        <TabsList>
          <TabsTrigger value="partners">Partners</TabsTrigger>
          <TabsTrigger value="materials">Material Master</TabsTrigger>
          <TabsTrigger value="sku-mappings">SKU Mappings</TabsTrigger>
          <TabsTrigger value="ship-to">Ship-to Mappings</TabsTrigger>
        </TabsList>

        <TabsContent value="partners" className="mt-4"><PartnersTab /></TabsContent>
        <TabsContent value="materials" className="mt-4"><MaterialsTab /></TabsContent>
        <TabsContent value="sku-mappings" className="mt-4"><SkuMappingsTab /></TabsContent>
        <TabsContent value="ship-to" className="mt-4"><ShipToTab /></TabsContent>
      </Tabs>
    </div>
  );
}
