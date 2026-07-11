"use client";

import { useEffect, useMemo, useRef } from "react";
import { Loader2 } from "lucide-react";
import { MessageItem } from "./message-item";
import { DateDivider, dayKey } from "@/components/whatsapp/date-divider";
import { EmptyState } from "@/components/whatsapp/empty-state";
import { useOpenPhoneThreadMessages } from "@/hooks";
import type { IncomingMessage } from "@/types";

interface MessageListProps {
  counterparty: string;
}

type ListItem =
  | { kind: "divider"; key: string; date: string }
  | { kind: "message"; key: string; message: IncomingMessage };

// Flatten pages (each is created_at DESC) into a chronological list
// (oldest first, newest last) interleaved with date dividers.
function flattenWithDividers(pages: { items: IncomingMessage[] }[] | undefined): ListItem[] {
  const out: ListItem[] = [];
  if (!pages) return out;
  let lastDayKey: string | null = null;
  for (const page of pages) {
    for (const m of [...page.items].reverse()) {
      const dk = dayKey(m.created_at);
      if (dk !== lastDayKey) {
        out.push({ kind: "divider", key: `d:${dk}`, date: m.created_at });
        lastDayKey = dk;
      }
      out.push({ kind: "message", key: m.id, message: m });
    }
  }
  return out;
}

const SCROLL_NEAR_BOTTOM_PX = 80;

export function MessageList({ counterparty }: MessageListProps) {
  const { data, isLoading, isError, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useOpenPhoneThreadMessages(counterparty);

  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  // Sentinel: when the user scrolls near the TOP of the list (oldest
  // messages), fetch the next older page. The sentinel is the first
  // child of the scroller, so "near the top" means "near the sentinel".
  useEffect(() => {
    const sentinel = sentinelRef.current;
    const scroller = scrollerRef.current;
    if (!sentinel || !scroller) return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            if (hasNextPage && !isFetchingNextPage) fetchNextPage();
          }
        }
      },
      { root: scroller, rootMargin: "200px 0px 0px 0px" }
    );
    io.observe(sentinel);
    return () => io.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  // On thread switch, jump the scroller to the bottom so the user sees
  // the newest messages. We do this in a microtask after the items
  // paint, otherwise the new scrollHeight is not yet available.
  useEffect(() => {
    requestAnimationFrame(() => {
      const el = scrollerRef.current;
      if (!el) return;
      el.scrollTop = el.scrollHeight;
    });
  }, [counterparty]);

  // When a refetch brings new messages (polling), keep the user's
  // current scroll position unless they were already at the bottom —
  // in which case, snap to the new bottom so the new message is visible.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const wasNearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_NEAR_BOTTOM_PX;
    if (wasNearBottom) {
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
      });
    }
  }, [data]);

  const items = useMemo(() => flattenWithDividers(data?.pages), [data]);

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
        title="Could not load messages"
        description="The backend returned an error. Check the server logs."
      />
    );
  }

  if (items.length === 0) {
    return (
      <EmptyState
        title="No messages yet"
        description="Messages exchanged with this number will appear here once they arrive through the OpenPhone webhook."
      />
    );
  }

  return (
    <div ref={scrollerRef} className="flex h-full flex-col overflow-y-auto p-4">
      <div ref={sentinelRef} className="h-1 shrink-0" />
      {isFetchingNextPage ? (
        <div className="text-muted-foreground flex justify-center py-2 text-xs">
          <Loader2 className="mr-1 h-3 w-3 animate-spin" /> loading older messages…
        </div>
      ) : !hasNextPage ? (
        <div className="text-muted-foreground py-2 text-center text-[10px]">
          — start of history —
        </div>
      ) : null}
      <div className="flex flex-col gap-2">
        {items.map((it) =>
          it.kind === "divider" ? (
            <DateDivider key={it.key} date={it.date} />
          ) : (
            <MessageItem key={it.key} message={it.message} />
          )
        )}
      </div>
    </div>
  );
}
