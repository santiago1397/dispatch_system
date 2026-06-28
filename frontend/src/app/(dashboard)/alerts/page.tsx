"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, X } from "lucide-react";
import { Button, Skeleton } from "@/components/ui";
import { useAlerts, useResolveAlert } from "@/hooks";
import { formatDateTime } from "@/lib/utils";
import { AlertKindBadge } from "@/components/alerts/alert-kind-badge";
import type { Alert } from "@/types";

export default function AlertsPage() {
  const [includeResolved, setIncludeResolved] = useState(false);
  const { data, isLoading, isError, refetch } = useAlerts({
    resolved: includeResolved,
  });
  const rows = useMemo(() => data?.items ?? [], [data]);

  const [expandedId, setExpandedId] = useState<string | null>(null);

  return (
    <div className="bg-background flex h-full flex-col overflow-hidden rounded-lg border">
      <div className="flex items-start justify-between gap-2 border-b px-4 py-3 sm:px-6">
        <div>
          <h1 className="text-sm font-semibold tracking-wide uppercase">
            Alerts
          </h1>
          <p className="text-muted-foreground mt-1 text-xs">
            Pipeline-health issues the alert engine has surfaced. Resolve
            once the underlying condition has been handled.
          </p>
        </div>
        <label className="text-muted-foreground flex items-center gap-1 text-xs">
          <input
            type="checkbox"
            checked={includeResolved}
            onChange={(e) => setIncludeResolved(e.target.checked)}
          />
          Show resolved
        </label>
      </div>

      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : isError ? (
          <div className="text-muted-foreground flex flex-col items-center gap-2 p-8 text-center text-xs">
            <p>Failed to load alerts.</p>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Retry
            </Button>
          </div>
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground p-8 text-center text-xs">
            No {includeResolved ? "" : "open "}alerts.
          </p>
        ) : (
          <ul className="divide-y">
            {rows.map((a) => (
              <AlertRow
                key={a.id}
                alert={a}
                expanded={expandedId === a.id}
                onToggle={() =>
                  setExpandedId(expandedId === a.id ? null : a.id)
                }
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function AlertRow({
  alert,
  expanded,
  onToggle,
}: {
  alert: Alert;
  expanded: boolean;
  onToggle: () => void;
}) {
  const resolve = useResolveAlert(alert.id);
  const isResolved = alert.resolved_at !== null;
  const [banner, setBanner] = useState<
    { kind: "success" | "error"; text: string } | null
  >(null);

  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 3000);
    return () => clearTimeout(t);
  }, [banner]);

  const onResolve = () => {
    resolve.mutate(undefined, {
      onSuccess: () =>
        setBanner({ kind: "success", text: "Alert resolved." }),
      onError: (err) =>
        setBanner({
          kind: "error",
          text: `Failed: ${err instanceof Error ? err.message : "unknown error"}`,
        }),
    });
  };

  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        className="hover:bg-muted/40 flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm transition-colors"
      >
        <AlertKindBadge kind={alert.kind} />
        <span className="text-muted-foreground text-xs">
          {formatDateTime(alert.detected_at)}
        </span>
        {alert.job_id ? (
          <span className="text-muted-foreground ml-auto font-mono text-[10px]">
            job {alert.job_id.slice(0, 8)}…
          </span>
        ) : (
          <span className="ml-auto" />
        )}
        {isResolved ? (
          <span className="text-muted-foreground text-[10px]">
            resolved {formatDateTime(alert.resolved_at!)}
          </span>
        ) : null}
      </button>
      {expanded ? (
        <div className="bg-muted/30 space-y-2 px-4 py-3 text-xs">
          <pre className="bg-background max-h-48 overflow-auto rounded-md border p-2 text-[11px]">
            {JSON.stringify(alert.payload ?? {}, null, 2)}
          </pre>
          {banner ? (
            <div
              className={`px-2 py-1 text-[11px] ${
                banner.kind === "success"
                  ? "text-green-700 dark:text-green-300"
                  : "text-red-700 dark:text-red-300"
              }`}
              role="status"
            >
              {banner.text}
            </div>
          ) : null}
          <div className="flex justify-end gap-2">
            {!isResolved ? (
              <Button
                variant="default"
                size="sm"
                onClick={onResolve}
                disabled={resolve.isPending}
                className="h-7 text-xs"
              >
                <Check className="h-3.5 w-3.5" />
                {resolve.isPending ? "Resolving…" : "Resolve"}
              </Button>
            ) : null}
            <Button
              variant="outline"
              size="sm"
              onClick={onToggle}
              className="h-7 text-xs"
            >
              <X className="h-3.5 w-3.5" />
              Close
            </Button>
          </div>
        </div>
      ) : null}
    </li>
  );
}