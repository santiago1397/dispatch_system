"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type { OpenPhoneThreadLabelRead, OpenPhoneThreadLabelUpsert } from "@/types";

export interface SetThreadLabelInput extends OpenPhoneThreadLabelUpsert {
  counterparty: string;
}

/**
 * Set a thread's company reference and/or free-text label. Display-only —
 * mirrors `PUT /openphone/threads/{counterparty}/label` on the backend,
 * which never touches classification. Invalidates the thread list so the
 * sidebar picks up the new name immediately.
 */
export function useSetOpenPhoneThreadLabel() {
  const qc = useQueryClient();
  return useMutation<OpenPhoneThreadLabelRead, ApiError, SetThreadLabelInput>({
    mutationFn: ({ counterparty, ...body }) =>
      apiClient.put<OpenPhoneThreadLabelRead>(API_ROUTES.OPENPHONE_THREAD_LABEL(counterparty), body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["openphone", "threads"] });
    },
  });
}

/**
 * Clear a thread's company reference/label.
 */
export function useClearOpenPhoneThreadLabel() {
  const qc = useQueryClient();
  return useMutation<void, ApiError, string>({
    mutationFn: (counterparty) =>
      apiClient.delete<void>(API_ROUTES.OPENPHONE_THREAD_LABEL(counterparty)),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["openphone", "threads"] });
    },
  });
}
