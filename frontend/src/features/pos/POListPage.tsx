import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
  getSortedRowModel,
} from "@tanstack/react-table";
import { useState } from "react";
import { ArrowUpDown, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Alert, AlertDescription } from "@/components/ui/alert";
import StatusBadge from "@/components/shared/StatusBadge";
import DateDisplay from "@/components/shared/DateDisplay";
import MoneyDisplay from "@/components/shared/MoneyDisplay";
import { TableSkeleton } from "@/components/shared/LoadingSkeleton";
import EmptyState from "@/components/shared/EmptyState";
import { fetchPOs } from "./api";
import type { POListItem, POStatus } from "@/types";

const PAGE_SIZE = 25;

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "All statuses" },
  { value: "RECEIVED", label: "Received" },
  { value: "PARSED", label: "Parsed" },
  { value: "VALIDATED", label: "Validated" },
  { value: "EXCEPTION", label: "Exception" },
  { value: "SAP_PENDING", label: "SAP Pending" },
  { value: "SAP_CONFIRMED", label: "SAP Confirmed" },
  { value: "SAP_REJECTED", label: "SAP Rejected" },
  { value: "CANCELLED", label: "Cancelled" },
  { value: "SUPERSEDED", label: "Superseded" },
];

const col = createColumnHelper<POListItem>();

const columns = [
  col.accessor("buyer_po_number", {
    header: "PO Number",
    cell: (info) => (
      <span className="font-mono text-sm font-medium">
        {info.getValue()}
        {info.row.original.version > 1 && (
          <span className="ml-1.5 text-xs font-sans text-muted-foreground">v{info.row.original.version}</span>
        )}
      </span>
    ),
  }),
  col.accessor("partner_name", {
    header: "Partner",
    cell: (info) => <span className="text-sm">{info.getValue()}</span>,
  }),
  col.accessor("po_status", {
    header: "Status",
    cell: (info) => <StatusBadge status={info.getValue() as POStatus} />,
  }),
  col.accessor("issue_date", {
    header: "Issue Date",
    cell: (info) =>
      info.getValue() ? <DateDisplay iso={info.getValue()!} format="dd MMM yyyy" /> : "—",
  }),
  col.accessor("line_count", {
    header: "Lines",
    cell: (info) => <span className="text-sm text-muted-foreground">{info.getValue()}</span>,
  }),
  col.accessor("grand_total", {
    header: "Total",
    cell: (info) =>
      info.getValue() ? (
        <MoneyDisplay amount={parseFloat(info.getValue()!)} />
      ) : (
        "—"
      ),
  }),
  col.accessor("b1_sales_order_doc_num", {
    header: "B1 SO#",
    cell: (info) =>
      info.getValue() ? (
        <span className="font-mono text-sm">{info.getValue()}</span>
      ) : (
        <span className="text-muted-foreground text-sm">—</span>
      ),
  }),
  col.accessor("created_at", {
    header: ({ column }) => (
      <Button
        variant="ghost"
        size="sm"
        className="h-auto p-0 font-medium hover:bg-transparent"
        onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
      >
        Received
        <ArrowUpDown className="ml-1 h-3 w-3" />
      </Button>
    ),
    cell: (info) => (
      <DateDisplay iso={info.getValue()} format="dd MMM HH:mm" />
    ),
  }),
];

export default function POListPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [sorting, setSorting] = useState<SortingState>([{ id: "created_at", desc: true }]);

  const search = searchParams.get("search") ?? "";
  const partner = searchParams.get("partner") ?? "";
  const status = searchParams.get("status") ?? "";
  const page = parseInt(searchParams.get("page") ?? "1", 10);
  const offset = (page - 1) * PAGE_SIZE;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["pos", { search, partner, status, page }],
    queryFn: () =>
      fetchPOs({
        search: search || undefined,
        partner_code: partner || undefined,
        po_status: status || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    placeholderData: (prev) => prev,
  });

  const table = useReactTable({
    data: data?.items ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    onSortingChange: setSorting,
    state: { sorting },
    manualPagination: true,
  });

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1;

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page"); // reset to page 1 on filter change
    setSearchParams(next, { replace: true });
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Purchase Orders</h1>
        {data && (
          <span className="text-sm text-muted-foreground">{data.total} total</span>
        )}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <Input
          placeholder="Search PO number…"
          value={search}
          onChange={(e) => setParam("search", e.target.value)}
          className="w-52"
        />
        <Input
          placeholder="Partner code"
          value={partner}
          onChange={(e) => setParam("partner", e.target.value)}
          className="w-40"
        />
        <Select value={status || "__all__"} onValueChange={(v) => setParam("status", v === "__all__" ? "" : v)}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((opt) => (
              <SelectItem key={opt.value || "__all__"} value={opt.value || "__all__"}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {isError && (
        <Alert variant="destructive">
          <AlertDescription>Failed to load purchase orders.</AlertDescription>
        </Alert>
      )}

      {isLoading ? (
        <TableSkeleton rows={8} cols={8} />
      ) : (data?.items ?? []).length === 0 ? (
        <EmptyState
          title="No purchase orders"
          description="No POs match your current filters."
        />
      ) : (
        <>
          <div className="rounded-md border overflow-x-auto">
            <Table>
              <TableHeader>
                {table.getHeaderGroups().map((hg) => (
                  <TableRow key={hg.id}>
                    {hg.headers.map((header) => (
                      <TableHead key={header.id}>
                        {flexRender(header.column.columnDef.header, header.getContext())}
                      </TableHead>
                    ))}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody>
                {table.getRowModel().rows.map((row) => (
                  <TableRow
                    key={row.id}
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => navigate(`/pos/${row.original.id}`)}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">
              Page {page} of {totalPages}
            </span>
            <div className="flex items-center gap-1">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setParam("page", String(page - 1))}
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setParam("page", String(page + 1))}
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
