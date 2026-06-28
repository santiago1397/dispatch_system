"use client";

import { cn } from "@/lib/utils";
import { ALERT_KIND_LABEL, type AlertKind } from "@/types";

const STYLE: Record<AlertKind, string> = {
  stuck_dispatched:
    "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-200",
  stuck_in_progress:
    "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-200",
  appt_time_passed:
    "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  closing_missing:
    "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
  dispatch_no_match:
    "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-200",
  unattributed_reply:
    "bg-pink-100 text-pink-800 dark:bg-pink-900/40 dark:text-pink-200",
};

export function AlertKindBadge({
  kind,
  className,
}: {
  kind: AlertKind;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        STYLE[kind] ??
          "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200",
        className
      )}
    >
      {ALERT_KIND_LABEL[kind]}
    </span>
  );
}