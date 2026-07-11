"use client";

import { useMemo, useState } from "react";
import { Button, Skeleton } from "@/components/ui";
import { useCompanyReport } from "@/hooks";
import type { CompanyReportRow } from "@/types";

type Period = "day" | "week" | "month" | "custom";

const PERIOD_LABEL: Record<Period, string> = {
  day: "Day",
  week: "Week",
  month: "Month",
  custom: "Custom",
};

/** Today in UTC, ``YYYY-MM-DD`` — matches the backend's UTC day boundaries. */
function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function toIso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** [start, end] (both inclusive) for the period containing ``pickedIso``.
 *  ``custom`` is handled separately by the caller — it has no single
 *  "picked" date to derive from.
 */
function rangeForPeriod(period: Period, pickedIso: string): { start: string; end: string } {
  const picked = new Date(`${pickedIso}T00:00:00Z`);

  if (period === "day" || period === "custom") {
    return { start: pickedIso, end: pickedIso };
  }

  if (period === "week") {
    // Monday-Sunday week containing picked date (UTC).
    const dow = picked.getUTCDay(); // 0=Sun..6=Sat
    const mondayOffset = dow === 0 ? -6 : 1 - dow;
    const monday = new Date(picked);
    monday.setUTCDate(picked.getUTCDate() + mondayOffset);
    const sunday = new Date(monday);
    sunday.setUTCDate(monday.getUTCDate() + 6);
    return { start: toIso(monday), end: toIso(sunday) };
  }

  // month: calendar month containing picked date.
  const first = new Date(Date.UTC(picked.getUTCFullYear(), picked.getUTCMonth(), 1));
  const last = new Date(Date.UTC(picked.getUTCFullYear(), picked.getUTCMonth() + 1, 0));
  return { start: toIso(first), end: toIso(last) };
}

const BUCKET_COLUMNS: {
  key: keyof Omit<CompanyReportRow, "company_id" | "company_name" | "total">;
  label: string;
}[] = [
  { key: "still_open", label: "Still open" },
  { key: "scheduled_another_day", label: "Scheduled (other day)" },
  { key: "closed_completed", label: "Closed / completed" },
  { key: "canceled", label: "Canceled" },
  { key: "rejected", label: "Rejected" },
];

export default function ReportsPage() {
  const [period, setPeriod] = useState<Period>("day");
  const [pickedDate, setPickedDate] = useState<string>(todayIso());
  const [customStart, setCustomStart] = useState<string>(todayIso());
  const [customEnd, setCustomEnd] = useState<string>(todayIso());

  const { start, end } = useMemo(() => {
    if (period === "custom") {
      // Tolerate the end date being picked before the start date while
      // the user is mid-edit — swap rather than send an invalid range.
      return customStart <= customEnd
        ? { start: customStart, end: customEnd }
        : { start: customEnd, end: customStart };
    }
    return rangeForPeriod(period, pickedDate);
  }, [period, pickedDate, customStart, customEnd]);

  const { data, isLoading, isError, refetch, dataUpdatedAt } = useCompanyReport({
    start_date: start,
    end_date: end,
  });

  const rows = data?.items ?? [];
  const grandTotal = useMemo(
    () => rows.reduce((sum, r) => sum + r.total, 0),
    [rows]
  );

  return (
    <div className="bg-background flex h-full flex-col overflow-hidden rounded-lg border">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3 sm:px-6">
        <div>
          <h1 className="text-sm font-semibold tracking-wide uppercase">
            Company job status report
          </h1>
          <p className="text-muted-foreground mt-1 text-xs">
            Live per-company breakdown of today&apos;s (or the selected
            range&apos;s) jobs — refreshes automatically every 30s.
            {dataUpdatedAt ? (
              <> Last updated {new Date(dataUpdatedAt).toLocaleTimeString()}.</>
            ) : null}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="border-input bg-background inline-flex h-8 overflow-hidden rounded-md border text-xs">
            {(["day", "week", "month", "custom"] as Period[]).map((p) => (
              <button
                key={p}
                onClick={() => setPeriod(p)}
                className={`px-3 ${
                  period === p
                    ? "bg-primary text-primary-foreground"
                    : "hover:bg-muted"
                }`}
              >
                {PERIOD_LABEL[p]}
              </button>
            ))}
          </div>
          {period === "custom" ? (
            <div className="flex items-center gap-1">
              <input
                type="date"
                value={customStart}
                onChange={(e) => setCustomStart(e.target.value)}
                aria-label="Start date"
                className="border-input bg-background h-8 rounded-md border px-2 text-sm"
              />
              <span className="text-muted-foreground text-xs">to</span>
              <input
                type="date"
                value={customEnd}
                onChange={(e) => setCustomEnd(e.target.value)}
                aria-label="End date"
                className="border-input bg-background h-8 rounded-md border px-2 text-sm"
              />
            </div>
          ) : (
            <input
              type="date"
              value={pickedDate}
              onChange={(e) => setPickedDate(e.target.value)}
              aria-label="Date"
              className="border-input bg-background h-8 rounded-md border px-2 text-sm"
            />
          )}
          <Button variant="outline" size="sm" onClick={() => void refetch()} className="h-8 text-xs">
            Refresh
          </Button>
        </div>
      </div>

      <div className="text-muted-foreground border-b px-4 py-2 text-xs sm:px-6">
        {start === end ? start : `${start} → ${end}`}
      </div>

      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : isError ? (
          <div className="text-muted-foreground flex flex-col items-center gap-2 p-8 text-center text-xs">
            <p>Failed to load the report.</p>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Retry
            </Button>
          </div>
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground p-8 text-center text-xs">
            No jobs arrived in this range.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-muted/50 sticky top-0 z-10">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Company</th>
                {BUCKET_COLUMNS.map((c) => (
                  <th key={c.key} className="px-3 py-2 text-right font-medium">
                    {c.label}
                  </th>
                ))}
                <th className="px-3 py-2 text-right font-medium">Total</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.company_id} className="border-b">
                  <td className="px-3 py-2 font-medium">{row.company_name}</td>
                  {BUCKET_COLUMNS.map((c) => (
                    <td key={c.key} className="px-3 py-2 text-right tabular-nums">
                      {row[c.key]}
                    </td>
                  ))}
                  <td className="px-3 py-2 text-right font-semibold tabular-nums">
                    {row.total}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="bg-muted/30 border-t font-semibold">
                <td className="px-3 py-2">All companies</td>
                {BUCKET_COLUMNS.map((c) => (
                  <td key={c.key} className="px-3 py-2 text-right tabular-nums">
                    {rows.reduce((sum, r) => sum + r[c.key], 0)}
                  </td>
                ))}
                <td className="px-3 py-2 text-right tabular-nums">{grandTotal}</td>
              </tr>
            </tfoot>
          </table>
        )}
      </div>
    </div>
  );
}
