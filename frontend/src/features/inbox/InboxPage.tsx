import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Mail, Paperclip, CheckCircle2, Clock, AlertCircle, Inbox, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useToast } from "@/hooks/use-toast";
import DateDisplay from "@/components/shared/DateDisplay";
import { fetchInboxPartners, fetchInboxMessages, retryAllFailed } from "./api";
import type { InboxPartner, InboxMessageItem } from "./api";

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
  partner: InboxPartner;
  isActive: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left px-3 py-3 rounded-md transition-colors group",
        isActive
          ? "bg-primary text-primary-foreground"
          : "hover:bg-accent text-foreground",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Mail className={cn("h-4 w-4 shrink-0", isActive ? "text-primary-foreground" : "text-muted-foreground")} />
          <span className="font-medium text-sm truncate">{partner.name}</span>
        </div>
        {partner.total > 0 && (
          <span
            className={cn(
              "text-xs font-semibold shrink-0 px-1.5 py-0.5 rounded-full",
              isActive
                ? "bg-primary-foreground/20 text-primary-foreground"
                : "bg-muted text-muted-foreground",
            )}
          >
            {partner.total}
          </span>
        )}
      </div>
      {partner.pending > 0 && !isActive && (
        <p className="text-xs text-orange-500 mt-0.5 pl-6">{partner.pending} pending parse</p>
      )}
      {partner.failed > 0 && !isActive && (
        <p className="text-xs text-destructive mt-0.5 pl-6">{partner.failed} failed</p>
      )}
    </button>
  );
}

function MessageRow({
  msg,
  onClick,
}: {
  msg: InboxMessageItem;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="w-full text-left px-4 py-3 border-b last:border-0 hover:bg-accent/50 transition-colors group"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate leading-tight">
            {msg.subject ?? "(no subject)"}
          </p>
          <p className="text-xs text-muted-foreground truncate mt-0.5">
            {msg.sender ?? "Unknown sender"}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <span className="text-xs text-muted-foreground whitespace-nowrap">
            <DateDisplay iso={msg.received_at} format="dd MMM, HH:mm" />
          </span>
          <ParseStatusBadge status={msg.parse_status} />
        </div>
      </div>
      <div className="flex items-center gap-3 mt-1.5">
        {msg.attachment_count > 0 && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Paperclip className="h-3 w-3" />
            {msg.attachment_count} {msg.attachment_count === 1 ? "file" : "files"}
          </span>
        )}
        {msg.po_number && (
          <span className="text-xs text-primary font-medium">PO: {msg.po_number}</span>
        )}
      </div>
    </button>
  );
}

export default function InboxPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedPartner = searchParams.get("partner") ?? "";
  const page = parseInt(searchParams.get("page") ?? "1", 10);
  const offset = (page - 1) * PAGE_SIZE;
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const retryAllMutation = useMutation({
    mutationFn: () => retryAllFailed(selectedPartner),
    onSuccess: (data) => {
      toast({
        title: `${data.queued_count} parse jobs queued`,
        description: "Failed messages will be re-processed shortly.",
      });
      queryClient.invalidateQueries({ queryKey: ["inbox"] });
    },
    onError: () => {
      toast({ title: "Failed to queue retries", variant: "destructive" });
    },
  });

  const { data: partners, isLoading: loadingPartners } = useQuery({
    queryKey: ["inbox", "partners"],
    queryFn: fetchInboxPartners,
    staleTime: 30_000,
  });

  const { data: messages, isLoading: loadingMessages } = useQuery({
    queryKey: ["inbox", "messages", selectedPartner, page],
    queryFn: () => fetchInboxMessages(selectedPartner, offset, PAGE_SIZE),
    enabled: !!selectedPartner,
    placeholderData: (prev) => prev,
  });

  function selectPartner(code: string) {
    setSearchParams({ partner: code }, { replace: true });
  }

  const totalPages = messages ? Math.ceil(messages.total / PAGE_SIZE) : 1;

  return (
    <div className="h-[calc(100vh-3.5rem)] flex overflow-hidden -m-6">
      {/* ── Left panel: platform list ── */}
      <div className="w-60 shrink-0 border-r flex flex-col bg-muted/20">
        <div className="px-3 pt-4 pb-2 border-b">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground px-1">
            Platforms
          </h2>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {loadingPartners ? (
            Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full rounded-md" />
            ))
          ) : (partners ?? []).length === 0 ? (
            <p className="text-xs text-muted-foreground px-3 py-4">No email partners yet.</p>
          ) : (
            (partners ?? []).map((p) => (
              <PartnerItem
                key={p.code}
                partner={p}
                isActive={p.code === selectedPartner}
                onClick={() => selectPartner(p.code)}
              />
            ))
          )}
        </div>
      </div>

      {/* ── Right panel: message list ── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {!selectedPartner ? (
          <div className="flex-1 flex flex-col items-center justify-center text-center gap-3 text-muted-foreground">
            <Inbox className="h-12 w-12 opacity-20" />
            <div>
              <p className="font-medium">Select a platform</p>
              <p className="text-sm">Choose a partner on the left to view its PO emails.</p>
            </div>
          </div>
        ) : (
          <>
            {/* Header */}
            <div className="px-4 py-3 border-b flex items-center justify-between shrink-0 gap-3">
              <div>
                <h2 className="font-semibold text-sm">
                  {(partners ?? []).find((p) => p.code === selectedPartner)?.name ?? selectedPartner}
                </h2>
                {messages && (
                  <p className="text-xs text-muted-foreground">{messages.total} total emails</p>
                )}
              </div>
              <div className="flex items-center gap-2">
                {/* Retry all failed */}
                {(partners ?? []).find((p) => p.code === selectedPartner)?.failed ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs gap-1"
                    disabled={retryAllMutation.isPending}
                    onClick={() => retryAllMutation.mutate()}
                  >
                    <RefreshCw className={`h-3 w-3 ${retryAllMutation.isPending ? "animate-spin" : ""}`} />
                    Retry All Failed
                  </Button>
                ) : null}
                {/* Pagination */}
                {totalPages > 1 && (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <button
                      disabled={page <= 1}
                      onClick={() => setSearchParams({ partner: selectedPartner, page: String(page - 1) })}
                      className="px-2 py-1 rounded border hover:bg-accent disabled:opacity-40"
                    >
                      ‹
                    </button>
                    <span>{page} / {totalPages}</span>
                    <button
                      disabled={page >= totalPages}
                      onClick={() => setSearchParams({ partner: selectedPartner, page: String(page + 1) })}
                      className="px-2 py-1 rounded border hover:bg-accent disabled:opacity-40"
                    >
                      ›
                    </button>
                  </div>
                )}
              </div>{/* end flex items-center gap-2 */}
            </div>{/* end header */}

            {/* Message list */}
            <div className="flex-1 overflow-y-auto">
              {loadingMessages ? (
                <div className="p-4 space-y-2">
                  {Array.from({ length: 8 }).map((_, i) => (
                    <Skeleton key={i} className="h-16 w-full" />
                  ))}
                </div>
              ) : (messages?.items ?? []).length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full gap-2 text-muted-foreground">
                  <Mail className="h-8 w-8 opacity-20" />
                  <p className="text-sm">No emails found for this platform.</p>
                </div>
              ) : (
                <div>
                  {messages!.items.map((msg) => (
                    <MessageRow
                      key={msg.id}
                      msg={msg}
                      onClick={() => navigate(`/inbox/${msg.id}`)}
                    />
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
