"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type { WhatsappMessage, WhatsappMessageList } from "@/types";

const PAGE_SIZE = 100;

interface MessagesPage {
  items: WhatsappMessage[];
  nextSkip: number | null;
  total: number;
}

async function fetchMessagesPage(
  chatJid: string,
  skip: number
): Promise<MessagesPage> {
  const data = await apiClient.get<WhatsappMessageList>(
    API_ROUTES.WHATSAPP_MESSAGES,
    {
      params: {
        chat_jid: chatJid,
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

export function useWhatsappMessages(chatJid: string | null) {
  return useInfiniteQuery<MessagesPage, ApiError>({
    queryKey: ["whatsapp", "messages", chatJid],
    queryFn: ({ pageParam }) => fetchMessagesPage(chatJid!, pageParam as number),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.nextSkip,
    enabled: !!chatJid,
    refetchOnWindowFocus: true,
    refetchInterval: 30_000,
    staleTime: 10_000,
  });
}

export { PAGE_SIZE as WHATSAPP_MESSAGES_PAGE_SIZE };
