"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type { Alert, AlertKind, AlertList } from "@/types";

export interface AlertFilters {
  resolved?: boolean;
  kinds?: AlertKind[];
  /** Substring match against the related job's raw incoming message. */
  search?: string;
}

/**
 * Fetch the list of alerts (default: open only).
 *
 * Polls every 60 s — slower than the Jobs list because alerts are
 * mostly stuck / long-tail items, not fast-moving. The sidebar badge
 * uses this hook to render the unread count.
 */
export function useAlerts(filters: AlertFilters = {}) {
  return useQuery<AlertList, ApiError>({
    queryKey: ["alerts", filters],
    queryFn: () => {
      // FastAPI's ``kinds: list[str] = Query(...)`` expects repeated
      // params (``?kinds=foo&kinds=bar``), NOT indexed brackets.
      // Build a URLSearchParams so append() handles the repeat.
      const sp = new URLSearchParams();
      if (filters.resolved) sp.set("resolved", "true");
      if (filters.kinds) {
        for (const k of filters.kinds) sp.append("kinds", k);
      }
      if (filters.search) sp.set("search", filters.search);
      return apiClient.get<AlertList>(API_ROUTES.ALERTS, { params: sp });
    },
    refetchOnWindowFocus: true,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

/**
 * Fetch a single alert by id (detail pane).
 */
export function useAlert(id: string | null) {
  return useQuery<Alert, ApiError>({
    queryKey: ["alert", id],
    queryFn: () => apiClient.get<Alert>(API_ROUTES.ALERT(id!)),
    enabled: !!id,
    staleTime: 30_000,
  });
}

/**
 * Manually resolve an alert.
 */
export function useResolveAlert(id: string) {
  const qc = useQueryClient();
  return useMutation<Alert, ApiError, void>({
    mutationFn: () =>
      apiClient.post<Alert>(API_ROUTES.ALERT_RESOLVE(id)),
    onSuccess: (data) => {
      qc.setQueryData(["alert", id], data);
      void qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}

/**
 * Mark every open alert as seen — clears the navbar badge without
 * resolving anything. Fired once when the Alerts page is opened.
 */
export function useMarkAlertsSeen() {
  const qc = useQueryClient();
  return useMutation<{ marked: number }, ApiError, void>({
    mutationFn: () => apiClient.post(API_ROUTES.ALERTS_MARK_SEEN),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}