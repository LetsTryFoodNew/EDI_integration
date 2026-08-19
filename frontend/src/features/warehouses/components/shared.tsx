import { Badge } from "@/components/ui/badge";

// Shared bits of the Branch / Warehouse tables.
//
// The two status columns are deliberately separate. SAP's own flags (OBPL.Disabled,
// OWHS.Inactive) say whether the record is live in the ERP; our `is_active` says
// whether this middleware will route work to it. A warehouse can be live in SAP but
// parked here — dock under repair — and collapsing the two would hide which system
// needs the fix.

export function SapStatus({ off, offLabel }: { off: boolean; offLabel: string }) {
  return off ? (
    <Badge variant="destructive" className="text-[10px]">
      {offLabel}
    </Badge>
  ) : (
    <Badge variant="outline" className="text-[10px]">
      Live
    </Badge>
  );
}

export function LocalStatus({ active }: { active: boolean }) {
  return (
    <Badge variant={active ? "default" : "secondary"} className="text-[10px]">
      {active ? "In use" : "Parked"}
    </Badge>
  );
}

export function ParkButton({
  active,
  pending,
  onClick,
}: {
  active: boolean;
  pending: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={pending}
      onClick={onClick}
      className="rounded border px-2 py-1 text-xs hover:bg-accent disabled:opacity-40"
      aria-label={active ? "Park this record" : "Put this record back in use"}
    >
      {active ? "Park" : "Resume"}
    </button>
  );
}
