"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type { CompanyReportBucket, CompanyReportJobsResponse } from "@/types";

export interface CompanyReportJobsFilters {
  company_id: string;
  /** Omit (or "total") for the company's "Total" column — every job in range. */
  bucket: CompanyReportBucket | "total";
  start_date: string;
  end_date: string;
}

/**
 * Fetch the individual jobs behind one company/bucket cell — or the
 * company's "Total" column — of the company-status report. Lets an
 * operator confirm the classification behind a count. Only enabled once a
 * cell is actually selected.
 */
export function useCompanyReportJobs(filters: CompanyReportJobsFilters | null) {
  return useQuery<CompanyReportJobsResponse, ApiError>({
    queryKey: ["company-report-jobs", filters],
    queryFn: () =>
      apiClient.get<CompanyReportJobsResponse>(API_ROUTES.REPORTS_COMPANY_STATUS_JOBS, {
        params: {
          company_id: filters!.company_id,
          ...(filters!.bucket !== "total" ? { bucket: filters!.bucket } : {}),
          start_date: filters!.start_date,
          end_date: filters!.end_date,
        },
      }),
    enabled: filters !== null,
  });
}
