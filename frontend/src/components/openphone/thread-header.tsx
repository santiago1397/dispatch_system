"use client";

import { useState } from "react";
import { Pencil } from "lucide-react";
import { useOpenPhoneThreads } from "@/hooks";
import { ThreadLabelEditor } from "./thread-label-editor";

interface ThreadHeaderProps {
  counterparty: string;
}

export function ThreadHeader({ counterparty }: ThreadHeaderProps) {
  const { data: threads } = useOpenPhoneThreads();
  const [editing, setEditing] = useState(false);

  const thread = threads?.find((t) => t.counterparty === counterparty);
  const displayName =
    thread?.label || thread?.company_display_name || thread?.company_name || counterparty;
  const showCounterpartySubtitle = displayName !== counterparty;

  return (
    <div className="border-b">
      <div className="flex h-12 items-center justify-between gap-2 px-4">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{displayName}</div>
          {showCounterpartySubtitle ? (
            <div className="text-muted-foreground truncate text-xs">{counterparty}</div>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => setEditing((v) => !v)}
          className="text-muted-foreground hover:bg-secondary hover:text-foreground shrink-0 rounded p-1.5 transition-colors"
          aria-label="Edit company / label"
        >
          <Pencil className="h-4 w-4" />
        </button>
      </div>
      {editing ? (
        <ThreadLabelEditor
          counterparty={counterparty}
          initialCompanyId={thread?.company_id ?? null}
          initialLabel={thread?.label ?? null}
          onClose={() => setEditing(false)}
        />
      ) : null}
    </div>
  );
}
