import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  ClipboardEdit,
  Globe,
  PenLine,
  CheckCircle2,
  Clock,
  AlertCircle,
  MinusCircle,
  Plus,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import DateDisplay from "@/components/shared/DateDisplay";
import EmptyState from "@/components/shared/EmptyState";
import { fetchInboxMessages } from "../inbox/api";
import type { InboxMessageItem } from "../inbox/api";
import { fetchManualPartners } from "./api";
import type { ManualPartner } from "./api";

const PAGE_SIZE = 50;

function ParseStatusBadge({ status }: { status: string }) {
  if (status === "SUCCESS")
    return (
      <Badge variant="default" className="text-xs gap-1">
        <CheckCircle2 className="h-3 w-3" />
        Parsed
      </Badge>
    );
  if (status === "FAILED")
    return (
      <Badge variant="destructive" className="text-xs gap-1">
        <AlertCircle className="h-3 w-3" />
        Failed
      </Badge>
    );
  if (status === "SKIPPED")
    return (
      <Badge variant="outline" className="text-xs gap-1">
        <MinusCircle className="h-3 w-3" />
        Not applicable
      </Badge>
    );
  return (
    <Badge variant="secondary" className="text-xs gap-1">
      <Clock className="h-3 w-3" />
      Pending
    </Badge>
  );
}

function PartnerItem({
  partner,
  isActive,
  onClick,
}: {
  partner: ManualPartner;
  isActive: boolean;
  onClick: () => void;
}) {
  const Icon = partner.source_channel === "PORTAL" ? Globe : PenLine;
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left px-3 py-3 rounded-md transition-colors",
        isActive
          ? "bg-primary text-primary-foreground"
          : "hover:bg-accent text-foreground",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className="h-4 w-4 shrink-0 opacity-70" />
          <span className="text-sm font-medium truncate">{partner.name}</span>
        </div>
        {partner.total > 0 && (
          <Badge
            variant={isActive ? "secondary" : "outline"}
            className="text-xs shrink-0"
          >
            {partner.total}
          </Badge>
        )}
      </div>
      <div
        className={cn(
          "text-[11px] mt-0.5 ml-6",
          isActive ? "text-primary-foreground/70" : "text-muted-foreground",
        )}
      >
        {partner.source_channel === "PORTAL"
          ? "Portal — handled manually until scraping is built"
          : "Manual entry"}
        {partner.failed > 0 && (
          <span className="text-destructive font-medium"> · {partner.failed} failed</span>
        )}
      </div>
    </button>
  );
}

function MessageRow({ msg, onClick }: { msg: InboxMessageItem; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="w-full text-left px-4 py-3 border-b hover:bg-muted/40 transition-colors"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium truncate">
          {msg.subject || msg.po_number || msg.external_id}
        </span>
        <ParseStatusBadge status={msg.parse_status} />
      </div>
      <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
        <DateDisplay iso={msg.received_at} format="dd MMM yyyy, HH:mm" />
        {msg.po_number && <span className="font-mono">{msg.po_number}</span>}
      </div>
    </button>
  );
}

export default function ManualInboxPage() {
  const navigate = useNavigate();
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  const {
    data: partners,
    isLoading: partnersLoading,
    isError: partnersError,
  } = useQuery({
    queryKey: ["manual-inbox", "partners"],
    queryFn: fetchManualPartners,
    refetchInterval: 60_000,
  });

  const { data: messages, isLoading: messagesLoading } = useQuery({
    queryKey: ["manual-inbox", "messages", selectedCode, page],
    queryFn: () => fetchInboxMessages(selectedCode!, (page - 1) * PAGE_SIZE, PAGE_SIZE),
    enabled: !!selectedCode,
    placeholderData: (prev) => prev,
  });

  const selected = partners?.find((p) => p.code === selectedCode);
  const totalPages = messages ? Math.max(1, Math.ceil(messages.total / PAGE_SIZE)) : 1;

  return (
    <div className="flex h-full min-h-0">
      {/* ── Partners panel ── */}
      <div className="w-72 shrink-0 border-r flex flex-col min-h-0">
        <div className="px-4 py-3 border-b">
          <h2 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            Platforms
          </h2>
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {partnersLoading ? (
            <>
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </>
          ) : partnersError ? (
            <Alert variant="destructive" className="m-2">
              <AlertDescription>Failed to load partners.</AlertDescription>
            </Alert>
          ) : !partners?.length ? (
            <p className="text-xs text-muted-foreground italic px-3 py-4">
              No manual or portal partners yet.
            </p>
          ) : (
            partners.map((p) => (
              <PartnerItem
                key={p.code}
                partner={p}
                isActive={p.code === selectedCode}
                onClick={() => {
                  setSelectedCode(p.code);
                  setPage(1);
                }}
              />
            ))
          )}
        </div>
      </div>

      {/* ── Messages panel ── */}
      <div className="flex-1 min-w-0 flex flex-col min-h-0">
        {!selected ? (
          <div className="flex-1 flex items-center justify-center">
            <EmptyState
              icon={<ClipboardEdit className="h-10 w-10" />}
              title="Select a platform"
              description="Choose a partner on the left to view its documents."
            />
          </div>
        ) : (
          <>
            <div className="px-4 py-3 border-b flex items-center justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-sm font-semibold truncate">{selected.name}</h2>
                <p className="text-xs text-muted-foreground">
                  {messages ? `${messages.total} documents` : "…"}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Badge variant="outline" className="text-xs">
                  {selected.source_channel}
                </Badge>
                <Button
                  size="sm"
                  onClick={() => navigate(`/manual-inbox/${selected.code}/new`)}
                >
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  New PO
                </Button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto">
              {messagesLoading ? (
                <div className="p-4 space-y-2">
                  <Skeleton className="h-14 w-full" />
                  <Skeleton className="h-14 w-full" />
                </div>
              ) : !messages?.items.length ? (
                <div className="flex-1 flex items-center justify-center py-16">
                  <div className="flex flex-col items-center gap-4">
                    <EmptyState
                      icon={<ClipboardEdit className="h-10 w-10" />}
                      title="No documents yet"
                      description={
                        selected.source_channel === "PORTAL"
                          ? "Portal scraping for this partner is not built yet, so its orders are keyed in. Once entered they are validated, mapped and pushed to SAP like any other."
                          : "Orders for this partner arrive by phone or paper. Key one in and it runs the same pipeline as every other partner."
                      }
                    />
                    <Button onClick={() => navigate(`/manual-inbox/${selected.code}/new`)}>
                      <Plus className="h-4 w-4 mr-1" />
                      Enter a purchase order
                    </Button>
                  </div>
                </div>
              ) : (
                messages.items.map((m) => (
                  <MessageRow
                    key={m.id}
                    msg={m}
                    onClick={() => navigate(`/inbox/${m.id}`)}
                  />
                ))
              )}
            </div>

            {totalPages > 1 && (
              <div className="border-t px-4 py-2 flex items-center justify-end gap-2 text-xs text-muted-foreground">
                <button
                  disabled={page <= 1}
                  onClick={() => setPage(page - 1)}
                  className="px-2 py-1 rounded border hover:bg-accent disabled:opacity-40"
                >
                  ‹
                </button>
                <span>
                  {page} / {totalPages}
                </span>
                <button
                  disabled={page >= totalPages}
                  onClick={() => setPage(page + 1)}
                  className="px-2 py-1 rounded border hover:bg-accent disabled:opacity-40"
                >
                  ›
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
