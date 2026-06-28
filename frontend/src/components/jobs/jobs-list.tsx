"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";
import { cn, formatRelativeTime } from "@/lib/utils";
import { JOBS_PAGE_SIZE, useDispatchJobs } from "@/hooks";
import { JobsPagination } from "./jobs-pagination";
import { MethodBadge } from "./method-badge";
import { StatusBadge } from "./status-badge";
import { JobsListSkeleton } from "./jobs-list-skeleton";
import { JobsListError } from "./jobs-list-error";
import { JobsListEmpty } from "./jobs-list-empty";
import type { DispatchJob, DispatchJobFilters } from "@/types";

/** Build a `DispatchJobFilters` object from the current URL search params. */
function filtersFromSp(sp: URLSearchParams): DispatchJobFilters {
  return {
    status: (sp.get("status") as DispatchJobFilters["status"]) ?? null,
    company_id: sp.get("company_id"),
    since: sp.get("from"),
    until: sp.get("to"),
    q: sp.get("q"),
  };
}

/** Build a query string from the current filters (without ?page=). */
function buildFilterQs(sp: URLSearchParams): string {
  const out = new URLSearchParams();
  for (const key of ["status", "company_id", "from", "to", "q"]) {
    const v = sp.get(key);
    if (v) out.set(key, v);
  }
  const qs = out.toString();
  return qs ? `?${qs}` : "";
}

export function JobsList() {
  const router = useRouter();
  const sp = useSearchParams();
  const params = useParams<{ id?: string }>();
  const activeId = params?.id ?? null;

  const filters = useMemo(() => filtersFromSp(sp), [sp]);
  const page = Math.max(1, Number(sp.get("page") ?? "1") || 1);
  const skip = (page - 1) * JOBS_PAGE_SIZE;

  const { data, isLoading, isError, isFetching, refetch } = useDispatchJobs(filters, skip);

  const goToPage = useCallback(
    (newPage: number) => {
      const filterQs = buildFilterQs(sp);
      const pageQs = newPage > 1 ? `${filterQs ? "&" : "?"}page=${newPage}` : "";
      router.replace(`/jobs${filterQs}${pageQs}`);
    },
    [router, sp]
  );

  const onRowClick = useCallback(
    (id: string) => {
      const filterQs = buildFilterQs(sp);
      const pageQs = page > 1 ? `${filterQs ? "&" : "?"}page=${page}` : "";
      router.push(`/jobs/${id}${filterQs}${pageQs}`);
    },
    [router, sp, page]
  );

  // Loading — show skeleton, but only on the first load (no data yet).
  if (isLoading && !data) {
    return <JobsListSkeleton />;
  }

  if (isError) {
    return <JobsListError onRetry={() => void refetch()} />;
  }

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / JOBS_PAGE_SIZE));

  if (total === 0) {
    return <JobsListEmpty />;
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex-1 overflow-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 sticky top-0 z-10">
            <tr>
              <th className="px-3 py-2 text-left font-medium">When</th>
              <th className="px-2 py-2 text-left font-medium">Status</th>
              <th className="px-2 py-2 text-left font-medium">Method</th>
              <th className="px-2 py-2 text-left font-medium">Company</th>
              <th className="px-2 py-2 text-left font-medium">Job type</th>
              <th className="px-3 py-2 text-left font-medium">Customer</th>
            </tr>
          </thead>
          <tbody className={cn(isFetching && "opacity-60")}>
            {items.map((job: DispatchJob) => (
              <tr
                key={job.id}
                onClick={() => onRowClick(job.id)}
                className={cn(
                  "hover:bg-muted/40 cursor-pointer border-b transition-colors",
                  activeId === job.id && "bg-secondary"
                )}
              >
                <td className="text-muted-foreground px-3 py-2 text-xs">
                  {formatRelativeTime(job.created_at)}
                </td>
                <td className="px-2 py-2">
                  <StatusBadge status={job.classification_status} />
                </td>
                <td className="px-2 py-2">
                  <MethodBadge method={job.classification_method} />
                </td>
                <td className="px-2 py-2">{job.company_name ?? "—"}</td>
                <td className="px-2 py-2">{job.job_type ?? "—"}</td>
                <td className="px-3 py-2">{job.customer_name ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <JobsPagination
        page={page}
        totalPages={totalPages}
        total={total}
        onChange={goToPage}
      />
    </div>
  );
}
