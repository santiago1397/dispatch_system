"use client";

import { useAlerts } from "@/hooks";

/**
 * Unread-count badge for the Alerts sidebar entry.
 *
 * Shows the open-alert total as a small chip. Polls at the hook's
 * cadence (60s) — same as the full Alerts page, so the badge never
 * lags the page.
 */
export function AlertsSidebarBadge() {
  const { data } = useAlerts();
  const total = data?.total ?? 0;
  if (total === 0) return null;
  return (
    <span className="bg-destructive text-destructive-foreground ml-auto inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-[10px] font-medium">
      {total > 99 ? "99+" : total}
    </span>
  );
}