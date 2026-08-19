import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Warehouse } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { TableSkeleton } from "@/components/shared/LoadingSkeleton";
import EmptyState from "@/components/shared/EmptyState";
import { useToast } from "@/hooks/use-toast";
import type { WarehouseMaster } from "@/types";
import { fetchBranches, fetchWarehouses, updateWarehouse } from "../api";
import { addressLine, dash } from "../format";
import { LocalStatus, ParkButton, SapStatus } from "./shared";

export default function WarehousesTab() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [branchFilter, setBranchFilter] = useState<string>("all");

  // Shares a cache key with BranchesTab, so switching tabs does not refetch.
  const branches = useQuery({
    queryKey: ["master-data", "branches", "all"],
    queryFn: () => fetchBranches({ limit: 500 }),
  });

  const bplId = branchFilter === "all" ? undefined : Number(branchFilter);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["master-data", "warehouses", bplId ?? "all"],
    queryFn: () => fetchWarehouses({ bpl_id: bplId, limit: 500 }),
    placeholderData: (prev) => prev,
  });

  const park = useMutation({
    mutationFn: (w: WarehouseMaster) => updateWarehouse(w.id, { is_active: !w.is_active }),
    onSuccess: (_res, w) => {
      toast({ title: w.is_active ? `${w.whs_code} parked` : `${w.whs_code} back in use` });
      queryClient.invalidateQueries({ queryKey: ["master-data", "warehouses"] });
    },
    onError: () => toast({ title: "Could not update the warehouse", variant: "destructive" }),
  });

  if (isError) {
    return (
      <Alert variant="destructive">
        <AlertDescription>Failed to load warehouses.</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={branchFilter} onValueChange={(v) => setBranchFilter(v ?? "all")}>
          <SelectTrigger className="w-full sm:w-64">
            <SelectValue placeholder="All branches" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All branches</SelectItem>
            {(branches.data?.items ?? []).map((b) => (
              <SelectItem key={b.id} value={String(b.bpl_id)}>
                {b.bpl_id} · {b.bpl_name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {data && (
          <span className="ml-auto text-xs text-muted-foreground">
            {data.total} warehouse{data.total === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {isLoading ? (
        <TableSkeleton rows={6} cols={8} />
      ) : !data?.items.length ? (
        <EmptyState
          icon={<Warehouse className="h-10 w-10" />}
          title="No warehouses"
          description={
            branchFilter === "all"
              ? "Nothing synced yet — SAP loads these via POST /api/master-data/warehouses/sync."
              : "This branch has no warehouses."
          }
        />
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Warehouse Code</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Branch</TableHead>
                <TableHead>Address</TableHead>
                <TableHead className="text-right">Location</TableHead>
                <TableHead>In SAP</TableHead>
                <TableHead>Locally</TableHead>
                <TableHead className="text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((w) => (
                <TableRow key={w.id}>
                  <TableCell className="font-mono text-sm">{w.whs_code}</TableCell>
                  <TableCell className="text-sm">
                    <p>{w.whs_name}</p>
                    {w.notes && (
                      <p className="text-[11px] italic text-muted-foreground">{w.notes}</p>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    <p>{dash(w.branch_name)}</p>
                    <p className="font-mono text-muted-foreground">BPLId {w.bpl_id}</p>
                  </TableCell>
                  <TableCell className="max-w-xs text-xs text-muted-foreground">
                    {dash(addressLine(w))}
                  </TableCell>
                  <TableCell className="text-right text-sm tabular-nums">
                    {w.location ?? "—"}
                  </TableCell>
                  <TableCell>
                    <SapStatus off={w.inactive} offLabel="Inactive" />
                  </TableCell>
                  <TableCell>
                    <LocalStatus active={w.is_active} />
                  </TableCell>
                  <TableCell className="text-right">
                    <ParkButton
                      active={w.is_active}
                      pending={park.isPending && park.variables?.id === w.id}
                      onClick={() => park.mutate(w)}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
