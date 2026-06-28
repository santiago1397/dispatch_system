"use client";

import { cn } from "@/lib/utils";
import {
  LIFECYCLE_STATUS_LABEL,
  type LifecycleStatus,
} from "@/types";

const STYLE: Record<LifecycleStatus, string> = {
  pending:
    "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200",
  dispatched:
    "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200",
  in_progress:
    "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200",
  appt_set:
    "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-200",
  needs_follow_up:
    "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-200",
  canceled:
    "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
  completed:
    "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200",
  closed:
    "bg-zinc-200 text-zinc-800 dark:bg-zinc-700 dark:text-zinc-100",
};

/**
 * Lifecycle status badge — used in the /jobs/[id] header.
 *
 * Falls back to the "pending" label + style when the value is null
 * (legacy rows pre-migration).
 */
export function LifecycleStatusBadge({
  status,
  className,
}: {
  status: LifecycleStatus | null;
  className?: string;
}) {
  const value: LifecycleStatus = status ?? "pending";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        STYLE[value],
        className
      )}
    >
      {LIFECYCLE_STATUS_LABEL[value]}
    </span>
  );
}