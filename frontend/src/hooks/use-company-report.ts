"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type { CompanyReportResponse } from "@/types";

export interface CompanyReportFilters {
  start_date: string;
  end_date: string;
}

/**
 * Fetch the live per-company job status breakdown for a date range.
 *
 * Computed on every request by the backend (no snapshot table), so this
 * polls every 30s to keep "today" live without a manual refresh.
 */
export function useCompanyReport(filters: CompanyReportFilters) {
  return useQuery<CompanyReportResponse, ApiError>({
    queryKey: ["company-report", filters],
    queryFn: () =>
      apiClient.get<CompanyReportResponse>(API_ROUTES.REPORTS_COMPANY_STATUS, {
        params: {
          start_date: filters.start_date,
          end_date: filters.end_date,
        },
      }),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
}
