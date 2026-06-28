"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type {
  Technician,
  TechnicianCreateInput,
  TechnicianList,
  TechnicianUpdateInput,
} from "@/types";

/** Page size for the /technicians list (small by design). */
export const TECHNICIANS_PAGE_SIZE = 200;

export interface TechnicianFilters {
  include_inactive?: boolean;
}

/**
 * Fetch the list of technicians (admin only).
 *
 * Refetches on focus but does NOT poll — the operator edits manually.
 */
export function useTechnicians(filters: TechnicianFilters = {}) {
  return useQuery<TechnicianList, ApiError>({
    queryKey: ["technicians", filters],
    queryFn: () =>
      apiClient.get<TechnicianList>(API_ROUTES.TECHNICIANS, {
        params: {
          ...(filters.include_inactive
            ? { include_inactive: "true" }
            : {}),
          limit: String(TECHNICIANS_PAGE_SIZE),
        },
      }),
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  });
}

/**
 * Fetch a single technician by id.
 */
export function useTechnician(id: string | null) {
  return useQuery<Technician, ApiError>({
    queryKey: ["technician", id],
    queryFn: () => apiClient.get<Technician>(API_ROUTES.TECHNICIAN(id!)),
    enabled: !!id,
    staleTime: 30_000,
  });
}

/**
 * Create a technician.
 */
export function useCreateTechnician() {
  const qc = useQueryClient();
  return useMutation<Technician, ApiError, TechnicianCreateInput>({
    mutationFn: (body) =>
      apiClient.post<Technician>(API_ROUTES.TECHNICIANS, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["technicians"] });
    },
  });
}

/**
 * Update a technician.
 */
export function useUpdateTechnician(id: string) {
  const qc = useQueryClient();
  return useMutation<Technician, ApiError, TechnicianUpdateInput>({
    mutationFn: (body) =>
      apiClient.patch<Technician>(API_ROUTES.TECHNICIAN(id), body),
    onSuccess: (data) => {
      qc.setQueryData(["technician", id], data);
      void qc.invalidateQueries({ queryKey: ["technicians"] });
    },
  });
}

/**
 * Soft-delete (deactivate) a technician.
 */
export function useDeactivateTechnician() {
  const qc = useQueryClient();
  return useMutation<void, ApiError, string>({
    mutationFn: (id) => apiClient.delete<void>(API_ROUTES.TECHNICIAN(id)),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["technicians"] });
    },
  });
}