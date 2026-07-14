import { Inbox } from "lucide-react";

interface Props {
  title?: string;
  description?: string;
  icon?: React.ReactNode;
}

export default function EmptyState({
  title = "Nothing here",
  description = "No results found.",
  icon,
}: Props) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center gap-3">
      <div className="text-muted-foreground">
        {icon ?? <Inbox className="h-10 w-10" />}
      </div>
      <p className="font-medium">{title}</p>
      <p className="text-sm text-muted-foreground max-w-xs">{description}</p>
    </div>
  );
}
