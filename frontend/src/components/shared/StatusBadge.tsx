import { Badge } from "@/components/ui/badge";

const STATUS_CONFIG: Record<string, { label: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
  RECEIVED:      { label: "Received",     variant: "outline" },
  RAW:           { label: "Received",    variant: "outline" },
  PARSED:        { label: "Parsed",       variant: "secondary" },
  VALIDATED:     { label: "Validated",    variant: "default" },
  EXCEPTION:     { label: "Exception",    variant: "destructive" },
  SAP_PENDING:   { label: "SAP Pending",  variant: "secondary" },
  SAP_CONFIRMED: { label: "Confirmed",    variant: "default" },
  SAP_REJECTED:  { label: "SAP Rejected", variant: "destructive" },
  CANCELLED:     { label: "Cancelled",    variant: "outline" },
  SUPERSEDED:    { label: "Superseded",   variant: "outline" },
  // Validation issue severities
  ERROR:         { label: "Error",        variant: "destructive" },
  WARNING:       { label: "Warning",      variant: "secondary" },
  INFO:          { label: "Info",         variant: "outline" },
  // Outbound statuses
  PENDING:       { label: "Pending",      variant: "secondary" },
  SENT:          { label: "Sent",         variant: "default" },
  FAILED:        { label: "Failed",       variant: "destructive" },
  SKIPPED:       { label: "Skipped",      variant: "outline" },
};

interface Props {
  status: string;
  className?: string;
}

export default function StatusBadge({ status, className }: Props) {
  const config = STATUS_CONFIG[status] ?? { label: status, variant: "outline" as const };
  return (
    <Badge variant={config.variant} className={className}>
      {config.label}
    </Badge>
  );
}
