"use client";

import { useMemo, useState } from "react";
import {
  Button,
  Sheet,
  SheetClose,
  SheetContent,
  SheetHeader,
  SheetTitle,
  Skeleton,
} from "@/components/ui";
import { useCompanyReport, useCompanyReportJobs } from "@/hooks";
import { todayBusinessIso } from "@/lib/business-date";
import type { CompanyReportBucket, CompanyReportRow } from "@/types";

type Period = "day" | "week" | "month" | "custom";

const PERIOD_LABEL: Record<Period, string> = {
  day: "Day",
  week: "Week",
  month: "Month",
  custom: "Custom",
};

/**
 * Today's Chicago business date, ``YYYY-MM-DD`` — matches the backend's
 * 5am-to-midnight America/Chicago business-day boundaries (see
 * `app.core.timezone` on the backend).
 */
function todayIso(): string {
  return todayBusinessIso();
}

function toIso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** [start, end] (both inclusive) for the period containing ``pickedIso``.
 *  ``custom`` is handled separately by the caller — it has no single
 *  "picked" date to derive from.
 *
 *  ``pickedIso`` is a business-date key (see ``businessDateIso``), not a
 *  live instant, so the arithmetic below just walks calendar dates —
 *  the UTC-as-date-key trick avoids DST edge cases without needing
 *  Chicago-awareness again here. Both week and month ranges are built
 *  from consecutive business days, matching the single-day 5am cutoff.
 */
function rangeForPeriod(period: Period, pickedIso: string): { start: string; end: string } {
  const picked = new Date(`${pickedIso}T00:00:00Z`);

  if (period === "day" || period === "custom") {
    return { start: pickedIso, end: pickedIso };
  }

  if (period === "week") {
    // Monday-Sunday business week containing the picked business date.
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
  key: CompanyReportBucket;
  label: string;
}[] = [
  { key: "still_open", label: "Still open" },
  { key: "scheduled_another_day", label: "Scheduled (other day)" },
  { key: "closed_completed", label: "Closed / completed" },
  { key: "canceled", label: "Canceled" },
  { key: "rejected", label: "Rejected" },
];

const BUCKET_LABEL: Record<CompanyReportBucket, string> = Object.fromEntries(
  BUCKET_COLUMNS.map((c) => [c.key, c.label])
) as Record<CompanyReportBucket, string>;

interface DrillDown {
  companyId: string;
  companyName: string;
  bucket: CompanyReportBucket | "total";
  bucketLabel: string;
}

export default function ReportsPage() {
  const [period, setPeriod] = useState<Period>("day");
  const [pickedDate, setPickedDate] = useState<string>(todayIso());
  const [customStart, setCustomStart] = useState<string>(todayIso());
  const [customEnd, setCustomEnd] = useState<string>(todayIso());
  const [drillDown, setDrillDown] = useState<DrillDown | null>(null);

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
                      {row[c.key] > 0 ? (
                        <button
                          onClick={() =>
                            setDrillDown({
                              companyId: row.company_id,
                              companyName: row.company_name,
                              bucket: c.key,
                              bucketLabel: c.label,
                            })
                          }
                          className="hover:text-primary underline decoration-dotted underline-offset-2"
                          title={`View ${c.label.toLowerCase()} jobs for ${row.company_name}`}
                        >
                          {row[c.key]}
                        </button>
                      ) : (
                        row[c.key]
                      )}
                    </td>
                  ))}
                  <td className="px-3 py-2 text-right font-semibold tabular-nums">
                    {row.total > 0 ? (
                      <button
                        onClick={() =>
                          setDrillDown({
                            companyId: row.company_id,
                            companyName: row.company_name,
                            bucket: "total",
                            bucketLabel: "All jobs",
                          })
                        }
                        className="hover:text-primary underline decoration-dotted underline-offset-2"
                        title={`View all jobs for ${row.company_name}`}
                      >
                        {row.total}
                      </button>
                    ) : (
                      row.total
                    )}
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

      <Sheet open={drillDown !== null} onOpenChange={(open) => !open && setDrillDown(null)}>
        {drillDown ? (
          <SheetContent side="right" className="w-full max-w-xl">
            <SheetHeader>
              <div>
                <SheetTitle>{drillDown.companyName}</SheetTitle>
                <p className="text-muted-foreground mt-1 text-xs">
                  {drillDown.bucketLabel} · {start === end ? start : `${start} → ${end}`}
                </p>
              </div>
              <SheetClose onClick={() => setDrillDown(null)} />
            </SheetHeader>
            <DrillDownJobs
              companyId={drillDown.companyId}
              bucket={drillDown.bucket}
              startDate={start}
              endDate={end}
            />
          </SheetContent>
        ) : null}
      </Sheet>
    </div>
  );
}

function DrillDownJobs({
  companyId,
  bucket,
  startDate,
  endDate,
}: {
  companyId: string;
  bucket: CompanyReportBucket | "total";
  startDate: string;
  endDate: string;
}) {
  const { data, isLoading, isError } = useCompanyReportJobs({
    company_id: companyId,
    bucket,
    start_date: startDate,
    end_date: endDate,
  });

  const jobs = data?.items ?? [];

  return (
    <div className="flex-1 overflow-auto p-4">
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : isError ? (
        <p className="text-muted-foreground text-center text-xs">
          Failed to load jobs for this cell.
        </p>
      ) : jobs.length === 0 ? (
        <p className="text-muted-foreground text-center text-xs">No jobs in this bucket.</p>
      ) : (
        <ul className="space-y-3">
          {jobs.map((job) => (
            <li key={job.job_id} className="rounded-md border p-3 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{job.address ?? "No address"}</span>
                <div className="flex shrink-0 gap-1">
                  {bucket === "total" ? (
                    <span className="bg-muted rounded px-1.5 py-0.5 font-mono text-[10px] uppercase">
                      {BUCKET_LABEL[job.bucket]}
                    </span>
                  ) : null}
                  <span className="bg-muted rounded px-1.5 py-0.5 font-mono text-[10px] uppercase">
                    {job.lifecycle_status}
                  </span>
                </div>
              </div>
              <div className="text-muted-foreground mt-1 flex flex-wrap gap-x-3 gap-y-1">
                <span>Arrived {new Date(job.first_message_at).toLocaleString()}</span>
                {job.appt_at ? (
                  <span>Appt {new Date(job.appt_at).toLocaleString()}</span>
                ) : null}
                {job.job_type ? <span>{job.job_type}</span> : null}
              </div>
              {job.customer_name || job.customer_phone ? (
                <div className="text-muted-foreground mt-1">
                  {[job.customer_name, job.customer_phone].filter(Boolean).join(" · ")}
                </div>
              ) : null}
              {job.message_preview ? (
                <p className="mt-2 line-clamp-2 italic">&ldquo;{job.message_preview}&rdquo;</p>
              ) : null}
              {job.dispatch_job_id ? (
                <a
                  href={`/jobs/${job.dispatch_job_id}`}
                  className="text-primary mt-2 inline-block underline"
                >
                  Open job →
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
