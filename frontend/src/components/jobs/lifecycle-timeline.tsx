"use client";

import { Inbox } from "lucide-react";
import { Skeleton } from "@/components/ui";
import { formatDateTime } from "@/lib/utils";
import { useJobLifecycle } from "@/hooks";
import {
  LIFECYCLE_SOURCE_LABEL,
  LIFECYCLE_STATUS_LABEL,
  type LifecycleStatus,
} from "@/types";

interface LifecycleTimelineProps {
  jobId: string;
}

const SOURCE_BADGE_STYLE: Record<string, string> = {
  operator_whatsapp:
    "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200",
  tech_whatsapp:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  closing_chat:
    "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-200",
  manual:
    "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-200",
  ambiguous_attribution:
    "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-200",
};

/**
 * Newest-first list of lifecycle events for a Job.
 *
 * Each row shows: source badge, from→to status transition, timestamp,
 * and any payload fields the operator might find useful (appt_iso,
 * intent, notes).
 */
export function LifecycleTimeline({ jobId }: LifecycleTimelineProps) {
  const { data, isLoading, isError } = useJobLifecycle(jobId);

  if (isLoading && !data) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <p className="text-muted-foreground text-xs">
        Failed to load lifecycle events.
      </p>
    );
  }

  const items = data?.items ?? [];
  if (items.length === 0) {
    return (
      <div className="text-muted-foreground flex items-center gap-2 py-2 text-xs">
        <Inbox className="h-3.5 w-3.5 opacity-60" />
        No lifecycle events yet.
      </div>
    );
  }

  return (
    <ol className="space-y-2">
      {items.map((ev) => {
        const fromLabel =
          (LIFECYCLE_STATUS_LABEL as Record<string, string>)[ev.from_status] ??
          ev.from_status;
        const toLabel =
          (LIFECYCLE_STATUS_LABEL as Record<string, string>)[ev.to_status] ??
          ev.to_status;
        const source = ev.source;
        const sourceLabel =
          LIFECYCLE_SOURCE_LABEL[source] ?? source;
        const badge = SOURCE_BADGE_STYLE[source] ??
          "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200";

        const payload = ev.payload ?? {};
        const apptIso =
          typeof payload.appt_iso === "string" ? payload.appt_iso : null;
        const notes =
          typeof payload.notes === "string" ? payload.notes : null;
        const intent =
          typeof payload.intent === "string" ? payload.intent : null;

        return (
          <li
            key={ev.id}
            className="bg-muted/30 space-y-1 rounded-md border p-2 text-xs"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${badge}`}
              >
                {sourceLabel}
              </span>
              <span className="text-muted-foreground">
                <span className="text-foreground">{fromLabel}</span>{" "}
                <span aria-hidden="true">→</span>{" "}
                <span className="text-foreground font-medium">{toLabel}</span>
              </span>
              <span className="text-muted-foreground ml-auto">
                {formatDateTime(ev.created_at)}
              </span>
            </div>
            {apptIso || notes || intent ? (
              <div className="text-muted-foreground space-y-0.5 text-[11px]">
                {apptIso ? <div>appt: {apptIso}</div> : null}
                {intent ? <div>intent: {intent}</div> : null}
                {notes ? (
                  <div className="whitespace-pre-wrap">notes: {notes}</div>
                ) : null}
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

// ``LifecycleStatus`` is re-exported above for consumers that want the
// narrowed type from a job's lifecycle_status column.
export type { LifecycleStatus };