import type { POStatus } from "../types";
import { STATUS_CONFIG } from "../utils";

export default function StatusBadge({ status }: { status: POStatus }) {
  const cfg = STATUS_CONFIG[status];
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${cfg.color} ${cfg.bg} ${cfg.border}`}
    >
      {cfg.label}
    </span>
  );
}
