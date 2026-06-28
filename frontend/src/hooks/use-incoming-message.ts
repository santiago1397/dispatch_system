"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type { IncomingMessage } from "@/types";

/**
 * Fetch a single IncomingMessage by id. The Jobs detail pane uses this
 * to retrieve the original message body and metadata (source channel,
 * sender phone, arrival time) that the classifier read.
 *
 * Refetches on demand only — paired with the dispatch-job detail, so
 * reclassify triggers both to refetch.
 */
export function useIncomingMessage(id: string | null) {
  return useQuery<IncomingMessage, ApiError>({
    queryKey: ["incoming-message", id],
    queryFn: () => apiClient.get<IncomingMessage>(API_ROUTES.INCOMING_MESSAGE(id!)),
    enabled: !!id,
    staleTime: 10_000,
  });
}
