"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";

export interface Company {
  id: string;
  name: string;
  display_name: string | null;
  pattern_type: string;
  identification_patterns: Array<Record<string, unknown>>;
  phone_numbers: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
}

interface CompanyList {
  items: Company[];
  total: number;
}

/**
 * Fetch the list of active companies for the Jobs page filter dropdown.
 * Companies change rarely — long staleTime, no polling.
 */
export function useCompanies() {
  return useQuery<CompanyList, ApiError>({
    queryKey: ["companies"],
    queryFn: () => apiClient.get<CompanyList>(API_ROUTES.COMPANIES),
    staleTime: 5 * 60_000,
  });
}
