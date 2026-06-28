"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/lib/api-client";
import { API_ROUTES } from "@/lib/constants";
import type {
  WhatsappTrackedChat,
  WhatsappTrackedChatList,
} from "@/types";

async function fetchTrackedChats(): Promise<WhatsappTrackedChat[]> {
  const data = await apiClient.get<WhatsappTrackedChatList>(
    API_ROUTES.WHATSAPP_TRACKED_CHATS
  );
  return data?.items ?? [];
}

export function useTrackedChats() {
  return useQuery<WhatsappTrackedChat[], ApiError>({
    queryKey: ["whatsapp", "tracked-chats"],
    queryFn: fetchTrackedChats,
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  });
}

export interface UpdateTrackedChatInput {
  chat_jid: string;
  display_name?: string | null;
  is_active?: boolean | null;
  chat_role?: string | null;
}

/**
 * Patch a tracked chat. Used by the /chat-roles admin page to flip a
 * chat's role to ``tech_dispatch`` (or back to ``other``).
 */
export function useUpdateTrackedChat() {
  const qc = useQueryClient();
  return useMutation<WhatsappTrackedChat, ApiError, UpdateTrackedChatInput>({
    mutationFn: ({ chat_jid, ...body }) =>
      apiClient.patch<WhatsappTrackedChat>(
        `/whatsapp/tracked-chats/${encodeURIComponent(chat_jid)}`,
        { body }
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["whatsapp", "tracked-chats"] });
    },
  });
}
