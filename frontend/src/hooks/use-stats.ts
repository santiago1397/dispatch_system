"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type { DailyStatsList, StatsScope } from "@/types";

export interface StatsFilters {
  snapshot_date?: string | null;
  scope?: StatsScope | null;
}

/**
 * Fetch the daily snapshot rows for a date.
 *
 * No polling — the operator picks a date and reads. The CLI
 * ``daily-stats`` command + APScheduler cron at 23:55 write the rows.
 */
export function useDailyStats(filters: StatsFilters = {}) {
  return useQuery<DailyStatsList, ApiError>({
    queryKey: ["daily-stats", filters],
    queryFn: () =>
      apiClient.get<DailyStatsList>(API_ROUTES.STATS, {
        params: {
          ...(filters.snapshot_date
            ? { snapshot_date: filters.snapshot_date }
            : {}),
          ...(filters.scope ? { scope: filters.scope } : {}),
        },
      }),
    staleTime: 5 * 60_000,
  });
}

/**
 * Trigger a CSV / JSON download for a date.
 *
 * Returns a function the operator calls from a button. Browser-side
 * blob handling so the file streams without buffering the whole body
 * in JS memory. Goes through the Next.js proxy so the auth cookie
 * travels with the request — raw ``fetch`` here would skip the
 * auto-refresh path used by every other API call.
 */
export function useExportStats() {
  return async (filters: StatsFilters & { format: "csv" | "json" }) => {
    const params = new URLSearchParams();
    if (filters.snapshot_date) params.set("snapshot_date", filters.snapshot_date);
    if (filters.scope) params.set("scope", filters.scope);
    params.set("format", filters.format);

    const url = `/api${API_ROUTES.STATS_EXPORT}?${params.toString()}`;
    const response = await fetch(url, {
      credentials: "include",
    });
    if (!response.ok) {
      throw new ApiError(
        response.status,
        "Failed to export stats",
        await response.text()
      );
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    // Sanitize the date so a future caller can't smuggle path separators
    // or extension overrides into the Content-Disposition-derived filename.
    const safeDate =
      filters.snapshot_date?.replace(/[^0-9-]/g, "") || "export";
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = `daily-stats-${safeDate}.${filters.format}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(objectUrl);
  };
}