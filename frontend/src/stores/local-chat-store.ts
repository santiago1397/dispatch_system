"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { nanoid } from "nanoid";
import type { ChatMessage, ToolCall } from "@/types";

const STORAGE_KEY = "agents_bots-local-chats";

export interface LocalConversation {
  id: string;
  title: string | null;
  messages: ChatMessage[];
  createdAt: string;
  updatedAt: string;
}

interface LocalChatState {
  conversations: LocalConversation[];
  currentConversationId: string | null;

  createConversation: () => string;
  selectConversation: (id: string | null) => void;
  deleteConversation: (id: string) => void;
  renameConversation: (id: string, title: string) => void;
  getCurrentConversation: () => LocalConversation | null;
  getCurrentMessages: () => ChatMessage[];

  addMessage: (message: ChatMessage) => void;
  updateMessage: (id: string, updater: (msg: ChatMessage) => ChatMessage) => void;
  addToolCall: (messageId: string, toolCall: ToolCall) => void;
  updateToolCall: (messageId: string, toolCallId: string, update: Partial<ToolCall>) => void;
  clearCurrentMessages: () => void;

  reset: () => void;
}

const initialState = {
  conversations: [] as LocalConversation[],
  currentConversationId: null as string | null,
};

export const useLocalChatStore = create<LocalChatState>()(
  persist(
    (set, get) => ({
      ...initialState,

      createConversation: () => {
        const id = nanoid();
        const now = new Date().toISOString();
        const newConversation: LocalConversation = {
          id,
          title: null,
          messages: [],
          createdAt: now,
          updatedAt: now,
        };
        set((state) => ({
          conversations: [newConversation, ...state.conversations],
          currentConversationId: id,
        }));
        return id;
      },

      selectConversation: (id) => {
        set({ currentConversationId: id });
      },

      deleteConversation: (id) => {
        set((state) => ({
          conversations: state.conversations.filter((c) => c.id !== id),
          currentConversationId:
            state.currentConversationId === id ? null : state.currentConversationId,
        }));
      },

      renameConversation: (id, title) => {
        set((state) => ({
          conversations: state.conversations.map((c) =>
            c.id === id ? { ...c, title, updatedAt: new Date().toISOString() } : c
          ),
        }));
      },

      getCurrentConversation: () => {
        const state = get();
        return state.conversations.find((c) => c.id === state.currentConversationId) || null;
      },

      getCurrentMessages: () => {
        const state = get();
        const conversation = state.conversations.find((c) => c.id === state.currentConversationId);
        return conversation?.messages || [];
      },

      addMessage: (message) => {
        set((state) => {
          const convId = state.currentConversationId;
          if (!convId) return state;

          return {
            conversations: state.conversations.map((c) =>
              c.id === convId
                ? {
                    ...c,
                    messages: [...c.messages, message],
                    updatedAt: new Date().toISOString(),
                    title:
                      c.title ||
                      (message.role === "user"
                        ? message.content.slice(0, 50) + (message.content.length > 50 ? "..." : "")
                        : null),
                  }
                : c
            ),
          };
        });
      },

      updateMessage: (id, updater) => {
        set((state) => {
          const convId = state.currentConversationId;
          if (!convId) return state;

          return {
            conversations: state.conversations.map((c) =>
              c.id === convId
                ? {
                    ...c,
                    messages: c.messages.map((msg) => (msg.id === id ? updater(msg) : msg)),
                    updatedAt: new Date().toISOString(),
                  }
                : c
            ),
          };
        });
      },

      addToolCall: (messageId, toolCall) => {
        set((state) => {
          const convId = state.currentConversationId;
          if (!convId) return state;

          return {
            conversations: state.conversations.map((c) =>
              c.id === convId
                ? {
                    ...c,
                    messages: c.messages.map((msg) =>
                      msg.id === messageId
                        ? {
                            ...msg,
                            toolCalls: [...(msg.toolCalls || []), toolCall],
                          }
                        : msg
                    ),
                    updatedAt: new Date().toISOString(),
                  }
                : c
            ),
          };
        });
      },

      updateToolCall: (messageId, toolCallId, update) => {
        set((state) => {
          const convId = state.currentConversationId;
          if (!convId) return state;

          return {
            conversations: state.conversations.map((c) =>
              c.id === convId
                ? {
                    ...c,
                    messages: c.messages.map((msg) =>
                      msg.id === messageId
                        ? {
                            ...msg,
                            toolCalls: msg.toolCalls?.map((tc) =>
                              tc.id === toolCallId ? { ...tc, ...update } : tc
                            ),
                          }
                        : msg
                    ),
                    updatedAt: new Date().toISOString(),
                  }
                : c
            ),
          };
        });
      },

      clearCurrentMessages: () => {
        set((state) => {
          const convId = state.currentConversationId;
          if (!convId) return state;

          return {
            conversations: state.conversations.map((c) =>
              c.id === convId ? { ...c, messages: [], updatedAt: new Date().toISOString() } : c
            ),
          };
        });
      },

      reset: () => set(initialState),
    }),
    {
      name: STORAGE_KEY,
      partialize: (state) => ({
        conversations: state.conversations.map((c) => ({
          ...c,
          messages: c.messages.map((m) => ({
            ...m,
            timestamp: m.timestamp instanceof Date ? m.timestamp.toISOString() : m.timestamp,
          })),
        })),
        currentConversationId: state.currentConversationId,
      }),
      onRehydrateStorage: () => (state) => {
        if (state) {
          state.conversations = state.conversations.map((c) => ({
            ...c,
            messages: c.messages.map((m) => ({
              ...m,
              timestamp: new Date(m.timestamp as unknown as string),
            })),
          }));
        }
      },
    }
  )
);
