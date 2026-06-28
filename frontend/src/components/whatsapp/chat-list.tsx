"use client";

import { Loader2, MessageCircle, MessageCircleOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTrackedChats } from "@/hooks";
import { EmptyState } from "./empty-state";
import type { WhatsappTrackedChat } from "@/types";

interface ChatListProps {
  activeChatJid: string | null;
  onSelect: (chatJid: string) => void;
}

function formatLastScraped(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  const now = new Date();
  const diffSec = Math.round((now.getTime() - d.getTime()) / 1000);
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return d.toLocaleDateString();
}

function sortChats(chats: WhatsappTrackedChat[]): WhatsappTrackedChat[] {
  return [...chats].sort((a, b) => {
    if (a.is_active !== b.is_active) return a.is_active ? -1 : 1;
    const aTs = a.last_scraped_at ? new Date(a.last_scraped_at).getTime() : 0;
    const bTs = b.last_scraped_at ? new Date(b.last_scraped_at).getTime() : 0;
    if (aTs !== bTs) return bTs - aTs;
    return a.display_name.localeCompare(b.display_name);
  });
}

export function ChatList({ activeChatJid, onSelect }: ChatListProps) {
  const { data: chats, isLoading, isError } = useTrackedChats();

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="text-muted-foreground h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (isError) {
    return (
      <EmptyState
        title="Could not load chats"
        description="The backend returned an error. Check the server logs."
      />
    );
  }

  if (!chats || chats.length === 0) {
    return (
      <EmptyState
        title="No WhatsApp chats tracked"
        description="Open the Dispatch extension, open a WhatsApp Web chat, and click Track to add it to the whitelist."
      />
    );
  }

  const sorted = sortChats(chats);

  return (
    <ul className="flex-1 overflow-y-auto p-2">
      {sorted.map((c) => {
        const isActive = c.chat_jid === activeChatJid;
        return (
          <li key={c.id}>
            <button
              type="button"
              onClick={() => onSelect(c.chat_jid)}
              className={cn(
                "flex w-full min-h-[44px] items-start gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors",
                isActive
                  ? "bg-secondary text-secondary-foreground"
                  : "text-foreground hover:bg-secondary/50"
              )}
            >
              {c.is_active ? (
                <MessageCircle className="mt-0.5 h-4 w-4 shrink-0 opacity-70" />
              ) : (
                <MessageCircleOff className="mt-0.5 h-4 w-4 shrink-0 opacity-40" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">{c.display_name}</span>
                  {!c.is_active ? (
                    <span className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10px]">
                      inactive
                    </span>
                  ) : null}
                </div>
                <div className="text-muted-foreground truncate text-xs">
                  {c.is_group ? "group" : "1:1"} · last scrape {formatLastScraped(c.last_scraped_at)}
                </div>
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
