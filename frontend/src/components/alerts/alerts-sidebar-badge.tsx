"use client";

import { useAlerts } from "@/hooks";

/**
 * Unseen-count badge for the Alerts sidebar entry.
 *
 * Shows alerts the operator hasn't viewed yet (``unseen``), not the full
 * unsolved total — that count lives inside the Alerts page itself.
 * Opening the page marks the open queue seen, which drops this badge to 0
 * even though those alerts may still be unresolved. Polls at the hook's
 * cadence (60s) — same as the full Alerts page, so the badge never lags.
 */
export function AlertsSidebarBadge() {
  const { data } = useAlerts();
  const unseen = data?.unseen ?? 0;
  if (unseen === 0) return null;
  return (
    <span className="bg-destructive text-destructive-foreground ml-auto inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-[10px] font-medium">
      {unseen > 99 ? "99+" : unseen}
    </span>
  );
}