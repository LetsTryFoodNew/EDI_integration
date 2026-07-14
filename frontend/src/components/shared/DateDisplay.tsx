import { formatInTimeZone } from "date-fns-tz";

const IST = "Asia/Kolkata";

interface Props {
  iso: string | null | undefined;
  format?: string;
  className?: string;
}

export default function DateDisplay({ iso, format = "dd MMM yyyy, HH:mm", className }: Props) {
  if (!iso) return <span className={className}>—</span>;
  try {
    return (
      <span className={className} title={iso}>
        {formatInTimeZone(new Date(iso), IST, format)}
      </span>
    );
  } catch {
    return <span className={className}>{iso}</span>;
  }
}
