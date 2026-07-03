import { useQuery } from "@tanstack/react-query";
import { CheckCircle, XCircle, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import apiClient from "@/lib/api-client";

interface HealthResponse {
  status: string;
  db: string;
  redis: string;
}

function StatusRow({ label, value }: { label: string; value: string }) {
  const ok = value === "ok";
  return (
    <div className="flex items-center justify-between py-2 border-b last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <div className="flex items-center gap-2">
        {ok ? (
          <CheckCircle className="h-4 w-4 text-green-500" />
        ) : (
          <XCircle className="h-4 w-4 text-destructive" />
        )}
        <span className="text-sm font-mono">{value}</span>
      </div>
    </div>
  );
}

export default function HomePage() {
  const { data, isLoading, isError, error } = useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: async () => {
      const res = await apiClient.get<HealthResponse>("/health");
      return res.data;
    },
    refetchInterval: 30_000,
  });

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-8">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">EDI Middleware</h1>
          <p className="text-sm text-muted-foreground">Let's Try Foods — SAP B1 Integration</p>
        </div>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              System Health
              {isLoading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
              {data && (
                <Badge variant={data.status === "ok" ? "default" : "destructive"}>
                  {data.status}
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading && (
              <div className="space-y-3">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
              </div>
            )}

            {isError && (
              <Alert variant="destructive">
                <AlertDescription>
                  Cannot reach backend:{" "}
                  {error instanceof Error ? error.message : "unknown error"}
                </AlertDescription>
              </Alert>
            )}

            {data && !isLoading && (
              <div>
                <StatusRow label="API" value="ok" />
                <StatusRow label="Database" value={data.db} />
                <StatusRow label="Redis" value={data.redis} />
              </div>
            )}
          </CardContent>
        </Card>

        <p className="text-center text-xs text-muted-foreground">
          Phase 0 skeleton · Full dashboard in Phase 8
        </p>
      </div>
    </div>
  );
}
