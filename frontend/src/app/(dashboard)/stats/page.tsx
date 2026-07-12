"use client";

import { useMemo, useState } from "react";
import { Download } from "lucide-react";
import { Button, Skeleton } from "@/components/ui";
import { useDailyStats, useExportStats } from "@/hooks";
import { todayBusinessIso } from "@/lib/business-date";
import {
  STATS_SCOPES,
  STATS_SCOPE_LABEL,
  type StatsScope,
} from "@/types";

/** Default to yesterday's Chicago business day — the daily-stats cron
 *  runs at 00:15 America/Chicago, shortly after each business day closes
 *  at midnight, so yesterday's snapshot is always ready by the time
 *  today's business day starts.
 */
function yesterdayIso(): string {
  const d = new Date(`${todayBusinessIso()}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

export default function StatsPage() {
  const [date, setDate] = useState<string>(yesterdayIso());
  const [scope, setScope] = useState<StatsScope | "all">("all");

  const filters = useMemo(
    () => ({
      snapshot_date: date,
      scope: scope === "all" ? null : scope,
    }),
    [date, scope]
  );

  const { data, isLoading, isError, refetch } = useDailyStats(filters);
  const exportStats = useExportStats();

  const rows = useMemo(() => data?.items ?? [], [data]);

  const onExport = async (format: "csv" | "json") => {
    try {
      await exportStats({ ...filters, format });
    } catch {
      // Export already opens a download — surface errors via window.alert
      // since there's no toast lib.
      window.alert("Export failed. Check the server logs.");
    }
  };

  return (
    <div className="bg-background flex h-full flex-col overflow-hidden rounded-lg border">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3 sm:px-6">
        <div>
          <h1 className="text-sm font-semibold tracking-wide uppercase">
            Daily stats
          </h1>
          <p className="text-muted-foreground mt-1 text-xs">
            Pre-computed rollups written by the daily-stats scheduler at
            23:55 local. Pick a date + scope to inspect, or export the
            full set as CSV / JSON.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            aria-label="Date"
            className="border-input bg-background h-8 rounded-md border px-2 text-sm"
          />
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value as StatsScope | "all")}
            aria-label="Scope"
            className="border-input bg-background h-8 rounded-md border px-2 text-sm"
          >
            <option value="all">All scopes</option>
            {STATS_SCOPES.map((s) => (
              <option key={s} value={s}>
                {STATS_SCOPE_LABEL[s]}
              </option>
            ))}
          </select>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void onExport("csv")}
            className="h-8 text-xs"
          >
            <Download className="h-3.5 w-3.5" />
            CSV
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void onExport("json")}
            className="h-8 text-xs"
          >
            <Download className="h-3.5 w-3.5" />
            JSON
          </Button>
        </div>
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
            <p>Failed to load snapshots.</p>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Retry
            </Button>
          </div>
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground p-8 text-center text-xs">
            No snapshots for {date}. Run{" "}
            <code>agents_bots cmd daily-stats --date={date}</code> or wait
            for the cron at 23:55.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-muted/50 sticky top-0 z-10">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Scope</th>
                <th className="px-3 py-2 text-left font-medium">Scope ID</th>
                <th className="px-3 py-2 text-left font-medium">Payload</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className="border-b align-top">
                  <td className="px-3 py-2 text-xs font-medium uppercase">
                    {STATS_SCOPE_LABEL[s.scope]}
                  </td>
                  <td className="text-muted-foreground px-3 py-2 font-mono text-[10px]">
                    {s.scope_id ? s.scope_id.slice(0, 8) + "…" : "—"}
                  </td>
                  <td className="px-3 py-2">
                    <pre className="bg-muted/30 max-h-48 overflow-auto rounded-md border p-2 text-[10px]">
                      {JSON.stringify(s.payload ?? {}, null, 2)}
                    </pre>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}