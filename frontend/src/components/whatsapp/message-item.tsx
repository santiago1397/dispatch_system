"use client";

import { cn } from "@/lib/utils";
import type { WhatsappMessage } from "@/types";

interface MessageItemProps {
  message: WhatsappMessage;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

export function MessageItem({ message }: MessageItemProps) {
  const isMine = message.is_from_me;
  const isDeleted = message.is_deleted;
  const sender = isMine ? "You" : message.sender_name || "(unknown)";
  const bodyText = isDeleted
    ? "This message was deleted"
    : message.body ?? "(no content)";

  return (
    <div className={cn("flex w-full", isMine ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[75%] rounded-lg px-3 py-2 text-sm shadow-sm",
          isMine
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-foreground",
          isDeleted && "italic opacity-70"
        )}
      >
        {!isMine ? (
          <div className="mb-0.5 text-xs font-semibold opacity-80">{sender}</div>
        ) : null}
        <div className="whitespace-pre-wrap break-words">{bodyText}</div>
        <div
          className={cn(
            "mt-1 text-[10px] opacity-60",
            isMine ? "text-right" : "text-left"
          )}
        >
          {formatTime(message.timestamp)}
        </div>
      </div>
    </div>
  );
}
