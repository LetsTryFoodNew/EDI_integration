import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate, Link } from "react-router-dom";
import {
  ArrowLeft,
  Paperclip,
  Download,
  ExternalLink,
  Mail,
  Calendar,
  User,
  Building2,
  FileText,
  RefreshCw,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useToast } from "@/hooks/use-toast";
import DateDisplay from "@/components/shared/DateDisplay";
import StatusBadge from "@/components/shared/StatusBadge";
import { fetchInboxMessage, retryParse, downloadAttachment } from "./api";
import type { AttachmentInfo } from "./api";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fileIcon(filename: string): string {
  const ext = filename.split(".").pop()?.toLowerCase();
  if (ext === "pdf") return "📄";
  if (ext === "xls" || ext === "xlsx") return "📊";
  return "📎";
}

function AttachmentCard({ att, messageId, index }: { att: AttachmentInfo; messageId: string; index: number }) {
  const [loading, setLoading] = useState(false);
  const { toast } = useToast();

  async function open() {
    setLoading(true);
    try {
      const blob = await downloadAttachment(messageId, index);
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank", "noopener");
      // Revoke after the new tab has had time to load the blob
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      toast({ title: "Failed to load attachment", variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center gap-3 p-3 rounded-lg border bg-muted/30 hover:bg-accent/30 transition-colors">
      <span className="text-2xl shrink-0">{fileIcon(att.filename)}</span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{att.filename}</p>
        <p className="text-xs text-muted-foreground">{formatBytes(att.size_bytes)}</p>
      </div>
      <button
        onClick={open}
        disabled={loading}
        className="flex items-center gap-1 text-xs text-primary hover:underline shrink-0 disabled:opacity-50"
      >
        {loading ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
        {loading ? "Loading…" : "Open"}
      </button>
    </div>
  );
}

function MetaRow({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3 py-2 border-b last:border-0">
      <div className="flex items-center gap-1.5 w-28 shrink-0 text-xs text-muted-foreground mt-0.5">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="text-sm flex-1 min-w-0 break-words">{value}</div>
    </div>
  );
}

export default function InboxDetailPage() {
  const { messageId } = useParams<{ messageId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const { data: msg, isLoading, isError } = useQuery({
    queryKey: ["inbox", "message", messageId],
    queryFn: () => fetchInboxMessage(messageId!),
    enabled: !!messageId,
  });

  const retryMutation = useMutation({
    mutationFn: () => retryParse(messageId!),
    onSuccess: () => {
      toast({ title: "Parse job queued", description: "The message will be re-parsed shortly." });
      queryClient.invalidateQueries({ queryKey: ["inbox"] });
    },
    onError: () => {
      toast({ title: "Failed to queue retry", variant: "destructive" });
    },
  });

  if (isLoading) {
    return (
      <div className="space-y-4 max-w-3xl">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (isError || !msg) {
    return (
      <Alert variant="destructive">
        <AlertDescription>Failed to load email details.</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="space-y-4 max-w-3xl">
      {/* Back */}
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to inbox
      </button>

      {/* Subject */}
      <div>
        <h1 className="text-xl font-semibold leading-tight">
          {msg.subject ?? "(no subject)"}
        </h1>
        <div className="flex items-center gap-2 mt-1.5 flex-wrap">
          <Badge variant="outline" className="text-xs">{msg.partner_name}</Badge>
          {msg.parse_status === "SUCCESS" ? (
            <Badge variant="default" className="text-xs">Parsed</Badge>
          ) : msg.parse_status === "FAILED" ? (
            <Badge variant="destructive" className="text-xs">Parse Failed</Badge>
          ) : (
            <Badge variant="secondary" className="text-xs">Pending Parse</Badge>
          )}
          {msg.parse_status !== "SUCCESS" && (
            <Button
              size="sm"
              variant="outline"
              className="h-6 text-xs gap-1 px-2"
              disabled={retryMutation.isPending}
              onClick={() => retryMutation.mutate()}
            >
              <RefreshCw className={`h-3 w-3 ${retryMutation.isPending ? "animate-spin" : ""}`} />
              {retryMutation.isPending ? "Queueing…" : "Retry Parse"}
            </Button>
          )}
        </div>
      </div>

      {/* Email metadata */}
      <Card>
        <CardContent className="pt-4">
          <MetaRow icon={User} label="From" value={msg.sender ?? "—"} />
          <MetaRow
            icon={Calendar}
            label="Received"
            value={<DateDisplay iso={msg.received_at} format="dd MMM yyyy, HH:mm:ss" />}
          />
          <MetaRow icon={Building2} label="Platform" value={msg.partner_name} />
          <MetaRow
            icon={Mail}
            label="Gmail ID"
            value={<span className="font-mono text-xs">{msg.external_id}</span>}
          />
        </CardContent>
      </Card>

      {/* Canonical PO link (if parsed) */}
      {msg.po_id && (
        <Card className="border-green-200 bg-green-50 dark:bg-green-950/20 dark:border-green-900">
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <FileText className="h-4 w-4 text-green-600" />
                <span className="text-sm font-medium text-green-700 dark:text-green-400">
                  Linked to Purchase Order
                </span>
              </div>
              <Link
                to={`/pos/${msg.po_id}`}
                className="flex items-center gap-1 text-sm text-primary hover:underline font-medium"
              >
                {msg.po_number}
                {msg.po_status && <StatusBadge status={msg.po_status} className="ml-1" />}
                <ExternalLink className="h-3 w-3 ml-0.5" />
              </Link>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Attachments */}
      {msg.attachments.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-1.5">
              <Paperclip className="h-4 w-4" />
              Attachments ({msg.attachments.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {msg.attachments.map((att, i) => (
              <AttachmentCard key={i} att={att} messageId={msg.id} index={i} />
            ))}
          </CardContent>
        </Card>
      )}

      {/* Body preview */}
      {msg.body_preview && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Email Body Preview</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="text-xs text-muted-foreground whitespace-pre-wrap font-sans leading-relaxed">
              {msg.body_preview}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
