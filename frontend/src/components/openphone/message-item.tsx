"use client";

import { cn } from "@/lib/utils";
import type { IncomingMessage } from "@/types";

interface MessageItemProps {
  message: IncomingMessage;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

export function MessageItem({ message }: MessageItemProps) {
  const isMine = message.direction === "outgoing";
  const bodyText = message.content ?? "(no content)";

  return (
    <div className={cn("flex w-full", isMine ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[75%] rounded-lg px-3 py-2 text-sm shadow-sm",
          isMine ? "bg-primary text-primary-foreground" : "bg-muted text-foreground"
        )}
      >
        <div className="whitespace-pre-wrap break-words">{bodyText}</div>
        <div className={cn("mt-1 text-[10px] opacity-60", isMine ? "text-right" : "text-left")}>
          {formatTime(message.created_at)}
        </div>
      </div>
    </div>
  );
}
