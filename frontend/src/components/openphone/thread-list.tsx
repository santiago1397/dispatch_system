"use client";

import { Loader2, Phone } from "lucide-react";
import { cn } from "@/lib/utils";
import { useOpenPhoneThreads } from "@/hooks";
import { EmptyState } from "@/components/whatsapp/empty-state";
import type { OpenPhoneThreadSummary } from "@/types";

interface ThreadListProps {
  activeCounterparty: string | null;
  onSelect: (counterparty: string) => void;
}

function displayNameFor(t: OpenPhoneThreadSummary): string {
  return t.label || t.company_display_name || t.company_name || t.counterparty;
}

function formatLastActivity(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffSec = Math.round((now.getTime() - d.getTime()) / 1000);
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return d.toLocaleDateString();
}

export function ThreadList({ activeCounterparty, onSelect }: ThreadListProps) {
  const { data: threads, isLoading, isError } = useOpenPhoneThreads();

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
        title="Could not load threads"
        description="The backend returned an error. Check the server logs."
      />
    );
  }

  if (!threads || threads.length === 0) {
    return (
      <EmptyState
        title="No OpenPhone messages yet"
        description="Threads appear here once messages arrive through the OpenPhone webhook."
      />
    );
  }

  return (
    <ul className="flex-1 overflow-y-auto p-2">
      {threads.map((t: OpenPhoneThreadSummary) => {
        const isActive = t.counterparty === activeCounterparty;
        return (
          <li key={t.counterparty}>
            <button
              type="button"
              onClick={() => onSelect(t.counterparty)}
              className={cn(
                "flex w-full min-h-[44px] items-start gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors",
                isActive
                  ? "bg-secondary text-secondary-foreground"
                  : "text-foreground hover:bg-secondary/50"
              )}
            >
              <Phone className="mt-0.5 h-4 w-4 shrink-0 opacity-70" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">{displayNameFor(t)}</span>
                  <span className="text-muted-foreground shrink-0 text-[10px]">
                    {formatLastActivity(t.last_created_at)}
                  </span>
                </div>
                <div className="text-muted-foreground truncate text-xs">
                  {t.last_direction === "outgoing" ? "You: " : ""}
                  {t.last_content || "(no content)"}
                </div>
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
