"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type {
  DispatchJob,
  DispatchJobFilters,
  DispatchJobList,
  JobLifecycleEventList,
  LifecycleTransitionInput,
} from "@/types";

/** Page size for the Jobs list. */
export const JOBS_PAGE_SIZE = 50;

/**
 * Build the URL params from a filters object + pagination.
 * Null/undefined values are omitted so the backend doesn't see them
 * (it would treat them as "no filter" anyway, but omission is cleaner).
 */
function buildJobsParams(
  filters: DispatchJobFilters,
  skip: number
): Record<string, string> {
  const params: Record<string, string> = {
    skip: String(skip),
    limit: String(JOBS_PAGE_SIZE),
  };
  if (filters.status) params.status = filters.status;
  if (filters.company_id) params.company_id = filters.company_id;
  if (filters.since) params.since = filters.since;
  if (filters.until) params.until = filters.until;
  if (filters.q) params.q = filters.q;
  return params;
}

/**
 * Fetch a page of dispatch jobs.
 *
 * Polls every 30s so the operator sees new jobs land without a manual
 * refresh. ``refetchOnWindowFocus`` matches the WhatsApp view's pattern
 * — switching back to the tab triggers an immediate refresh.
 */
export function useDispatchJobs(filters: DispatchJobFilters, skip: number) {
  return useQuery<DispatchJobList, ApiError>({
    queryKey: ["dispatch-jobs", filters, skip],
    queryFn: () =>
      apiClient.get<DispatchJobList>(API_ROUTES.JOBS, {
        params: buildJobsParams(filters, skip),
      }),
    refetchOnWindowFocus: true,
    refetchInterval: 30_000,
    staleTime: 10_000,
    placeholderData: (prev) => prev,
  });
}

/**
 * Fetch a single dispatch job by id. Used by the detail pane.
 * Refetches on demand (no polling) — the operator clicks Reclassify
 * to refresh after a change.
 */
export function useDispatchJob(id: string | null) {
  return useQuery<DispatchJob, ApiError>({
    queryKey: ["dispatch-job", id],
    queryFn: () => apiClient.get<DispatchJob>(API_ROUTES.JOB_DETAIL(id!)),
    enabled: !!id,
    staleTime: 10_000,
  });
}

/**
 * Re-run classification on a dispatch job.
 *
 * On success: refetches the detail query (so the new fields and status
 * render in place) and refetches the list query after a short delay
 * (so the row in the list picks up the new status on the next render
 * — we don't want the list to lag 30s when the operator just triggered
 * a change). Does NOT throw on 4xx/5xx — the caller shows a toast.
 */
export function useReclassifyJob(id: string) {
  const qc = useQueryClient();
  return useMutation<DispatchJob, ApiError, void>({
    mutationFn: () => apiClient.post<DispatchJob>(API_ROUTES.JOB_RECLASSIFY(id)),
    onSuccess: (data) => {
      qc.setQueryData(["dispatch-job", id], data);
      // Invalidate the list so the row's status/method badges update.
      // We invalidate instead of polling to give the operator immediate
      // feedback in the left pane.
      void qc.invalidateQueries({ queryKey: ["dispatch-jobs"] });
    },
  });
}

/**
 * Re-attempt matching for a ``closing_unmatched`` dispatch job.
 *
 * Used when the original Job arrives after the closing message has
 * already been processed. No re-extraction — the backend replays the
 * stored ClosingExtraction against the current Job table.
 */
export function useRematchClosing(id: string) {
  const qc = useQueryClient();
  return useMutation<DispatchJob, ApiError, void>({
    mutationFn: () =>
      apiClient.post<DispatchJob>(API_ROUTES.JOB_REMATCH_CLOSING(id)),
    onSuccess: (data) => {
      qc.setQueryData(["dispatch-job", id], data);
      void qc.invalidateQueries({ queryKey: ["dispatch-jobs"] });
    },
  });
}

/**
 * Fetch the lifecycle event timeline for a Job (newest-first).
 *
 * Used by the ``<LifecycleTimeline>`` component on ``/jobs/[id]``. No
 * polling — the operator clicks Reclassify or sets a manual override
 * to see fresh data.
 */
export function useJobLifecycle(id: string | null) {
  return useQuery<JobLifecycleEventList, ApiError>({
    queryKey: ["job-lifecycle", id],
    queryFn: () =>
      apiClient.get<JobLifecycleEventList>(API_ROUTES.JOBS_LIFECYCLE(id!)),
    enabled: !!id,
    staleTime: 10_000,
  });
}

/**
 * Manually correct the inferred lifecycle status of a Job from /jobs/[id].
 *
 * This is a state-correct tool only — used when the parser misclassified
 * a tech reply (e.g. "k" → LLM picked needs_follow_up but it was actually
 * "on the way"). It writes the audit event + updates lifecycle_status,
 * but does NOT produce any outbound message. The operator types replies
 * natively in WhatsApp / OpenPhone.
 *
 * On success: writes the updated Job into the detail cache and
 * invalidates the jobs list + lifecycle timeline.
 */
export function useSetLifecycleStatus(id: string) {
  const qc = useQueryClient();
  return useMutation<DispatchJob, ApiError, LifecycleTransitionInput>({
    mutationFn: (body) =>
      apiClient.patch<DispatchJob>(API_ROUTES.JOBS_LIFECYCLE(id), { body }),
    onSuccess: (data) => {
      qc.setQueryData(["dispatch-job", id], data);
      void qc.invalidateQueries({ queryKey: ["dispatch-jobs"] });
      void qc.invalidateQueries({ queryKey: ["job-lifecycle", id] });
    },
  });
}
