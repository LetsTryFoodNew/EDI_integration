import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2 } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import type { BranchMaster } from "@/types";
import { fetchBranches, updateBranch } from "../api";
import { addressLine, dash } from "../format";
import { LocalStatus, ParkButton, SapStatus } from "./shared";

export default function BranchesTab() {
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["master-data", "branches", "all"],
    queryFn: () => fetchBranches({ limit: 500 }),
  });

  const park = useMutation({
    mutationFn: (b: BranchMaster) => updateBranch(b.id, { is_active: !b.is_active }),
    onSuccess: (_res, b) => {
      toast({ title: b.is_active ? `${b.bpl_name} parked` : `${b.bpl_name} back in use` });
      queryClient.invalidateQueries({ queryKey: ["master-data", "branches"] });
    },
    onError: () => toast({ title: "Could not update the branch", variant: "destructive" }),
  });

  if (isError) {
    return (
      <Alert variant="destructive">
        <AlertDescription>Failed to load branches.</AlertDescription>
      </Alert>
    );
  }

  if (isLoading) return <TableSkeleton rows={4} cols={8} />;

  if (!data?.items.length) {
    return (
      <EmptyState
        icon={<Building2 className="h-10 w-10" />}
        title="No branches"
        description="Nothing synced yet — SAP loads these via POST /api/master-data/branches/sync."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="text-right">BPLId</TableHead>
            <TableHead>Branch Name</TableHead>
            <TableHead>GSTIN</TableHead>
            <TableHead>Address</TableHead>
            <TableHead className="text-right">Warehouses</TableHead>
            <TableHead>In SAP</TableHead>
            <TableHead>Locally</TableHead>
            <TableHead className="text-right">Action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.map((b) => (
            <TableRow key={b.id}>
              <TableCell className="text-right font-mono text-sm">{b.bpl_id}</TableCell>
              <TableCell className="text-sm">
                <p>{b.bpl_name}</p>
                {b.notes && (
                  <p className="text-[11px] italic text-muted-foreground">{b.notes}</p>
                )}
              </TableCell>
              {/* The branch GSTIN is the registration B1 uses to derive CGST/SGST vs IGST. */}
              <TableCell className="font-mono text-xs">{dash(b.gstin)}</TableCell>
              <TableCell className="max-w-xs text-xs text-muted-foreground">
                {dash(b.address ?? addressLine(b))}
              </TableCell>
              <TableCell className="text-right text-sm tabular-nums">
                {b.warehouse_count}
              </TableCell>
              <TableCell>
                <SapStatus off={b.disabled} offLabel="Disabled" />
              </TableCell>
              <TableCell>
                <LocalStatus active={b.is_active} />
              </TableCell>
              <TableCell className="text-right">
                <ParkButton
                  active={b.is_active}
                  pending={park.isPending && park.variables?.id === b.id}
                  onClick={() => park.mutate(b)}
                />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
