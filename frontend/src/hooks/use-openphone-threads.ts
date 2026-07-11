"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type { OpenPhoneThreadList, OpenPhoneThreadSummary } from "@/types";

async function fetchThreads(): Promise<OpenPhoneThreadSummary[]> {
  const data = await apiClient.get<OpenPhoneThreadList>(API_ROUTES.OPENPHONE_THREADS);
  return data?.items ?? [];
}

export function useOpenPhoneThreads() {
  return useQuery<OpenPhoneThreadSummary[], ApiError>({
    queryKey: ["openphone", "threads"],
    queryFn: fetchThreads,
    refetchOnWindowFocus: true,
    refetchInterval: 30_000,
    staleTime: 10_000,
  });
}
