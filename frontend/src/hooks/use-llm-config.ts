"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type { LLMConfigRead, LLMConfigUpdate } from "@/types";

const KEY = ["settings", "llm"] as const;

export function useLLMConfig() {
  return useQuery<LLMConfigRead, ApiError>({
    queryKey: KEY,
    queryFn: () => apiClient.get<LLMConfigRead>(API_ROUTES.SETTINGS_LLM),
    staleTime: 30_000,
  });
}

export function useUpdateLLMConfig() {
  const qc = useQueryClient();
  return useMutation<LLMConfigRead, ApiError, LLMConfigUpdate>({
    mutationFn: (body) =>
      apiClient.put<LLMConfigRead>(API_ROUTES.SETTINGS_LLM, body),
    onSuccess: (data) => qc.setQueryData(KEY, data),
  });
}

export function useResetLLMConfig() {
  const qc = useQueryClient();
  return useMutation<LLMConfigRead, ApiError, void>({
    mutationFn: () => apiClient.delete<LLMConfigRead>(API_ROUTES.SETTINGS_LLM),
    onSuccess: (data) => qc.setQueryData(KEY, data),
  });
}
