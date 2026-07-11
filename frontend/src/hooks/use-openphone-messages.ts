"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type { IncomingMessage } from "@/types";

const PAGE_SIZE = 100;

interface IncomingMessageList {
  items: IncomingMessage[];
  total: number;
}

interface MessagesPage {
  items: IncomingMessage[];
  nextSkip: number | null;
  total: number;
}

async function fetchMessagesPage(counterparty: string, skip: number): Promise<MessagesPage> {
  const data = await apiClient.get<IncomingMessageList>(
    API_ROUTES.OPENPHONE_THREAD_MESSAGES(counterparty),
    {
      params: {
        skip: String(skip),
        limit: String(PAGE_SIZE),
      },
    }
  );
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const nextSkip = skip + items.length < total ? skip + items.length : null;
  return { items, nextSkip, total };
}

export function useOpenPhoneThreadMessages(counterparty: string | null) {
  return useInfiniteQuery<MessagesPage, ApiError>({
    queryKey: ["openphone", "thread-messages", counterparty],
    queryFn: ({ pageParam }) => fetchMessagesPage(counterparty!, pageParam as number),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.nextSkip,
    enabled: !!counterparty,
    refetchOnWindowFocus: true,
    refetchInterval: 30_000,
    staleTime: 10_000,
  });
}

export { PAGE_SIZE as OPENPHONE_MESSAGES_PAGE_SIZE };
